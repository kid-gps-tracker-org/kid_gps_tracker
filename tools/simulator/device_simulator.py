"""
nRF Cloud Device Simulator for Kid GPS Tracker
================================================

Simulates an nRF9151 DK by creating a virtual "soft device" on nRF Cloud.
No physical hardware or device certificates needed -- only an API key.

The simulator:
  - Creates and onboards a virtual device on nRF Cloud (first run)
  - Connects via MQTT using auto-generated certificates
  - Sends GPS (Tokyo walking route), temperature, and alerts
  - Receives and responds to shadow config changes (locationInterval, counterEnable)
  - Responds to AT command requests from nRF Cloud Terminal

Usage:
    1. Get your API key from nRF Cloud portal (Account -> API Key)
    2. Copy config_template.json to config.json
    3. Set your api_key in config.json
    4. pip install -r requirements.txt
    5. python device_simulator.py

Keyboard commands during simulation:
    a - Send alert (button press)
    t - Send temperature reading now
    g - Send GPS location now
    c - Send test counter
    s - Print current shadow config
    i - Print route info
    q - Quit
"""

import json
import ssl
import time
import sys
import os
import threading
import random
import math
import tempfile
import hashlib
from datetime import datetime, timezone
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError

import paho.mqtt.client as mqtt

# ============================================================
# Tokyo walking route: circular route around central Tokyo
# ============================================================
TOKYO_ROUTE = [
    {"name": "Tokyo Station",         "lat": 35.6812, "lng": 139.7671},
    {"name": "Imperial Palace",       "lat": 35.6852, "lng": 139.7528},
    {"name": "Kudanshita",            "lat": 35.6938, "lng": 139.7510},
    {"name": "Iidabashi",             "lat": 35.7020, "lng": 139.7450},
    {"name": "Korakuen",              "lat": 35.7078, "lng": 139.7509},
    {"name": "Ochanomizu",            "lat": 35.6994, "lng": 139.7633},
    {"name": "Akihabara",             "lat": 35.6984, "lng": 139.7731},
    {"name": "Ueno Park",             "lat": 35.7146, "lng": 139.7734},
    {"name": "Asakusa",               "lat": 35.7148, "lng": 139.7967},
    {"name": "Skytree",               "lat": 35.7101, "lng": 139.8107},
    {"name": "Ryogoku",               "lat": 35.6962, "lng": 139.7939},
    {"name": "Kiyosumi Garden",       "lat": 35.6812, "lng": 139.7975},
    {"name": "Tsukiji",               "lat": 35.6654, "lng": 139.7707},
    {"name": "Ginza",                 "lat": 35.6717, "lng": 139.7645},
    {"name": "Hibiya Park",           "lat": 35.6735, "lng": 139.7568},
    {"name": "Tokyo Tower",           "lat": 35.6586, "lng": 139.7454},
    {"name": "Roppongi",              "lat": 35.6627, "lng": 139.7312},
    {"name": "Akasaka",               "lat": 35.6765, "lng": 139.7376},
    {"name": "Yotsuya",               "lat": 35.6860, "lng": 139.7301},
    {"name": "Back to Tokyo Station", "lat": 35.6812, "lng": 139.7671},
]


def interpolate_points(p1, p2, num_steps):
    """Generate intermediate points between two waypoints."""
    points = []
    for i in range(1, num_steps + 1):
        ratio = i / num_steps
        lat = p1["lat"] + (p2["lat"] - p1["lat"]) * ratio
        lng = p1["lng"] + (p2["lng"] - p1["lng"]) * ratio
        points.append({"lat": lat, "lng": lng})
    return points


def build_route(waypoints, steps_between=3):
    """Build a detailed route with interpolated points between waypoints."""
    route = []
    for i in range(len(waypoints) - 1):
        route.append(waypoints[i])
        route.extend(interpolate_points(waypoints[i], waypoints[i + 1], steps_between))
    route.append(waypoints[-1])
    return route


def now_ms():
    """Current time in milliseconds since epoch."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


# ============================================================
# nRF Cloud REST API Client
# ============================================================
class NrfCloudApi:
    """Minimal nRF Cloud REST API client using only stdlib."""

    def __init__(self, api_key, api_host="https://api.nrfcloud.com"):
        self.api_key = api_key
        self.api_host = api_host

    def _request(self, method, path, body=None):
        url = f"{self.api_host}{path}"
        data = json.dumps(body).encode("utf-8") if body else None
        req = urllib_request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib_request.urlopen(req) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            print(f"[API] {method} {path} -> HTTP {e.code}: {error_body[:300]}")
            raise

    def get_account(self):
        return self._request("GET", "/v1/account")

    def get_device(self, device_id):
        return self._request("GET", f"/v1/devices/{device_id}")

    def update_device_state(self, device_id, state):
        return self._request("PATCH", f"/v1/devices/{device_id}/state", state)

    def onboard_device(self, device_id, cert_pem):
        """Onboard a device by uploading its self-signed certificate.

        nRF Cloud expects CSV format: deviceId,"certPem"
        with Content-Type: application/octet-stream
        """
        # CSV format: deviceId,[subType],[tags],[fwTypes],"certPem"
        # subType, tags, fwTypes are optional but commas are needed
        csv_line = f'{device_id},,simulator,,"{cert_pem.strip()}\n"'
        data = csv_line.encode("utf-8")

        url = f"{self.api_host}/v1/devices"
        req = urllib_request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Content-Type", "application/octet-stream")
        try:
            with urllib_request.urlopen(req) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body.strip() else {}
        except HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            print(f"[API] POST /v1/devices -> HTTP {e.code}: {error_body[:300]}")
            raise


# ============================================================
# Device Certificate Management
# ============================================================
class DeviceCerts:
    """Generate and manage device certificates for MQTT connection."""

    def __init__(self, device_id, certs_dir):
        self.device_id = device_id
        self.certs_dir = certs_dir
        self.key_path = os.path.join(certs_dir, f"{device_id}.key.pem")
        self.cert_path = os.path.join(certs_dir, f"{device_id}.cert.pem")
        self.ca_path = os.path.join(certs_dir, "AmazonRootCA1.pem")

    @property
    def exists(self):
        return (os.path.exists(self.key_path) and
                os.path.exists(self.cert_path) and
                os.path.exists(self.ca_path))

    def download_ca(self):
        """Download Amazon Root CA 1."""
        if os.path.exists(self.ca_path):
            return
        print("[Certs] Downloading Amazon Root CA 1...")
        url = "https://www.amazontrust.com/repository/AmazonRootCA1.pem"
        req = urllib_request.Request(url)
        with urllib_request.urlopen(req) as resp:
            ca_pem = resp.read()
        with open(self.ca_path, "wb") as f:
            f.write(ca_pem)
        print(f"[Certs] Saved: {self.ca_path}")

    def generate_key_and_self_signed_cert(self):
        """Generate EC private key and self-signed certificate using openssl CLI.

        nRF Cloud accepts self-signed certificates for device onboarding.
        """
        print("[Certs] Generating device key pair and self-signed certificate...")

        # Generate EC private key
        ret = os.system(
            f'openssl ecparam -genkey -name prime256v1 -noout '
            f'-out "{self.key_path}" 2>nul'
        )
        if ret != 0:
            raise RuntimeError(
                "Failed to generate key. Ensure OpenSSL is installed and in PATH."
            )

        # Generate self-signed certificate (valid for 10 years)
        ret = os.system(
            f'openssl req -new -x509 -key "{self.key_path}" '
            f'-out "{self.cert_path}" -days 3650 '
            f'-subj "/CN={self.device_id}" 2>nul'
        )
        if ret != 0:
            raise RuntimeError("Failed to generate self-signed certificate")

        with open(self.cert_path, "r") as f:
            cert_pem = f.read()

        print(f"[Certs] Key:  {self.key_path}")
        print(f"[Certs] Cert: {self.cert_path}")
        return cert_pem


# ============================================================
# Device Simulator
# ============================================================
class DeviceSimulator:
    def __init__(self, config_path="config.json"):
        self.config = self._load_config(config_path)
        self.client = None
        self.connected = False
        self.route = build_route(TOKYO_ROUTE)
        self.route_index = 0
        self.shadow_config = {
            "counterEnable": False,
            "locationInterval": self.config["simulation"]["location_interval_seconds"],
        }
        self.test_counter = 0
        self.running = False
        self._diag_mode = False
        self.mqtt_host = None
        self.topic_prefix = None

        # API client
        self.api = NrfCloudApi(
            self.config["nrf_cloud"]["api_key"],
            self.config["nrf_cloud"].get("api_host", "https://api.nrfcloud.com"),
        )

        # Device ID
        self.device_id = self.config["nrf_cloud"]["device_id"]

        # Certs directory
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.certs_dir = os.path.join(base_dir, "certs")
        os.makedirs(self.certs_dir, exist_ok=True)

        self.certs = DeviceCerts(self.device_id, self.certs_dir)

    def _load_config(self, path):
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
        if not os.path.exists(config_path):
            print(f"ERROR: Config file not found: {config_path}")
            print("Copy config_template.json to config.json and fill in your settings.")
            sys.exit(1)

        with open(config_path, "r") as f:
            return json.load(f)

    # ---- Device Provisioning ----

    def provision_device(self):
        """Provision a new virtual device on nRF Cloud."""
        print(f"\n[Provision] Setting up device: {self.device_id}")

        # Download CA cert
        self.certs.download_ca()

        # Check if device already exists and has certs
        if self.certs.exists:
            print("[Provision] Device certificates already exist, reusing.")
            return True

        # Get account info for MQTT endpoint
        print("[Provision] Fetching account info...")
        account = self.api.get_account()
        self.mqtt_host = account.get("mqttEndpoint", "mqtt.nrfcloud.com")
        # Try multiple possible field names for team/tenant ID
        team_id = (account.get("teamId", "") or
                   account.get("tenantId", "") or
                   account.get("team", {}).get("tenantId", "") or
                   account.get("mqttTopicPrefix", "").split("/")[0] if
                   account.get("mqttTopicPrefix") else "")
        mqtt_prefix = account.get("mqttTopicPrefix", "")
        print(f"[Provision] MQTT endpoint: {self.mqtt_host}")
        print(f"[Provision] Team ID: {team_id}")
        print(f"[Provision] MQTT prefix: {mqtt_prefix}")

        # Generate key pair and self-signed certificate
        cert_pem = self.certs.generate_key_and_self_signed_cert()

        # Onboard device via API (upload certificate)
        print("[Provision] Uploading certificate to nRF Cloud...")
        try:
            self.api.onboard_device(self.device_id, cert_pem)
            print("[Provision] Device onboarded successfully!")
        except HTTPError as e:
            if e.code == 409:
                print("[Provision] Device already exists on nRF Cloud.")
                print("[Provision] If you need to re-provision, delete the device")
                print("            from the nRF Cloud portal, then run again.")
                return False
            print(f"[Provision] Onboarding failed (HTTP {e.code}).")
            return False

        # Don't cache topics here; _load_mqtt_info will fetch the
        # correct topics from the device shadow after provisioning
        return True

    def _save_mqtt_info(self, info):
        """Save MQTT connection info to a local cache file."""
        base_dir = os.path.dirname(os.path.abspath(__file__))
        info_path = os.path.join(base_dir, "certs", f"{self.device_id}.mqtt_info.json")
        with open(info_path, "w") as f:
            json.dump(info, f, indent=2)

    def _load_mqtt_info(self):
        """Load cached MQTT connection info, or fetch from nRF Cloud APIs."""
        base_dir = os.path.dirname(os.path.abspath(__file__))
        info_path = os.path.join(base_dir, "certs", f"{self.device_id}.mqtt_info.json")
        if os.path.exists(info_path):
            with open(info_path, "r") as f:
                info = json.load(f)
            # Validate that it has the required topic fields
            if info.get("topic_d2c") and info.get("topic_c2d"):
                return info

        # Fetch from nRF Cloud APIs
        print("[MQTT] Fetching connection info from nRF Cloud...")

        # Get MQTT endpoint from account API
        account = self.api.get_account()
        mqtt_host = account.get("mqttEndpoint", "mqtt.nrfcloud.com")

        # Get actual topics from device state (shadow)
        device = self.api.get_device(self.device_id)
        state = device.get("state", {})
        desired = state.get("desired", {})
        pairing = desired.get("pairing", {})
        topics = pairing.get("topics", {})

        topic_d2c = topics.get("d2c", "")
        topic_c2d = topics.get("c2d", "")

        if not topic_d2c or not topic_c2d:
            # Fallback: construct from mqttTopicPrefix with /m/d/ path
            prefix = account.get("mqttTopicPrefix", "").rstrip("/")
            topic_d2c = f"{prefix}/m/d/{self.device_id}/d2c"
            topic_c2d = f"{prefix}/m/d/{self.device_id}/+/r"
            print(f"[MQTT] Topics not in device shadow, using fallback")

        print(f"[MQTT] d2c topic: {topic_d2c}")
        print(f"[MQTT] c2d topic: {topic_c2d}")

        info = {
            "mqtt_host": mqtt_host,
            "topic_d2c": topic_d2c,
            "topic_c2d": topic_c2d,
        }
        self._save_mqtt_info(info)
        return info

    # ---- MQTT Callbacks ----

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            print(f"[MQTT] Connected to {self.mqtt_host}")

            if self._diag_mode:
                print("[DIAG] Connected OK. Holding connection (no subscribe)...")
                print("[DIAG] Waiting 10 seconds to see if connection stays...")
                return

            # Subscribe to c2d topic
            client.subscribe(self.topic_c2d)
            print(f"[MQTT] Subscribed to: {self.topic_c2d}")
        else:
            rc_messages = {
                1: "incorrect protocol version",
                2: "invalid client identifier",
                3: "server unavailable",
                4: "bad username or password",
                5: "not authorized",
            }
            reason = rc_messages.get(rc, "unknown")
            print(f"[MQTT] Connection failed: rc={rc} ({reason})")
            self.connected = False

    def _on_disconnect(self, client, userdata, rc):
        self.connected = False
        if rc != 0:
            print(f"[MQTT] Unexpected disconnect (rc={rc}), will attempt reconnect...")
        else:
            print("[MQTT] Disconnected")

    def _is_c2d_topic(self, topic):
        """Check if a topic matches the c2d subscription pattern.

        c2d pattern is like: .../m/d/{deviceId}/+/r
        Actual topics will be: .../m/d/{deviceId}/{something}/r
        """
        return topic.endswith("/r") and f"/m/d/{self.device_id}/" in topic

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            print(f"[MQTT] Received non-JSON on {topic}: {msg.payload[:100]}")
            return

        if self._is_c2d_topic(topic):
            self._handle_c2d_message(payload)
        else:
            print(f"[MQTT] Message on {topic}: {json.dumps(payload, indent=2)[:200]}")

    # ---- Cloud-to-Device Message Handling ----

    def _handle_c2d_message(self, payload):
        app_id = payload.get("appId", "")
        msg_type = payload.get("messageType", "")
        print(f"[C2D] Received: appId={app_id}, messageType={msg_type}")
        print(f"[C2D] Payload: {json.dumps(payload, indent=2)[:300]}")

        if app_id == "MODEM" and msg_type == "CMD":
            data = payload.get("data", "")
            print(f"[C2D] AT command request: {data}")
            self._send_at_response(data)
        elif app_id == "CONFIG":
            # Handle config changes via c2d
            data = payload.get("data", {})
            self._handle_config_update(data)

    def _handle_config_update(self, config):
        """Handle config update received via c2d."""
        updated = False
        if "counterEnable" in config:
            self.shadow_config["counterEnable"] = config["counterEnable"]
            print(f"[Config] counterEnable -> {config['counterEnable']}")
            updated = True
        if "locationInterval" in config:
            self.shadow_config["locationInterval"] = int(config["locationInterval"])
            print(f"[Config] locationInterval -> {config['locationInterval']}s")
            updated = True
        if updated:
            print(f"[Config] Updated: {json.dumps(self.shadow_config)}")

    def _send_at_response(self, command):
        responses = {
            "AT+CGMR": "mfw_nrf91x1_2.0.2",
            "AT+CGSN": self.device_id,
            "AT%HWVERSION": "nRF9151 LACA AAA (simulator)",
            "AT+CIMI": "440100000000000",
        }
        response = responses.get(command.strip(), "ERROR")
        msg = {
            "appId": "MODEM",
            "messageType": "DATA",
            "ts": now_ms(),
            "data": response,
        }
        self._publish_d2c(msg)
        print(f"[AT] Response to '{command}': {response}")

    # ---- Data Sending ----

    def _publish_d2c(self, message):
        if not self.connected:
            print("[MQTT] Not connected, dropping message")
            return False
        payload = json.dumps(message)
        result = self.client.publish(self.topic_d2c, payload, qos=1)
        return result.rc == mqtt.MQTT_ERR_SUCCESS

    def send_gnss_location(self):
        """Send current GPS position from the Tokyo route."""
        point = self.route[self.route_index]
        lat = point["lat"] + random.gauss(0, 0.0001)
        lng = point["lng"] + random.gauss(0, 0.0001)
        accuracy = random.uniform(3.0, 15.0)

        msg = {
            "appId": "GNSS",
            "ts": now_ms(),
            "data": {
                "lat": round(lat, 6),
                "lon": round(lng, 6),
                "acc": round(accuracy, 1),
            },
        }

        name = point.get("name", f"Point {self.route_index}")
        if self._publish_d2c(msg):
            print(
                f"[GNSS] Sent: {lat:.6f}N {lng:.6f}E "
                f"(acc:{accuracy:.1f}m) near {name}"
            )

        self.route_index = (self.route_index + 1) % len(self.route)

    def send_temperature(self):
        """Send simulated temperature reading."""
        base = self.config["simulation"]["temperature_base"]
        variation = self.config["simulation"]["temperature_variation"]
        hour = datetime.now().hour
        daily_offset = variation * math.sin((hour - 6) * math.pi / 12)
        temp = base + daily_offset + random.gauss(0, 0.5)

        msg = {
            "appId": "TEMP",
            "messageType": "DATA",
            "ts": now_ms(),
            "data": round(temp, 1),
        }

        if self._publish_d2c(msg):
            print(f"[TEMP] Sent: {temp:.1f} C")

    def send_test_counter(self):
        msg = {
            "appId": "COUNT",
            "messageType": "DATA",
            "ts": now_ms(),
            "data": self.test_counter,
        }
        if self._publish_d2c(msg):
            print(f"[COUNT] Sent: {self.test_counter}")
            self.test_counter += 1

    def send_alert(self, alert_type, value=0, description=None):
        msg = {
            "appId": "ALERT",
            "messageType": "DATA",
            "ts": now_ms(),
            "data": {"type": alert_type, "value": value},
        }
        if description:
            msg["data"]["description"] = description
        if self._publish_d2c(msg):
            print(f"[ALERT] Sent: type={alert_type}, desc={description}")

    def send_device_info(self):
        """Send device info on startup via d2c topic."""
        app_version = self.config["simulation"].get("app_version", "0.0.1")
        msg = {
            "appId": "DEVICE",
            "messageType": "DATA",
            "ts": now_ms(),
            "data": {
                "networkInfo": {
                    "networkCode": "10",
                    "areaCode": "1234",
                    "mccmnc": "44010",
                    "ipAddress": "10.0.0.1",
                    "cellID": "ABCD1234",
                    "rsrp": -85,
                },
                "simInfo": {
                    "iccid": "8981100000000000000",
                    "imsi": "440100000000000",
                },
                "appVersion": app_version,
                "config": self.shadow_config,
            },
        }
        self._publish_d2c(msg)
        print(f"[Device] Sent device info (version: {app_version})")

    # ---- Connection Setup ----

    def connect(self):
        """Establish MQTT connection to nRF Cloud."""
        if not self.certs.exists:
            print("[MQTT] ERROR: Device certificates not found.")
            print("       Run provisioning first (this happens automatically on first run).")
            return False

        mqtt_info = self._load_mqtt_info()
        self.mqtt_host = mqtt_info["mqtt_host"]

        # Use exact topics from nRF Cloud device shadow
        # d2c: prod/{tenantId}/m/d/{deviceId}/d2c
        # c2d: prod/{tenantId}/m/d/{deviceId}/+/r
        self.topic_d2c = mqtt_info["topic_d2c"]
        self.topic_c2d = mqtt_info["topic_c2d"]

        self.client = mqtt.Client(client_id=self.device_id)

        # Reconnect backoff: 1s initial, 30s max
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)

        # TLS with device certificates
        self.client.tls_set(
            ca_certs=self.certs.ca_path,
            certfile=self.certs.cert_path,
            keyfile=self.certs.key_path,
            cert_reqs=ssl.CERT_REQUIRED,
            tls_version=ssl.PROTOCOL_TLSv1_2,
        )

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

        print(f"[MQTT] Connecting to {self.mqtt_host}:8883...")
        print(f"[MQTT] Device ID: {self.device_id}")
        print(f"[MQTT] d2c: {self.topic_d2c}")
        print(f"[MQTT] c2d: {self.topic_c2d}")

        try:
            self.client.connect(self.mqtt_host, 8883, keepalive=120)
        except Exception as e:
            print(f"[MQTT] Connection error: {e}")
            return False

        self.client.loop_start()

        timeout = 30
        while not self.connected and timeout > 0:
            time.sleep(1)
            timeout -= 1

        if not self.connected:
            print("[MQTT] Connection timed out!")
            return False

        if not self._diag_mode:
            time.sleep(2)
            self.send_device_info()
        return True

    def disconnect(self):
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
            print("[MQTT] Disconnected from nRF Cloud")

    # ---- Main Simulation Loop ----

    def run(self):
        # Step 1: Provision device if needed
        if not self.certs.exists:
            if not self.provision_device():
                print("\nProvisioning failed. Please check your API key and try again.")
                return

        # Step 2: Connect via MQTT
        if not self.connect():
            print("Failed to connect. Exiting.")
            return

        self.running = True
        print("\n" + "=" * 60)
        print("  nRF Cloud Device Simulator Running")
        print(f"  Device: {self.device_id}")
        print("=" * 60)
        print("  Commands:")
        print("    a - Send alert (button press)")
        print("    t - Send temperature now")
        print("    g - Send GPS location now")
        print("    c - Send test counter")
        print("    s - Print current shadow config")
        print("    i - Print route info")
        print("    q - Quit")
        print("=" * 60 + "\n")

        # Send startup alert
        self.send_alert(1, 0, "Device simulator started")

        # Start periodic threads
        location_thread = threading.Thread(target=self._location_loop, daemon=True)
        temperature_thread = threading.Thread(target=self._temperature_loop, daemon=True)
        location_thread.start()
        temperature_thread.start()

        # Keyboard input loop
        try:
            while self.running:
                try:
                    cmd = input().strip().lower()
                    if cmd == "q":
                        print("Shutting down...")
                        self.running = False
                    elif cmd == "a":
                        self.send_alert(0, 0, "Button pressed")
                    elif cmd == "t":
                        self.send_temperature()
                    elif cmd == "g":
                        self.send_gnss_location()
                    elif cmd == "c":
                        self.send_test_counter()
                    elif cmd == "s":
                        print(f"[Config] {json.dumps(self.shadow_config, indent=2)}")
                    elif cmd == "i":
                        idx = self.route_index
                        total = len(self.route)
                        print(f"[Route] Position {idx}/{total}")
                        print(f"[Route] Interval: {self.shadow_config['locationInterval']}s")
                except EOFError:
                    break
        except KeyboardInterrupt:
            print("\nShutting down...")

        self.running = False
        self.disconnect()

    def _location_loop(self):
        time.sleep(5)
        while self.running:
            if self.connected:
                self.send_gnss_location()
                if self.shadow_config.get("counterEnable", False):
                    self.send_test_counter()

            interval = self.shadow_config.get("locationInterval", 300)
            for _ in range(interval):
                if not self.running:
                    return
                time.sleep(1)

    def _temperature_loop(self):
        time.sleep(10)
        while self.running:
            if self.connected:
                self.send_temperature()

            interval = self.config["simulation"].get("temperature_interval_seconds", 300)
            for _ in range(interval):
                if not self.running:
                    return
                time.sleep(1)


if __name__ == "__main__":
    print("=" * 60)
    print("  Kid GPS Tracker - nRF Cloud Device Simulator")
    print("  Creates a virtual device (no hardware needed)")
    print("=" * 60)
    print()

    diag_mode = "--diag" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    config_file = args[0] if args else "config.json"
    simulator = DeviceSimulator(config_file)

    if diag_mode:
        print("[DIAG] Running connection diagnostics...\n")

        # 1. Check device status via REST API
        print("[DIAG] Step 1: Checking device status on nRF Cloud...")
        try:
            device = simulator.api.get_device(simulator.device_id)
            print(f"[DIAG] Device found on nRF Cloud!")
            print(f"[DIAG]   State: {device.get('state', {})}")
            print(f"[DIAG]   Firmware: {device.get('firmware', {})}")
            print(f"[DIAG]   Tags: {device.get('tags', [])}")
            print(f"[DIAG]   Full response: {json.dumps(device, indent=2)[:500]}")
        except HTTPError as e:
            if e.code == 404:
                print(f"[DIAG] Device NOT found on nRF Cloud!")
                print(f"[DIAG] The device may need to be re-provisioned.")
            else:
                print(f"[DIAG] API error: HTTP {e.code}")
        except Exception as e:
            print(f"[DIAG] Error checking device: {e}")

        print()

        # 2. Test MQTT connection without subscribing
        print("[DIAG] Step 2: Testing MQTT connection (no subscribe)...")
        simulator._diag_mode = True
        if not simulator.certs.exists:
            print("[DIAG] No certs found. Run without --diag first to provision.")
            sys.exit(1)

        if not simulator.connect():
            print("[DIAG] Connection failed!")
            sys.exit(1)

        # Hold connection for 10 seconds
        for i in range(10):
            time.sleep(1)
            status = "OK" if simulator.connected else "DISCONNECTED"
            print(f"[DIAG] {i+1}s - Connection: {status}")
            if not simulator.connected:
                print("[DIAG] Connection dropped WITHOUT any subscribe/publish!")
                print("[DIAG] This suggests a certificate or IoT policy issue.")
                break

        if simulator.connected:
            print("[DIAG] Connection stable for 10s without subscribing!")
            print("[DIAG] The issue is likely topic permissions.")

            # 3. Try subscribing to c2d
            print()
            print("[DIAG] Step 3: Testing subscribe to c2d topic...")
            simulator._diag_mode = False
            simulator.client.subscribe(simulator.topic_c2d)
            print(f"[DIAG] Subscribed to: {simulator.topic_c2d}")
            time.sleep(3)
            status = "OK" if simulator.connected else "DISCONNECTED"
            print(f"[DIAG] After subscribe: {status}")

        simulator.disconnect()
        print("\n[DIAG] Diagnostics complete.")
    else:
        simulator.run()
