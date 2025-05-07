# Placeholder for controller.py 
import paho.mqtt.client as mqtt
import json
import time
import sqlite3
import logging
import os
import requests # For Weather API
import threading # For periodic weather updates
from dotenv import load_dotenv # For .env file

# Load environment variables from .env file
load_dotenv()

# --- Logger Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - CONTROLLER - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# MQTT Broker Parameters
BROKER_ADDRESS = "localhost"
BROKER_PORT = 1883
MQTT_TOPIC_TEMPERATURE = "home/1/temperature"
MQTT_TOPIC_HEATER_STATUS = "home/1/heater_status" # Optional: for publishing heater status
CONTROLLER_COMMAND_TOPIC = "smart_thermostat/controller/command" # New topic for commands

# Controller Logic Parameters
ORIGINAL_SETPOINT_TEMP = 20.0  # Base desired temperature
global_current_setpoint_temp = ORIGINAL_SETPOINT_TEMP # Dynamically adjusted

# Database Parameters
DB_NAME = "database/temperature_log.db"

# Weather API Parameters (WeatherAPI.com)
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY") # Load from .env
global_weather_api_location = "London" # Default location, managed in script memory
global_last_fetched_outside_temp = None # Store the last successfully fetched outside temperature
WEATHER_API_BASE_URL = "http://api.weatherapi.com/v1/current.json"
WEATHER_FETCH_INTERVAL = 60  # Seconds (1 minute)
weather_thread_stop_event = threading.Event()
weather_fetch_trigger_event = threading.Event() # For immediate fetch requests

def fetch_weather_data(api_key, location):
    """Fetches current weather data from WeatherAPI.com."""
    if not api_key:
        logger.warning("Weather API key not found. Skipping weather data fetch.")
        return None
    if not location:
        logger.warning("Weather API location not set. Skipping weather data fetch.")
        return None
        
    params = {
        "key": api_key,
        "q": location
    }
    logger.info(f"Fetching weather for location: {location}")
    try:
        response = requests.get(WEATHER_API_BASE_URL, params=params, timeout=10)
        response.raise_for_status()  # Raise an exception for HTTP errors (4xx or 5xx)
        data = response.json()
        if "current" in data and "temp_c" in data["current"]:
            outside_temp_c = data["current"]["temp_c"]
            logger.info(f"Successfully fetched weather for {location}. Outside temp: {outside_temp_c}°C")
            return outside_temp_c
        else:
            logger.warning(f"Weather data for {location} is incomplete: {data}")
            return None
    except requests.exceptions.HTTPError as http_err:
        logger.error(f"HTTP error fetching weather for {location}: {http_err} - Response: {response.text}")
    except requests.exceptions.ConnectionError as conn_err:
        logger.error(f"Connection error fetching weather for {location}: {conn_err}")
    except requests.exceptions.Timeout as timeout_err:
        logger.error(f"Timeout fetching weather for {location}: {timeout_err}")
    except requests.exceptions.RequestException as req_err:
        logger.error(f"Error fetching weather for {location}: {req_err}")
    except json.JSONDecodeError:
        logger.error(f"Error decoding weather API JSON response for {location}.")
    return None

def adjust_setpoint(outside_temp_c, original_setpoint):
    """Adjusts the heating setpoint based on outside temperature."""
    global global_current_setpoint_temp, global_last_fetched_outside_temp
    if outside_temp_c is None:
        logger.warning("Cannot adjust setpoint, outside temperature not available.")
        # Do not clear global_last_fetched_outside_temp here, keep the last known good one
        return

    global_last_fetched_outside_temp = outside_temp_c # Store successfully fetched temp
    adjusted_setpoint = original_setpoint
    if outside_temp_c < 5.0:
        adjusted_setpoint = original_setpoint - 1.0
        logger.info(f"Outside temp ({outside_temp_c}°C) is < 5°C. Adjusting setpoint to {adjusted_setpoint}°C.")
    elif outside_temp_c > 18.0:
        adjusted_setpoint = original_setpoint + 1.0
        logger.info(f"Outside temp ({outside_temp_c}°C) is > 18°C. Adjusting setpoint to {adjusted_setpoint}°C.")
    else:
        # Keep original setpoint if within moderate range or if it's already the adjusted one
        if global_current_setpoint_temp != original_setpoint: # Only log if it's changing back
             logger.info(f"Outside temp ({outside_temp_c}°C) is moderate. Reverting/keeping setpoint at {original_setpoint}°C.")
        adjusted_setpoint = original_setpoint


    new_setpoint = round(adjusted_setpoint, 1)
    if global_current_setpoint_temp != new_setpoint:
        global_current_setpoint_temp = new_setpoint
        logger.info(f"Global setpoint updated to: {global_current_setpoint_temp}°C based on outside temp {outside_temp_c}°C")
    else:
        logger.info(f"Setpoint remains: {global_current_setpoint_temp}°C based on outside temp {outside_temp_c}°C")


def do_weather_update_and_setpoint_adjustment():
    """Performs a single weather update and setpoint adjustment."""
    global global_weather_api_location # Use the global, potentially updated location
    current_location_to_fetch = global_weather_api_location
    if not WEATHER_API_KEY or not current_location_to_fetch:
        logger.warning("Weather API Key or Location not set. Dynamic setpoint adjustment disabled for this cycle.")
        return

    outside_temp = fetch_weather_data(WEATHER_API_KEY, current_location_to_fetch)
    if outside_temp is not None:
        adjust_setpoint(outside_temp, ORIGINAL_SETPOINT_TEMP)

def periodically_fetch_weather_and_adjust_setpoint():
    """Periodically fetches weather and adjusts the setpoint, or on demand."""
    logger.info("Weather update thread started.")
    
    # Initial fetch on startup
    do_weather_update_and_setpoint_adjustment()

    while not weather_thread_stop_event.is_set():
        # Wait for the trigger event or timeout for the regular interval
        run_now = weather_fetch_trigger_event.wait(WEATHER_FETCH_INTERVAL)
        if weather_thread_stop_event.is_set(): # Check if stop was requested during wait
            break
        if run_now:
            logger.info("Immediate weather fetch triggered.")
            weather_fetch_trigger_event.clear() # Reset the trigger
        
        do_weather_update_and_setpoint_adjustment()
        
    logger.info("Weather update thread stopped.")

def setup_database():
    """Creates the database and table if they don't exist."""
    os.makedirs(os.path.dirname(DB_NAME), exist_ok=True)
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS readings (
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            temperature REAL,
            action TEXT,
            setpoint REAL,
            outside_temp REAL NULLABLE,
            location TEXT NULLABLE 
        )
    ''') # Added location to DB
    conn.commit()
    conn.close()
    logger.info(f"Database {DB_NAME} setup complete with location column.")

def log_to_database(temperature, action, current_setpoint, outside_temperature, current_location):
    """Logs the temperature reading, action, setpoint, and outside temp to the SQLite database."""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO readings (temperature, action, setpoint, outside_temp, location) VALUES (?, ?, ?, ?, ?)",
                       (temperature, action, current_setpoint, outside_temperature, current_location))
        conn.commit()
        logger.debug(f"Logged to DB: temp={temperature}, action='{action}', setpoint={current_setpoint}, outside_temp={outside_temperature}, loc={current_location}")
    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
    finally:
        if conn:
            conn.close()

def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        logger.info(f"Controller connected to MQTT Broker at {BROKER_ADDRESS}:{BROKER_PORT}")
        client.subscribe(MQTT_TOPIC_TEMPERATURE)
        logger.info(f"Subscribed to topic: {MQTT_TOPIC_TEMPERATURE}")
        client.subscribe(CONTROLLER_COMMAND_TOPIC) # Subscribe to command topic
        logger.info(f"Subscribed to command topic: {CONTROLLER_COMMAND_TOPIC}")
    else:
        logger.error(f"Controller failed to connect, return code {rc}")

def on_message(client, userdata, msg):
    """Callback function to handle incoming messages."""
    global global_current_setpoint_temp, global_weather_api_location, global_last_fetched_outside_temp
    
    if msg.topic == CONTROLLER_COMMAND_TOPIC:
        try:
            payload = json.loads(msg.payload.decode())
            logger.info(f"Received command on {msg.topic}: {payload}")
            command = payload.get("command")
            if command == "UPDATE_LOCATION":
                new_location = payload.get("location")
                if new_location:
                    if global_weather_api_location != new_location:
                        logger.info(f"Command received to update weather location from '{global_weather_api_location}' to: '{new_location}'")
                        global_weather_api_location = new_location
                        do_weather_update_and_setpoint_adjustment()
                        logger.info(f"Logging location update event to database for new location: {new_location}")
                        log_to_database(temperature=None, 
                                        action="LOCATION_UPDATE", 
                                        current_setpoint=global_current_setpoint_temp, 
                                        outside_temperature=global_last_fetched_outside_temp,
                                        current_location=global_weather_api_location) # Log new location
                    else:
                        logger.info(f"Location already set to '{new_location}'. No update needed or re-triggering fetch.")
                else:
                    logger.warning("UPDATE_LOCATION command received without location data.")
            else:
                logger.warning(f"Unknown command received: {command}")
        except json.JSONDecodeError:
            logger.error(f"Error decoding JSON command: {msg.payload.decode()}")
        except Exception as e:
            logger.error(f"Error processing command: {e}", exc_info=True)
        return # Command processed, no further action for this message type

    # Existing temperature message handling
    if msg.topic == MQTT_TOPIC_TEMPERATURE:
        try:
            logger.debug(f"Received message on {msg.topic}: {msg.payload.decode()}")
            data = json.loads(msg.payload.decode())
            
            if "temperature" in data:
                current_indoor_temp = data["temperature"]
                logger.info(f"Indoor: {current_indoor_temp}°C, Setpoint: {global_current_setpoint_temp}°C, Location: {global_weather_api_location}, Last Outside: {global_last_fetched_outside_temp}°C")
                
                action = "HEATER OFF"
                if current_indoor_temp < global_current_setpoint_temp:
                    action = "HEATER ON"
                
                logger.info(f"Action: {action}")
                log_to_database(current_indoor_temp, action, global_current_setpoint_temp, global_last_fetched_outside_temp, global_weather_api_location)
            else:
                logger.warning("Temperature message lacks 'temperature' key.")
        except json.JSONDecodeError:
            logger.error(f"Error decoding JSON temperature: {msg.payload.decode()}")
        except Exception as e:
            logger.error(f"Error in on_message (temperature): {e}", exc_info=True)

# --- Main script execution ---
setup_database()

# Ensure API key is loaded before starting weather thread or MQTT client
if not WEATHER_API_KEY:
    logger.warning("WEATHER_API_KEY not found in .env file. Weather-based setpoint adjustment will be disabled.")

# Create MQTT client
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="thermostat_controller_1")
client.on_connect = on_connect
client.on_message = on_message

# Start the weather update thread
weather_thread = threading.Thread(target=periodically_fetch_weather_and_adjust_setpoint, daemon=True)
weather_thread.start()

try:
    logger.info(f"Controller attempting to connect to MQTT broker at {BROKER_ADDRESS}:{BROKER_PORT}...")
    client.connect(BROKER_ADDRESS, BROKER_PORT, 60)
except ConnectionRefusedError:
    logger.error(f"Controller connection refused. Is the MQTT broker running at {BROKER_ADDRESS}:{BROKER_PORT}?")
    weather_thread_stop_event.set()
    if weather_thread.is_alive(): weather_thread.join()
    exit(1)
except Exception as e:
    logger.error(f"An error occurred during controller connection: {e}")
    weather_thread_stop_event.set()
    if weather_thread.is_alive(): weather_thread.join()
    exit(1)

try:
    client.loop_forever()
except KeyboardInterrupt:
    logger.info("Controller script terminated by user.")
except Exception as e:
    logger.error(f"An unexpected error occurred in controller: {e}", exc_info=True)
finally:
    logger.info("Stopping weather update thread...")
    weather_thread_stop_event.set()
    weather_fetch_trigger_event.set() # Wake up thread so it can check stop_event
    if weather_thread.is_alive():
        weather_thread.join(timeout=5) # Wait for thread to finish
        if weather_thread.is_alive(): # If it's still alive after timeout
             logger.warning("Weather thread did not stop gracefully.")

    logger.info("Disconnecting controller from MQTT broker...")
    client.disconnect()
    logger.info("Controller script finished.") 