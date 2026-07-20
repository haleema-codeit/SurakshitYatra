from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import os

db = SQLAlchemy()

class Admin(UserMixin, db.Model):
    """Admin user model for authentication"""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class DriverUser(UserMixin, db.Model):
    """Driver user model for authentication"""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    license_number = db.Column(db.String(50), nullable=True)
    phone = db.Column(db.String(15), nullable=True)
    vehicle_assigned = db.Column(db.String(50), nullable=True)
    emergency_contact_name = db.Column(db.String(120), nullable=True)
    emergency_contact_phone = db.Column(db.String(15), nullable=True)
    system_mode = db.Column(db.String(20), default='Public') # 'Public' or 'Private'
    preferred_language = db.Column(db.String(20), default='English')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Driver(db.Model):
    """Driver model for managing drivers by admin"""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    license_number = db.Column(db.String(50), unique=True, nullable=False)
    phone = db.Column(db.String(15), nullable=True)
    email = db.Column(db.String(120), nullable=True)
    photo_filename = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(20), default='Active')  # Active, Inactive, Suspended
    emergency_contact_name = db.Column(db.String(120), nullable=True)
    emergency_contact_phone = db.Column(db.String(15), nullable=True)
    system_mode = db.Column(db.String(20), default='Public') # 'Public' or 'Private'
    preferred_language = db.Column(db.String(20), default='English')
    last_latitude = db.Column(db.Float, nullable=True)
    last_longitude = db.Column(db.Float, nullable=True)
    last_location_update = db.Column(db.DateTime, nullable=True)
    vehicle_assigned = db.Column(db.Integer, db.ForeignKey('vehicle.id'), nullable=True)
    vehicle = db.relationship('Vehicle', backref='assigned_drivers')
    hire_date = db.Column(db.DateTime, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'license_number': self.license_number,
            'phone': self.phone,
            'email': self.email,
            'photo_filename': self.photo_filename,
            'status': self.status,
            'emergency_contact_name': self.emergency_contact_name,
            'emergency_contact_phone': self.emergency_contact_phone,
            'system_mode': self.system_mode,
            'hire_date': self.hire_date.strftime('%Y-%m-%d') if self.hire_date else '',
        }


class Vehicle(db.Model):
    """Vehicle model for managing vehicles"""
    id = db.Column(db.Integer, primary_key=True)
    vehicle_number = db.Column(db.String(50), unique=True, nullable=False)
    vehicle_type = db.Column(db.String(50), nullable=False)  # Bus, Truck, Car, etc.
    route_name = db.Column(db.String(120), nullable=True)
    status = db.Column(db.String(20), default='Active')  # Active, Inactive, Maintenance
    capacity = db.Column(db.Integer, nullable=True)
    registration_date = db.Column(db.DateTime, default=datetime.utcnow)
    last_service = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'vehicle_number': self.vehicle_number,
            'vehicle_type': self.vehicle_type,
            'route_name': self.route_name,
            'status': self.status,
            'capacity': self.capacity,
            'registration_date': self.registration_date.strftime('%Y-%m-%d') if self.registration_date else '',
            'last_service': self.last_service.strftime('%Y-%m-%d') if self.last_service else '',
        }


# ======================== DEPOT MANAGEMENT MODELS ========================

class Depot(db.Model):
    """Depot model for managing transport depots"""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    location = db.Column(db.String(255), nullable=False)
    city = db.Column(db.String(100), nullable=False)
    manager_name = db.Column(db.String(120), nullable=True)
    contact_phone = db.Column(db.String(15), nullable=True)
    total_buses = db.Column(db.Integer, default=0)
    total_drivers = db.Column(db.Integer, default=0)
    current_latitude = db.Column(db.Float, nullable=True, default=28.7041) # Default Delhi
    current_longitude = db.Column(db.Float, nullable=True, default=77.1025)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    buses = db.relationship('Bus', backref='depot', cascade='all, delete-orphan')
    routes = db.relationship('Route', backref='depot', cascade='all, delete-orphan')
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'location': self.location,
            'city': self.city,
            'manager_name': self.manager_name,
            'contact_phone': self.contact_phone,
            'total_buses': self.total_buses,
            'total_drivers': self.total_drivers,
            'latitude': self.current_latitude,
            'longitude': self.current_longitude
        }


class Bus(db.Model):
    """Bus model for depot management system"""
    id = db.Column(db.Integer, primary_key=True)
    bus_number = db.Column(db.String(50), unique=True, nullable=False)
    depot_id = db.Column(db.Integer, db.ForeignKey('depot.id'), nullable=False)
    status = db.Column(db.String(20), default='Available')  # Available, In Service, Under Maintenance, Delayed
    capacity = db.Column(db.Integer, nullable=False)
    license_plate = db.Column(db.String(50), unique=True, nullable=False)
    last_maintenance = db.Column(db.DateTime, nullable=True)
    next_maintenance = db.Column(db.DateTime, nullable=True)
    fuel_level = db.Column(db.Integer, default=100)  # Percentage
    
    # GPS Tracking Fields
    current_latitude = db.Column(db.Float, nullable=True)
    current_longitude = db.Column(db.Float, nullable=True)
    last_location_update = db.Column(db.DateTime, nullable=True)
    is_tracking_active = db.Column(db.Boolean, default=False)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    assignments = db.relationship('DriverAssignment', backref='bus', cascade='all, delete-orphan')
    route_assignments = db.relationship('BusAssignment', backref='bus', cascade='all, delete-orphan')
    location_history = db.relationship('BusLocation', backref='bus', cascade='all, delete-orphan')
    
    def to_dict(self):
        return {
            'id': self.id,
            'bus_number': self.bus_number,
            'status': self.status,
            'capacity': self.capacity,
            'fuel_level': self.fuel_level,
            'latitude': self.current_latitude,
            'longitude': self.current_longitude,
            'is_tracking_active': self.is_tracking_active,
        }


class Route(db.Model):
    """Route model for bus routes"""
    id = db.Column(db.Integer, primary_key=True)
    route_name = db.Column(db.String(120), nullable=False)
    route_code = db.Column(db.String(50), unique=True, nullable=False)
    depot_id = db.Column(db.Integer, db.ForeignKey('depot.id'), nullable=False)
    start_point = db.Column(db.String(120), nullable=False)
    end_point = db.Column(db.String(120), nullable=False)
    distance_km = db.Column(db.Float, nullable=False)
    estimated_duration_minutes = db.Column(db.Integer, nullable=False)
    demand_level = db.Column(db.String(20), default='Medium')  # Low, Medium, High, Peak
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    schedules = db.relationship('Schedule', backref='route', cascade='all, delete-orphan')
    assignments = db.relationship('BusAssignment', backref='route', cascade='all, delete-orphan')
    
    def to_dict(self):
        return {
            'id': self.id,
            'route_name': self.route_name,
            'route_code': self.route_code,
            'start_point': self.start_point,
            'end_point': self.end_point,
            'distance_km': self.distance_km,
            'estimated_duration_minutes': self.estimated_duration_minutes,
            'demand_level': self.demand_level,
        }


class Schedule(db.Model):
    """Schedule model for bus/driver schedules"""
    id = db.Column(db.Integer, primary_key=True)
    route_id = db.Column(db.Integer, db.ForeignKey('route.id'), nullable=False)
    schedule_date = db.Column(db.Date, nullable=False)
    departure_time = db.Column(db.String(5), nullable=False)  # HH:MM format
    arrival_time = db.Column(db.String(5), nullable=False)
    trips_per_day = db.Column(db.Integer, default=1)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'departure_time': self.departure_time,
            'arrival_time': self.arrival_time,
            'trips_per_day': self.trips_per_day,
        }


class DriverShift(db.Model):
    """Driver shift management model"""
    id = db.Column(db.Integer, primary_key=True)
    driver_id = db.Column(db.Integer, db.ForeignKey('driver.id'), nullable=False)
    shift_date = db.Column(db.Date, nullable=False)
    shift_type = db.Column(db.String(20), nullable=False)  
    start_time = db.Column(db.String(5), nullable=False)  
    end_time = db.Column(db.String(5), nullable=False)
    status = db.Column(db.String(20), default='Scheduled')  
    max_hours = db.Column(db.Integer, default=8)
    is_weekend = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationship
    driver = db.relationship('Driver', backref='shifts')
    
    def to_dict(self):
        return {
            'id': self.id,
            'driver_id': self.driver_id,
            'shift_type': self.shift_type,
            'start_time': self.start_time,
            'end_time': self.end_time,
            'status': self.status,
        }


class DriverAssignment(db.Model):
    """Driver to bus assignment model"""
    id = db.Column(db.Integer, primary_key=True)
    driver_id = db.Column(db.Integer, db.ForeignKey('driver.id'), nullable=False)
    bus_id = db.Column(db.Integer, db.ForeignKey('bus.id'), nullable=False)
    assignment_date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.String(5), nullable=False)  
    end_time = db.Column(db.String(5), nullable=False)
    status = db.Column(db.String(20), default='Assigned')  
    assignment_reason = db.Column(db.String(50), default='Scheduled')  
    created_by = db.Column(db.Integer, db.ForeignKey('admin.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    driver = db.relationship('Driver', backref='assignments')
    admin = db.relationship('Admin', backref='driver_assignments')
    
    def to_dict(self):
        return {
            'id': self.id,
            'driver_id': self.driver_id,
            'bus_id': self.bus_id,
            'status': self.status,
        }


class BusAssignment(db.Model):
    """Bus to route assignment model"""
    id = db.Column(db.Integer, primary_key=True)
    bus_id = db.Column(db.Integer, db.ForeignKey('bus.id'), nullable=False)
    route_id = db.Column(db.Integer, db.ForeignKey('route.id'), nullable=False)
    assignment_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), default='Assigned')  
    expected_departure = db.Column(db.String(5), nullable=False)  
    actual_departure = db.Column(db.String(5), nullable=True)
    assignment_reason = db.Column(db.String(50), default='Scheduled') 
    created_by = db.Column(db.Integer, db.ForeignKey('admin.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    admin = db.relationship('Admin', backref='bus_assignments')
    
    def to_dict(self):
        return {
            'id': self.id,
            'bus_id': self.bus_id,
            'route_id': self.route_id,
            'status': self.status,
        }


class Alert(db.Model):
    """Alert/Notification model for depot operations"""
    id = db.Column(db.Integer, primary_key=True)
    alert_type = db.Column(db.String(50), nullable=False) 
    severity = db.Column(db.String(20), default='Medium') 
    message = db.Column(db.String(500), nullable=False)
    related_bus_id = db.Column(db.Integer, db.ForeignKey('bus.id'), nullable=True)
    related_driver_id = db.Column(db.Integer, db.ForeignKey('driver.id'), nullable=True)
    is_resolved = db.Column(db.Boolean, default=False)
    resolved_by = db.Column(db.Integer, db.ForeignKey('admin.id'), nullable=True)
    resolution_note = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    resolved_at = db.Column(db.DateTime, nullable=True)
    
    # Relationships
    related_bus = db.relationship('Bus', backref='alerts')
    related_driver = db.relationship('Driver', backref='alerts')
    admin = db.relationship('Admin', backref='resolved_alerts')
    
    def to_dict(self):
        return {
            'id': self.id,
            'alert_type': self.alert_type,
            'severity': self.severity,
            'message': self.message,
            'is_resolved': self.is_resolved,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        }


class OperationLog(db.Model):
    """Operation log model for records and history"""
    id = db.Column(db.Integer, primary_key=True)
    log_type = db.Column(db.String(50), nullable=False) 
    description = db.Column(db.String(500), nullable=False)
    bus_id = db.Column(db.Integer, db.ForeignKey('bus.id'), nullable=True)
    driver_id = db.Column(db.Integer, db.ForeignKey('driver.id'), nullable=True)
    route_id = db.Column(db.Integer, db.ForeignKey('route.id'), nullable=True)
    performed_by = db.Column(db.Integer, db.ForeignKey('admin.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    bus = db.relationship('Bus', backref='operation_logs')
    driver = db.relationship('Driver', backref='operation_logs')
    route = db.relationship('Route', backref='operation_logs')
    admin = db.relationship('Admin', backref='operation_logs')
    
    def to_dict(self):
        return {
            'id': self.id,
            'log_type': self.log_type,
            'description': self.description,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        }


class BusLocation(db.Model):
    """Bus location tracking model for real-time and historical tracking"""
    id = db.Column(db.Integer, primary_key=True)
    bus_id = db.Column(db.Integer, db.ForeignKey('bus.id'), nullable=False)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    speed = db.Column(db.Float, nullable=True)  
    altitude = db.Column(db.Float, nullable=True)  
    accuracy = db.Column(db.Float, nullable=True) 
    heading = db.Column(db.Float, nullable=True)  
    address = db.Column(db.String(500), nullable=True)  
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'bus_id': self.bus_id,
            'latitude': self.latitude,
            'longitude': self.longitude,
            'speed': self.speed,
            'altitude': self.altitude,
            'accuracy': self.accuracy,
            'heading': self.heading,
            'address': self.address,
            'timestamp': self.timestamp.strftime('%Y-%m-%d %H:%M:%S') if self.timestamp else None,
        }
