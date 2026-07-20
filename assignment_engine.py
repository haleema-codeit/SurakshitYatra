"""
Smart Assignment Engine for Depot Management System
Handles automatic assignment of drivers to buses and buses to routes
based on availability, workload balancing, and demand
"""

from models import (
    db, Driver, Bus, Route, Schedule, DriverShift, DriverAssignment, 
    BusAssignment, Alert, OperationLog
)
from datetime import datetime, timedelta, date
from enum import Enum


class AssignmentStatus(Enum):
    """Assignment status enumeration"""
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class SmartAssignmentEngine:
    """Smart assignment engine for automatic and manual assignments"""

    DEFAULT_SERVICE_START = '06:00'
    DEFAULT_TURNAROUND_MINUTES = 30
    BUS_TURNAROUND_MINUTES = 15
    DEMAND_TRIPS = {
        'Low': 2,
        'Medium': 4,
        'High': 6,
        'Peak': 8
    }
    
    def __init__(self):
        self.unassigned_buses = []
        self.unassigned_drivers = []
        self.conflicts = []
    
    #  DRIVER ASSIGNMENT 
    
    def get_available_drivers(self, assignment_date, start_time, end_time):
        """
        Get drivers available for assignment on given date and time
        
        Criteria:
        - Driver status is Active
        - No conflicting shift
        - No conflicting assignment
        - Workload is balanced
        """
        available_drivers = []
        
        if isinstance(assignment_date, str):
            assignment_date = datetime.strptime(assignment_date, '%Y-%m-%d').date()
        
        # Get all active drivers
        active_drivers = Driver.query.filter_by(status='Active').all()
        
        for driver in active_drivers:
            # Check for shifts. If shifts are used, the driver MUST have a shift covering the time.
            shifts = DriverShift.query.filter(
                DriverShift.driver_id == driver.id,
                DriverShift.shift_date == assignment_date,
                DriverShift.status.in_(['On Duty', 'Scheduled'])
            ).all()
            
            if shifts:
               
                if not any(shift.start_time <= start_time for shift in shifts):
                    continue
          
            
            # Check for existing assignments today
            existing_assignments = DriverAssignment.query.filter(
                DriverAssignment.driver_id == driver.id,
                DriverAssignment.assignment_date == assignment_date
            ).all()
            
            # If we want to enforce 1 driver 1 bus per day:
            if existing_assignments:
                # If any existing assignment is for a DIFFERENT bus, this driver is not available for this bus
                if any(a.bus_id != bus_id for a in existing_assignments):
                    continue
                
                
                has_assignment_conflict = False
                for assignment in existing_assignments:
                    if self._time_conflict(
                        assignment.start_time,
                        assignment.end_time,
                        start_time,
                        end_time
                    ):
                        has_assignment_conflict = True
                        break
                if has_assignment_conflict:
                    continue
            
            # Check workload (not exceeding 8 hours or max_hours per shift type)
            if self._is_workload_exceeded(driver.id, assignment_date, start_time, end_time):
                continue
            
            available_drivers.append(driver)
        
        return available_drivers
    
    def get_best_driver_for_bus(self, bus_id, assignment_date, start_time, end_time):
        """
        Get the best available driver for a specific bus
        Priority based on:
        1. Least workload hours
        2. Experience (most recent assignments)
        3. Closest location (if tracked)
        """
        available_drivers = self.get_available_drivers(
            assignment_date, 
            start_time, 
            end_time
        )
        
        if not available_drivers:
            return None
        
   
        driver_scores = []
        
        for driver in available_drivers:
            workload_hours = self._get_driver_workload_hours(
                driver.id, 
                assignment_date
            )
            recent_assignments = DriverAssignment.query.filter(
                DriverAssignment.driver_id == driver.id,
                DriverAssignment.status == 'Completed'
            ).count()
            
            
            score = (8 - workload_hours) * 10 + min(recent_assignments, 50)
            
            driver_scores.append({
                'driver': driver,
                'score': score,
                'workload_hours': workload_hours
            })
        
      
        if driver_scores:
            best_driver = max(driver_scores, key=lambda x: x['score'])
            return best_driver['driver']
        
        return None
    
    def assign_driver_to_bus(self, driver_id, bus_id, assignment_date, 
                            start_time, end_time, admin_id, 
                            reason='Scheduled'):
        """
        Assign a driver to a bus for a specific time period
        
        Returns:
        - assignment object if successful
        - None if assignment fails
        """
        try:
            # Convert assignment_date string to date object if needed
            if isinstance(assignment_date, str):
                assignment_date_obj = datetime.strptime(assignment_date, '%Y-%m-%d').date()
            else:
                assignment_date_obj = assignment_date
            
            if not isinstance(assignment_date_obj, date):
                raise ValueError(f"assignment_date must be a date object, got {type(assignment_date_obj)}")
            
            driver = Driver.query.get(driver_id)
            bus = Bus.query.get(bus_id)
            
            if not driver or not bus:
                return None
            
            # Check for conflicts
            conflicts = self._check_driver_conflicts(
                driver_id, assignment_date_obj, start_time, end_time
            )
            
            if conflicts:
                self._create_alert(
                    'Conflict',
                    'Critical',
                    f'Driver has conflicting assignments or shifts: {conflicts}',
                    None,
                    driver_id
                )
                return None
            
            # Create assignment
            assignment = DriverAssignment(
                driver_id=driver_id,
                bus_id=bus_id,
                assignment_date=assignment_date_obj,
                start_time=start_time,
                end_time=end_time,
                status='Assigned',
                assignment_reason=reason,
                created_by=admin_id
            )
            
            db.session.add(assignment)
            
            # Log operation
            self._create_operation_log(
                'Driver Assignment',
                f'Driver {driver.name} assigned to Bus {bus.bus_number} from {start_time} to {end_time}',
                bus_id=bus_id,
                driver_id=driver_id,
                performed_by=admin_id
            )
            
            db.session.commit()
            return assignment
            
        except Exception as e:
            db.session.rollback()
            return None
    
    # BUS ASSIGNMENT 
    
    def get_available_buses(self, route_id, assignment_date, 
                           expected_departure_time):
       
        available_buses = []
        route = Route.query.get(route_id)
        
        if not route:
            return available_buses
            
        if isinstance(assignment_date, str):
            assignment_date = datetime.strptime(assignment_date, '%Y-%m-%d').date()
        
        # Get all buses from the same depot
        all_buses = Bus.query.filter(
            Bus.depot_id == route.depot_id,
            Bus.status.in_(['Available', 'In Service'])
        ).all()
        
        for bus in all_buses:
            # Check for maintenance
            if bus.next_maintenance and assignment_date >= bus.next_maintenance.date():
                continue
            
          
            minimum_capacity = {
                'Low': 15,
                'Medium': 25,
                'High': 35,
                'Peak': 45
            }.get(route.demand_level or 'Medium', 25)

            if bus.capacity < minimum_capacity:
                continue
            
            
            if self._bus_has_route_conflict(
                bus.id,
                assignment_date,
                route,
                expected_departure_time
            ):
                continue
            
            available_buses.append(bus)
        
        return available_buses
    
    def get_best_bus_for_route(self, route_id, assignment_date, 
                              expected_departure_time):
        """
        Select best bus for a route based on:
        1. Current demand level for the route
        2. Bus capacity
        3. Fuel level
        4. Workload balancing
        """
        available_buses = self.get_available_buses(
            route_id, 
            assignment_date, 
            expected_departure_time
        )
        
        if not available_buses:
            return None
        
        route = Route.query.get(route_id)
        
        bus_scores = []
        
        for bus in available_buses:
            # Score based on multiple factors
            capacity_score = (bus.capacity / 50) * 10 if bus.capacity else 0
            fuel_score = bus.fuel_level / 10  # Higher fuel is better
            
            # Count existing assignments for load balancing
            existing_assignments = BusAssignment.query.filter(
                BusAssignment.bus_id == bus.id,
                BusAssignment.assignment_date == assignment_date,
                BusAssignment.status.in_(['Assigned', 'In Service'])
            ).count()
            
            # HUGE BONUS for a completely unassigned bus to ensure all buses are used
            new_bus_bonus = 50 if existing_assignments == 0 else 0
            load_balance_score = (5 - existing_assignments) * 5
            
            total_score = capacity_score + fuel_score + load_balance_score + new_bus_bonus
            
            bus_scores.append({
                'bus': bus,
                'score': total_score
            })
        
        if bus_scores:
            best_bus = max(bus_scores, key=lambda x: x['score'])
            return best_bus['bus']
        
        return None
    
    def assign_bus_to_route(self, bus_id, route_id, assignment_date,
                           expected_departure_time, admin_id,
                           reason='Scheduled'):
        """
        Assign a bus to a route for a specific departure time
        
        Returns:
        - assignment object if successful
        - None if assignment fails
        """
        try:
            # Convert assignment_date string to date object if needed
            if isinstance(assignment_date, str):
                assignment_date_obj = datetime.strptime(assignment_date, '%Y-%m-%d').date()
            else:
                assignment_date_obj = assignment_date
            
            if not isinstance(assignment_date_obj, date):
                raise ValueError(f"assignment_date must be a date object, got {type(assignment_date_obj)}")
            
            bus = Bus.query.get(bus_id)
            route = Route.query.get(route_id)
            
            if not bus or not route:
                return None

            existing_trip = BusAssignment.query.filter(
                BusAssignment.route_id == route_id,
                BusAssignment.assignment_date == assignment_date_obj,
                BusAssignment.expected_departure == expected_departure_time,
                BusAssignment.status.in_(['Assigned', 'In Service', 'Delayed'])
            ).first()

            if existing_trip:
                return existing_trip

            if self._bus_has_route_conflict(
                bus_id,
                assignment_date_obj,
                route,
                expected_departure_time
            ):
                self._create_alert(
                    'Conflict',
                    'High',
                    f'Bus {bus.bus_number} is already planned near {expected_departure_time}',
                    bus_id,
                    None
                )
                return None
            
            # Create bus assignment
            assignment = BusAssignment(
                bus_id=bus_id,
                route_id=route_id,
                assignment_date=assignment_date_obj,
                status='Assigned',
                expected_departure=expected_departure_time,
                assignment_reason=reason,
                created_by=admin_id
            )
            
            # Update bus status
            bus.status = 'In Service'
            
            db.session.add(assignment)
            
            # Log operation
            self._create_operation_log(
                'Bus Assignment',
                f'Bus {bus.bus_number} assigned to Route {route.route_code}',
                bus_id=bus_id,
                route_id=route_id,
                performed_by=admin_id
            )
            
            db.session.commit()
            return assignment
            
        except Exception as e:
            db.session.rollback()
            return None
    
    #  AUTOMATIC ASSIGNMENT
    
    def auto_assign_daily_operations(self, assignment_date, depot_id=None, admin_id=None):
        """
        Automatically assign drivers and buses for a day's operations
        
        Process:
        1. Build a daily trip plan from schedules, or route demand when no schedule exists
        2. For each trip, find a bus with enough time between trips
        3. Assign a matching driver for the same trip window
        4. Skip already-created trips so repeated clicks are safe
        """
        # Initialize tracking sets for one‑to‑one enforcement
        used_driver_ids = set()
        used_bus_ids = set()
        # Initialise result counters
        results = {
            'trips_planned': 0,
            'buses_assigned': 0,
            'drivers_assigned': 0,
            'skipped_existing': 0,
            'conflicts': [],
            'unassigned_buses': [],
            'unassigned_drivers': [],
            'used_default_plan': False
        }        
        try:
            from datetime import datetime as dt
            assignment_date_obj = dt.strptime(assignment_date, '%Y-%m-%d').date() if isinstance(assignment_date, str) else assignment_date
            performed_by = admin_id or 1
            
            # Get all routes to be operated on this date
            routes_to_operate = Route.query.filter_by(is_active=True)
            if depot_id:
                routes_to_operate = routes_to_operate.filter_by(depot_id=depot_id)
            
            routes_to_operate = routes_to_operate.all()
            
            for route in routes_to_operate:
                schedules = Schedule.query.filter(
                    Schedule.route_id == route.id,
                    Schedule.schedule_date == assignment_date_obj,
                    Schedule.is_active == True
                ).all()

                if not schedules:
                    results['used_default_plan'] = True

                trip_plan = self._build_trip_plan(route, schedules)

                for trip in trip_plan:
                    results['trips_planned'] += 1
                    departure_time = trip['departure_time']
                    arrival_time = trip['arrival_time']

                    existing_bus_assignment = self._get_existing_bus_assignment(
                        route.id,
                        assignment_date_obj,
                        departure_time
                    )

                    if existing_bus_assignment:
                        results['skipped_existing'] += 1
                        existing_driver = self._get_existing_driver_assignment_for_bus(
                            existing_bus_assignment.bus_id,
                            assignment_date_obj,
                            departure_time,
                            arrival_time
                        )

                        if not existing_driver:
                            best_driver = self.get_best_driver_for_bus(
                                existing_bus_assignment.bus_id,
                                assignment_date_obj,
                                departure_time,
                                arrival_time
                            )

                            if best_driver and best_driver.id not in used_driver_ids:
                                driver_assignment = self.assign_driver_to_bus(
                                    best_driver.id,
                                    existing_bus_assignment.bus_id,
                                    assignment_date_obj,
                                    departure_time,
                                    arrival_time,
                                    admin_id=performed_by,
                                    reason='Auto Scheduled'
                                )

                                if driver_assignment:
                                    results['drivers_assigned'] += 1
                                    used_driver_ids.add(best_driver.id)
                            else:
                                results['unassigned_drivers'].append(
                                    f"Route {route.route_code} at {departure_time}"
                                )
                        else:
                            used_driver_ids.add(existing_driver.driver_id)
                        continue

                    # Find best bus
                    best_bus = self.get_best_bus_for_route(
                        route.id,
                        assignment_date_obj,
                        departure_time
                    )
                    
                    # Skip buses that have already been assigned a driver
                    if best_bus and best_bus.id in used_bus_ids:
                        results['unassigned_buses'].append(
                            f"Route {route.route_code} at {departure_time} (bus already used)"
                        )
                        continue
                    
                    if best_bus:
                        # Assign bus to route
                        bus_assignment = self.assign_bus_to_route(
                            best_bus.id,
                            route.id,
                            assignment_date_obj,
                            departure_time,
                            admin_id=performed_by,
                            reason='Auto Scheduled'
                        )
                        
                        if bus_assignment:
                            results['buses_assigned'] += 1
                            used_bus_ids.add(best_bus.id)
                            
                            # Find and assign driver
                            best_driver = self.get_best_driver_for_bus(
                                best_bus.id,
                                assignment_date_obj,
                                departure_time,
                                arrival_time
                            )
                            
                            if best_driver:
                                driver_assignment = self.assign_driver_to_bus(
                                    best_driver.id,
                                    best_bus.id,
                                    assignment_date_obj,
                                    departure_time,
                                    arrival_time,
                                    admin_id=performed_by,
                                    reason='Auto Scheduled'
                                )
                                
                                if driver_assignment:
                                    results['drivers_assigned'] += 1
                            else:
                                results['unassigned_drivers'].append(
                                    f"Route {route.route_code} at {departure_time} - Bus {best_bus.bus_number}"
                                )
                        else:
                            results['unassigned_buses'].append(
                                f"Route {route.route_code} at {departure_time}"
                            )
                    else:
                        results['unassigned_buses'].append(
                            f"Route {route.route_code} at {departure_time}"
                        )
            
            return results
            
        except Exception as e:
            results['conflicts'].append(str(e))
            return results
    
    def clear_assignments(self, assignment_date, depot_id=None, admin_id=None):
        """
        Clear all assignments for a specific date
        """
        try:
            from datetime import datetime as dt
            assignment_date_obj = dt.strptime(assignment_date, '%Y-%m-%d').date() if isinstance(assignment_date, str) else assignment_date
            
            # Filter driver assignments
            driver_q = DriverAssignment.query.filter(DriverAssignment.assignment_date == assignment_date_obj)
            if depot_id:
                # Need to join with Bus to check depot_id
                driver_q = driver_q.join(Bus).filter(Bus.depot_id == depot_id)
            
            driver_assignments = driver_q.all()
            for a in driver_assignments:
                db.session.delete(a)
            
            # Filter bus assignments
            bus_q = BusAssignment.query.filter(BusAssignment.assignment_date == assignment_date_obj)
            if depot_id:
                bus_q = bus_q.join(Bus).filter(Bus.depot_id == depot_id)
            
            bus_assignments = bus_q.all()
            for a in bus_assignments:
                # Before deleting, reset bus status if it was In Service
                bus = Bus.query.get(a.bus_id)
                if bus and bus.status == 'In Service':
                    bus.status = 'Available'
                db.session.delete(a)
            
            # Log operation
            self._create_operation_log(
                'Clear Assignments',
                f'All assignments cleared for {assignment_date}',
                performed_by=admin_id
            )
            
            db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            return False

   
    def _build_trip_plan(self, route, schedules):
        """Return real trip windows for a route, using schedules or practical defaults."""
        trip_plan = []

        if schedules:
            for schedule in schedules:
                trips_per_day = max(schedule.trips_per_day or 1, 1)
                duration = self._duration_between_times(
                    schedule.departure_time,
                    schedule.arrival_time
                ) or route.estimated_duration_minutes or 60
                interval = duration + self.DEFAULT_TURNAROUND_MINUTES

                for trip_index in range(trips_per_day):
                    departure = self._add_minutes(schedule.departure_time, interval * trip_index)
                    arrival = self._add_minutes(departure, duration)
                    trip_plan.append({
                        'departure_time': departure,
                        'arrival_time': arrival,
                        'source': 'schedule'
                    })

            return trip_plan

        demand_level = route.demand_level or 'Medium'
        requested_trips = self.DEMAND_TRIPS.get(demand_level, self.DEMAND_TRIPS['Medium'])
        duration = route.estimated_duration_minutes or 60
        interval = duration + self.DEFAULT_TURNAROUND_MINUTES
        service_minutes = 16 * 60
        possible_trips = max(1, service_minutes // interval)
        trips_to_create = max(1, min(requested_trips, possible_trips))

        for trip_index in range(trips_to_create):
            departure = self._add_minutes(self.DEFAULT_SERVICE_START, interval * trip_index)
            arrival = self._add_minutes(departure, duration)
            trip_plan.append({
                'departure_time': departure,
                'arrival_time': arrival,
                'source': 'default'
            })

        return trip_plan

    def _get_existing_bus_assignment(self, route_id, assignment_date, departure_time):
        """Find an existing active bus assignment for the exact route trip."""
        return BusAssignment.query.filter(
            BusAssignment.route_id == route_id,
            BusAssignment.assignment_date == assignment_date,
            BusAssignment.expected_departure == departure_time,
            BusAssignment.status.in_(['Assigned', 'In Service', 'Delayed'])
        ).first()

    def _get_existing_driver_assignment_for_bus(self, bus_id, assignment_date, start_time, end_time):
        """Find a driver already covering this bus trip window."""
        return DriverAssignment.query.filter(
            DriverAssignment.bus_id == bus_id,
            DriverAssignment.assignment_date == assignment_date,
            DriverAssignment.start_time == start_time,
            DriverAssignment.end_time == end_time,
            DriverAssignment.status.in_(['Assigned', 'In Progress'])
        ).first()

    def _bus_has_route_conflict(self, bus_id, assignment_date, route, departure_time):
        """Check whether a bus can run this route around its other trips for the day."""
        existing_assignments = BusAssignment.query.filter(
            BusAssignment.bus_id == bus_id,
            BusAssignment.assignment_date == assignment_date,
            BusAssignment.status.in_(['Assigned', 'In Service', 'Delayed'])
        ).all()

        new_duration = route.estimated_duration_minutes or 60

        for assignment in existing_assignments:
            existing_route = Route.query.get(assignment.route_id)
            if not existing_route:
                continue

            existing_duration = existing_route.estimated_duration_minutes or 60
            if self._trip_windows_overlap(
                assignment.expected_departure,
                existing_duration,
                departure_time,
                new_duration,
                self.BUS_TURNAROUND_MINUTES
            ):
                return True

        return False

    def _trip_windows_overlap(self, departure_one, duration_one, departure_two, duration_two, buffer_minutes=0):
        """Compare two same-day trip windows with a turnaround buffer."""
        start_one = self._time_to_minutes(departure_one)
        start_two = self._time_to_minutes(departure_two)

        if start_one is None or start_two is None:
            return False

        end_one = start_one + duration_one
        end_two = start_two + duration_two

        return start_one < (end_two + buffer_minutes) and start_two < (end_one + buffer_minutes)

    def _duration_between_times(self, start_time, end_time):
        """Calculate minutes between two HH:MM values."""
        start_minutes = self._time_to_minutes(start_time)
        end_minutes = self._time_to_minutes(end_time)

        if start_minutes is None or end_minutes is None:
            return 0

        if end_minutes <= start_minutes:
            end_minutes += 24 * 60

        return end_minutes - start_minutes

    def _add_minutes(self, time_value, minutes):
        """Add minutes to an HH:MM time and return HH:MM."""
        base_minutes = self._time_to_minutes(time_value)
        if base_minutes is None:
            return time_value

        total = (base_minutes + minutes) % (24 * 60)
        return f"{total // 60:02d}:{total % 60:02d}"

    def _time_to_minutes(self, time_value):
        """Convert HH:MM to minutes from midnight."""
        try:
            parsed = datetime.strptime(time_value, '%H:%M')
            return parsed.hour * 60 + parsed.minute
        except:
            return None
    
    def _time_conflict(self, start1, end1, start2, end2):
        """Check if two time periods overlap"""
        try:
            s1 = datetime.strptime(start1, '%H:%M').time()
            e1 = datetime.strptime(end1, '%H:%M').time()
            s2 = datetime.strptime(start2, '%H:%M').time()
            e2 = datetime.strptime(end2, '%H:%M').time()
            
            return not (e1 <= s2 or e2 <= s1)
        except:
            return False
    
    def _is_workload_exceeded(self, driver_id, assignment_date, start_time, end_time):
        """Check if adding this assignment would exceed max workload"""
        current_workload = self._get_driver_workload_hours(driver_id, assignment_date)
        new_hours = self._calculate_hours(start_time, end_time)
        
        MAX_DAILY_HOURS = 12
        return (current_workload + new_hours) > MAX_DAILY_HOURS
    
    def _get_driver_workload_hours(self, driver_id, assignment_date):
        """Calculate total hours already assigned to driver on a date"""
        assignments = DriverAssignment.query.filter(
            DriverAssignment.driver_id == driver_id,
            DriverAssignment.assignment_date == assignment_date,
            DriverAssignment.status.in_(['Assigned', 'In Progress', 'Completed'])
        ).all()
        
        total_hours = 0
        for assignment in assignments:
            total_hours += self._calculate_hours(
                assignment.start_time,
                assignment.end_time
            )
        
        return total_hours
    
    def _calculate_hours(self, start_time, end_time):
        """Calculate hours between two times"""
        try:
            s = datetime.strptime(start_time, '%H:%M')
            e = datetime.strptime(end_time, '%H:%M')
            
            if e < s:  # Handle next day shifts
                e = e.replace(day=e.day + 1)
            
            return (e - s).total_seconds() / 3600
        except:
            return 0
    
    def _check_driver_conflicts(self, driver_id, assignment_date, start_time, end_time):
        """Check for driver scheduling conflicts"""
        conflicts = []
        
        # Determine the assignment date object to avoid string comparison errors
        if isinstance(assignment_date, str):
            assignment_date = datetime.strptime(assignment_date, '%Y-%m-%d').date()
        
        # Check assignments (must not overlap with another task)
        assignment_conflicts = DriverAssignment.query.filter(
            DriverAssignment.driver_id == driver_id,
            DriverAssignment.assignment_date == assignment_date,
            DriverAssignment.status.in_(['Assigned', 'In Progress'])
        ).all()
        
        for assignment in assignment_conflicts:
            if self._time_conflict(assignment.start_time, assignment.end_time, start_time, end_time):
                bus = Bus.query.get(assignment.bus_id)
                conflicts.append(f"Bus {bus.bus_number if bus else 'N/A'} already assigned")
        
        return conflicts
    
    def _can_double_assign(self, bus, route, departure_time):
        """Check if bus can handle multiple trips on same day"""
        # Simple logic: allow if trips are far enough apart
        MIN_GAP_MINUTES = 120  # 2 hours between trips
        
        existing_assignment = BusAssignment.query.filter(
            BusAssignment.bus_id == bus.id,
            BusAssignment.status.in_(['Assigned', 'In Service'])
        ).first()
        
        if existing_assignment:
            existing_route = Route.query.get(existing_assignment.route_id)
            gap = self._calculate_gap(
                existing_route.estimated_duration_minutes,
                existing_assignment.expected_departure,
                departure_time
            )
            return gap >= MIN_GAP_MINUTES
        
        return True
    
    def _calculate_gap(self, duration_minutes, departure_current, departure_next):
        """Calculate gap between end of current route and start of next"""
        try:
            current = datetime.strptime(departure_current, '%H:%M')
            next_dep = datetime.strptime(departure_next, '%H:%M')
            
            current_end = current + timedelta(minutes=duration_minutes)
            gap_minutes = (next_dep - current_end).total_seconds() / 60
            
            return gap_minutes
        except:
            return 0
    
    def _create_alert(self, alert_type, severity, message, bus_id=None, driver_id=None):
        """Create an alert for the admin"""
        try:
            alert = Alert(
                alert_type=alert_type,
                severity=severity,
                message=message,
                related_bus_id=bus_id,
                related_driver_id=driver_id,
                is_resolved=False
            )
            db.session.add(alert)
            db.session.commit()
        except:
            pass
    
    def _create_operation_log(self, log_type, description, bus_id=None, 
                             driver_id=None, route_id=None, performed_by=None):
        """Create an operation log entry"""
        try:
            log = OperationLog(
                log_type=log_type,
                description=description,
                bus_id=bus_id,
                driver_id=driver_id,
                route_id=route_id,
                performed_by=performed_by
            )
            db.session.add(log)
            db.session.commit()
        except:
            pass



assignment_engine = SmartAssignmentEngine()
