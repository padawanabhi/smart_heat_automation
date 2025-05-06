# Placeholder for sensor.py 
import paho.mqtt.client as mqtt
import time
import json
import random
import logging

# --- Logger Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - SENSOR - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# MQTT Broker Parameters
BROKER_ADDRESS = "localhost"  # Or "test.mosquitto.org" for a public broker
BROKER_PORT = 1883
MQTT_TOPIC = "home/1/temperature"

# Simulation Parameters
INITIAL_TEMP = 20.0
TEMP_VARIATION = 0.5  # How much the temperature can change each step
PUBLISH_INTERVAL = 5  # Seconds

current_temperature = INITIAL_TEMP

def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        logger.info(f"Connected to MQTT Broker at {BROKER_ADDRESS}:{BROKER_PORT}")
    else:
        logger.error(f"Failed to connect, return code {rc}")

def on_publish(client, userdata, mid, properties=None, reason_code=None):
    # logger.debug(f"Message {mid} published.")
    pass

def simulate_temperature(current_temp):
    """Simulates a slightly varying temperature around the current temperature."""
    change = random.uniform(-TEMP_VARIATION, TEMP_VARIATION)
    new_temp = current_temp + change
    # Basic bounds to keep temperature somewhat realistic
    if new_temp < 10:
        new_temp = 10.0
    elif new_temp > 30:
        new_temp = 30.0
    return round(new_temp, 2)

# Create MQTT client
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="temperature_sensor_1")
client.on_connect = on_connect
client.on_publish = on_publish

try:
    logger.info(f"Attempting to connect to MQTT broker at {BROKER_ADDRESS}:{BROKER_PORT}...")
    client.connect(BROKER_ADDRESS, BROKER_PORT, 60)
except ConnectionRefusedError:
    logger.error(f"Connection refused. Is the MQTT broker running at {BROKER_ADDRESS}:{BROKER_PORT}?")
    exit(1)
except Exception as e:
    logger.error(f"An error occurred during connection: {e}")
    exit(1)

client.loop_start() # Start a background thread to handle network traffic

try:
    while True:
        current_temperature = simulate_temperature(current_temperature)
        payload = json.dumps({"temperature": current_temperature})
        
        result = client.publish(MQTT_TOPIC, payload)
        # result: [0, 1] where 0 is success, 1 is failure.
        # result.rc should be MQTT_ERR_SUCCESS on success.
        
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            logger.info(f"Published to {MQTT_TOPIC}: {payload}")
        else:
            logger.error(f"Failed to publish message to {MQTT_TOPIC}. Error code: {result.rc}")
            # Attempt to reconnect or handle error
            if not client.is_connected():
                logger.warning("Client disconnected. Attempting to reconnect...")
                try:
                    client.reconnect()
                    logger.info("Reconnected successfully.")
                except Exception as e:
                    logger.error(f"Reconnect failed: {e}")
                    time.sleep(5) # Wait before retrying


        time.sleep(PUBLISH_INTERVAL)

except KeyboardInterrupt:
    logger.info("Sensor script terminated by user.")
except Exception as e:
    logger.error(f"An unexpected error occurred: {e}", exc_info=True)
finally:
    logger.info("Disconnecting from MQTT broker...")
    client.loop_stop()
    client.disconnect()
    logger.info("Sensor script finished.") 