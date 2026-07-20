/**
 * Surakshit Yatra - Bus GPS Tracking Client
 * Usage in driver app: Include this script and call BusTracker.init(busId)
 */

const BusTracker = (() => {
    let isTracking = false;
    let gpsWatchId = null;
    let busId = null;
    let serverUrl = window.location.origin;
    let updateInterval = 10000; // Update every 10 seconds
    let currentStatus = 'In Service';
    let lastLocationTime = 0;

    const config = {
        enableHighAccuracy: true,
        maximumAge: 0,
        timeout: 5000
    };

    /**
     * Set current safety status
     * @param {string} status - SAFE, WARNING, or DANGER
     */
    function setStatus(status) {
        if (status === 'SAFE') currentStatus = 'In Service';
        else if (status === 'WARNING') currentStatus = 'Warning';
        else if (status === 'DANGER') currentStatus = 'Danger';
        else currentStatus = status;
        
        console.log(`📊 Bus status updated to: ${currentStatus}`);
    }

    /**
     * Initialize the GPS tracker
     * @param {number} id - Bus ID
     * @param {Object} options - Configuration options
     */
    function init(id, options = {}) {
        busId = id;
        updateInterval = options.updateInterval || 10000;
        serverUrl = options.serverUrl || window.location.origin;

        if (!navigator.geolocation) {
            console.error('❌ Geolocation is not supported by this browser');
            logError('Geolocation not supported');
            return false;
        }

        console.log(`✅ BusTracker initialized for Bus ID: ${busId}`);
        return true;
    }

    /**
     * Start continuous GPS tracking
     */
    async function startTracking() {
        if (isTracking) {
            console.warn('⚠️ Tracking already active');
            return false;
        }

        console.log('📍 Starting GPS tracking...');

        // Try to get one-time position immediately to send to server right away
        try {
            const firstPos = await getCurrentPosition();
            if (firstPos) {
                console.log('✅ Initial location acquired');
                // Artificial call to handlePositionSuccess with a constructed position object
                handlePositionSuccess({ coords: firstPos, timestamp: Date.now() });
            }
        } catch (err) {
            console.warn('⚠️ Initial location failed, waiting for watchPosition:', err);
        }

        // Start watching position with continuous updates
        gpsWatchId = navigator.geolocation.watchPosition(
            handlePositionSuccess,
            handlePositionError,
            config
        );

        isTracking = true;
        logEvent('Tracking started');
        return true;
    }

    /**
     * Stop GPS tracking
     */
    function stopTracking() {
        if (gpsWatchId !== null) {
            navigator.geolocation.clearWatch(gpsWatchId);
            gpsWatchId = null;
            isTracking = false;
            console.log('🛑 GPS tracking stopped');
            logEvent('Tracking stopped');
            return true;
        }
        return false;
    }

    /**
     * Handle successful position update
     */
    async function handlePositionSuccess(position) {
        const now = Date.now();
        
        // Always allow the very first update
        const isFirstUpdate = (lastLocationTime === 0);

        // Throttle updates to prevent excessive requests
        if (!isFirstUpdate && (now - lastLocationTime < updateInterval)) {
            return;
        }

        lastLocationTime = now;

        try {
            const coords = position.coords;
            const locationData = {
                bus_id: busId,
                latitude: coords.latitude,
                longitude: coords.longitude,
                speed: coords.speed, // meters per second
                altitude: coords.altitude,
                accuracy: coords.accuracy,
                heading: coords.heading,
                status: currentStatus,
                timestamp: new Date().toISOString()
            };

            // Send to server
            const response = await fetch(`${serverUrl}/api/tracking/update-location`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(locationData)
            });

            if (response.ok) {
                const result = await response.json();
                console.log('✅ Location updated:', {
                    lat: coords.latitude,
                    lon: coords.longitude,
                    speed: coords.speed ? (coords.speed * 3.6).toFixed(2) : 0 + ' km/h'
                });
                logEvent('Location updated');
                onLocationUpdate(locationData);
            } else {
                console.error('❌ Failed to update location:', response.status);
                logError('Location update failed');
            }
        } catch (error) {
            console.error('❌ Error sending location:', error);
            logError('Network error');
        }
    }

    /**
     * Handle position error
     */
    function handlePositionError(error) {
        let errorMessage = '';
        
        switch (error.code) {
            case error.PERMISSION_DENIED:
                errorMessage = 'User denied location permission';
                break;
            case error.POSITION_UNAVAILABLE:
                errorMessage = 'Location information is unavailable';
                break;
            case error.TIMEOUT:
                errorMessage = 'Request to get location timed out';
                break;
            default:
                errorMessage = 'An unknown error occurred';
        }

        console.error('📍 GPS Error:', errorMessage);
        logError(errorMessage);
    }

    /**
     * Get current GPS status
     */
    function getStatus() {
        return {
            isTracking: isTracking,
            busId: busId,
            lastUpdate: lastLocationTime ? new Date(lastLocationTime).toLocaleTimeString() : 'Never',
            updateInterval: updateInterval
        };
    }

    /**
     * Get current position (one-time)
     */
    async function getCurrentPosition() {
        return new Promise((resolve, reject) => {
            navigator.geolocation.getCurrentPosition(
                (position) => resolve(position.coords),
                (error) => reject(error),
                config
            );
        });
    }

    /**
     * Log events to server
     */
    async function logEvent(eventType, data = {}) {
        try {
            await fetch(`${serverUrl}/api/tracking/log`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    bus_id: busId,
                    event_type: eventType,
                    data: data,
                    timestamp: new Date().toISOString()
                })
            });
        } catch (error) {
            console.error('Error logging event:', error);
        }
    }

    /**
     * Log errors to server
     */
    async function logError(errorMessage) {
        try {
            await fetch(`${serverUrl}/api/tracking/error-log`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    bus_id: busId,
                    error: errorMessage,
                    timestamp: new Date().toISOString()
                })
            });
        } catch (error) {
            console.error('Error logging error:', error);
        }
    }

    /**
     * Callback for location updates (override in your app)
     */
    let onLocationUpdate = (data) => {
        console.log('Location callback:', data);
    };

    /**
     * Set location update callback
     */
    function onUpdate(callback) {
        onLocationUpdate = callback;
    }

    /**
     * Request location permissions (for modern browsers)
     */
    async function requestPermissions() {
        try {
            const permission = await navigator.permissions.query({ name: 'geolocation' });
            return permission.state;
        } catch (error) {
            console.error('Error checking permissions:', error);
            return 'unknown';
        }
    }

    /**
     * Public API
     */
    return {
        init,
        startTracking,
        stopTracking,
        setStatus,
        getStatus,
        getCurrentPosition,
        requestPermissions,
        onUpdate,
        isTracking: () => isTracking,
        getBusId: () => busId
    };
})();

/**
 * Usage Example in HTML:
 * 
 * <script src="/static/js/bus-tracker.js"></script>
 * <script>
 *     // Initialize tracker with bus ID
 *     BusTracker.init(123, { updateInterval: 10000 });
 * 
 *     // Set callback for location updates
 *     BusTracker.onUpdate((data) => {
 *         console.log('New location:', data);
 *         // Update UI here
 *     });
 * 
 *     // Start tracking
 *     BusTracker.startTracking();
 * 
 *     // Get status
 *     console.log(BusTracker.getStatus());
 * 
 *     // Stop tracking when needed
 *     // BusTracker.stopTracking();
 * </script>
 */
