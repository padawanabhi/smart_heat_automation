# Placeholder for controller.py 
import paho.mqtt.client as mqtt
import json
import time
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
CONTROLLER_STATUS_TOPIC = "smart_thermostat/controller/status_feed" # For publishing its state

# Controller Logic Parameters
ORIGINAL_SETPOINT_TEMP = 20.0  # Base desired temperature
global_current_setpoint_temp = ORIGINAL_SETPOINT_TEMP # Dynamically adjusted

# Weather API Parameters (WeatherAPI.com)
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY") # Load from .env
global_weather_api_location = "London" # Default location, managed in script memory
global_last_fetched_outside_temp = None # Store the last successfully fetched outside temperature
WEATHER_API_BASE_URL = "http://api.weatherapi.com/v1/current.json"
WEATHER_FETCH_INTERVAL = 60  # Seconds (1 minute) # Ensure this is used by the loop
weather_thread_stop_event = threading.Event()
weather_fetch_trigger_event = threading.Event() # For immediate fetch requests

# --- Global MQTT client for weather thread ---
mqtt_client_global = None

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
        # Also, do not modify global_current_setpoint_temp if outside_temp is None
        # It will retain its last valid value (either original or previously adjusted)
        return

    global_last_fetched_outside_temp = outside_temp_c # Store successfully fetched temp
    adjusted_setpoint = original_setpoint # Start with original_setpoint for calculation
    
    current_base_setpoint_for_adjustment = ORIGINAL_SETPOINT_TEMP # Always adjust from the base

    if outside_temp_c < 5.0:
        adjusted_setpoint = current_base_setpoint_for_adjustment - 1.0
        logger.info(f"Outside temp ({outside_temp_c}°C) is < 5°C. Adjusting setpoint from base {current_base_setpoint_for_adjustment}°C to {adjusted_setpoint}°C.")
    elif outside_temp_c > 18.0:
        adjusted_setpoint = current_base_setpoint_for_adjustment + 1.0
        logger.info(f"Outside temp ({outside_temp_c}°C) is > 18°C. Adjusting setpoint from base {current_base_setpoint_for_adjustment}°C to {adjusted_setpoint}°C.")
    else:
        # If moderate, setpoint should be the original_setpoint
        adjusted_setpoint = current_base_setpoint_for_adjustment
        logger.info(f"Outside temp ({outside_temp_c}°C) is moderate. Setting setpoint to base {adjusted_setpoint}°C.")

    new_setpoint = round(adjusted_setpoint, 1)
    if global_current_setpoint_temp != new_setpoint:
        global_current_setpoint_temp = new_setpoint
        logger.info(f"Global setpoint updated to: {global_current_setpoint_temp}°C based on outside temp {outside_temp_c}°C")
    else:
        logger.info(f"Setpoint remains: {global_current_setpoint_temp}°C (calculated new: {new_setpoint}°C) based on outside temp {outside_temp_c}°C")


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
    # This function no longer publishes; its caller (periodic loop or command handler) will.

def publish_controller_status(client):
    """Publishes the current state of the controller."""
    status_payload = {
        "location": global_weather_api_location,
        "current_setpoint": global_current_setpoint_temp,
        "last_outside_temp": global_last_fetched_outside_temp,
        "timestamp": time.time()
    }
    try:
        client.publish(CONTROLLER_STATUS_TOPIC, json.dumps(status_payload))
        logger.info(f"Published controller status to {CONTROLLER_STATUS_TOPIC}: {status_payload}")
    except Exception as e:
        logger.error(f"Failed to publish controller status: {e}")

def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        logger.info(f"Controller connected to MQTT Broker at {BROKER_ADDRESS}:{BROKER_PORT}")
        client.subscribe(CONTROLLER_COMMAND_TOPIC) # Only needs to listen to commands
        logger.info(f"Subscribed to command topic: {CONTROLLER_COMMAND_TOPIC}")
        # Perform an initial weather check and setpoint adjustment upon connection
        # This helps ensure the first published status is as up-to-date as possible
        logger.info("Performing initial weather check and setpoint adjustment on connect...")
        do_weather_update_and_setpoint_adjustment() # Update globals
        publish_controller_status(client) # Publish initial status using (potentially) updated globals
    else:
        logger.error(f"Controller failed to connect, return code {rc}")

def on_message(client, userdata, msg):
    """Callback function to handle incoming messages."""
    global global_weather_api_location, mqtt_client_global # Ensure mqtt_client_global is accessible if needed for publishing from here
    # Also ensure global_current_setpoint_temp and global_last_fetched_outside_temp are declared if modified or heavily used for logic
    # For now, only global_weather_api_location is directly modified here.

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
                        do_weather_update_and_setpoint_adjustment() # This updates globals
                        # logger.info(f"Logging location update event to database for new location: {new_location}") # Controller no longer logs to DB
                        publish_controller_status(client) # Publish status after change
                    else:
                        logger.info(f"Location already set to '{new_location}'. No update needed. Publishing current status.")
                        publish_controller_status(client) # Still publish status to ensure dashboard gets it if it missed earlier
                else:
                    logger.warning("UPDATE_LOCATION command received without location data.")
            else:
                logger.warning(f"Unknown command received: {command}")
        except json.JSONDecodeError:
            logger.error(f"Error decoding JSON command: {msg.payload.decode()}")
        except Exception as e:
            logger.error(f"Error processing command: {e}", exc_info=True)
        # return # Command processed, no further action for this message type # Commented out to match original structure

# --- Periodic Weather Update Thread ---
def periodic_weather_update_loop():
    """Periodically fetches weather, adjusts setpoint, and publishes status."""
    global mqtt_client_global # Use the global client

    if not WEATHER_API_KEY:
        logger.warning("Periodic weather updates: Weather API Key not set. Thread will not run logic.")
        return # Exit thread if no API key

    logger.info("Periodic weather update thread started.")
    while not weather_thread_stop_event.is_set():
        logger.info("Periodic weather update cycle: Performing weather check and setpoint adjustment.")
        
        do_weather_update_and_setpoint_adjustment() # This updates global variables

        if mqtt_client_global and mqtt_client_global.is_connected():
            publish_controller_status(mqtt_client_global)
        else:
            logger.warning("Periodic weather update: MQTT client not available/connected during status publish attempt.")

        # Wait for the next interval or until a trigger/stop event
        triggered = weather_fetch_trigger_event.wait(timeout=WEATHER_FETCH_INTERVAL)
        if weather_thread_stop_event.is_set(): # Check stop event immediately after wait returns
            logger.info("Periodic weather update thread: stop event detected after wait.")
            break
        if triggered:
            weather_fetch_trigger_event.clear() # Reset the trigger
            logger.info("Periodic weather update thread was triggered for an early run.")
            # The loop will continue and perform an update in the next iteration
    logger.info("Periodic weather update thread has stopped.")

# --- Main script execution ---
# Create MQTT client
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="thermostat_controller_1")
mqtt_client_global = client # Assign the main client to the global variable for the thread

client.on_connect = on_connect
client.on_message = on_message

# Start the periodic weather update thread
# This thread will now run its loop and periodically publish status.
weather_update_thread = threading.Thread(target=periodic_weather_update_loop, daemon=True)
# Removed: Pass client instance to the weather thread 
# Removed: weather_update_thread = threading.Thread(target=do_weather_update_and_setpoint_adjustment, daemon=True)

try:
    logger.info(f"Controller attempting to connect to MQTT broker at {BROKER_ADDRESS}:{BROKER_PORT}...")
    client.connect(BROKER_ADDRESS, BROKER_PORT, 60)
    weather_update_thread.start() # Start thread after connect attempt, though it checks is_connected internally
except ConnectionRefusedError:
    logger.error(f"Controller connection refused. Is the MQTT broker running at {BROKER_ADDRESS}:{BROKER_PORT}?")
    weather_thread_stop_event.set()
    if weather_update_thread.is_alive(): weather_update_thread.join()
    exit(1)
except Exception as e:
    logger.error(f"An error occurred during controller connection: {e}")
    weather_thread_stop_event.set()
    if weather_update_thread.is_alive(): weather_update_thread.join()
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
    if weather_update_thread.is_alive():
        weather_update_thread.join(timeout=5) # Wait for thread to finish
        if weather_update_thread.is_alive(): # If it's still alive after timeout
             logger.warning("Weather thread did not stop gracefully.")

    logger.info("Disconnecting controller from MQTT broker...")
    client.disconnect()
    logger.info("Controller script finished.") 