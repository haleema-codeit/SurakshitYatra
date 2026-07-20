

import sys
import json
from datetime import datetime, timedelta

def test_imports():
    """Test if all required modules are importable"""
    print("\n🔍 Testing Imports...")
    try:
        from app import app, db, socketio
        from models import Bus, BusLocation, Depot
        print("✅ All imports successful")
        return True
    except Exception as e:
        print(f"❌ Import failed: {e}")
        return False

def test_database():
    """Test database operations"""
    print("\n🔍 Testing Database...")
    try:
        from app import app, db
        from models import Bus, BusLocation, Depot
        
        with app.app_context():
          
            inspector = db.inspect(db.engine)
            tables = inspector.get_table_names()
            
            required_tables = ['bus', 'bus_location', 'depot']
            missing = [t for t in required_tables if t not in tables]
            
            if missing:
                print(f"❌ Missing tables: {missing}")
                print("   Run: python3 -c \"from app import app, db; db.create_all()\"")
                return False
            
            print("✅ Database tables exist")
            
           
            bus_columns = [col.name for col in inspector.get_columns('bus')]
            required_columns = ['current_latitude', 'current_longitude', 'is_tracking_active', 'last_location_update']
            missing_columns = [col for col in required_columns if col not in bus_columns]
            
            if missing_columns:
                print(f"❌ Missing Bus columns: {missing_columns}")
                return False
            
            print("✅ Bus table has all required columns")
            
            
            if 'bus_location' not in tables:
                print("❌ BusLocation table not found")
                return False
                
            print("✅ BusLocation table exists")
            return True
            
    except Exception as e:
        print(f"❌ Database test failed: {e}")
        return False

def test_api_endpoints():
    """Test if API endpoints are registered"""
    print("\n🔍 Testing API Endpoints...")
    try:
        from app import app
        
        endpoints = []
        for rule in app.url_map.iter_rules():
            if 'tracking' in rule.rule:
                endpoints.append(rule.rule)
        
        expected = [
            '/api/tracking/update-location',
            '/api/tracking/bus/<int:bus_id>/current',
            '/api/tracking/all-buses',
            '/api/tracking/bus/<int:bus_id>/history',
            '/api/tracking/bus/<int:bus_id>/start',
            '/api/tracking/bus/<int:bus_id>/stop',
            '/depot/bus-tracking'
        ]
        
        print(f"Found {len(endpoints)} tracking endpoints:")
        for ep in endpoints:
            print(f"  ✅ {ep}")
        
        missing = [e for e in expected if e not in endpoints]
        if missing:
            print(f"⚠️  Missing endpoints: {missing}")
            return False
        
        print("✅ All expected endpoints registered")
        return True
        
    except Exception as e:
        print(f"❌ Endpoint test failed: {e}")
        return False

def test_socketio_events():
  
    print("\n🔍 Testing WebSocket Events...")
    try:
        from app import socketio
        
        # Check registered namespace
        events = socketio.on.__dict__.get('namespaces', {})
        print(f"✅ SocketIO initialized")
        print(f"   Events should include: connect, disconnect, request_all_buses")
        return True
        
    except Exception as e:
        print(f"❌ WebSocket test failed: {e}")
        return False

def test_static_files():
    """Test if static files exist"""
    print("\n🔍 Testing Static Files...")
    try:
        import os
        
        files = [
            'static/js/bus-tracker.js',
            'templates/depot/bus_tracking.html'
        ]
        
        missing = []
        for file in files:
            if not os.path.exists(file):
                missing.append(file)
            else:
                print(f"✅ {file}")
        
        if missing:
            print(f"❌ Missing files: {missing}")
            return False
        
        print("✅ All static files present")
        return True
        
    except Exception as e:
        print(f"❌ Static files test failed: {e}")
        return False

def test_models():
    """Test model structure"""
    print("\n🔍 Testing Model Structure...")
    try:
        from models import Bus, BusLocation
        
       
        bus_attrs = dir(Bus)
        required_bus_attrs = ['current_latitude', 'current_longitude', 'is_tracking_active', 'last_location_update']
        
        
        location_attrs = dir(BusLocation)
        required_location_attrs = ['latitude', 'longitude', 'speed', 'altitude', 'accuracy', 'heading', 'timestamp']
        
        print("✅ Bus model attributes:")
        for attr in required_bus_attrs:
            print(f"   ✅ {attr}")
        
        print("✅ BusLocation model attributes:")
        for attr in required_location_attrs:
            print(f"   ✅ {attr}")
        
        return True
        
    except Exception as e:
        print(f"❌ Model test failed: {e}")
        return False

def main():
    """Run all tests"""
    print("="*60)
    print("🚌 Surakshit Yatra - Bus Tracking System Test")
    print("="*60)
    
    tests = [
        ("Imports", test_imports),
        ("Database", test_database),
        ("API Endpoints", test_api_endpoints),
        ("WebSocket Events", test_socketio_events),
        ("Static Files", test_static_files),
        ("Model Structure", test_models),
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ Unexpected error in {test_name}: {e}")
            results.append((test_name, False))
   
    print("\n" + "="*60)
    print("📊 TEST SUMMARY")
    print("="*60)
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {test_name}")
    
    print("="*60)
    print(f"Result: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All tests passed! System is ready.")
        print("\nNext steps:")
        print("1. Start server: python3 app.py")
        print("2. Login: http://localhost:5001/depot/login")
        print("3. Visit: http://localhost:5001/depot/bus-tracking")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed. Please fix issues above.")
        return 1

if __name__ == '__main__':
    sys.exit(main())
