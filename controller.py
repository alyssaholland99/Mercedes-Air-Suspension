import os
import time
from collections import deque
from flask import Flask, render_template_string, jsonify, request

# Import GPIO library
try:
    from gpiozero import OutputDevice
    ON_PI = True
except ImportError:
    ON_PI = False

# Import ADS1115 ADC library
try:
    import board
    import busio
    import adafruit_ads1x15.ads1115 as ADS
    from adafruit_ads1x15.analog_in import AnalogIn
    HAS_ADC = True
except (ImportError, ValueError):
    HAS_ADC = False

app = Flask(__name__)

# Pin Assignments
PIN_COMPRESSOR = 22  # Compressor relay
PIN_LEFT_VALVE = 23  # Left dump valve
PIN_RIGHT_VALVE = 24 # Right dump valve
PIN_MAIN_VALVE = 25  # Main exhaust/dump valve

# Initialize Relays (Active LOW, default HIGH/OFF)
if ON_PI:
    compressor = OutputDevice(PIN_COMPRESSOR, active_high=False, initial_value=False)
    left_valve = OutputDevice(PIN_LEFT_VALVE, active_high=False, initial_value=False)
    right_valve = OutputDevice(PIN_RIGHT_VALVE, active_high=False, initial_value=False)
    main_valve = OutputDevice(PIN_MAIN_VALVE, active_high=False, initial_value=False)
else:
    print("[WARNING] Running outside Raspberry Pi. GPIO commands simulated.")

# Initialize ADS1115 ADC
if HAS_ADC:
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        ads = ADS.ADS1115(i2c)
        sensor_left = AnalogIn(ads, ADS.P0)   # Left Viano sensor on A0
        sensor_right = AnalogIn(ads, ADS.P1)  # Right Viano sensor on A1
        print("[ADC] ADS1115 initialized successfully.")
    except Exception as e:
        print(f"[WARNING] ADC Initialization failed: {e}")
        HAS_ADC = False
else:
    print("[WARNING] CircuitPython ADS1115 library not installed. Readings simulated.")

# Sensor & Height Settings
# Adjust MAX_HEIGHT_VOLTAGE based on physical sensor readings at full extension
MAX_HEIGHT_VOLTAGE = 4.20  # Max height limit threshold in Volts
MIN_HEIGHT_VOLTAGE = 0.50  # Min height limit threshold in Volts
TARGET_MID_VOLTAGE = 2.50  # Mid-range target height in Volts
TOLERANCE_VOLTS = 0.08     # Allowed variance (+/- 0.08V)

# --- DUTY CYCLE SAFETY TRACKER ---
MAX_RUN_TIME = 120.0   # Max allowed seconds active
WINDOW_SIZE = 300.0    # Rolling window of 300s (5 minutes)

usage_history = deque()
active_start_time = None

def get_recent_run_time():
    """Calculates total active relay seconds within the last 300 seconds."""
    now = time.time()
    cutoff = now - WINDOW_SIZE
    total_seconds = 0.0

    while usage_history and usage_history[0][1] < cutoff:
        usage_history.popleft()

    for start, end in usage_history:
        effective_start = max(start, cutoff)
        total_seconds += (end - effective_start)

    if active_start_time is not None:
        effective_start = max(active_start_time, cutoff)
        total_seconds += (now - effective_start)

    return total_seconds

def can_activate():
    """Returns True if unused runtime remains within the 5-minute window."""
    return get_recent_run_time() < MAX_RUN_TIME

def record_stop():
    """Logs active duration upon relay de-activation."""
    global active_start_time
    if active_start_time is not None:
        now = time.time()
        usage_history.append((active_start_time, now))
        active_start_time = None

def read_height_sensors():
    """Returns (left_volts, right_volts)."""
    if HAS_ADC:
        try:
            return round(sensor_left.voltage, 2), round(sensor_right.voltage, 2)
        except Exception:
            return 0.0, 0.0
    return 5, 2.60  # Mock values for offline testing

def is_at_max_height():
    """Checks if either height sensor has reached or exceeded the MAX_HEIGHT_VOLTAGE limit."""
    v_left, v_right = read_height_sensors()
    return (v_left >= MAX_HEIGHT_VOLTAGE) or (v_right >= MAX_HEIGHT_VOLTAGE)

def all_off():
    """Turn off all relays immediately and log safety metrics."""
    record_stop()
    if ON_PI:
        compressor.off()
        left_valve.off()
        right_valve.off()
        main_valve.off()
    else:
        print("[MOCK] All Relays OFF")

# Web UI Template
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Air Suspension Control</title>
    <style>
        * { box-sizing: border-box; touch-action: manipulation; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background-color: #121212;
            color: #ffffff;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            margin: 0;
            padding: 15px;
        }
        h1 { margin-bottom: 15px; font-size: 1.4rem; text-align: center; text-transform: uppercase; color: #888; }
        
        .sensor-panel {
            display: flex;
            justify-content: space-between;
            width: 100%;
            max-width: 400px;
            background: #1e1e1e;
            padding: 15px;
            border-radius: 12px;
            margin-bottom: 15px;
            border: 1px solid #333;
        }
        .sensor-card {
            text-align: center;
            flex: 1;
        }
        .sensor-card:first-child { border-right: 1px solid #333; }
        .sensor-label { font-size: 0.8rem; color: #aaa; text-transform: uppercase; }
        .sensor-val { font-size: 1.6rem; font-weight: bold; color: #4caf50; margin-top: 5px; }

        .duty-panel {
            width: 100%;
            max-width: 400px;
            background: #1e1e1e;
            padding: 10px 15px;
            border-radius: 12px;
            margin-bottom: 15px;
            border: 1px solid #333;
            text-align: center;
        }
        .duty-label { font-size: 0.75rem; color: #aaa; text-transform: uppercase; margin-bottom: 5px; }
        .duty-bar-bg { background: #333; height: 8px; border-radius: 4px; overflow: hidden; }
        .duty-bar-fill { background: #4caf50; height: 100%; width: 100%; transition: width 0.3s ease, background 0.3s ease; }

        .grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
            width: 100%;
            max-width: 400px;
        }
        .full-width { grid-column: span 2; }

        button {
            background: #2a2a2a;
            border: 2px solid #333;
            color: #fff;
            padding: 20px 10px;
            font-size: 1rem;
            font-weight: bold;
            border-radius: 12px;
            cursor: pointer;
            transition: background 0.05s ease, transform 0.05s ease, border-color 0.05s ease;
            user-select: none;
            -webkit-user-select: none;
            -webkit-tap-highlight-color: transparent;
            -webkit-touch-callout: none;
            touch-action: none;
        }

        button:active, button.active {
            transform: scale(0.95);
            filter: brightness(1.3);
        }

        .btn-up { border-color: #2e7d32; }
        .btn-up:active, .btn-up.active { background-color: #4caf50; border-color: #81c784; color: #000; }

        .btn-down { border-color: #c62828; }
        .btn-down:active, .btn-down.active { background-color: #f44336; border-color: #e57373; color: #fff; }

        .btn-auto-side { border-color: #0288d1; background: #01579b; }
        .btn-auto-side:active, .btn-auto-side.active { background-color: #0288d1; border-color: #4fc3f7; }

        .btn-balance-all { border-color: #ff9800; background: #e65100; color: #fff; }
        .btn-balance-all:active, .btn-balance-all.active { background-color: #ff9800; border-color: #ffb74d; color: #000; }

        .status-box {
            margin-top: 20px;
            padding: 10px;
            background: #1e1e1e;
            border-radius: 8px;
            width: 100%;
            max-width: 400px;
            text-align: center;
            font-size: 0.85rem;
            color: #aaa;
        }
    </style>
</head>
<body>

    <h1>Air Suspension Control</h1>

    <!-- LIVE HEIGHT SENSOR READINGS -->
    <div class="sensor-panel">
        <div class="sensor-card">
            <div class="sensor-label">Left Height</div>
            <div class="sensor-val" id="val-left">-- V</div>
        </div>
        <div class="sensor-card">
            <div class="sensor-label">Right Height</div>
            <div class="sensor-val" id="val-right">-- V</div>
        </div>
    </div>

    <!-- THERMAL DUTY CYCLE MONITOR -->
    <div class="duty-panel">
        <div class="duty-label" id="duty-text">Safety Runtime Available: 120.0s / 120s</div>
        <div class="duty-bar-bg">
            <div class="duty-bar-fill" id="duty-bar"></div>
        </div>
    </div>

    <!-- CONTROLS -->
    <div class="grid">
        <button class="full-width btn-up" 
                ontouchstart="handleTouchStart(event, 'all_up')" 
                ontouchend="handleTouchEnd(event)" 
                onmousedown="start('all_up')" 
                onmouseup="stop()">Up</button>

        <button class="full-width btn-down" 
                ontouchstart="handleTouchStart(event, 'all_down')" 
                ontouchend="handleTouchEnd(event)" 
                onmousedown="start('all_down')" 
                onmouseup="stop()">Down</button>

        <button class="btn-down" 
                ontouchstart="handleTouchStart(event, 'left_down')" 
                ontouchend="handleTouchEnd(event)" 
                onmousedown="start('left_down')" 
                onmouseup="stop()">Left Down</button>

        <button class="btn-down" 
                ontouchstart="handleTouchStart(event, 'right_down')" 
                ontouchend="handleTouchEnd(event)" 
                onmousedown="start('right_down')" 
                onmouseup="stop()">Right Down</button>

        <button class="full-width btn-auto-side" onclick="triggerAction('/api/auto-balance')">L + R Balance (Disabled)</button>
        <button class="full-width btn-balance-all" onclick="triggerAction('/api/preset-middle')">Full Balance (Disabled)</button>
    </div>

    <div class="status-box" id="status">Status: Idle</div>

    <script>
        let activeAction = null;

        document.querySelectorAll('button').forEach(btn => {
            btn.addEventListener('contextmenu', e => e.preventDefault());
            btn.addEventListener('selectstart', e => e.preventDefault());
        });

        function handleTouchStart(e, action) {
            if (e.cancelable) e.preventDefault();
            e.currentTarget.classList.add('active');
            start(action);
        }

        function handleTouchEnd(e) {
            if (e.cancelable) e.preventDefault();
            document.querySelectorAll('button').forEach(btn => btn.classList.remove('active'));
            stop();
        }

        window.addEventListener('touchcancel', handleTouchEnd);

        function updateSensors() {
            fetch('/api/sensors')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('val-left').innerText = data.left.toFixed(2) + ' V';
                    document.getElementById('val-right').innerText = data.right.toFixed(2) + ' V';
                    
                    let remaining = data.remaining_time;
                    let pct = (remaining / 120.0) * 100;
                    let bar = document.getElementById('duty-bar');
                    let text = document.getElementById('duty-text');
                    
                    bar.style.width = pct + '%';
                    text.innerText = `Safety Runtime Available: ${remaining.toFixed(1)}s / 120s`;
                    
                    if (pct < 20) {
                        bar.style.backgroundColor = '#f44336';
                    } else if (pct < 50) {
                        bar.style.backgroundColor = '#ff9800';
                    } else {
                        bar.style.backgroundColor = '#4caf50';
                    }
                }).catch(() => {});
        }

        setInterval(updateSensors, 500);
        updateSensors();

        function start(action) {
            if (activeAction === action) return;
            activeAction = action;
            document.getElementById('status').innerText = "Active: " + action.replace('_', ' ').toUpperCase();
            fetch('/api/control', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: action })
            }).then(r => {
                if (!r.ok) {
                    r.json().then(err => {
                        document.getElementById('status').innerText = "LIMIT REACHED: " + err.message;
                    });
                }
            });
        }

        function stop() {
            if (!activeAction) return;
            activeAction = null;
            document.getElementById('status').innerText = "Status: Idle";
            fetch('/api/control', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: 'stop' })
            });
        }

        function triggerAction(endpoint) {
            document.getElementById('status').innerText = "Status: Processing Preset...";
            fetch(endpoint, { method: 'POST' })
                .then(r => r.json())
                .then(res => {
                    document.getElementById('status').innerText = "Status: " + res.message;
                });
        }

        window.oncontextmenu = function(e) { e.preventDefault(); e.stopPropagation(); return false; };
        window.addEventListener('touchcancel', stop);
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/sensors', methods=['GET'])
def get_sensors():
    v_left, v_right = read_height_sensors()
    remaining = max(0.0, round(MAX_RUN_TIME - get_recent_run_time(), 1))
    return jsonify({"left": v_left, "right": v_right, "remaining_time": remaining})

@app.route('/api/control', methods=['POST'])
def control():
    global active_start_time
    data = request.get_json() or {}
    action = data.get('action')

    # Release action
    if action == 'stop':
        all_off()
        return jsonify({"status": "ok", "action": "stop"})

    # Safety Check 1: Max Height Over-Inflation Protection
    if action == 'all_up' and is_at_max_height():
        all_off()
        v_left, v_right = read_height_sensors()
        print(f"[BLOCKED] Max height limit reached (L: {v_left}V, R: {v_right}V >= {MAX_HEIGHT_VOLTAGE}V)")
        return jsonify({
            "status": "error",
            "message": f"Max Height Reached ({MAX_HEIGHT_VOLTAGE}V limit)!"
        }), 400

    # Safety Check 2: Thermal Duty Cycle Limit
    if not can_activate():
        all_off()
        return jsonify({
            "status": "error",
            "message": "Thermal Limit Reached (120s/5m)! Please Wait."
        }), 429

    all_off()
    active_start_time = time.time()

    if action == 'all_up':
        if ON_PI: compressor.on()

    elif action == 'left_down':
        if ON_PI:
            left_valve.on()
            main_valve.on()

    elif action == 'right_down':
        if ON_PI:
            right_valve.on()
            main_valve.on()

    elif action == 'all_down':
        if ON_PI:
            left_valve.on()
            right_valve.on()
            main_valve.on()

    return jsonify({"status": "ok", "action": action})

@app.route('/api/auto-balance', methods=['POST'])
def auto_balance():
    return jsonify({"status": "disabled", "message": "Auto-Balance Disabled"})

@app.route('/api/preset-middle', methods=['POST'])
def preset_middle():
    return jsonify({"status": "disabled", "message": "Preset Middle Disabled"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80, debug=False)