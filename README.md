# Simulated Smart Thermostat Controller

This small project simulates a single-zone smart thermostat using Python and IoT protocols. The goal is to show how software can control heating by processing sensor data and issuing commands automatically. For example, one Python script can publish simulated room-temperature readings to an MQTT broker, and another can subscribe, apply a threshold or schedule, and "turn the heater" on/off in code. MQTT is widely used for IoT messaging, making it ideal for exchanging sensor and control data in this setup. (Alternatively, you could simulate a Modbus-capable thermostat: libraries like PyModbus include a full Modbus server/simulator for testing client apps.)

## Goals
- Automate the heating decision for a single room.
- Demonstrate remote control logic.

## Architecture
- A **Sensor module** (Python) generates temperature data (e.g. random or scripted) and publishes JSON messages (e.g. `{"temp":21.5}`) to an MQTT topic.
- A **Controller module** (Python) subscribes to that topic, logs readings to an SQL database (e.g. SQLite or PostgreSQL), and applies simple logic (if temp < setpoint, "turn on heater", else "turn off"). The controller could also use an external API (like a weather API) to adjust behavior.

## Tools/Tech
- Python 3
- Paho MQTT client library
- An MQTT broker (e.g. Mosquitto locally)
- A lightweight database (SQLite for logs)
- Optionally use PyModbus to simulate a Modbus register for the heater status.
- Git for version control and host code on GitHub with clear README.

## MQTT Broker Setup (Mosquitto)

These instructions are for macOS using Homebrew. For other operating systems, please refer to the [official Mosquitto download page](https://mosquitto.org/download/).

1.  **Install Mosquitto:**
    ```bash
    brew install mosquitto
    ```

2.  **Run Mosquitto:**

    You have two main options:

    *   **Option A: Run as a background service (recommended for general use):**
        ```bash
        brew services start mosquitto
        ```
        To stop the service:
        ```bash
        brew services stop mosquitto
        ```
        To restart the service:
        ```bash
        brew services restart mosquitto
        ```

    *   **Option B: Run manually in the foreground (useful for development to see live logs):**
        Open a new terminal window and run:
        ```bash
        /opt/homebrew/opt/mosquitto/sbin/mosquitto -c /opt/homebrew/etc/mosquitto/mosquitto.conf
        ```
        You should see log output in the terminal. Press `Ctrl+C` to stop it. The default MQTT port is `1883`.

## Weather API Integration (Optional)

The controller can optionally fetch real-time weather data from [WeatherAPI.com](https://www.weatherapi.com/) to dynamically adjust the heating setpoint. This can help in making smarter heating decisions based on external conditions.

**Setup:**

1.  **Sign up for an API Key:**
    *   Go to [WeatherAPI.com](https://www.weatherapi.com/) and register for a free API key.

2.  **Create and Configure `.env` file:**
    *   In the root directory of the project, create a file named `.env` (if it doesn't already exist).
    *   Add your WeatherAPI.com API key and desired location to this file in the following format:
        ```env
        WEATHER_API_KEY="YOUR_ACTUAL_API_KEY"
        WEATHER_API_LOCATION="YourCityNameOrPostalCode" # e.g., "London" or "90210"
        ```
    *   Replace `YOUR_ACTUAL_API_KEY` with your key and `YourCityNameOrPostalCode` with your preferred location.
    *   The `.env` file is included in `.gitignore` and should **not** be committed to version control.

**How it works:**
*   The `controller.py` script uses the `python-dotenv` library to load credentials from the `.env` file.
*   It fetches the current outside temperature periodically (e.g., every 30 minutes).
*   Based on the outside temperature, it applies a simple logic to adjust the base heating setpoint. For example:
    *   If very cold outside (e.g., < 5°C), the target indoor temperature might be slightly lowered.
    *   If mild outside (e.g., > 18°C), the target indoor temperature might be slightly raised.
*   This adjusted setpoint is then used by the controller to decide whether to turn the heater on or off.
*   The adjusted setpoint and the fetched outside temperature (at the time of adjustment) are logged for context, and the setpoint used for each heating decision is logged in the database.

If you do not wish to use this feature, you can simply omit the `WEATHER_API_KEY` from your `.env` file, or leave it blank. The script will detect this and disable the weather-based adjustments, falling back to the `ORIGINAL_SETPOINT_TEMP`.

## Implementation Outline
1.  **Setup MQTT**: Install/run a broker (e.g. Mosquitto).
2.  **Simulated Sensor**: Write a Python script using `paho-mqtt` to publish a temperature (random walk or sinusoidal) every few seconds on topic like `home/1/temperature`.
3.  **Controller Logic**: Write a Python subscriber that reads the MQTT data, writes it to the database, and checks it against a target (setpoint). If the temperature is below target, the script logs a "heater on" action; if above, "heater off". It could publish back to another topic or simply print actions.
4.  **Optional API Integration**: Fetch real weather data via a REST API to influence the target temperature or simulate outside conditions.
5.  **Logging & Version Control**: Store all readings/actions in SQL tables. Use Git with branches to track development and host the project on GitHub.

## Potential Extensions
- Build a simple web or mobile dashboard (e.g. Flask + JavaScript charts) to display live sensor values and heating status.
- Add more zones or rooms by running multiple sensor threads with different MQTT topics.
- Experiment with a PID controller or schedule for smarter control.
- In future, the simulated MQTT setup could be replaced with real hardware (Raspberry Pi sensor) to show end-to-end readiness.


