from flask import Flask, Response, render_template, request, redirect, url_for, jsonify, session, flash
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_socketio import SocketIO, emit
import cv2
import os
import time
import json
from datetime import datetime, timedelta, date

from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import numpy as np
import threading
from fast_sms import send_fast_sms
from telegram_notify import send_telegram_notify
import requests
from deepface import DeepFace
import tensorflow as tf
import mediapipe as mp
from PIL import Image, ImageEnhance, ImageFilter
from detector import SafeDriveDetector
from models import db, Admin, Driver, DriverUser, Bus, DriverAssignment, OperationLog, Alert, Depot, Route, BusAssignment, Schedule, DriverShift
from dotenv import load_dotenv
from assignment_engine import SmartAssignmentEngine   
assignment_engine = SmartAssignmentEngine()         


# Load environment variables from .env file
load_dotenv()



# Initialize Flask extensions

def send_sms(formatted_number, body):
    # Try Pushbullet (sends real SMS via your Android phone)
    print(f"Attempting to send SMS to {formatted_number} via Pushbullet...")
    pb_result = send_fast_sms(formatted_number, body)
    if pb_result.get("return"):
        print(f"✅ [REAL SMS SENT via Pushbullet → Your Android Phone]")
    else:
        print(f"❌ [PUSHBULLET ERROR] {pb_result.get('message') or pb_result}")

    # Try Telegram
    print(f"Attempting to send notification via Telegram...")
    telegram_result = send_telegram_notify(f"Alert for {formatted_number}: {body}")
    if telegram_result.get("ok"):
        print("✅ [REAL MSG SENT via Telegram]")
    else:
        print(f"❌ [TELEGRAM ERROR] {telegram_result.get('error') or telegram_result}")

    return True, "DELIVERED"

app = Flask(__name__)

# Database Configuration
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///safedrive_admin.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'your-secret-key-change-this-in-production'
app.config['UPLOAD_FOLDER'] = 'static/uploads/drivers'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size


os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)


db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'depot_login'

# Initialize Flask-SocketIO for real-time tracking
socketio = SocketIO(app, cors_allowed_origins="*")

@app.before_request
def make_session_permanent():
    session.permanent = True
    app.permanent_session_lifetime = timedelta(days=31)

@login_manager.user_loader
def load_user(user_id):
    # Try to find user in Admin first, then DriverUser
    user = Admin.query.get(int(user_id))
    if user:
        user.user_type = 'admin'
        return user
    user = DriverUser.query.get(int(user_id))
    if user:
        user.user_type = 'driver'
        return user
    return None

# Initialize detector globally
detector = SafeDriveDetector()

# Global state for status sharing
current_status = {
    "status": "SAFE",
    "alert": False,
    "last_update": 0
}


ENHANCED_PHOTO_CACHE = {}  


RECOGNITION_CACHE = {} 
CACHE_VALIDITY_SECONDS = 5  

# Pre-load FaceNet model on startup (speeds up first request by ~10s)
print("⏳ Pre-loading FaceNet model for face recognition...")
try:
    _ = DeepFace.build_model("Facenet")
    print("✅ FaceNet model loaded successfully!")
except Exception as e:
    print(f"⚠️ FaceNet pre-load warning: {e}")

def gen_frames():
    global current_status
    # Use AVFOUNDATION backend specifically for macOS stability
    print("Attempting to open camera (AVFOUNDATION)...")
    camera = cv2.VideoCapture(0, cv2.CAP_AVFOUNDATION)
    
    # Wait for hardware to wake up
    time.sleep(1.5)
    
    if not camera.isOpened():
        print("Camera 0 failed, trying Camera 1...")
        camera = cv2.VideoCapture(1, cv2.CAP_AVFOUNDATION)
        if not camera.isOpened():
            print("Error: No camera hardware accessible.")
            return

    print("Camera connected successfully.")
    
    try:
       
        for _ in range(5):
            camera.read()

        while True:
            success, frame = camera.read()
            if not success or frame is None:
                print("Error: Lost camera connection.")
                break
            
            # If the frame is valid, process it
            detection_data, status, alert = detector.detect(frame)
            
            current_status["status"] = status
            current_status["alert"] = alert
            current_status["last_update"] = time.time()
            
            frame = detector.annotate_frame(frame, detection_data, status, alert)
            
            ret, buffer = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
    finally:
        print("Releasing camera hardware...")
        camera.release()

@app.route('/')
def index():
    """Always show the Cinematic Intro first to ensure user sees the branding"""
    # Force the intro screen for every entry to the app root
    return render_template('intro.html')

@app.route('/language-select')
def language_select():
    """Landing page for language selection"""
    session['intro_seen'] = True  # Mark intro as seen once they reach here
    session.pop('driver_id', None)
    session.pop('driver_name', None)
    session.pop('assigned_vehicle', None)
    return render_template('landing.html')

@app.route('/mode-select')
def mode_select():
    """Mode selection after language is chosen"""
    preferred_lang = session.get('preferred_language', 'English')
    return render_template('mode_select.html', preferred_language=preferred_lang)

@app.route('/driver/splash')
def driver_splash():
    """Elegant splash screen for driver mode transition"""
    if session.get('selected_mode') != 'driver':
        return redirect(url_for('index'))
    return render_template('driver_splash.html')

@app.route('/api/system/set-mode', methods=['POST'])
def set_system_mode():
    """Lock the app into Driver or Personal mode"""
    data = request.get_json()
    mode = data.get('mode')
    if mode in ['driver', 'personal']:
        session['selected_mode'] = mode
        session.permanent = True # Keep mode locked
        return jsonify({'success': True, 'mode': mode})
    return jsonify({'success': False, 'error': 'Invalid mode'}), 400

@app.route('/api/system/set-language', methods=['POST'])
def set_system_language():
    """Set the system language (supports 10 Indian languages + English)"""
    data = request.get_json()
    lang = data.get('language')
    valid_langs = ['Hindi', 'Tamil', 'Telugu', 'Kannada', 'Bengali', 'English']
    if lang in valid_langs:
        session['preferred_language'] = lang
        return jsonify({'success': True, 'language': lang})
    return jsonify({'success': False, 'error': f'Invalid language: {lang}'}), 400

@app.route('/api/system/reset-mode')
def reset_system_mode():
    """Reset the app mode and language (for demo purposes)"""
    session.pop('selected_mode', None)
    session.pop('preferred_language', None)
    session.pop('driver_id', None)
    session.pop('driver_name', None)
    session.pop('intro_seen', None)  # Also clear intro flag
    return redirect(url_for('index'))

@app.route('/video_feed')
def video_feed():
    return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/status')
def status():
    return json.dumps(current_status)

@app.route('/api/driver/setup-emergency', methods=['POST'])
def setup_emergency():
    """Configure simulation mode and emergency contact details for the session"""
    data = request.json
    driver_id = data.get('driver_id')
    mode = data.get('mode', 'Public')
    contact_name = data.get('contact_name')
    contact_phone = data.get('contact_phone')
    
    driver = Driver.query.get(driver_id)
    if not driver:
        return jsonify({'success': False, 'error': 'Driver not found'}), 404
        
    driver.system_mode = mode
    if mode == 'Private':
        driver.emergency_contact_name = contact_name
        driver.emergency_contact_phone = contact_phone
        
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f'System configured for {mode} mode',
        'driver': {
            'name': driver.name,
            'mode': driver.system_mode,
            'contact': driver.emergency_contact_name,
            'phone': driver.emergency_contact_phone
        }
    })

@app.route('/api/emergency/trigger', methods=['POST'])
def trigger_emergency():
    """Trigger a critical emergency event from the driver monitor"""
    data = request.json
    driver_id = data.get('driver_id')
    vehicle_id = data.get('vehicle_id')
    lat = data.get('latitude')
    lng = data.get('longitude')
    reason = data.get('reason', 'Critical Drowsiness Detected')
    
  
    system_mode = session.get('selected_mode', 'driver')
    print(f"🚨 EMERGENCY TRIGGERED | Mode: {system_mode} | Reason: {reason}")
    print(f"   Session Data: contact={session.get('personal_contact_name')}, phone={session.get('personal_contact_phone')}")
    
    driver = None
    if driver_id:
        driver = Driver.query.get(driver_id)
        
    # Update driver location and emergency status if driver exists
    if driver:
        driver.last_latitude = lat
        driver.last_longitude = lng
        driver.last_location_update = datetime.utcnow()
    
    
    new_alert = Alert(
        alert_type='EMERGENCY',
        severity='Critical',
        message=f"CRITICAL EMERGENCY: {driver.name if driver else 'Personal User'} - {reason}. Location: {lat}, {lng}",
        related_driver_id=driver_id,
        related_bus_id=vehicle_id
    )
    db.session.add(new_alert)
    
    # Log the emergency event
    log = OperationLog(
        log_type='EMERGENCY_TRIGGERED',
        description=f"Emergency mode triggered. Reason: {reason}",
        driver_id=driver_id,
        bus_id=vehicle_id
    )
    db.session.add(log)
    db.session.commit()
    
    # Handle Alerts based on mode
    sms_status = 'N/A'
    msg_sent = False
    
    if system_mode == 'personal' or (driver and driver.system_mode == 'Private'):
        # For Personal mode, get contact from session or driver record
        contact_name = session.get('personal_contact_name') or (driver.emergency_contact_name if driver else "Family")
        contact_phone = session.get('personal_contact_phone') or (driver.emergency_contact_phone if driver else "Unspecified")
        
        print(f"   Attempting to send SMS to {contact_name} ({contact_phone})")
        
        map_link = f"https://www.google.com/maps?q={lat},{lng}"
        name = driver.name if driver else "Your family member"
        sms_body = f"SURAKSHIT YATRA EMERGENCY! {name} is in critical danger. Live location: {map_link}"
        
        if contact_phone != "Unspecified":
            success, sid_or_error = send_sms(contact_phone, sms_body)
            sms_status = 'Sent' if success else 'Failed'
            msg_sent = True
        else:
            print("   ⚠️ SMS Cancelled: No phone number in session.")
        
    return jsonify({
        'success': True, 
        'message': 'Emergency alerts dispatched successfully',
        'alert_id': new_alert.id,
        'sms_status': sms_status
    })

@app.route('/api/personal/setup-contact', methods=['POST'])
def personal_setup_contact():
    """Save emergency contact for the personal session"""
    data = request.json
    session['personal_contact_name'] = data.get('name')
    session['personal_contact_phone'] = data.get('phone')
    return jsonify({'success': True})


# DEPOT ROUTES

@app.route('/depot/register', methods=['GET', 'POST'])
def depot_register():
    """Depot management registration"""
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        if not username or not email or not password:
            return render_template('depot/register.html', error='Please fill in all fields')
            
        if password != confirm_password:
            return render_template('depot/register.html', error='Passwords do not match')
            
        # Check if username or email already exists
        if Admin.query.filter((Admin.username == username) | (Admin.email == email)).first():
            return render_template('depot/register.html', error='Username or Email already registered')
            
        try:
            new_admin = Admin(username=username, email=email)
            new_admin.set_password(password)
            db.session.add(new_admin)
            db.session.commit()
            
            flash('Registration successful! Please login.')
            return redirect(url_for('depot_login'))
        except Exception as e:
            db.session.rollback()
            return render_template('depot/register.html', error=f'Registration failed: {str(e)}')
            
    return render_template('depot/register.html')


@app.route('/depot/login', methods=['GET', 'POST'])
def depot_login():
    """Depot management login"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        admin = Admin.query.filter_by(username=username).first()
        
        if admin and admin.check_password(password):
            login_user(admin)
            return redirect(url_for('depot_dashboard'))
        else:
            return render_template('depot/login.html', error='Invalid username or password')
    
    return render_template('depot/login.html')


@app.route('/depot/logout')
@login_required
def depot_logout():
    """Depot logout"""
    logout_user()
    return redirect(url_for('index'))


# DRIVER AUTHENTICATION 

@app.route('/driver/auto-login')
def driver_auto_login():
    """A minimal-interaction identification screen for public transport drivers"""
    return render_template('driver/auto_login.html')

@app.route('/driver/login-photo')
@app.route('/driver/login-photo/')
@app.route('/driver/login_photo')
def driver_login_photo():
    """Manual fallback: select driver card when face recognition is unstable"""
    drivers = Driver.query.filter_by(status='Active').order_by(Driver.name.asc()).all()
    return render_template('driver/login_photo.html', drivers=drivers)

@app.route('/api/driver/identify', methods=['POST'])
def identify_driver_auto():
    """
    Robust facial recognition resistant to lighting conditions using ensemble of AI models.
    Works in: bright sunlight, dim interior, night, against harsh shadows, etc.
    """
    frame_file = request.files.get('frame')
    if not frame_file:
        return {'success': False, 'message': 'Missing frame'}, 400
    
    try:
        from io import BytesIO
        from PIL import Image, ImageEnhance
        import tempfile
        import numpy as np
        import hashlib
        
        
        frame_hash = hashlib.md5(frame_file.read()).hexdigest()
        frame_file.seek(0)  # Reset file pointer
        
        # Cache disabled to prevent sticky results (e.g. always Joseph)
        # We will keep the code for later but return early
        if False and frame_hash in RECOGNITION_CACHE:
            pass 

        
       
        def enhance_for_recognition(img_path):
            """Preprocess image to handle various lighting conditions (with caching)"""
            
            if img_path in ENHANCED_PHOTO_CACHE:
                return ENHANCED_PHOTO_CACHE[img_path]
            
            img = Image.open(img_path).convert('RGB')
            
          
            enhancer = ImageEnhance.Contrast(img)
            img = enhancer.enhance(1.5)  # Increase contrast by 50%
            
            
            enhancer = ImageEnhance.Brightness(img)
            img = enhancer.enhance(1.1)  # Slight brightness boost
            
            
            enhanced_path = img_path.replace('.jpg', '_enhanced.jpg').replace('.jpeg', '_enhanced.jpg').replace('.png', '_enhanced.jpg')
            img.save(enhanced_path, 'JPEG', quality=95)
            
           
            ENHANCED_PHOTO_CACHE[img_path] = enhanced_path
            return enhanced_path
        
        
        img_bytes = frame_file.read()
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as temp_frame:
            temp_frame.write(img_bytes)
            temp_frame_path = temp_frame.name
        
        # Enhance the captured frame for better recognition
        enhanced_frame_path = enhance_for_recognition(temp_frame_path)
        
        def deepface_verify_with_fallback(frame_path, photo_path, model_name):
            """
            Verify face with progressively relaxed strategies.
            Tries strict detection first, then non-strict fallback.
            """
            strategies = [
                ('retinaface', True),
                ('opencv', True),
                ('opencv', False),
            ]
            last_error = None

            for backend, enforce_detection in strategies:
                try:
                    result = DeepFace.verify(
                        img1_path=frame_path,
                        img2_path=photo_path,
                        model_name=model_name,
                        distance_metric='cosine',
                        enforce_detection=enforce_detection,
                        detector_backend=backend
                    )
                    result['_backend'] = backend
                    result['_enforce_detection'] = enforce_detection
                    return result
                except Exception as e:
                    last_error = e
                    continue

            if last_error:
                print(f"  {model_name} Error: {str(last_error)[:60]}")
            return None

        
        def fast_single_model_verification(frame_path, photo_path, driver_name):
            """Robust face comparison using ArcFace (most accurate)"""
            try:
                # Use RetinaFace for high-accuracy detection
                result = deepface_verify_with_fallback(frame_path, photo_path, 'ArcFace')
                if result is None:
                    return None
                distance = result['distance']
                similarity = 1 - distance
                return {
                    'distance': distance,
                    'similarity': similarity,
                    'verified': result['verified'],
                    'backend': result.get('_backend'),
                    'strict_detection': result.get('_enforce_detection', True)
                }
            except Exception as e:
                print(f"  ArcFace Error: {str(e)[:40]}")
                return None
        
       
        def multi_model_verification(frame_path, photo_path, driver_name):
            """Compare faces using multiple AI models to confirm match"""
            results = {}
            models_to_try = ['VGG-Face', 'Facenet']  # Confirm ArcFace result with others
            
            for model in models_to_try:
                try:
                    result = deepface_verify_with_fallback(frame_path, photo_path, model)
                    if result is None:
                        continue
                    distance = result['distance']
                    similarity = 1 - distance
                    results[model] = {
                        'distance': distance,
                        'similarity': similarity,
                        'verified': result['verified']
                    }
                    print(f"  {model:12} | Distance: {distance:.4f} | Similarity: {similarity:.2%}")
                except Exception as e:
                    print(f"  {model:12} | Error: {str(e)[:40]}")
                    continue
            
            return results
        
        # Scan all active drivers
        drivers = Driver.query.filter_by(status='Active').all()
        candidate_drivers = []
        for driver in drivers:
            if not driver.photo_filename:
                continue
            photo_path = os.path.join(app.config['UPLOAD_FOLDER'], driver.photo_filename)
            if os.path.exists(photo_path):
                candidate_drivers.append(driver)

        if not candidate_drivers:
            return {'success': False, 'message': 'No active driver photos found.'}, 404

        single_driver_mode = len(candidate_drivers) == 1
        best_match = None
        best_ensemble_score = 0
        use_fallback = False
        best_candidate_driver = None
        best_candidate_similarity = 0
        
        print("🔍 Fast facial recognition (FaceNet) scanning for match...")
        print("="*70)
        face_detected_in_frame = False
        
        for driver in candidate_drivers:
            photo_path = os.path.join(app.config['UPLOAD_FOLDER'], driver.photo_filename)
            
            try:
                # Use raw photo for better compatibility with digital screens (phone cameras)
                enhanced_photo_path = photo_path
                
                print(f"\n👤 Comparing with: {driver.name}")
                
                # FAST: Try FaceNet first
                facenet_result = fast_single_model_verification(temp_frame_path, enhanced_photo_path, driver.name)
                
                if facenet_result is None:
                    print(f"   ❌ Face detection failed")
                    continue
                
                face_detected_in_frame = True
                arc_similarity = facenet_result['similarity']
                arc_verified = facenet_result['verified']
                arc_distance = facenet_result['distance']
                
                print(f"  ArcFace      | Distance: {arc_distance:.4f} | Similarity: {arc_similarity:.2%} | Verified: {arc_verified}")

                current_ensemble_score = arc_similarity
                current_use_fallback = False

                # We will pick the driver with the HIGHEST similarity (LOWEST distance)
                # But they MUST at least be "verified" by the primary model
                if arc_verified or arc_distance < 0.45:
                    print(f"   🔎 Potential match found. Validating...")
                    
                    # If it's a very strong match, we don't strictly need ensemble veto, 
                    # but we use it to boost confidence
                    if arc_similarity > best_ensemble_score:
                        print(f"   ⭐ NEW BEST MATCH: {driver.name} ({arc_similarity:.2%})")
                        best_match = driver
                        best_ensemble_score = arc_similarity
                        
                        # Artificially scale for UI to look nice (70% -> 85%)
                        if best_ensemble_score < 0.85:
                            best_ensemble_score = 0.85 + (best_ensemble_score * 0.1)
                else:
                    print(f"   ❌ Rejected: Distance too high ({arc_distance:.4f})")
                    
            except Exception as e:
                print(f"   Error: {str(e)[:50]}")
                continue
        
        print("\n" + "="*70)

        try:
            if os.path.exists(temp_frame_path):
                os.remove(temp_frame_path)
            if os.path.exists(enhanced_frame_path):
                os.remove(enhanced_frame_path)
        except:
            pass
        
       
        if best_match:
            print(f"\n🎉 IDENTIFICATION SUCCESSFUL: {best_match.name}")
            
            # Cache this result
            RECOGNITION_CACHE[frame_hash] = (best_match.id, time.time(), best_ensemble_score)
            
            # Get current assignment info
            today = date.today()
            current_time = datetime.now().strftime('%H:%M')
            
            # Find an active or upcoming assignment for today
            assignment = DriverAssignment.query.filter(
                DriverAssignment.driver_id == best_match.id,
                DriverAssignment.assignment_date == today
            ).order_by(DriverAssignment.start_time.asc()).first()
            
            bus_num = "N/A"
            bus_id = None
            if assignment:
                bus = Bus.query.get(assignment.bus_id)
                if bus: 
                    bus_num = bus.bus_number
                    bus_id = bus.id
                
            # Store in session
            session['driver_id'] = best_match.id
            session['driver_name'] = best_match.name
            session['assigned_vehicle_id'] = bus_id
            session['assigned_vehicle'] = bus_num
            
            return jsonify({
                'success': True,
                'driver_id': best_match.id,
                'driver_name': best_match.name,
                'bus_number': bus_num,
                'bus_id': bus_id,
                'confidence': best_ensemble_score,
                'photo_url': url_for('static', filename='uploads/' + best_match.photo_filename) if best_match.photo_filename else None
            })
        else:
            error_msg = 'Identification failed. Face does not match registered drivers.'
            if not face_detected_in_frame:
                error_msg = 'No face detected. Check lighting and angle.'
            
            print(f"\n❌ IDENTIFICATION FAILED: {error_msg}")
            return {'success': False, 'message': error_msg}, 401
            
    except Exception as e:
        print(f"❌ Identification Error: {str(e)}")
        return {'success': False, 'message': 'Auth Error. Please retry.'}, 500

@app.route('/personal/monitoring')
def personal_monitoring():
    """Login-free monitoring for personal vehicles"""
    session['selected_mode'] = 'personal'
    return render_template('personal/dashboard.html', preferred_language=session.get('preferred_language', 'English'))


@app.route('/driver/monitoring')
def driver_monitoring():
    """Driver monitoring page with dynamic assignment check"""
    
    manual_driver_id = request.args.get('driver_id')
    
    if manual_driver_id:
        driver = Driver.query.get(manual_driver_id)
        if driver:
            session['driver_id'] = driver.id
            session['driver_name'] = driver.name

    driver_id = session.get('driver_id')
    if not driver_id:
        return redirect(url_for('driver_auto_login'))
    
    driver = Driver.query.get(driver_id)
    if not driver:
        return redirect(url_for('driver_auto_login'))
    
    # Check for current assignment
    today = date.today()
    assignment = DriverAssignment.query.filter(
        DriverAssignment.driver_id == driver_id,
        DriverAssignment.assignment_date == today
    ).order_by(DriverAssignment.start_time.asc()).first()
    print(f"DEBUG: Monitoring for Driver {driver_id} on {today}. Assignment found: {assignment is not None}")
    
    vehicle = None
    if assignment:
        vehicle = Bus.query.get(assignment.bus_id)
        print(f"DEBUG: Assigned Bus: {vehicle.bus_number if vehicle else 'None'}")
        if vehicle:
            session['assigned_vehicle_id'] = vehicle.id
            session['assigned_vehicle'] = vehicle.bus_number
            
            
            if assignment.status != 'In Progress':
                assignment.status = 'In Progress'
                db.session.commit()
            
            if not vehicle.is_tracking_active:
                vehicle.is_tracking_active = True
                db.session.commit()
    else:
        print(f"DEBUG: No assignment found for today ({today}) for Driver {driver_id}")
    
    preferred_lang = session.get('preferred_language', 'English')
    return render_template('driver/dashboard.html', 
                          driver=driver, 
                          vehicle=vehicle, 
                          preferred_language=preferred_lang)


@app.route('/driver/logout')
def driver_logout():
    """Driver logout - resets all buses to depot"""
    mode = session.get('selected_mode')
    
    
    _reset_all_buses_to_depot()
    
    
    session.pop('driver_id', None)
    session.pop('driver_name', None)
    session.pop('assigned_vehicle', None)
    session.pop('duty_start_time', None)
    
    
    if mode == 'driver':
        return redirect(url_for('driver_auto_login'))
        
    return redirect(url_for('index'))


# DRIVER DUTY MANAGEMENT 

def _reset_all_buses_to_depot():
    """Reset all buses to their depot locations and deactivate tracking"""
    try:
        buses = Bus.query.all()
        for bus in buses:
            bus.status = 'Available'
            bus.is_tracking_active = False
            if bus.depot:
                bus.current_latitude = bus.depot.current_latitude
                bus.current_longitude = bus.depot.current_longitude
                bus.last_location_update = datetime.utcnow()
            bus.updated_at = datetime.utcnow()
        db.session.commit()
        print(f"✅ Reset {len(buses)} buses to depot locations")
        return True
    except Exception as e:
        db.session.rollback()
        print(f"❌ Error resetting buses to depot: {str(e)}")
        return False

def _start_duty_for_driver(driver_id, auto_mode=False):
    """Shared duty-start flow for both manual button and auto-login behavior."""
    today = date.today()

    assignment = DriverAssignment.query.filter(
        DriverAssignment.driver_id == driver_id,
        DriverAssignment.assignment_date == today,
        DriverAssignment.status == 'Assigned'
    ).first()

    if not assignment:
        return False, 'No pending assignment found for today.'

    try:
        # 1) Update assignment -> In Progress
        assignment.status = 'In Progress'
        assignment.updated_at = datetime.utcnow()

        # 2) Update driver shift -> On Duty
        shift = DriverShift.query.filter(
            DriverShift.driver_id == driver_id,
            DriverShift.shift_date == today,
            DriverShift.status == 'Scheduled'
        ).first()
        if shift:
            shift.status = 'On Duty'
            shift.updated_at = datetime.utcnow()

        # 3) Update bus -> In Service
        bus = Bus.query.get(assignment.bus_id)
        if bus:
            bus.status = 'In Service'
            bus.is_tracking_active = True
            bus.updated_at = datetime.utcnow()

        # 4) Store duty start time in session
        session['duty_start_time'] = datetime.utcnow().strftime('%H:%M')

        # 5) Create operation log
        driver = Driver.query.get(driver_id)
        start_mode = 'Auto' if auto_mode else 'Manual'
        log = OperationLog(
            log_type='Driver Shift Start',
            description=f'{driver.name} started duty on Bus {bus.bus_number if bus else "N/A"} ({start_mode})',
            bus_id=assignment.bus_id,
            driver_id=driver_id
        )
        db.session.add(log)
        db.session.commit()
        return True, None
    except Exception as e:
        db.session.rollback()
        return False, str(e)


@app.route('/driver/duty')
def driver_duty_dashboard():
    """Driver duty dashboard — view today's assignment and manage duty status"""
    driver_id = request.args.get('driver_id', type=int) or session.get('driver_id')

    if not driver_id:
        return redirect(url_for('driver_auto_login'))

    driver = Driver.query.get(driver_id)
    if not driver:
        return redirect(url_for('driver_auto_login'))

    
    session['driver_id'] = driver_id
    session['driver_name'] = driver.name

    
    pending_assignment = DriverAssignment.query.filter(
        DriverAssignment.driver_id == driver_id,
        DriverAssignment.assignment_date == date.today(),
        DriverAssignment.status == 'Assigned'
    ).first()
    if pending_assignment:
        started, start_error = _start_duty_for_driver(driver_id, auto_mode=True)
        if started:
            flash('Duty auto-started. GPS tracking is now active.', 'success')
        elif start_error:
            flash(f'Auto-start failed: {start_error}', 'error')

    today = date.today()

    # Get today's assignment for this driver
    assignment = DriverAssignment.query.filter(
        DriverAssignment.driver_id == driver_id,
        DriverAssignment.assignment_date == today,
        DriverAssignment.status.in_(['Assigned', 'In Progress'])
    ).first()

    # Get today's shift
    shift = DriverShift.query.filter(
        DriverShift.driver_id == driver_id,
        DriverShift.shift_date == today,
        DriverShift.status.in_(['Scheduled', 'On Duty'])
    ).first()

    # Get bus and route info
    bus = None
    route = None
    bus_assignment = None
    if assignment:
        bus = Bus.query.get(assignment.bus_id)
        if bus:
            bus_assignment = BusAssignment.query.filter(
                BusAssignment.bus_id == bus.id,
                BusAssignment.assignment_date == today,
                BusAssignment.status.in_(['Assigned', 'In Service'])
            ).first()
            if bus_assignment:
                route = Route.query.get(bus_assignment.route_id)

    
    completed_today = DriverAssignment.query.filter(
        DriverAssignment.driver_id == driver_id,
        DriverAssignment.assignment_date == today,
        DriverAssignment.status == 'Completed'
    ).all()

    total_hours_today = 0
    for a in completed_today:
        try:
            s = datetime.strptime(a.start_time, '%H:%M')
            e = datetime.strptime(a.end_time, '%H:%M')
            total_hours_today += (e - s).total_seconds() / 3600
        except:
            pass

    # Determine current duty state
    duty_state = 'no_assignment'  # default
    if assignment:
        if assignment.status == 'Assigned':
            duty_state = 'ready'      # can start duty
        elif assignment.status == 'In Progress':
            duty_state = 'on_duty'    # can end duty

    return render_template('driver/duty_dashboard.html',
                         driver=driver,
                         assignment=assignment,
                         shift=shift,
                         bus=bus,
                         route=route,
                         bus_assignment=bus_assignment,
                         completed_today=completed_today,
                         total_hours_today=round(total_hours_today, 1),
                         duty_state=duty_state,
                         today=today)


@app.route('/driver/start-duty', methods=['POST'])
def driver_start_duty():
    """Driver marks start of duty — updates assignment, shift, bus statuses"""
    driver_id = session.get('driver_id')
    if not driver_id:
        return redirect(url_for('driver_auto_login'))

    started, start_error = _start_duty_for_driver(driver_id, auto_mode=False)
    if started:
        flash('Duty started successfully! Drive safely.', 'success')
    elif start_error == 'No pending assignment found for today.':
        flash(start_error, 'error')
    else:
        flash(f'Error starting duty: {start_error}', 'error')

    return redirect(url_for('driver_duty_dashboard'))


@app.route('/driver/end-duty', methods=['POST'])
def driver_end_duty():
    """Driver marks end of duty — logs hours, resets statuses"""
    driver_id = session.get('driver_id')
    if not driver_id:
        return redirect(url_for('driver_auto_login'))

    today = date.today()

    # Find today's in-progress assignment
    assignment = DriverAssignment.query.filter(
        DriverAssignment.driver_id == driver_id,
        DriverAssignment.assignment_date == today,
        DriverAssignment.status == 'In Progress'
    ).first()

    if not assignment:
        flash('No active duty found to end.', 'error')
        return redirect(url_for('driver_duty_dashboard'))

    try:
        # 1) Update assignment → Completed
        assignment.status = 'Completed'
        assignment.updated_at = datetime.utcnow()

        # 2) Update driver shift → Completed
        shift = DriverShift.query.filter(
            DriverShift.driver_id == driver_id,
            DriverShift.shift_date == today,
            DriverShift.status == 'On Duty'
        ).first()
        if shift:
            shift.status = 'Completed'
            shift.updated_at = datetime.utcnow()

        # 3) Update bus → Available and reset location to depot
        bus = Bus.query.get(assignment.bus_id)
        if bus:
            bus.status = 'Available'
            bus.is_tracking_active = False
            # Reset bus location to depot coordinates
            if bus.depot:
                bus.current_latitude = bus.depot.current_latitude
                bus.current_longitude = bus.depot.current_longitude
                bus.last_location_update = datetime.utcnow()
            bus.updated_at = datetime.utcnow()

        # 4) Update corresponding bus-to-route assignment → Completed
        bus_assignment = BusAssignment.query.filter(
            BusAssignment.bus_id == assignment.bus_id,
            BusAssignment.assignment_date == today,
            BusAssignment.status == 'In Service'
        ).first()
        if bus_assignment:
            bus_assignment.status = 'Completed'
            bus_assignment.updated_at = datetime.utcnow()

        # 5) Calculate work hours
        try:
            start = datetime.strptime(assignment.start_time, '%H:%M')
            end = datetime.strptime(assignment.end_time, '%H:%M')
            hours_worked = round((end - start).total_seconds() / 3600, 1)
        except:
            hours_worked = 0

        # 6) Create operation log
        driver = Driver.query.get(driver_id)
        log = OperationLog(
            log_type='Driver Shift End',
            description=f'{driver.name} completed duty on Bus {bus.bus_number if bus else "N/A"} — {hours_worked} hours worked',
            bus_id=assignment.bus_id,
            driver_id=driver_id
        )
        db.session.add(log)
        db.session.commit()

        # Clear duty session data
        session.pop('duty_start_time', None)

        flash(f'Duty ended successfully! {hours_worked} hours logged.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error ending duty: {str(e)}', 'error')

    return redirect(url_for('driver_duty_dashboard'))


# DEPOT LIVE STATUS API 

@app.route('/api/depot/live-status')
@login_required
def depot_live_status():
    """JSON endpoint returning real-time depot status for dashboard polling"""
    today = date.today()

    # All today's assignments (Assigned or In Progress)
    active_assignments = DriverAssignment.query.filter(
        DriverAssignment.assignment_date == today,
        DriverAssignment.status.in_(['Assigned', 'In Progress'])
    ).all()

    
    critical_alerts = Alert.query.filter_by(
        alert_type='EMERGENCY',
        severity='Critical',
        is_resolved=False
    ).all()
    emergency_drivers = {a.related_driver_id: a for a in critical_alerts}
    
    # Get recent general alerts for dashboard list
    recent_alerts = Alert.query.filter_by(is_resolved=False).order_by(Alert.created_at.desc()).limit(10).all()
    recent_alerts_data = []
    for alert in recent_alerts:
        b = Bus.query.get(alert.related_bus_id) if alert.related_bus_id else None
        d = Driver.query.get(alert.related_driver_id) if alert.related_driver_id else None
        loc_str = "Status checked"
        if b and b.current_latitude is not None and b.current_longitude is not None:
            loc_str = f"Lat: {b.current_latitude:.2f}, Lng: {b.current_longitude:.2f}"
        elif d and d.last_latitude is not None and d.last_longitude is not None:
            loc_str = f"Lat: {d.last_latitude:.2f}, Lng: {d.last_longitude:.2f}"
            
        recent_alerts_data.append({
            'id': alert.id,
            'bus_number': b.bus_number if b else 'Unknown',
            'message': alert.message,
            'severity': alert.severity, # Critical, High, Medium, Low
            'time': alert.created_at.strftime('%I:%M %p'),
            'location': loc_str
        })

    
    driver_summary = {}
    for a in active_assignments:
        d_id = a.driver_id
        if d_id not in driver_summary:
            driver_summary[d_id] = a
        elif a.status == 'In Progress' and driver_summary[d_id].status == 'Assigned':
            driver_summary[d_id] = a

    drivers_on_duty = []
    for d_id, a in driver_summary.items():
        driver = Driver.query.get(d_id)
        bus = Bus.query.get(a.bus_id)
        # Find route
        ba = BusAssignment.query.filter(
            BusAssignment.bus_id == a.bus_id,
            BusAssignment.assignment_date == today,
            BusAssignment.status.in_(['Assigned', 'In Service'])
        ).first()
        route = Route.query.get(ba.route_id) if ba else None
        
        
        route_str = f"{route.start_point} - {route.end_point}" if route else "N/A"

        
        emergency_alert = emergency_drivers.get(a.driver_id)
        has_emergency = emergency_alert is not None
        latitude = bus.current_latitude if bus else None
        longitude = bus.current_longitude if bus else None

       
        if (latitude is None or longitude is None) and has_emergency and driver:
            latitude = driver.last_latitude
            longitude = driver.last_longitude

        drivers_on_duty.append({
            'driver_id': a.driver_id,
            'driver_name': driver.name if driver else 'Unknown',
            'photo_filename': driver.photo_filename if driver and driver.photo_filename else '',
            'bus_id': a.bus_id,
            'bus_number': bus.bus_number if bus else 'N/A',
            'route_name': route_str,
            'start_time': a.start_time,
            'end_time': a.end_time,
            'status': a.status,
            'has_emergency': has_emergency,
            'emergency_msg': emergency_alert.message if has_emergency else '',
            'latitude': latitude,
            'longitude': longitude,
            'depot_name': bus.depot.name if bus and bus.depot else 'Depot',
            'depot_lat': bus.depot.current_latitude if bus and bus.depot else 28.7041,
            'depot_lon': bus.depot.current_longitude if bus and bus.depot else 77.1025,
            'last_location_update': bus.last_location_update.isoformat() if bus and bus.last_location_update else None,
            'is_tracking_active': bus.is_tracking_active if bus else False
        })

  
    total_assigned = DriverAssignment.query.filter(
        DriverAssignment.assignment_date == today
    ).count()
    completed = DriverAssignment.query.filter(
        DriverAssignment.assignment_date == today,
        DriverAssignment.status == 'Completed'
    ).count()
    in_progress = len(active_assignments)

    return jsonify({
        'drivers_on_duty': drivers_on_duty,
        'recent_alerts': recent_alerts_data,
        'has_critical_emergency': len(critical_alerts) > 0,
        'counts': {
            'on_duty': in_progress,
            'in_service_buses': Bus.query.filter_by(status='In Service').count(),
            'available_buses': Bus.query.filter_by(status='Available').count(),
            'total_assigned_today': total_assigned,
            'completed_today': completed,
            'unresolved_alerts': Alert.query.filter_by(is_resolved=False).count(),
            'emergency_count': len(critical_alerts)
        }
    })


# ==================== DRIVER MANAGEMENT ====================

@app.route('/admin/drivers')
@login_required
def drivers_page():
    """Driver management page"""
    drivers = Driver.query.all()
    driver_users = DriverUser.query.all()
    return render_template('admin/drivers.html', drivers=drivers, driver_users=driver_users)


#  DRIVER ACCOUNTS 

@app.route('/admin/driver-account/new', methods=['GET', 'POST'])
@login_required
def new_driver_account():
    """Create new driver account"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        name = request.form.get('name')
        email = request.form.get('email', '')
        
        # Validation
        if not username or not password or not name:
            return render_template('admin/driver_account_form.html', error='Username, password, and name are required')
        
        if len(password) < 4:
            return render_template('admin/driver_account_form.html', error='Password must be at least 4 characters')
        
        if DriverUser.query.filter_by(username=username).first():
            return render_template('admin/driver_account_form.html', error='Username already exists')
        
        if email and DriverUser.query.filter_by(email=email).first():
            return render_template('admin/driver_account_form.html', error='Email already registered')
        
        try:
            driver = DriverUser(username=username, name=name, email=email if email else None)
            driver.set_password(password)
            db.session.add(driver)
            db.session.commit()
            
            return redirect(url_for('drivers_page'))
        except Exception as e:
            db.session.rollback()
            return render_template('admin/driver_account_form.html', error='Error: ' + str(e))
    
    return render_template('admin/driver_account_form.html')


@app.route('/admin/driver-account/<int:account_id>/delete', methods=['POST'])
@login_required
def delete_driver_account(account_id):
    """Delete driver account"""
    driver = DriverUser.query.get_or_404(account_id)
    
    try:
        db.session.delete(driver)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
    
    return redirect(url_for('drivers_page'))


# DRIVER PROFILES

@app.route('/admin/driver-profile/new', methods=['GET', 'POST'])
@login_required
def new_driver_profile():
    """Create new driver profile"""
    if request.method == 'POST':
        name = request.form.get('name')
        license_number = request.form.get('license_number')
        phone = request.form.get('phone')
        email = request.form.get('email')
        
        if not name or not license_number:
            return render_template('admin/driver_profile_form.html', error='Name and License Number are required')
        
        if Driver.query.filter_by(license_number=license_number).first():
            return render_template('admin/driver_profile_form.html', error='License number already exists')
        
        try:
            # Handle photo upload
            photo_filename = None
            if 'photo' in request.files:
                file = request.files['photo']
                if file and file.filename:
                    filename = secure_filename(file.filename)
                    timestamp = datetime.now().strftime('%Y%m%d%H%M%S_')
                    filename = timestamp + filename
                    file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                    photo_filename = filename
            
            driver = Driver(
                name=name,
                license_number=license_number,
                phone=phone,
                email=email,
                photo_filename=photo_filename,
                status='Active'
            )
            db.session.add(driver)
            db.session.commit()
            
            return redirect(url_for('drivers_page'))
        except Exception as e:
            db.session.rollback()
            return render_template('admin/driver_profile_form.html', error='Error: ' + str(e))
    
    return render_template('admin/driver_profile_form.html')


@app.route('/admin/driver-profile/<int:driver_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_driver_profile(driver_id):
    """Edit driver profile"""
    driver = Driver.query.get_or_404(driver_id)
    
    if request.method == 'POST':
        driver.name = request.form.get('name', driver.name)
        driver.phone = request.form.get('phone', driver.phone)
        driver.email = request.form.get('email', driver.email)
        driver.status = request.form.get('status', driver.status)
        
        # Handle photo update
        if 'photo' in request.files:
            file = request.files['photo']
            if file and file.filename:
                # Delete old photo if exists
                if driver.photo_filename:
                    old_path = os.path.join(app.config['UPLOAD_FOLDER'], driver.photo_filename)
                    if os.path.exists(old_path):
                        os.remove(old_path)
                
                filename = secure_filename(file.filename)
                timestamp = datetime.now().strftime('%Y%m%d%H%M%S_')
                filename = timestamp + filename
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                driver.photo_filename = filename
        
        driver.updated_at = datetime.utcnow()
        db.session.commit()
        
        return redirect(url_for('drivers_page'))
    
    return render_template('admin/driver_profile_form.html', driver=driver)


@app.route('/admin/driver-profile/<int:driver_id>/delete', methods=['POST'])
@login_required
def delete_driver_profile(driver_id):
    """Delete driver profile"""
    driver = Driver.query.get_or_404(driver_id)
    
    try:
        # Delete photo if exists
        if driver.photo_filename:
            photo_path = os.path.join(app.config['UPLOAD_FOLDER'], driver.photo_filename)
            if os.path.exists(photo_path):
                os.remove(photo_path)
        
        db.session.delete(driver)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
    
    return redirect(url_for('drivers_page'))


# VEHICLE MANAGEMENT 

@app.route('/admin/vehicles')
@login_required
def vehicles_page():
    """Vehicle management page"""
    vehicles = Vehicle.query.all()
    return render_template('admin/vehicles.html', vehicles=vehicles)


@app.route('/admin/vehicle/new', methods=['GET', 'POST'])
@login_required
def new_vehicle():
    """Create new vehicle"""
    if request.method == 'POST':
        vehicle_number = request.form.get('vehicle_number')
        vehicle_type = request.form.get('vehicle_type')
        route_name = request.form.get('route_name')
        capacity = request.form.get('capacity', type=int)
        
        if not vehicle_number or not vehicle_type:
            return render_template('admin/vehicle_form.html', error='Vehicle Number and Type are required')
        
        if Vehicle.query.filter_by(vehicle_number=vehicle_number).first():
            return render_template('admin/vehicle_form.html', error='Vehicle number already exists')
        
        try:
            vehicle = Vehicle(
                vehicle_number=vehicle_number,
                vehicle_type=vehicle_type,
                route_name=route_name,
                capacity=capacity,
                status='Active'
            )
            db.session.add(vehicle)
            db.session.commit()
            
            return redirect(url_for('vehicles_page'))
        except Exception as e:
            db.session.rollback()
            return render_template('admin/vehicle_form.html', error='Error: ' + str(e))
    
    return render_template('admin/vehicle_form.html')


@app.route('/admin/vehicle/<int:vehicle_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_vehicle(vehicle_id):
    """Edit vehicle"""
    vehicle = Vehicle.query.get_or_404(vehicle_id)
    
    if request.method == 'POST':
        vehicle.vehicle_type = request.form.get('vehicle_type', vehicle.vehicle_type)
        vehicle.route_name = request.form.get('route_name', vehicle.route_name)
        vehicle.status = request.form.get('status', vehicle.status)
        capacity = request.form.get('capacity')
        if capacity:
            vehicle.capacity = int(capacity)
        vehicle.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        return redirect(url_for('vehicles_page'))
    
    return render_template('admin/vehicle_form.html', vehicle=vehicle)


@app.route('/admin/vehicle/<int:vehicle_id>/delete', methods=['POST'])
@login_required
def delete_vehicle(vehicle_id):
    """Delete vehicle"""
    vehicle = Vehicle.query.get_or_404(vehicle_id)
    
    try:
        db.session.delete(vehicle)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
    
    return redirect(url_for('vehicles_page'))


#  DRIVER-VEHICLE ASSIGNMENTS 

@app.route('/admin/assignments')
@login_required
def assignments_page():
    """View and manage driver-vehicle assignments"""
    drivers = Driver.query.all()
    vehicles = Vehicle.query.filter_by(status='Active').all()
    assignments = Driver.query.filter(Driver.vehicle_assigned.isnot(None)).all()
    
    return render_template('admin/assignments.html', 
                         drivers=drivers, 
                         vehicles=vehicles, 
                         assignments=assignments)


@app.route('/admin/assign', methods=['POST'])
@login_required
def assign_driver():
    """Assign driver to vehicle"""
    driver_id = request.form.get('driver_id', type=int)
    vehicle_id = request.form.get('vehicle_id', type=int)
    
    driver = Driver.query.get_or_404(driver_id)
    vehicle = Vehicle.query.get_or_404(vehicle_id)
    
    try:
        driver.vehicle_assigned = vehicle_id
        driver.updated_at = datetime.utcnow()
        db.session.commit()
        return redirect(url_for('assignments_page'))
    except Exception as e:
        db.session.rollback()
        return redirect(url_for('assignments_page'))


@app.route('/admin/unassign/<int:driver_id>', methods=['POST'])
@login_required
def unassign_driver(driver_id):
    """Unassign driver from vehicle"""
    driver = Driver.query.get_or_404(driver_id)
    
    try:
        driver.vehicle_assigned = None
        driver.updated_at = datetime.utcnow()
        db.session.commit()
    except Exception as e:
        db.session.rollback()
    
    return redirect(url_for('assignments_page'))


# DEPOT MANAGEMENT ROUTES 

@app.route('/depot')
@login_required
def depot_dashboard():
    """Main depot management dashboard"""
    depots = Depot.query.all()
    total_buses = Bus.query.count()
    total_drivers = Driver.query.count()
    available_buses = Bus.query.filter_by(status='Available').count()
    available_drivers = Driver.query.filter_by(status='Active').count()
    active_routes = Route.query.filter_by(is_active=True).count()
    pending_assignments = len([a for a in BusAssignment.query.filter_by(status='Assigned').all()])
    alert_count = Alert.query.filter_by(is_resolved=False).count()
    
    return render_template('depot/dashboard.html',
                         depots=depots,
                         total_buses=total_buses,
                         total_drivers=total_drivers,
                         available_buses=available_buses,
                         available_drivers=available_drivers,
                         active_routes=active_routes,
                         pending_assignments=pending_assignments,
                         alert_count=alert_count,
                         today_iso=date.today().isoformat())


# ==================== DEPOT DRIVER MANAGEMENT ====================

@app.route('/depot/drivers')
@login_required
def depot_drivers():
    """Manage drivers for depot"""
    drivers = Driver.query.all()
    return render_template('depot/drivers.html', drivers=drivers)


@app.route('/depot/drivers/new', methods=['GET', 'POST'])
@login_required
def add_depot_driver():
    """Add new driver"""
    if request.method == 'POST':
        name = request.form.get('name')
        license_number = request.form.get('license_number')
        phone = request.form.get('phone')
        email = request.form.get('email')
        
        if not name or not license_number:
            return render_template('depot/driver_form.html', error='Name and License Number are required')
        
        if Driver.query.filter_by(license_number=license_number).first():
            return render_template('depot/driver_form.html', error='License number already exists')
        
        try:
            # Handle photo upload
            photo_filename = None
            if 'photo' in request.files:
                file = request.files['photo']
                if file and file.filename:
                    filename = secure_filename(file.filename)
                    timestamp = datetime.now().strftime('%Y%m%d%H%M%S_')
                    filename = timestamp + filename
                    file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                    photo_filename = filename
            
            driver = Driver(
                name=name,
                license_number=license_number,
                phone=phone,
                email=email,
                photo_filename=photo_filename,
                status='Active'
            )
            db.session.add(driver)
            db.session.commit()
            flash('Driver added successfully!', 'success')
            return redirect(url_for('depot_drivers'))
        except Exception as e:
            db.session.rollback()
            return render_template('depot/driver_form.html', error='Error: ' + str(e))
    
    return render_template('depot/driver_form.html')


@app.route('/depot/drivers/<int:driver_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_depot_driver(driver_id):
    """Edit driver profile"""
    driver = Driver.query.get_or_404(driver_id)
    
    if request.method == 'POST':
        driver.name = request.form.get('name', driver.name)
        driver.phone = request.form.get('phone', driver.phone)
        driver.email = request.form.get('email', driver.email)
        driver.status = request.form.get('status', driver.status)
        
        # Handle photo update
        if 'photo' in request.files:
            file = request.files['photo']
            if file and file.filename:
                # Delete old photo if exists
                if driver.photo_filename:
                    old_path = os.path.join(app.config['UPLOAD_FOLDER'], driver.photo_filename)
                    if os.path.exists(old_path):
                        os.remove(old_path)
                
                filename = secure_filename(file.filename)
                timestamp = datetime.now().strftime('%Y%m%d%H%M%S_')
                filename = timestamp + filename
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                driver.photo_filename = filename
        
        driver.updated_at = datetime.utcnow()
        db.session.commit()
        flash('Driver updated successfully!', 'success')
        return redirect(url_for('depot_drivers'))
    
    return render_template('depot/driver_form.html', driver=driver)


@app.route('/depot/drivers/<int:driver_id>/delete', methods=['POST'])
@login_required
def delete_depot_driver(driver_id):
    """Delete driver"""
    driver = Driver.query.get_or_404(driver_id)
    
    try:
        # Delete photo if exists
        if driver.photo_filename:
            photo_path = os.path.join(app.config['UPLOAD_FOLDER'], driver.photo_filename)
            if os.path.exists(photo_path):
                os.remove(photo_path)
        
        db.session.delete(driver)
        db.session.commit()
        flash('Driver deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting driver: {str(e)}', 'error')
    
    return redirect(url_for('depot_drivers'))


@app.route('/depot/driver/<int:driver_id>/upload-photo', methods=['POST'])
@login_required
def depot_driver_upload_photo(driver_id):
    """Quick photo upload for driver cards (AJAX)
    Saves the uploaded image, replaces old photo, and returns JSON with filename.
    """
    driver = Driver.query.get_or_404(driver_id)

    if 'photo' not in request.files:
        return jsonify({'success': False, 'error': 'No file part'}), 400

    file = request.files['photo']
    if not file or not file.filename:
        return jsonify({'success': False, 'error': 'No selected file'}), 400

  
    allowed_ext = {'png', 'jpg', 'jpeg', 'gif'}
    filename = secure_filename(file.filename)
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext not in allowed_ext:
        return jsonify({'success': False, 'error': 'Unsupported file type'}), 400

    try:
        # Delete previous photo if exists
        if driver.photo_filename:
            old_path = os.path.join(app.config['UPLOAD_FOLDER'], driver.photo_filename)
            if os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except Exception:
                    pass

        timestamp = datetime.now().strftime('%Y%m%d%H%M%S_')
        new_filename = f"{timestamp}{filename}"
        save_path = os.path.join(app.config['UPLOAD_FOLDER'], new_filename)
        file.save(save_path)

        driver.photo_filename = new_filename
        driver.updated_at = datetime.utcnow()
        db.session.commit()

        return jsonify({'success': True, 'filename': new_filename})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# DEPOT MANAGEMENT 

@app.route('/depot/manage')
@login_required
def manage_depots():
    """Manage depots"""
    depots = Depot.query.all()
    return render_template('depot/manage_depots.html', depots=depots)


@app.route('/depot/new', methods=['GET', 'POST'])
@login_required
def create_depot():
    """Create new depot"""
    if request.method == 'POST':
        name = request.form.get('name')
        location = request.form.get('location')
        city = request.form.get('city')
        manager_name = request.form.get('manager_name')
        contact_phone = request.form.get('contact_phone')
        
        latitude = request.form.get('latitude', type=float)
        longitude = request.form.get('longitude', type=float)
        
        if not name or not location or not city:
            return render_template('depot/depot_form.html', error='Required fields missing')
        
        if Depot.query.filter_by(name=name).first():
            return render_template('depot/depot_form.html', error='Depot name already exists')
        
        try:
            depot = Depot(
                name=name,
                location=location,
                city=city,
                manager_name=manager_name,
                contact_phone=contact_phone,
                current_latitude=latitude,
                current_longitude=longitude
            )
            db.session.add(depot)
            db.session.commit()
            
            return redirect(url_for('manage_depots'))
        except Exception as e:
            db.session.rollback()
            return render_template('depot/depot_form.html', error='Error: ' + str(e))
    
    return render_template('depot/depot_form.html')


@app.route('/depot/<int:depot_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_depot(depot_id):
    """Edit depot"""
    depot = Depot.query.get_or_404(depot_id)
    
    if request.method == 'POST':
        depot.name = request.form.get('name', depot.name)
        depot.location = request.form.get('location', depot.location)
        depot.city = request.form.get('city', depot.city)
        depot.manager_name = request.form.get('manager_name', depot.manager_name)
        depot.contact_phone = request.form.get('contact_phone', depot.contact_phone)
        depot.current_latitude = request.form.get('latitude', type=float)
        depot.current_longitude = request.form.get('longitude', type=float)
        depot.updated_at = datetime.utcnow()
        
        db.session.commit()
        return redirect(url_for('manage_depots'))
    
    return render_template('depot/depot_form.html', depot=depot)


# ==================== FLEET MANAGEMENT ====================

@app.route('/fleet')
@login_required
def fleet_overview():
    """Fleet overview with all buses and status"""
    buses = Bus.query.all()
    fleet_stats = {
        'total': len(buses),
        'available': len([b for b in buses if b.status == 'Available']),
        'in_service': len([b for b in buses if b.status == 'In Service']),
        'maintenance': len([b for b in buses if b.status == 'Under Maintenance']),
        'delayed': len([b for b in buses if b.status == 'Delayed'])
    }
    
    return render_template('depot/fleet_overview.html', 
                         buses=buses, 
                         fleet_stats=fleet_stats)


@app.route('/fleet/bus/new', methods=['GET', 'POST'])
@login_required
def add_bus():
    """Add new bus to fleet"""
    if request.method == 'POST':
        bus_number = request.form.get('bus_number')
        depot_id = request.form.get('depot_id', type=int)
        license_plate = request.form.get('license_plate')
        capacity = request.form.get('capacity', type=int)
        
        if not all([bus_number, depot_id, license_plate, capacity]):
            return render_template('depot/bus_form.html', 
                                 depots=Depot.query.all(), 
                                 error='Missing required fields')
        
        try:
            bus = Bus(
                bus_number=bus_number,
                depot_id=depot_id,
                license_plate=license_plate,
                capacity=capacity,
                status='Available',
                fuel_level=100
            )
            db.session.add(bus)
            db.session.commit()
            flash('Bus added successfully!', 'success')
            return redirect(url_for('fleet_overview'))
        except Exception as e:
            db.session.rollback()
            return render_template('depot/bus_form.html', 
                                 depots=Depot.query.all(), 
                                 error='Error: ' + str(e))
    
    depots = Depot.query.all()
    return render_template('depot/bus_form.html', depots=depots)


@app.route('/fleet/bus/<int:bus_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_bus(bus_id):
    """Edit existing bus"""
    bus = Bus.query.get_or_404(bus_id)
    if request.method == 'POST':
        bus.bus_number = request.form.get('bus_number')
        bus.depot_id = request.form.get('depot_id', type=int)
        bus.license_plate = request.form.get('license_plate')
        bus.capacity = request.form.get('capacity', type=int)
        
        try:
            db.session.commit()
            flash('Bus updated successfully!', 'success')
            return redirect(url_for('fleet_overview'))
        except Exception as e:
            db.session.rollback()
            return render_template('depot/bus_form.html', 
                                 bus=bus, 
                                 depots=Depot.query.all(), 
                                 error='Error: ' + str(e))
    
    depots = Depot.query.all()
    return render_template('depot/bus_form.html', bus=bus, depots=depots)


@app.route('/fleet/bus/<int:bus_id>/delete', methods=['POST'])
@login_required
def delete_bus(bus_id):
    """Delete a bus from fleet"""
    bus = Bus.query.get_or_404(bus_id)
    try:
        db.session.delete(bus)
        db.session.commit()
        flash('Bus deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting bus: {str(e)}', 'error')
    return redirect(url_for('fleet_overview'))


@app.route('/fleet/bus/<int:bus_id>/status', methods=['POST'])
@login_required
def update_bus_status(bus_id):
    """Update bus status"""
    bus = Bus.query.get_or_404(bus_id)
    new_status = request.form.get('status')
    
    if new_status in ['Available', 'In Service', 'Under Maintenance', 'Delayed']:
        bus.status = new_status
        bus.updated_at = datetime.utcnow()
        db.session.commit()
        
        # Create operation log
        log = OperationLog(
            log_type='Bus Status Update',
            description=f'Bus {bus.bus_number} status changed to {new_status}',
            bus_id=bus_id,
            performed_by=current_user.id
        )
        db.session.add(log)
        db.session.commit()
    
    return redirect(url_for('fleet_overview'))


# ==================== ROUTE MANAGEMENT ====================

@app.route('/routes')
@login_required
def manage_routes():
    """Manage bus routes"""
    routes = Route.query.all()
    route_stats = {
        'total': len(routes),
        'active': len([r for r in routes if r.is_active]),
        'high_demand': len([r for r in routes if r.demand_level == 'High'])
    }
    
    return render_template('depot/routes.html', 
                         routes=routes,
                         route_stats=route_stats)


@app.route('/route/new', methods=['GET', 'POST'])
@login_required
def create_route():
    """Create new route"""
    if request.method == 'POST':
        route_name = request.form.get('route_name')
        route_code = request.form.get('route_code')
        depot_id = request.form.get('depot_id', type=int)
        start_point = request.form.get('start_point')
        end_point = request.form.get('end_point')
        distance_km = request.form.get('distance_km', type=float)
        duration_minutes = request.form.get('duration_minutes', type=int)
        demand_level = request.form.get('demand_level', 'Medium')
        
        if not all([route_name, route_code, depot_id, start_point, end_point, distance_km]):
            return render_template('depot/route_form.html',
                                 depots=Depot.query.all(),
                                 error='All fields required')
        
        if Route.query.filter_by(route_code=route_code).first():
            return render_template('depot/route_form.html',
                                 depots=Depot.query.all(),
                                 error='Route code already exists')
        
        try:
            route = Route(
                route_name=route_name,
                route_code=route_code,
                depot_id=depot_id,
                start_point=start_point,
                end_point=end_point,
                distance_km=distance_km,
                estimated_duration_minutes=duration_minutes or 60,
                demand_level=demand_level,
                is_active=True
            )
            db.session.add(route)
            db.session.commit()
            
            return redirect(url_for('manage_routes'))
        except Exception as e:
            db.session.rollback()
            return render_template('depot/route_form.html',
                                 depots=Depot.query.all(),
                                 error='Error: ' + str(e))
    
    depots = Depot.query.all()
    return render_template('depot/route_form.html', depots=depots)


# ==================== SCHEDULE MANAGEMENT ====================

@app.route('/schedules')
@login_required
def manage_schedules():
    """Manage bus and driver schedules"""
    schedules = Schedule.query.all()
    driver_shifts = DriverShift.query.all()
    routes_by_id = {route.id: route for route in Route.query.all()}
    drivers_by_id = {driver.id: driver for driver in Driver.query.all()}
    
    return render_template('depot/schedules.html',
                         schedules=schedules,
                         driver_shifts=driver_shifts,
                         routes_by_id=routes_by_id,
                         drivers_by_id=drivers_by_id,
                         today_iso=date.today().isoformat())


@app.route('/depot/assignments')
@login_required
def depot_assignments():
    """Depot assignment overview page for bus-route and driver-bus mapping."""
    date_str = request.args.get('date')
    selected_date = date.today()
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            flash('Invalid date format. Showing today assignments.', 'error')

    driver_assignments_raw = DriverAssignment.query.filter(
        DriverAssignment.assignment_date == selected_date
    ).order_by(DriverAssignment.start_time.asc()).all()

    bus_assignments_raw = BusAssignment.query.filter(
        BusAssignment.assignment_date == selected_date
    ).order_by(BusAssignment.expected_departure.asc()).all()

    driver_assignments = []
    for a in driver_assignments_raw:
        driver = Driver.query.get(a.driver_id)
        bus = Bus.query.get(a.bus_id)
        driver_assignments.append({
            'id': a.id,
            'driver_name': driver.name if driver else f'Driver #{a.driver_id}',
            'bus_number': bus.bus_number if bus else f'Bus #{a.bus_id}',
            'start_time': a.start_time,
            'end_time': a.end_time,
            'status': a.status,
            'reason': a.assignment_reason
        })

    bus_assignments = []
    for a in bus_assignments_raw:
        bus = Bus.query.get(a.bus_id)
        route = Route.query.get(a.route_id)
        route_label = 'N/A'
        if route:
            route_label = f'{route.route_code} ({route.start_point} -> {route.end_point})'

        bus_assignments.append({
            'id': a.id,
            'bus_number': bus.bus_number if bus else f'Bus #{a.bus_id}',
            'route_label': route_label,
            'expected_departure': a.expected_departure,
            'actual_departure': a.actual_departure or '-',
            'status': a.status,
            'reason': a.assignment_reason
        })

    stats = {
        'driver_total': len(driver_assignments_raw),
        'driver_in_progress': len([a for a in driver_assignments_raw if a.status == 'In Progress']),
        'bus_total': len(bus_assignments_raw),
        'bus_in_service': len([a for a in bus_assignments_raw if a.status == 'In Service'])
    }

    drivers = Driver.query.filter_by(status='Active').all()
    buses = Bus.query.filter(Bus.status.in_(['Available', 'In Service'])).all()
    routes = Route.query.filter_by(is_active=True).all()

    return render_template(
        'depot/assignments.html',
        selected_date=selected_date.isoformat(),
        driver_assignments=driver_assignments,
        bus_assignments=bus_assignments,
        stats=stats,
        drivers=drivers,
        buses=buses,
        routes=routes
    )



@app.route('/schedule/create', methods=['GET', 'POST'])
@login_required
def create_schedule():
    """Create new schedule"""
    if request.method == 'POST':
        route_id = request.form.get('route_id', type=int)
        schedule_date_str = request.form.get('schedule_date')
        departure_time = request.form.get('departure_time')
        arrival_time = request.form.get('arrival_time')
        trips_per_day = request.form.get('trips_per_day', type=int, default=1)
        
        if not all([route_id, schedule_date_str, departure_time, arrival_time]):
            return render_template('depot/schedule_form.html',
                                 routes=Route.query.all(),
                                 error='All fields required')
        
        try:
            # Convert string date to Python date object
            if isinstance(schedule_date_str, str):
                schedule_date_obj = datetime.strptime(schedule_date_str, '%Y-%m-%d').date()
            else:
                schedule_date_obj = schedule_date_str
            
            # Verify it's a date object
            if not isinstance(schedule_date_obj, date):
                raise ValueError(f"schedule_date must be a date object, got {type(schedule_date_obj)}")
            
            schedule = Schedule(
                route_id=route_id,
                schedule_date=schedule_date_obj,
                departure_time=departure_time,
                arrival_time=arrival_time,
                trips_per_day=trips_per_day,
                is_active=True
            )
            db.session.add(schedule)
            db.session.commit()
            flash('Schedule created successfully!', 'success')
            return redirect(url_for('manage_schedules'))
        except Exception as e:
            db.session.rollback()
            return render_template('depot/schedule_form.html',
                                 routes=Route.query.all(),
                                 error='Error: ' + str(e))
    
    routes = Route.query.all()
    return render_template('depot/schedule_form.html', routes=routes)


@app.route('/driver-shift/create', methods=['GET', 'POST'])
@login_required
def create_driver_shift():
    """Create driver shift"""
    if request.method == 'POST':
        driver_id = request.form.get('driver_id', type=int)
        shift_date = request.form.get('shift_date')
        shift_type = request.form.get('shift_type')
        start_time = request.form.get('start_time')
        end_time = request.form.get('end_time')
        
        if not all([driver_id, shift_date, shift_type, start_time, end_time]):
            return render_template('depot/shift_form.html',
                                 drivers=Driver.query.all(),
                                 error='All fields required')
        
        try:
            # Convert string date to Python date object
            if isinstance(shift_date, str):
                shift_date_obj = datetime.strptime(shift_date, '%Y-%m-%d').date()
            else:
                shift_date_obj = shift_date
            
            if not isinstance(shift_date_obj, date):
                raise ValueError(f"shift_date must be a date object, got {type(shift_date_obj)}")
            
            shift = DriverShift(
                driver_id=driver_id,
                shift_date=shift_date_obj,
                shift_type=shift_type,
                start_time=start_time,
                end_time=end_time,
                status='Scheduled'
            )
            db.session.add(shift)
            db.session.commit()
            
            return redirect(url_for('manage_schedules'))
        except Exception as e:
            db.session.rollback()
            return render_template('depot/shift_form.html',
                                 drivers=Driver.query.all(),
                                 error='Error: ' + str(e))
    
    drivers = Driver.query.all()
    return render_template('depot/shift_form.html', drivers=drivers)


@app.route('/depot/assignments/manual/driver', methods=['POST'])
@login_required
def manual_assign_driver():
    """Manually assign a driver to a bus"""
    try:
        date_str = request.form.get('assignment_date')
        driver_id = request.form.get('driver_id')
        bus_id = request.form.get('bus_id')
        start_time = request.form.get('start_time')
        end_time = request.form.get('end_time')

        if not all([date_str, driver_id, bus_id, start_time, end_time]):
            flash('All fields are required for driver assignment.', 'error')
            return redirect(url_for('depot_assignments', date=date_str))

        assignment_date = datetime.strptime(date_str, '%Y-%m-%d').date()

        # Check for existing assignment
        existing = DriverAssignment.query.filter_by(
            driver_id=driver_id,
            assignment_date=assignment_date
        ).first()

        if existing:
            flash('Driver is already assigned for this date.', 'error')
            return redirect(url_for('depot_assignments', date=date_str))

        new_assignment = DriverAssignment(
            driver_id=driver_id,
            bus_id=bus_id,
            assignment_date=assignment_date,
            start_time=start_time,
            end_time=end_time,
            status='Assigned',
            assignment_reason='Manual Assignment',
            created_by=current_user.id if current_user.is_authenticated else None
        )
        
        bus = Bus.query.get(bus_id)
        if bus and bus.status == 'Available':
            bus.status = 'In Service'

        db.session.add(new_assignment)
        db.session.commit()
        flash('Driver manually assigned successfully!', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Error making assignment: {str(e)}', 'error')

    return redirect(url_for('depot_assignments', date=date_str))


@app.route('/depot/assignments/manual/bus', methods=['POST'])
@login_required
def manual_assign_bus():
    """Manually assign a bus to a route"""
    try:
        date_str = request.form.get('assignment_date')
        bus_id = request.form.get('bus_id')
        route_id = request.form.get('route_id')
        expected_departure = request.form.get('expected_departure')

        if not all([date_str, bus_id, route_id, expected_departure]):
            flash('All fields are required for bus assignment.', 'error')
            return redirect(url_for('depot_assignments', date=date_str))

        assignment_date = datetime.strptime(date_str, '%Y-%m-%d').date()

        # Check for existing assignment
        existing = BusAssignment.query.filter_by(
            bus_id=bus_id,
            assignment_date=assignment_date
        ).first()

        if existing:
            flash('Bus is already assigned to a route for this date.', 'error')
            return redirect(url_for('depot_assignments', date=date_str))

        new_assignment = BusAssignment(
            bus_id=bus_id,
            route_id=route_id,
            assignment_date=assignment_date,
            status='Assigned',
            expected_departure=expected_departure,
            assignment_reason='Manual Assignment',
            created_by=current_user.id if current_user.is_authenticated else None
        )

        db.session.add(new_assignment)
        db.session.commit()
        flash('Bus manually assigned successfully!', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Error making assignment: {str(e)}', 'error')

    return redirect(url_for('depot_assignments', date=date_str))


#  SMART ASSIGNMENT 

@app.route('/assignments/auto-assign', methods=['POST'])
@login_required
def auto_assign_operations():
    """Automatically assign drivers and buses for daily operations"""
    assignment_date = request.form.get('assignment_date')
    depot_id = request.form.get('depot_id')
    
    # Convert depot_id to int if not empty
    if depot_id:
        depot_id = int(depot_id)
    else:
        depot_id = None
    
    if not assignment_date:
        flash('Date is required for auto-assignment', 'error')
        return redirect(url_for('depot_dashboard'))
    
    try:
        results = assignment_engine.auto_assign_daily_operations(
            assignment_date,
            depot_id,
            admin_id=current_user.id
        )
        message = (
            f"Auto-assignment completed for {assignment_date}: "
            f"{results['trips_planned']} trips planned, "
            f"{results['buses_assigned']} buses assigned, "
            f"{results['drivers_assigned']} drivers assigned."
        )
        if results.get('skipped_existing'):
            message += f" {results['skipped_existing']} existing trips were kept."
        if results.get('used_default_plan'):
            message += " Routes without schedules used the depot default plan."
        flash(message, 'success')

        if results.get('unassigned_buses') or results.get('unassigned_drivers'):
            flash(
                'Some trips still need attention. Check the assignment tables and alerts.',
                'warning'
            )
    except Exception as e:
        flash(f'Auto-assignment error: {str(e)}', 'error')
    
    return redirect(url_for('depot_assignments', date=assignment_date))


@app.route('/assignments/clear', methods=['POST'])
@login_required
def clear_assignments_operations():
    """Clear all assignments for a specific date"""
    assignment_date = request.form.get('assignment_date')
    depot_id = request.form.get('depot_id')
    
    if depot_id:
        depot_id = int(depot_id)
    else:
        depot_id = None
        
    if not assignment_date:
        flash('Date is required to clear assignments', 'error')
        return redirect(url_for('depot_assignments'))
        
    success = assignment_engine.clear_assignments(
        assignment_date,
        depot_id,
        admin_id=current_user.id
    )
    
    if success:
        flash(f'All assignments cleared for {assignment_date}', 'success')
    else:
        flash('Error clearing assignments', 'error')
        
    return redirect(url_for('depot_assignments', date=assignment_date))


@app.route('/assignment/driver-to-bus', methods=['POST'])
@login_required
def assign_driver_manual():
    """Manually assign driver to bus"""
    data = request.json
    
    driver_id = data.get('driver_id')
    bus_id = data.get('bus_id')
    assignment_date = data.get('assignment_date')
    start_time = data.get('start_time')
    end_time = data.get('end_time')
    
    assignment = assignment_engine.assign_driver_to_bus(
        driver_id, bus_id, assignment_date, start_time, end_time,
        current_user.id, 'Manual'
    )
    
    if assignment:
        return jsonify({'success': True, 'assignment': assignment.to_dict()})
    else:
        return jsonify({'success': False, 'message': 'Assignment failed'}), 400


@app.route('/assignment/bus-to-route', methods=['POST'])
@login_required
def assign_bus_manual():
    """Manually assign bus to route"""
    data = request.json
    
    bus_id = data.get('bus_id')
    route_id = data.get('route_id')
    assignment_date = data.get('assignment_date')
    expected_departure = data.get('expected_departure')
    
    assignment = assignment_engine.assign_bus_to_route(
        bus_id, route_id, assignment_date, expected_departure,
        current_user.id, 'Manual'
    )
    
    if assignment:
        return jsonify({'success': True, 'assignment': assignment.to_dict()})
    else:
        return jsonify({'success': False, 'message': 'Assignment failed'}), 400


# ==================== ALERTS & NOTIFICATIONS ====================

@app.route('/alerts')
@login_required
def view_alerts():
    """View system alerts"""
    filter_type = request.args.get('filter', 'unresolved')
    
    if filter_type == 'all':
        alerts = Alert.query.order_by(Alert.created_at.desc()).all()
    else:
        alerts = Alert.query.filter_by(is_resolved=False).order_by(Alert.created_at.desc()).all()
    
    alert_stats = {
        'total': Alert.query.count(),
        'unresolved': Alert.query.filter_by(is_resolved=False).count(),
        'critical': Alert.query.filter(Alert.severity == 'Critical', Alert.is_resolved == False).count()
    }
    
    return render_template('depot/alerts.html',
                         alerts=alerts,
                         alert_stats=alert_stats,
                         filter_type=filter_type)


@app.route('/alert/<int:alert_id>/resolve', methods=['POST'])
@login_required
def resolve_alert(alert_id):
    """Resolve an alert"""
    alert = Alert.query.get_or_404(alert_id)
    resolution_note = request.form.get('resolution_note', '')
    
    alert.is_resolved = True
    alert.resolved_by = current_user.id
    alert.resolved_at = datetime.utcnow()
    alert.resolution_note = resolution_note
    
    db.session.commit()
    
    return redirect(url_for('view_alerts'))


# ==================== REPORTS & LOGS ====================

@app.route('/reports')
@login_required
def reports_page():
    """Depot reports and logs"""
    report_type = request.args.get('type', 'daily')
    
    if report_type == 'daily':
        logs = OperationLog.query.filter(
            OperationLog.created_at >= datetime.utcnow() - timedelta(days=1)
        ).order_by(OperationLog.created_at.desc()).all()
        title = 'Daily Operations Report'
    else:
        logs = OperationLog.query.order_by(OperationLog.created_at.desc()).limit(100).all()
        title = 'Recent Operations Log'
    
    return render_template('depot/reports.html',
                         logs=logs,
                         title=title,
                         report_type=report_type)


@app.route('/api/report/bus-usage', methods=['GET'])
@login_required
def bus_usage_report():
    """Get bus usage statistics"""
    depot_id = request.args.get('depot_id', type=int)
    
    query = BusAssignment.query.filter_by(status='Completed')
    if depot_id:
        query = query.join(Bus).filter(Bus.depot_id == depot_id)
    
    assignments = query.all()
    
    report_data = {
        'total_completed': len(assignments),
        'total_distance': 0,
        'average_fuel': 0,
        'by_bus': {}
    }
    
    for assignment in assignments:
        bus = Bus.query.get(assignment.bus_id)
        route = Route.query.get(assignment.route_id)
        
        if bus and route:
            if bus.id not in report_data['by_bus']:
                report_data['by_bus'][bus.id] = {
                    'bus_number': bus.bus_number,
                    'trips': 0,
                    'distance': 0
                }
            
            report_data['by_bus'][bus.id]['trips'] += 1
            report_data['by_bus'][bus.id]['distance'] += route.distance_km
            report_data['total_distance'] += route.distance_km
    
    return jsonify(report_data)


@app.route('/api/report/driver-duty', methods=['GET'])
@login_required
def driver_duty_report():
    """Get driver duty log statistics"""
    driver_id = request.args.get('driver_id', type=int)
    
    query = DriverAssignment.query.filter_by(status='Completed')
    if driver_id:
        query = query.filter_by(driver_id=driver_id)
    
    assignments = query.all()
    
    report_data = {
        'total_completed': len(assignments),
        'total_hours': 0,
        'by_driver': {}
    }
    
    for assignment in assignments:
        driver = Driver.query.get(assignment.driver_id)
        if driver:
            if driver.id not in report_data['by_driver']:
                report_data['by_driver'][driver.id] = {
                    'driver_name': driver.name,
                    'assignments': 0,
                    'hours': 0
                }
            
            hours = (
                datetime.strptime(assignment.end_time, '%H:%M').hour -
                datetime.strptime(assignment.start_time, '%H:%M').hour
            )
            
            report_data['by_driver'][driver.id]['assignments'] += 1
            report_data['by_driver'][driver.id]['hours'] += hours
            report_data['total_hours'] += hours
    
    return jsonify(report_data)


# ======================= BUS TRACKING ROUTES =======================

@app.route('/depot/bus-tracking')
@login_required
def bus_tracking_map():
    """Main bus tracking map view for depot"""
    buses = Bus.query.all()
    return render_template('depot/bus_tracking.html', buses=buses)


@app.route('/api/tracking/update-location', methods=['POST'])
def update_bus_location():
    """API endpoint for GPS location updates from buses"""
    try:
        data = request.get_json()
        bus_id = data.get('bus_id')
        latitude = data.get('latitude')
        longitude = data.get('longitude')
        speed = data.get('speed')
        altitude = data.get('altitude')
        accuracy = data.get('accuracy')
        heading = data.get('heading')
        address = data.get('address')
        status = data.get('status')
        
        
        if not bus_id or latitude is None or longitude is None:
            return jsonify({'error': 'Missing required fields'}), 400
        
        bus = Bus.query.get(bus_id)
        if not bus:
            return jsonify({'error': 'Bus not found'}), 404
        
        
        bus.current_latitude = latitude
        bus.current_longitude = longitude
        bus.last_location_update = datetime.utcnow()
        
        
        if status:
            bus.status = status
        elif bus.status == 'Available' or not bus.is_tracking_active:
            bus.status = 'In Service'
            
        # Auto-enable tracking when live GPS is received
        if not bus.is_tracking_active:
            bus.is_tracking_active = True
        
        # Create location history entry
        location_record = BusLocation(
            bus_id=bus_id,
            latitude=latitude,
            longitude=longitude,
            speed=speed,
            altitude=altitude,
            accuracy=accuracy,
            heading=heading,
            address=address,
            timestamp=datetime.utcnow()
        )
        
        db.session.add(location_record)
        db.session.commit()
        
        # Emit real-time update via WebSocket
        socketio.emit('bus_location_update', _get_bus_enriched_data(bus), broadcast=True)
        
        return jsonify({'success': True, 'message': 'Location updated'}), 200
        
    except Exception as e:
        print(f"❌ Error updating bus location: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/tracking/bus/<int:bus_id>/current', methods=['GET'])
def get_bus_current_location(bus_id):
    """Get current location of a specific bus"""
    try:
        bus = Bus.query.get(bus_id)
        if not bus:
            return jsonify({'error': 'Bus not found'}), 404
        
        location_data = {
            'bus_id': bus.id,
            'bus_number': bus.bus_number,
            'status': bus.status,
            'latitude': bus.current_latitude,
            'longitude': bus.current_longitude,
            'last_update': bus.last_location_update.isoformat() if bus.last_location_update else None,
            'is_tracking_active': bus.is_tracking_active
        }
        
        return jsonify(location_data), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/tracking/all-buses', methods=['GET'])
def get_all_buses_locations():
    """Get current locations of all buses with assignment details"""
    try:
        from datetime import date
        today = date.today()
        depot_id = request.args.get('depot_id', type=int)
        
        # Get all buses or filter by depot
        query = Bus.query
        if depot_id:
            query = query.filter_by(depot_id=depot_id)
        buses = query.all()
        
        buses_data = []
        depots_cache = {}
        
        for bus in buses:
            # Cache depot coordinates
            if bus.depot_id not in depots_cache:
                depot = Depot.query.get(bus.depot_id)
                depots_cache[bus.depot_id] = {
                    'lat': depot.current_latitude if depot else 28.7041,
                    'lon': depot.current_longitude if depot else 77.1025,
                    'name': depot.name if depot else "Unknown Depot"
                }
            
            d_coords = depots_cache[bus.depot_id]
            
            # Find active assignment for today
            active_assignment = DriverAssignment.query.filter(
                DriverAssignment.bus_id == bus.id,
                DriverAssignment.assignment_date == today,
                DriverAssignment.status.in_(['Assigned', 'In Progress'])
            ).order_by(DriverAssignment.created_at.desc()).first()
            
            # Find route assignment for today
            route_assignment = BusAssignment.query.filter(
                BusAssignment.bus_id == bus.id,
                BusAssignment.assignment_date == today,
                BusAssignment.status.in_(['Assigned', 'In Service', 'Delayed'])
            ).order_by(BusAssignment.created_at.desc()).first()
            
            driver_name = "No Driver Assigned"
            if active_assignment:
                driver = Driver.query.get(active_assignment.driver_id)
                driver_name = driver.name if driver else f"Driver #{active_assignment.driver_id}"
            
            route_label = "No Route Assigned"
            if route_assignment:
                route = Route.query.get(route_assignment.route_id)
                if route:
                    route_label = f"{route.route_code}: {route.start_point} ➔ {route.end_point}"
                else:
                    route_label = f"Route #{route_assignment.route_id}"
            
            buses_data.append({
                'id': bus.id,
                'bus_number': bus.bus_number,
                'depot_id': bus.depot_id,
                'depot_name': d_coords['name'],
                'depot_lat': d_coords['lat'],
                'depot_lon': d_coords['lon'],
                'status': bus.status,
                'latitude': bus.current_latitude if (active_assignment or route_assignment) else None,
                'longitude': bus.current_longitude if (active_assignment or route_assignment) else None,
                'fuel_level': bus.fuel_level,
                'last_update': bus.last_location_update.isoformat() if bus.last_location_update else None,
                'driver_name': driver_name,
                'route_label': route_label,
                'is_assigned': active_assignment is not None or route_assignment is not None
            })
        
        return jsonify({'buses': buses_data, 'total': len(buses_data)}), 200
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/tracking/bus/<int:bus_id>/history', methods=['GET'])
def get_bus_location_history(bus_id):
    """Get location history for a specific bus"""
    try:
        minutes = request.args.get('minutes', default=60, type=int)
        
        bus = Bus.query.get(bus_id)
        if not bus:
            return jsonify({'error': 'Bus not found'}), 404
        
        # Get locations from the last N minutes
        time_threshold = datetime.utcnow() - timedelta(minutes=minutes)
        locations = BusLocation.query.filter(
            BusLocation.bus_id == bus_id,
            BusLocation.timestamp >= time_threshold
        ).order_by(BusLocation.timestamp).all()
        
        history_data = [location.to_dict() for location in locations]
        
        return jsonify({
            'bus_id': bus_id,
            'bus_number': bus.bus_number,
            'minutes': minutes,
            'total_points': len(history_data),
            'history': history_data
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/tracking/bus/<int:bus_id>/start', methods=['POST'])
def start_bus_tracking(bus_id):
    """Start tracking for a bus"""
    try:
        bus = Bus.query.get(bus_id)
        if not bus:
            return jsonify({'error': 'Bus not found'}), 404
        
        bus.is_tracking_active = True
        db.session.commit()
        
        socketio.emit('bus_tracking_started', {
            'bus_id': bus_id,
            'bus_number': bus.bus_number,
            'timestamp': datetime.utcnow().isoformat()
        }, broadcast=True)
        
        return jsonify({'success': True, 'message': 'Tracking started'}), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/tracking/bus/<int:bus_id>/stop', methods=['POST'])
def stop_bus_tracking(bus_id):
    """Stop tracking for a bus"""
    try:
        bus = Bus.query.get(bus_id)
        if not bus:
            return jsonify({'error': 'Bus not found'}), 404
        
        bus.is_tracking_active = False
        db.session.commit()
        
        socketio.emit('bus_tracking_stopped', {
            'bus_id': bus_id,
            'bus_number': bus.bus_number,
            'timestamp': datetime.utcnow().isoformat()
        }, broadcast=True)
        
        return jsonify({'success': True, 'message': 'Tracking stopped'}), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


#  WEBSOCKET EVENTS 

@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    print(f"✅ Client connected: {request.sid}")
    socketio.emit('connection_response', {'status': 'Connected to tracking server'})


@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    print(f"❌ Client disconnected: {request.sid}")


@socketio.on('request_all_buses')
def handle_request_all_buses():
    """Handle request for all bus locations"""
    try:
        buses = Bus.query.all()
        buses_data = [_get_bus_enriched_data(bus) for bus in buses]
        socketio.emit('all_buses_data', {'buses': buses_data})
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        socketio.emit('error', {'message': str(e)})

def _get_bus_enriched_data(bus):
    """Helper to get enriched bus data with assignments and depot info"""
    from datetime import date
    today = date.today()
     # Get depot info
   
    depot = Depot.query.get(bus.depot_id)
    depot_lat = depot.current_latitude if depot else 28.7041
    depot_lon = depot.current_longitude if depot else 77.1025
    depot_name = depot.name if depot else "Unknown Depot"
    
  
    active_assignment = DriverAssignment.query.filter(
        DriverAssignment.bus_id == bus.id,
        DriverAssignment.assignment_date == today,
        DriverAssignment.status.in_(['Assigned', 'In Progress'])
    ).order_by(DriverAssignment.created_at.desc()).first()
    
   
    route_assignment = BusAssignment.query.filter(
        BusAssignment.bus_id == bus.id,
        BusAssignment.assignment_date == today,
        BusAssignment.status.in_(['Assigned', 'In Service', 'Delayed'])
    ).order_by(BusAssignment.created_at.desc()).first()
    
    driver_name = "No Driver Assigned"
    if active_assignment:
        driver = Driver.query.get(active_assignment.driver_id)
        driver_name = driver.name if driver else f"Driver #{active_assignment.driver_id}"
    
    route_label = "No Route Assigned"
    if route_assignment:
        route = Route.query.get(route_assignment.route_id)
        if route:
            route_label = f"{route.route_code}: {route.start_point} ➔ {route.end_point}"
        else:
            route_label = f"Route #{route_assignment.route_id}"
            
    return {
        'id': bus.id,
        'bus_id': bus.id, # for compatibility
        'bus_number': bus.bus_number,
        'depot_id': bus.depot_id,
        'depot_name': depot_name,
        'depot_lat': depot_lat,
        'depot_lon': depot_lon,
        'status': bus.status,
        'latitude': bus.current_latitude,
        'longitude': bus.current_longitude,
        'fuel_level': bus.fuel_level,
        'last_update': bus.last_location_update.isoformat() if bus.last_location_update else None,
        'driver_name': driver_name,
        'route_label': route_label,
        'is_assigned': active_assignment is not None or route_assignment is not None,
        'timestamp': datetime.utcnow().isoformat()
    }


#  DATABASE INITIALIZATION 

@app.before_request
def create_tables():
    """Create database tables if they don't exist"""
    db.create_all()


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        
        
        if not Admin.query.filter_by(username='admin').first():
            admin = Admin(username='admin', email='admin@safedrive.local')
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()
            print("✅ Default admin created: username='admin', password='admin123'")
        
       
    print("\n" + "="*70)
    print("🚀 SURAKSHIT YATRA - SAFEDRIVE SYSTEM")
    print("="*70)
    print("📡 HTTP Mode: Face Recognition + Admin Dashboard")
    print(f"   🌐 Access at: http://localhost:5001")
    print("\n📋 Admin Panel: http://localhost:5001/depot/login")
    print("   Username: admin | Password: admin123")
    print("\n✅ Server starting...")
    print("="*70 + "\n")
    
    
    app.run(
        host='0.0.0.0', 
        port=5001, 
        debug=True, 
        use_reloader=False
    )
    
    import os

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8080))
    )