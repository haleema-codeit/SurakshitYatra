#!/usr/bin/env python
"""Initialize the database with fresh schema"""

import os
import sys

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from models import Admin, Driver, DriverUser, Vehicle


db_path = 'safedrive_admin.db'
if os.path.exists(db_path):
    os.remove(db_path)
    print(f"✓ Deleted old database: {db_path}")

with app.app_context():
    
    print("Dropping all existing tables...")
    db.drop_all()
    
    print("Creating all tables...")
    db.create_all()
    
  
    inspector = db.inspect(db.engine)
    tables = inspector.get_table_names()
    print(f"\n✓ Tables created: {tables}")
    
    #
    if not Admin.query.filter_by(username='admin').first():
        admin = Admin(username='admin', email='admin@safedrive.local')
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()
        print("✓ Default admin created")
    
    print("\n✅ Database initialized successfully!")
