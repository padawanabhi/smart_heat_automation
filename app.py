from flask import Flask, render_template, request, redirect, url_for, Response
import sqlite3
import json
import plotly
import plotly.graph_objs as go
import os
from dotenv import load_dotenv, set_key
import paho.mqtt.client as mqtt # For publishing commands
import logging # For app logging
from datetime import date, datetime # For daily DB name
import time # For SSE updates
from queue import Queue # For thread-safe SSE message passing

load_dotenv() # Load existing .env variables

app = Flask(__name__)

# --- App Logger Setup ---
log_format = "%(asctime)s - FLASK_APP - %(levelname)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=log_format, datefmt="%Y-%m-%d %H:%M:%S")
flask_logger = logging.getLogger(__name__)

# Database Parameters
DB_BASE_NAME = "temperature_log"
DB_DIRECTORY = "database"

def get_daily_db_name():
    today_str = date.today().isoformat()
    return os.path.join(DB_DIRECTORY, f"{DB_BASE_NAME}_{today_str}.db")

# Global to track the current DB name app.py is writing to
current_app_db_name = get_daily_db_name()

def setup_daily_database(db_name):
    os.makedirs(os.path.dirname(db_name), exist_ok=True)
    conn = sqlite3.connect(db_name)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS readings (
            timestamp DATETIME, 
            temperature REAL NULLABLE,
            action TEXT NULLABLE,
            setpoint REAL NULLABLE,
            outside_temp REAL NULLABLE,
            location TEXT NULLABLE,
            source TEXT 
        )
    ''')
    conn.commit()
    conn.close()
    flask_logger.info(f"App ensured database {db_name} is setup with refined schema.")

# Ensure DB for today is setup when app starts
setup_daily_database(current_app_db_name)

def log_data_to_db(source_type, data_payload):
    global current_app_db_name
    # Check for date change and switch DB if necessary
    daily_name = get_daily_db_name()
    if daily_name != current_app_db_name:
        flask_logger.info(f"App logging: Date changed. Switching DB from {current_app_db_name} to {daily_name}")
        current_app_db_name = daily_name
        setup_daily_database(current_app_db_name)

    db_timestamp = data_payload.get('timestamp_iso', datetime.now().isoformat())    
    temp = data_payload.get('temperature')
    act = data_payload.get('action') 
    sp = data_payload.get('current_setpoint')
    ot = data_payload.get('last_outside_temp')
    loc = data_payload.get('location')

    # Adjust data based on source_type for consistent DB schema
    if source_type == "sensor_update":
        # 'action' is already determined and in data_payload for sensor_update
        # 'current_setpoint', 'last_outside_temp', 'location' are also added to data_payload
        # before calling this function if they come from latest_controller_status context.
        pass # Data should be pre-structured
    elif source_type == "controller_status":
        temp = None 
        act = None  
        # sp, ot, loc are directly from controller_status payload

    try:
        conn = sqlite3.connect(current_app_db_name)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO readings (timestamp, temperature, action, setpoint, outside_temp, location, source) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (db_timestamp, temp, act, sp, ot, loc, source_type))
        conn.commit()
        flask_logger.info(f"Logged to DB {current_app_db_name} (src:{source_type}): T={temp}, A={act}, SP={sp}")
    except sqlite3.Error as e:
        flask_logger.error(f"DB error logging {source_type} to {current_app_db_name}: {e}")
    finally:
        if conn: conn.close()

# MQTT Parameters for publishing commands
BROKER_ADDRESS = "localhost"
BROKER_PORT = 1883
CONTROLLER_COMMAND_TOPIC = "smart_thermostat/controller/command"

# Define globals that will hold the client instances, initialized to None
app_mqtt_subscriber_client = None
_mqtt_subscriber_started = False

app_mqtt_publisher_client = None
# mqtt_publisher_connected is already defined
_mqtt_publisher_started = False # This flag tracks if setup has run for the publisher

# MQTT Client for Subscribing to Data Feeds
TEMP_DATA_TOPIC = "home/1/temperature"
CONTROLLER_STATUS_TOPIC = "smart_thermostat/controller/status_feed"

# In-memory store for latest data from MQTT for SSE
latest_sensor_data = {"temperature": None, "timestamp_iso": None, "action": None}
latest_controller_status = {"location": "Unknown", "current_setpoint": None, "last_outside_temp": None, "timestamp_iso": None}

# Thread-safe queue for SSE messages
sse_queue = Queue()

def on_subscriber_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        flask_logger.info(f"Flask MQTT subscriber connected from process {os.getpid()}. Subscribing to: {TEMP_DATA_TOPIC}, {CONTROLLER_STATUS_TOPIC}")
        client.subscribe([(TEMP_DATA_TOPIC, 0), (CONTROLLER_STATUS_TOPIC, 0)])
    else: flask_logger.error(f"Flask MQTT sub connect failed, code {rc}")

def on_subscriber_disconnect(client, userdata, flags, rc, properties=None):
    flask_logger.warning(f"Flask MQTT subscriber client disconnected from process {os.getpid()} with result code {rc}. Flags: {flags}")
    # global _mqtt_subscriber_started # Potentially problematic if reloader process also hits this
    # _mqtt_subscriber_started = False

def on_subscriber_message(client, userdata, msg):
    global latest_sensor_data, latest_controller_status
    flask_logger.debug(f"App MQTT Sub received on {msg.topic}")
    try:
        payload = json.loads(msg.payload.decode())
        received_time_iso = datetime.now().isoformat()

        if msg.topic == TEMP_DATA_TOPIC:
            if "temperature" in payload:
                temp_value = payload.get("temperature")
                action_for_temp = None
                setpoint_for_action = latest_controller_status.get('current_setpoint')
                
                current_sensor_event_data = {
                    "temperature": temp_value,
                    "action": None, 
                    "timestamp_iso": received_time_iso,
                    "current_setpoint": setpoint_for_action, 
                    "last_outside_temp": latest_controller_status.get('last_outside_temp'),
                    "location": latest_controller_status.get('location')
                }
                if temp_value is not None and setpoint_for_action is not None:
                    current_sensor_event_data["action"] = "HEATER ON" if temp_value < setpoint_for_action else "HEATER OFF"
                
                latest_sensor_data.update(current_sensor_event_data) # Update in-memory for SSE use
                log_data_to_db("sensor_update", current_sensor_event_data)
                sse_queue.put({"type": "sensor_update", "data": latest_sensor_data.copy()}) 
        
        elif msg.topic == CONTROLLER_STATUS_TOPIC:
            flask_logger.info(f"DEBUG: app.py received message on CONTROLLER_STATUS_TOPIC. Payload: {payload}")
            # Explicitly build the structure for latest_controller_status and SSE
            new_controller_status_data = {
                "location": payload.get("location", latest_controller_status["location"]), 
                "current_setpoint": payload.get("current_setpoint", latest_controller_status["current_setpoint"]),
                "last_outside_temp": payload.get("last_outside_temp", latest_controller_status["last_outside_temp"]),
                "timestamp_iso": received_time_iso 
            }
            latest_controller_status.update(new_controller_status_data)
            
            log_data_to_db("controller_status", latest_controller_status.copy()) 
            sse_queue.put({"type": "controller_status", "data": latest_controller_status.copy()})

            # Re-evaluate sensor action if setpoint changed
            if latest_sensor_data.get("temperature") is not None and latest_controller_status.get("current_setpoint") is not None:
                new_action = "HEATER ON" if latest_sensor_data["temperature"] < latest_controller_status['current_setpoint'] else "HEATER OFF"
                if new_action != latest_sensor_data.get("action"):
                    latest_sensor_data["action"] = new_action
                    sse_queue.put({"type": "sensor_update", "data": latest_sensor_data.copy()}) 

    except Exception as e: flask_logger.error(f"Error in app on_subscriber_message: {e}", exc_info=True)

def setup_app_mqtt_subscriber():
    global _mqtt_subscriber_started, app_mqtt_subscriber_client 
    
    # Simplified: Only proceed if not already started in this specific process.
    # The decision to call this function is now handled in if __name__ == '__main__'
    if _mqtt_subscriber_started:
        flask_logger.info(f"Flask MQTT subscriber already started in process {os.getpid()}.")
        return

    if app_mqtt_subscriber_client is None: # Instantiate client only if not already done
        app_mqtt_subscriber_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="flask_dashboard_data_subscriber")
        flask_logger.info(f"Flask MQTT subscriber client CREATED in process {os.getpid()}.")

    try:
        app_mqtt_subscriber_client.on_connect = on_subscriber_connect
        app_mqtt_subscriber_client.on_message = on_subscriber_message
        app_mqtt_subscriber_client.on_disconnect = on_subscriber_disconnect 
        app_mqtt_subscriber_client.connect(BROKER_ADDRESS, BROKER_PORT, 60)
        app_mqtt_subscriber_client.loop_start()
        _mqtt_subscriber_started = True 
        flask_logger.info(f"Flask MQTT subscriber client loop initiated from process {os.getpid()}.")
    except Exception as e:
        flask_logger.error(f"Failed to setup Flask MQTT subscriber: {e}")
        if app_mqtt_subscriber_client and app_mqtt_subscriber_client.is_connected():
            app_mqtt_subscriber_client.disconnect() # Clean up on error
        # Do not set app_mqtt_subscriber_client to None here, as it's managed globally and might be retried if setup is called again.
        _mqtt_subscriber_started = False # Reset started flag to allow potential retry if setup is called again by controlling logic.

# --- Publisher connect/disconnect handlers ---
def on_publisher_connect(client, userdata, flags, rc, properties=None):
    global mqtt_publisher_connected
    if rc == 0:
        flask_logger.info(f"Flask MQTT publisher client successfully connected to broker from process {os.getpid()}.")
        mqtt_publisher_connected = True
    else:
        flask_logger.error(f"Flask MQTT publisher client failed to connect, return code {rc}")
        mqtt_publisher_connected = False

def on_publisher_disconnect(client, userdata, flags, rc, properties=None): # Added flags, properties optional
    global mqtt_publisher_connected
    flask_logger.warning(f"Flask MQTT publisher client disconnected from process {os.getpid()} with result code {rc}. Flags: {flags}")
    mqtt_publisher_connected = False
    # global _mqtt_publisher_started 
    # _mqtt_publisher_started = False # Uncomment if disconnect should allow re-running setup

def setup_app_mqtt_publisher():
    global _mqtt_publisher_started, app_mqtt_publisher_client, mqtt_publisher_connected 
    
    # Simplified: Only proceed if not already started in this specific process.
    if _mqtt_publisher_started:
        flask_logger.info(f"Flask MQTT publisher already started in process {os.getpid()}. Current connection status: {mqtt_publisher_connected}")
        return mqtt_publisher_connected

    if app_mqtt_publisher_client is None: # Instantiate client only if not already done
        app_mqtt_publisher_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="flask_dashboard_publisher")
        flask_logger.info(f"Flask MQTT publisher client CREATED in process {os.getpid()}.")

    try:
        app_mqtt_publisher_client.on_connect = on_publisher_connect      
        app_mqtt_publisher_client.on_disconnect = on_publisher_disconnect 
        app_mqtt_publisher_client.connect(BROKER_ADDRESS, BROKER_PORT, 60)
        app_mqtt_publisher_client.loop_start()
        _mqtt_publisher_started = True 
        flask_logger.info(f"Flask MQTT publisher client loop initiated from process {os.getpid()}. Connection status via callback.")
        # Return True to indicate setup was attempted; actual status is via callback and mqtt_publisher_connected
        return True # This return is for the case where the function might be called expecting a direct success/fail of setup attempt.
    except Exception as e:
        flask_logger.error(f"Failed to connect or start loop for Flask MQTT publisher: {e}")
        if app_mqtt_publisher_client and app_mqtt_publisher_client.is_connected():
            app_mqtt_publisher_client.disconnect()
        # Do not set app_mqtt_publisher_client to None here.
        _mqtt_publisher_started = False # Reset
        mqtt_publisher_connected = False 
        return False
    # Note: The original 'else' for skipped setup is removed as this function should now only be called by the correct process.

# Remove original module-level calls to setup functions if they exist
# (They were commented out in the snippet provided before, ensuring they are not active)
# # setup_app_mqtt_publisher()
# # setup_app_mqtt_subscriber() 

def get_db_connection(db_name_to_connect):
    # Ensure directory exists before trying to connect, esp if file might not exist yet
    os.makedirs(os.path.dirname(db_name_to_connect), exist_ok=True)
    conn = sqlite3.connect(db_name_to_connect)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/dashboard_feed') # SSE endpoint
def dashboard_feed():
    def event_stream():
        while True:
            # Wait for a message from the queue
            message = sse_queue.get() # This will block until a message is available
            if message:
                yield f"data: {json.dumps(message)}\n\n"
                flask_logger.debug(f"SSE Sent: {message}")
    return Response(event_stream(), mimetype="text/event-stream")

@app.route('/')
def index():
    today_db_name = get_daily_db_name()
    latest_reading = None
    chart_readings_db = []
    current_location_display = "Not set"

    try:
        conn = get_db_connection(today_db_name)
        # Get the very latest reading from today's DB for cards
        latest_reading_row = conn.execute("SELECT timestamp, temperature, action, setpoint, outside_temp, location FROM readings ORDER BY timestamp DESC LIMIT 1").fetchone()
        if latest_reading_row:
            latest_reading = dict(latest_reading_row) # Convert row to dict for easier template access
            if latest_reading['location']:
                 current_location_display = latest_reading['location']
        
        # For the chart - get last 50 readings from today's DB
        chart_readings_db_rows = conn.execute("SELECT timestamp, temperature, setpoint, outside_temp FROM readings ORDER BY timestamp DESC LIMIT 50").fetchall()
        conn.close()
        chart_readings_db = [dict(row) for row in chart_readings_db_rows]

    except sqlite3.OperationalError as e:
        flask_logger.warning(f"Database {today_db_name} likely doesn't exist or is empty: {e}")
    except Exception as e:
        flask_logger.error(f"Error querying database {today_db_name}: {e}")

    timestamps = [r['timestamp'] for r in reversed(chart_readings_db)]
    temps = [r['temperature'] for r in reversed(chart_readings_db) if r.get('temperature') is not None]
    setpoints = [r['setpoint'] for r in reversed(chart_readings_db) if r.get('setpoint') is not None]
    timestamps_temps = [r['timestamp'] for r in reversed(chart_readings_db) if r.get('temperature') is not None]
    timestamps_setpoints = [r['timestamp'] for r in reversed(chart_readings_db) if r.get('setpoint') is not None]
    outside_temps_chart = [r['outside_temp'] for r in reversed(chart_readings_db) if r.get('outside_temp') is not None]
    timestamps_outside = [r['timestamp'] for r in reversed(chart_readings_db) if r.get('outside_temp') is not None]

    fig = go.Figure()
    if timestamps_temps and temps: fig.add_trace(go.Scatter(x=timestamps_temps, y=temps, mode='lines+markers', name='Indoor Temp (°C)'))
    if timestamps_setpoints and setpoints: fig.add_trace(go.Scatter(x=timestamps_setpoints, y=setpoints, mode='lines', name='Setpoint (°C)'))
    if timestamps_outside and outside_temps_chart: fig.add_trace(go.Scatter(x=timestamps_outside, y=outside_temps_chart, mode='lines+markers', name='Outside Temp (°C)', line=dict(dash='dot')))
    
    fig.update_layout(title=f'Temperature Log ({date.today().isoformat()})', xaxis_title='Time', yaxis_title='Temperature (°C)')
    chart_json = json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)

    return render_template('index.html',
                           latest_reading=latest_reading,
                           chart_json=chart_json,
                           current_weather_location=current_location_display,
                           initial_sensor_data=latest_sensor_data, 
                           initial_controller_status=latest_controller_status,
                           mqtt_available=mqtt_publisher_connected)

@app.route('/update_location', methods=['POST'])
def update_location():
    global mqtt_publisher_connected
    new_location = request.form.get('location')
    if new_location:
        flask_logger.info(f"Dashboard request to update controller location to: {new_location}")
        if not mqtt_publisher_connected or not app_mqtt_publisher_client.is_connected():
            flask_logger.warning("MQTT publisher not connected. Attempting reconnect...")
            setup_app_mqtt_publisher()

        if mqtt_publisher_connected and app_mqtt_publisher_client.is_connected():
            command_payload = {"command": "UPDATE_LOCATION", "location": new_location}
            try:
                result = app_mqtt_publisher_client.publish(CONTROLLER_COMMAND_TOPIC, json.dumps(command_payload))
                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    flask_logger.info(f"Published UPDATE_LOCATION command: {command_payload}")
                else:
                    flask_logger.error(f"Failed to publish UPDATE_LOCATION command. MQTT Error: {result.rc}")
            except Exception as e:
                flask_logger.error(f"Error publishing MQTT command: {e}")
        else:
             flask_logger.error("MQTT publisher client is not connected. Cannot send UPDATE_LOCATION command.")
    return redirect(url_for('index'))

if __name__ == '__main__':
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        flask_logger.info(f"MQTT Control: Werkzeug child process (PID: {os.getpid()}). Running MQTT setup.")
        setup_app_mqtt_publisher()
        setup_app_mqtt_subscriber()
    else:
        # This is the parent reloader process if app.run() below uses debug=True (default reloader=True)
        # Or it's the single process if app.run() uses debug=False/reloader=False.
        # Given app.run(debug=True, ...) is used, this 'else' is for the parent reloader.
        flask_logger.info(f"MQTT Control: Main Werkzeug process (PID: {os.getpid()}). Assuming reloader parent. Skipping MQTT setup.")
    
    app.run(debug=True, host='0.0.0.0', port=5001, threaded=True) 