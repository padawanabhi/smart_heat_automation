from flask import Flask, render_template, request, redirect, url_for
import sqlite3
import json
import plotly
import plotly.graph_objs as go
import os
from dotenv import load_dotenv, set_key
import paho.mqtt.client as mqtt # For publishing commands
import logging # For app logging

load_dotenv() # Load existing .env variables

app = Flask(__name__)

# --- App Logger Setup ---
log_format = "%(asctime)s - FLASK_APP - %(levelname)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=log_format, datefmt="%Y-%m-%d %H:%M:%S")
flask_logger = logging.getLogger(__name__)

DB_NAME = "database/temperature_log.db"

# MQTT Parameters for publishing commands
BROKER_ADDRESS = "localhost"
BROKER_PORT = 1883
CONTROLLER_COMMAND_TOPIC = "smart_thermostat/controller/command"

mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="flask_dashboard_publisher")
mqtt_connected = False # Initialize as False

def setup_mqtt_client():
    global mqtt_connected
    try:
        mqtt_client.connect(BROKER_ADDRESS, BROKER_PORT, 60)
        mqtt_client.loop_start()
        flask_logger.info(f"Flask MQTT client connected to broker at {BROKER_ADDRESS}:{BROKER_PORT}")
        mqtt_connected = True
        return True
    except Exception as e:
        flask_logger.error(f"Failed to connect Flask MQTT client to broker: {e}")
        mqtt_connected = False
        return False

# Attempt to setup MQTT client when app starts
setup_mqtt_client()

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    conn = get_db_connection()
    # Fetch the most recent record that has a location set, likely the LOCATION_UPDATE or any sensor reading
    latest_reading = conn.execute("SELECT timestamp, temperature, action, setpoint, outside_temp, location FROM readings WHERE location IS NOT NULL ORDER BY timestamp DESC LIMIT 1").fetchone()
    
    # If no reading with location, get the absolute latest for other data
    if not latest_reading:
        latest_reading = conn.execute("SELECT timestamp, temperature, action, setpoint, outside_temp, location FROM readings ORDER BY timestamp DESC LIMIT 1").fetchone()

    current_location_display = "Not set"
    if latest_reading and latest_reading['location']:
        current_location_display = latest_reading['location']

    # Chart data (no change needed here as it already includes outside_temp)
    chart_readings_db = conn.execute("SELECT timestamp, temperature, setpoint, outside_temp FROM readings ORDER BY timestamp DESC LIMIT 50").fetchall()
    conn.close()

    timestamps = [r['timestamp'] for r in reversed(chart_readings_db)]
    temps = [r['temperature'] for r in reversed(chart_readings_db) if r['temperature'] is not None]
    setpoints = [r['setpoint'] for r in reversed(chart_readings_db) if r['setpoint'] is not None]
    # Align timestamps for plotting, only include if corresponding data exists
    timestamps_temps = [r['timestamp'] for r in reversed(chart_readings_db) if r['temperature'] is not None]
    timestamps_setpoints = [r['timestamp'] for r in reversed(chart_readings_db) if r['setpoint'] is not None]

    outside_temps_chart = [r['outside_temp'] for r in reversed(chart_readings_db) if r['outside_temp'] is not None]
    timestamps_outside = [r['timestamp'] for r in reversed(chart_readings_db) if r['outside_temp'] is not None]

    fig = go.Figure()
    if timestamps_temps and temps:
        fig.add_trace(go.Scatter(x=timestamps_temps, y=temps, mode='lines+markers', name='Indoor Temp (°C)'))
    if timestamps_setpoints and setpoints:
        fig.add_trace(go.Scatter(x=timestamps_setpoints, y=setpoints, mode='lines', name='Setpoint (°C)'))
    if timestamps_outside and outside_temps_chart:
        fig.add_trace(go.Scatter(x=timestamps_outside, y=outside_temps_chart, mode='lines+markers', name='Outside Temp (°C)', line=dict(dash='dot')))
    
    fig.update_layout(title='Temperature Log', xaxis_title='Time', yaxis_title='Temperature (°C)')
    chart_json = json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)

    return render_template('index.html',
                           latest_reading=latest_reading,
                           chart_json=chart_json,
                           current_weather_location=current_location_display, # Use location from DB
                           mqtt_available=mqtt_connected)

@app.route('/update_location', methods=['POST'])
def update_location():
    global mqtt_connected
    new_location = request.form.get('location')
    if new_location:
        flask_logger.info(f"Dashboard request to update controller location to: {new_location}")
        # No longer updating .env file for location here

        if not mqtt_connected or not mqtt_client.is_connected():
            flask_logger.warning("MQTT client not connected. Attempting to reconnect before sending command...")
            setup_mqtt_client()

        if mqtt_connected and mqtt_client.is_connected():
            command_payload = {"command": "UPDATE_LOCATION", "location": new_location}
            try:
                result = mqtt_client.publish(CONTROLLER_COMMAND_TOPIC, json.dumps(command_payload))
                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    flask_logger.info(f"Published UPDATE_LOCATION command: {command_payload}")
                else:
                    flask_logger.error(f"Failed to publish UPDATE_LOCATION command. MQTT Error: {result.rc}")
            except Exception as e:
                flask_logger.error(f"Error publishing MQTT command: {e}")
        else:
             flask_logger.error("MQTT client is not connected after attempting reconnect. Cannot send UPDATE_LOCATION command.")

    return redirect(url_for('index'))

if __name__ == '__main__':
    if not os.path.exists(os.path.dirname(DB_NAME)):
        os.makedirs(os.path.dirname(DB_NAME))
        flask_logger.info(f"Created directory {os.path.dirname(DB_NAME)}")
    
    app.run(debug=True, host='0.0.0.0', port=5001) 