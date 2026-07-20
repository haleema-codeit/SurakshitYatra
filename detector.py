import os
import cv2
import numpy as np
import time


os.environ.setdefault("MEDIAPIPE_DISABLE_GPU", "1")

import mediapipe as mp

try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except (ImportError, Exception):
    YOLO_AVAILABLE = False
    YOLO = None

class SafeDriveDetector:
    def __init__(self, model_path='models/yolov10n.pt'):
        self.model_path = model_path
        self.model = None
        
        # YOLOv10 for general detection (optional)
        if YOLO_AVAILABLE:
            try:
                self.model = YOLO(model_path)
            except Exception as e:
                print(f"Warning: Could not load YOLOv10 ({e})")
                try:
                    self.model = YOLO('yolov8n.pt')
                except Exception as e2:
                    print(f"Warning: YOLOv8 also not available ({e2})")
                    self.model = None 

        # MediaPipe FaceMesh for Precision Eye Tracking
        self.mp_face_mesh = mp.solutions.face_mesh
        self.face_mesh = None
        self.face_mesh_available = False
        try:
            self.face_mesh = self.mp_face_mesh.FaceMesh(
                max_num_faces=1,
                refine_landmarks=True,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5
            )
            self.face_mesh_available = True
        except Exception as e:
            print(f"Warning: MediaPipe FaceMesh unavailable; running without facial landmarks ({e})")
        self.mp_drawing = mp.solutions.drawing_utils
        self.drawing_spec = self.mp_drawing.DrawingSpec(thickness=1, circle_radius=1, color=(0, 255, 0))

        # Eye Landmark Indices (MediaPipe)
        self.LEFT_EYE = [362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398]
        self.RIGHT_EYE = [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246]
        self.LEFT_IRIS = [474, 475, 476, 477]
        self.RIGHT_IRIS = [469, 470, 471, 472]
        self.LEFT_EYE_CORNERS = (362, 263)
        self.RIGHT_EYE_CORNERS = (33, 133)
        self.LEFT_EYE_VERTICAL = (386, 374)
        self.RIGHT_EYE_VERTICAL = (159, 145)
        
        # Mouth Landmark Indices (MAR)
        self.MOUTH_VERTICAL = [13, 14]
        self.MOUTH_HORIZONTAL = [78, 308]
        self.MAR_THRESHOLD = 0.4 
        
        # Thresholds & State
        self.EAR_THRESHOLD = 0.22  # Slightly raised for more reliable eye closure detection
        self.drowsy_frames = 0
        self.yawn_frames = 0
        self.phone_frames = 0
        self.head_down_frames = 0
        self.eye_closed_started_at = None
        self.eye_closed_seconds = 0.0
        # Hysteresis: eyes must be open for this many consecutive frames to reset the timer
        self.EYE_OPEN_RESET_FRAMES = 3
        self.eye_open_frames = 0  # consecutive open-eye frame counter
        
        self.DROWSY_SECONDS = 9.0  # ~6 seconds of warning (after 3s SLEEPY) before DANGER
        self.SLEEPY_SECONDS = 3.0  
        self.YAWN_ALERT_THRESHOLD = 30 
        self.PHONE_ALERT_THRESHOLD = 2  
        self.PHONE_COOLDOWN_THRESHOLD = 15 
        self.PHONE_DANGER_THRESHOLD = 240  # ~8 seconds total (2s warning + 6s danger delay)
        
        self.phone_frames = 0
        self.phone_miss_frames = 0
        self.phone_active = False 
        
        self.HEAD_DOWN_ALERT_THRESHOLD = 15 # ~0.5s trigger
        self.HEAD_DOWN_DANGER_THRESHOLD = 180 # ~6s later trigger
        self.HEAD_DOWN_TILT_THRESHOLD = 0.40 
        self.head_down_frames = 0

        # Attention monitoring is based on face pose and iris position, not only phone visibility.
        self.GAZE_HORIZONTAL_THRESHOLD = 0.18
        self.GAZE_DOWN_THRESHOLD = 0.68
        self.HEAD_YAW_THRESHOLD = 0.085
        self.ROAD_ATTENTION_WARNING_SECONDS = 2.0
        self.ROAD_ATTENTION_DANGER_SECONDS = 8.0 # ~6s warning period
        self.ATTENTION_RESET_FRAMES = 5
        self.SIDE_GLANCE_WINDOW_SECONDS = 8.0
        self.SIDE_GLANCE_REPEAT_COUNT = 3
        self.road_attention_started_at = None
        self.road_attention_seconds = 0.0
        self.attention_reset_frames = 0
        self.gaze_diverted_frames = 0
        self.side_glance_active = False
        self.side_glance_count = 0
        self.side_glance_window_started_at = None
        self.gaze_horizontal_baseline = None
        self.yaw_baseline = None
        self.BASELINE_ALPHA = 0.02
        
        self.last_face_time = time.time()
        self.absence_alert_time = 5.0 
        
    def get_ear(self, landmarks, eye_indices):
        v1 = np.linalg.norm(np.array([landmarks[eye_indices[12]].x, landmarks[eye_indices[12]].y]) - 
                            np.array([landmarks[eye_indices[4]].x, landmarks[eye_indices[4]].y]))
        v2 = np.linalg.norm(np.array([landmarks[eye_indices[14]].x, landmarks[eye_indices[14]].y]) - 
                            np.array([landmarks[eye_indices[2]].x, landmarks[eye_indices[2]].y]))
        h = np.linalg.norm(np.array([landmarks[eye_indices[0]].x, landmarks[eye_indices[0]].y]) - 
                           np.array([landmarks[eye_indices[8]].x, landmarks[eye_indices[8]].y]))
        return (v1 + v2) / (2.0 * h)

    def get_mar(self, landmarks):
        v = np.linalg.norm(np.array([landmarks[self.MOUTH_VERTICAL[0]].x, landmarks[self.MOUTH_VERTICAL[0]].y]) - 
                           np.array([landmarks[self.MOUTH_VERTICAL[1]].x, landmarks[self.MOUTH_VERTICAL[1]].y]))
        h = np.linalg.norm(np.array([landmarks[self.MOUTH_HORIZONTAL[0]].x, landmarks[self.MOUTH_HORIZONTAL[0]].y]) - 
                           np.array([landmarks[self.MOUTH_HORIZONTAL[1]].x, landmarks[self.MOUTH_HORIZONTAL[1]].y]))
        return v / h

    def _center_point(self, landmarks, indices):
        return np.array([
            np.mean([landmarks[idx].x for idx in indices]),
            np.mean([landmarks[idx].y for idx in indices])
        ])

    def _eye_gaze_ratio(self, landmarks, iris_indices, corner_indices, vertical_indices):
        if max(iris_indices + list(corner_indices) + list(vertical_indices)) >= len(landmarks):
            return None

        iris = self._center_point(landmarks, iris_indices)
        c1 = landmarks[corner_indices[0]]
        c2 = landmarks[corner_indices[1]]
        left_x, right_x = sorted([c1.x, c2.x])
        eye_width = right_x - left_x
        if eye_width <= 0:
            return None

        top = landmarks[vertical_indices[0]]
        bottom = landmarks[vertical_indices[1]]
        top_y, bottom_y = sorted([top.y, bottom.y])
        eye_height = bottom_y - top_y
        if eye_height <= 0:
            return None

        return {
            "horizontal": (iris[0] - left_x) / eye_width,
            "vertical": (iris[1] - top_y) / eye_height
        }

    def _estimate_attention(self, landmarks, face_height, face_width, nose_tip, face_center_x):
        left_gaze = self._eye_gaze_ratio(
            landmarks, self.LEFT_IRIS, self.LEFT_EYE_CORNERS, self.LEFT_EYE_VERTICAL
        )
        right_gaze = self._eye_gaze_ratio(
            landmarks, self.RIGHT_IRIS, self.RIGHT_EYE_CORNERS, self.RIGHT_EYE_VERTICAL
        )
        gaze_samples = [g for g in [left_gaze, right_gaze] if g]

        avg_horizontal = 0.5
        vertical_ratio = 0.5
        if gaze_samples:
            avg_horizontal = float(np.mean([g["horizontal"] for g in gaze_samples]))
            vertical_ratio = float(np.mean([g["vertical"] for g in gaze_samples]))

        yaw_ratio = (nose_tip.x - face_center_x) / face_width if face_width > 0 else 0
        if self.gaze_horizontal_baseline is None:
            self.gaze_horizontal_baseline = avg_horizontal
        if self.yaw_baseline is None:
            self.yaw_baseline = yaw_ratio

        horizontal_deviation = abs(avg_horizontal - self.gaze_horizontal_baseline)
        yaw_deviation = yaw_ratio - self.yaw_baseline
        stable_forward_pose = (
            horizontal_deviation < self.GAZE_HORIZONTAL_THRESHOLD * 0.55 and
            abs(yaw_deviation) < self.HEAD_YAW_THRESHOLD * 0.55 and
            vertical_ratio <= self.GAZE_DOWN_THRESHOLD
        )
        if stable_forward_pose:
            self.gaze_horizontal_baseline = (
                (1.0 - self.BASELINE_ALPHA) * self.gaze_horizontal_baseline +
                self.BASELINE_ALPHA * avg_horizontal
            )
            self.yaw_baseline = (
                (1.0 - self.BASELINE_ALPHA) * self.yaw_baseline +
                self.BASELINE_ALPHA * yaw_ratio
            )

        gaze_diverted = horizontal_deviation > self.GAZE_HORIZONTAL_THRESHOLD or vertical_ratio > self.GAZE_DOWN_THRESHOLD
        side_attention = abs(yaw_deviation) > self.HEAD_YAW_THRESHOLD or horizontal_deviation > self.GAZE_HORIZONTAL_THRESHOLD
        looking_down_with_eyes = vertical_ratio > self.GAZE_DOWN_THRESHOLD

        return {
            "gaze_diverted": gaze_diverted,
            "side_attention": side_attention,
            "looking_down_with_eyes": looking_down_with_eyes,
            "horizontal_deviation": horizontal_deviation,
            "vertical_ratio": vertical_ratio,
            "yaw_ratio": yaw_deviation,
            "face_height": face_height
        }

    def _update_attention_state(self, attention_off, side_attention):
        now = time.time()

        if attention_off:
            self.attention_reset_frames = 0
            if self.road_attention_started_at is None:
                self.road_attention_started_at = now
            self.road_attention_seconds = now - self.road_attention_started_at
        else:
            self.attention_reset_frames += 1
            if self.attention_reset_frames >= self.ATTENTION_RESET_FRAMES:
                self.road_attention_started_at = None
                self.road_attention_seconds = 0.0

        if side_attention and not self.side_glance_active:
            if (
                self.side_glance_window_started_at is None or
                now - self.side_glance_window_started_at > self.SIDE_GLANCE_WINDOW_SECONDS
            ):
                self.side_glance_window_started_at = now
                self.side_glance_count = 0
            self.side_glance_count += 1
            self.side_glance_active = True
        elif not side_attention:
            self.side_glance_active = False

        if (
            self.side_glance_window_started_at is not None and
            now - self.side_glance_window_started_at > self.SIDE_GLANCE_WINDOW_SECONDS
        ):
            self.side_glance_window_started_at = None
            self.side_glance_count = 0

    def detect(self, frame):
        h, w = frame.shape[:2]
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        # 1. YOLOv10 Detection (Phones)
        phone_detected_visually = False
        detections = []
        
        if self.model is not None:
            yolo_results = self.model(frame, verbose=False)
            
            for r in yolo_results:
                for box in r.boxes:
                    cls = int(box.cls[0])
                    conf = float(box.conf[0])
                    label = self.model.names[cls]
                    
                    # Detect phones 
                    phone_classes = ["cell phone", "phone", "mobile", "remote"]
                    is_phone = any(c in label.lower() for c in phone_classes) or cls == 67
                    
                    min_conf = 0.20 if is_phone else 0.40  # Lower threshold for phones
                    if conf < min_conf: continue
                    
                    if is_phone: 
                        phone_detected_visually = True
                        label = "cell phone"
                        print(f"📱 Phone detected! Confidence: {conf:.2f}")
                    
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    detections.append({"box": [x1, y1, x2, y2], "label": label, "conf": conf})

        # Phone persistence logic with Hysteresis 
        if phone_detected_visually:
            self.phone_frames += 1
            self.phone_miss_frames = 0
            if self.phone_frames > self.PHONE_ALERT_THRESHOLD:
                self.phone_active = True
        else:
            self.phone_miss_frames += 1
            if self.phone_miss_frames > self.PHONE_COOLDOWN_THRESHOLD:
                self.phone_frames = 0
                self.phone_active = False
        
        # Danger Escalation Logic
        phone_is_danger = self.phone_active and self.phone_frames > self.PHONE_DANGER_THRESHOLD

        # 2. MediaPipe FaceMesh
        mesh_results = None
        if self.face_mesh_available and self.face_mesh is not None:
            try:
                mesh_results = self.face_mesh.process(rgb_frame)
            except Exception:
                mesh_results = None
        status_labels = []
        alert = False
        face_box = None
        
        if mesh_results and mesh_results.multi_face_landmarks:
            self.last_face_time = time.time()
            face_landmarks = mesh_results.multi_face_landmarks[0].landmark
            
            # EAR/MAR Calculation
            left_ear = self.get_ear(face_landmarks, self.LEFT_EYE)
            right_ear = self.get_ear(face_landmarks, self.RIGHT_EYE)
            avg_ear = (left_ear + right_ear) / 2.0
            mar = self.get_mar(face_landmarks)

            # Face Bounding Box
            coords = [(int(l.x * w), int(l.y * h)) for l in face_landmarks]
            x_coords = [c[0] for c in coords]
            y_coords = [c[1] for c in coords]
            face_box = [min(x_coords), min(y_coords), max(x_coords), max(y_coords)]

            # Alert States
            eyes_closed = avg_ear < self.EAR_THRESHOLD
            yawning = mar > self.MAR_THRESHOLD
            
            
            forehead = face_landmarks[10]
            chin = face_landmarks[152]
            nose_tip = face_landmarks[1]
            eye_left = face_landmarks[362]
            eye_right = face_landmarks[33]
            eye_y = (eye_left.y + eye_right.y) / 2.0
            
            
            left_ear_lp = face_landmarks[234]
            right_ear_lp = face_landmarks[454]
            
            face_height = chin.y - forehead.y
            nose_drop = nose_tip.y - eye_y
            
    
            face_width = abs(right_ear_lp.x - left_ear_lp.x)
            face_center_x = (left_ear_lp.x + right_ear_lp.x) / 2.0
            
           
            normalized_drop = nose_drop / face_height if face_height > 0 else 0
            
            # Get detailed attention data (gaze, yaw, etc.)
            attention_data = self._estimate_attention(face_landmarks, face_height, face_width, nose_tip, face_center_x)
            gaze_diverted = attention_data["gaze_diverted"]
            side_attention = attention_data["side_attention"]
            
           
            head_down_visually = normalized_drop > self.HEAD_DOWN_TILT_THRESHOLD and nose_drop > 0# Only consider head-down as attention off-road, ignoring sensitive gaze/iris tracking
            attention_off_road = head_down_visually 
            
          
            if eyes_closed:
                self.eye_open_frames = 0  # reset open-eye counter
                self.drowsy_frames += 1
                if self.eye_closed_started_at is None:
                    self.eye_closed_started_at = time.time()
                self.eye_closed_seconds = time.time() - self.eye_closed_started_at
            else:
                self.eye_open_frames += 1
                
                if self.eye_open_frames >= self.EYE_OPEN_RESET_FRAMES:
                    self.drowsy_frames = 0
                    self.eye_closed_started_at = None
                    self.eye_closed_seconds = 0.0
                else:
                   
                    if self.eye_closed_started_at is not None:
                        self.eye_closed_seconds = time.time() - self.eye_closed_started_at

            if head_down_visually:
                self.head_down_frames += 1
            else:
                self.head_down_frames = 0

            if gaze_diverted:
                self.gaze_diverted_frames += 1
            else:
                self.gaze_diverted_frames = 0

            self._update_attention_state(attention_off_road, side_attention)

            if yawning:
                self.yawn_frames += 1
            else:
                self.yawn_frames = 0
                
       
            if self.eye_closed_seconds >= self.DROWSY_SECONDS:
                status_labels.append("DANGER: DROWSY")
                alert = True
            elif self.eye_closed_seconds >= self.SLEEPY_SECONDS:
                status_labels.append("WARNING: SLEEPY")
            
            if self.head_down_frames > self.HEAD_DOWN_DANGER_THRESHOLD:
                status_labels.append("DANGER: HEAD DOWN")
                alert = True
            elif self.head_down_frames > self.HEAD_DOWN_ALERT_THRESHOLD:
                status_labels.append("HEAD DISTRACTION (DOWN)")

            if self.road_attention_seconds >= self.ROAD_ATTENTION_DANGER_SECONDS:
                status_labels.append("DANGER: ROAD ATTENTION LOST")
                alert = True
            elif self.road_attention_seconds >= self.ROAD_ATTENTION_WARNING_SECONDS:
                status_labels.append("ROAD ATTENTION WARNING")

            if self.yawn_frames > self.YAWN_ALERT_THRESHOLD:
                status_labels.append("YAWNING")
                # Yawning is a warning, but sustained yawning could be danger
                if self.yawn_frames > self.YAWN_ALERT_THRESHOLD * 2:
                    status_labels.append("DANGER: SEVERE FATIGUE")
                    alert = True

        else:
            self.drowsy_frames = 0
            self.eye_open_frames = 0
            self.eye_closed_started_at = None
            self.eye_closed_seconds = 0.0
            self.gaze_diverted_frames = 0
            self.road_attention_started_at = None
            self.road_attention_seconds = 0.0
            self.attention_reset_frames = 0
            self.side_glance_active = False
            self.gaze_horizontal_baseline = None
            self.yaw_baseline = None

            # Driver Absence logic
            absence_duration = time.time() - self.last_face_time
            if absence_duration > self.absence_alert_time:
                status_labels.append("NO FACE SEEN")
                alert = True
                
        if self.phone_active:
            if phone_is_danger:
                status_labels.append("DANGER: PHONE USAGE")
                alert = True
            else:
                status_labels.append("PHONE USAGE (WARNING)")


        if not status_labels:
            status = "SAFE"
        else:
            status = " | ".join(status_labels)

        
        landmarks = mesh_results.multi_face_landmarks[0] if (mesh_results and mesh_results.multi_face_landmarks) else None
        
        return (detections, face_box, status_labels, landmarks), status, alert

    def annotate_frame(self, frame, detection_data, status, alert):
        detections, face_box, status_labels, face_landmarks_proto = detection_data
        h, w = frame.shape[:2]
        
        # Draw Face Landmarks (Manual Drawing for reliability)
        if face_landmarks_proto:
            landmarks = face_landmarks_proto.landmark
            
            # 1. Draw Eyes (Manual Outlines - Thick Red for visibility)
            for eye_indices in [self.LEFT_EYE, self.RIGHT_EYE]:
                pts = np.array([[int(landmarks[idx].x * w), int(landmarks[idx].y * h)] for idx in eye_indices], np.int32)
                cv2.polylines(frame, [pts], True, (0, 0, 255), 2) # Red outline

            # 2. Draw Mouth (Orange)
            mouth_indices = self.MOUTH_VERTICAL + self.MOUTH_HORIZONTAL
            for idx in mouth_indices:
                l = landmarks[idx]
                cv2.circle(frame, (int(l.x * w), int(l.y * h)), 3, (0, 165, 255), -1)

            # 3. Show Tracking Status near face
            if face_box:
                cv2.putText(frame, "TRACKING ACTIVE", (face_box[0], face_box[1] - 10), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
                cv2.rectangle(frame, (face_box[0], face_box[1]), (face_box[2], face_box[3]), (255, 255, 0), 1)

            # Standard MediaPipe drawing (Face outline only, no eyebrows)
            try:
                self.mp_drawing.draw_landmarks(
                    image=frame,
                    landmark_list=face_landmarks_proto,
                    connections=self.mp_face_mesh.FACEMESH_FACE_OVAL,
                    landmark_drawing_spec=None,
                    connection_drawing_spec=self.mp_drawing.DrawingSpec(color=(0, 255, 0), thickness=1, circle_radius=1)
                )
            except:
                pass

        # Draw YOLO detections
        for det in detections:
            x1, y1, x2, y2 = det["box"]
            color = (0, 255, 0) if not alert else (0, 0, 255)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.putText(frame, det["label"], (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

        # Draw Face Bounding Box
        if face_box:
            x1, y1, x2, y2 = face_box
            cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 255, 0), 2)
       
        is_safe = status == "SAFE" or not status_labels
        color = (0, 255, 0) if is_safe else (0, 0, 255)
        
        y0, dy = 50, 40
        for i, line in enumerate(status_labels if status_labels else ["SAFE"]):
            cv2.putText(frame, line, (20, y0 + i*dy), cv2.FONT_HERSHEY_SIMPLEX, 1, color, 3)
            
        return frame
