"""
Setup Helper for nRF Cloud Device Simulator
=============================================

This script helps you set up the device simulator by:
  1. Creating config.json from the template
  2. Verifying your API key works
  3. Checking if OpenSSL is available (needed for certificate generation)

The simulator creates a virtual "soft device" on nRF Cloud.
No physical hardware needed -- only an nRF Cloud API key.

Usage:
    python setup_credentials.py
"""

import os
import sys
import json
import shutil


def check_openssl():
    """Check if OpenSSL is available."""
    ret = os.system("openssl version >nul 2>nul" if sys.platform == "win32"
                    else "openssl version >/dev/null 2>&1")
    return ret == 0


def verify_api_key(api_key, api_host):
    """Verify the API key by calling the account endpoint."""
    from urllib import request as urllib_request
    from urllib.error import HTTPError

    url = f"{api_host}/v1/account"
    req = urllib_request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib_request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return True, data
    except HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_template = os.path.join(script_dir, "config_template.json")
    config_file = os.path.join(script_dir, "config.json")
    certs_dir = os.path.join(script_dir, "certs")

    print("=" * 60)
    print("  nRF Cloud Device Simulator - Setup")
    print("=" * 60)
    print()

    # Step 1: Check OpenSSL
    print("[1/3] Checking OpenSSL...")
    if check_openssl():
        print("  [OK] OpenSSL is available")
    else:
        print("  [!] OpenSSL not found!")
        print("      Install via: winget install ShiningLight.OpenSSL")
        print("      Or download: https://slproweb.com/products/Win32OpenSSL.html")
        print("      After installing, add to PATH and restart this script.")
        print()
        resp = input("  Continue anyway? (y/N): ").strip().lower()
        if resp != "y":
            return

    print()

    # Step 2: Create config.json
    print("[2/3] Setting up config.json...")

    if os.path.exists(config_file):
        print(f"  config.json already exists.")
        resp = input("  Overwrite? (y/N): ").strip().lower()
        if resp != "y":
            print("  Keeping existing config.json")
            with open(config_file, "r") as f:
                config = json.load(f)
        else:
            config = _create_config(config_template, config_file)
    else:
        config = _create_config(config_template, config_file)

    print()

    # Step 3: Verify API key
    print("[3/3] Verifying API key...")
    api_key = config.get("nrf_cloud", {}).get("api_key", "")
    api_host = config.get("nrf_cloud", {}).get("api_host", "https://api.nrfcloud.com")

    if not api_key or "<YOUR_" in api_key:
        print("  [!] API key not set. Please edit config.json.")
    else:
        ok, result = verify_api_key(api_key, api_host)
        if ok:
            team_id = result.get("teamId", "unknown")
            plan = result.get("plan", {}).get("type", "unknown")
            mqtt_endpoint = result.get("mqttEndpoint", "unknown")
            print(f"  [OK] API key is valid!")
            print(f"       Team ID: {team_id}")
            print(f"       Plan: {plan}")
            print(f"       MQTT endpoint: {mqtt_endpoint}")
        else:
            print(f"  [!] API key verification failed: {result}")
            print("      Check your key at: nrfcloud.com -> Account -> API Key")

    # Summary
    print()
    print("=" * 60)
    print("  Setup Summary")
    print("=" * 60)

    device_id = config.get("nrf_cloud", {}).get("device_id", "not set")
    print(f"  Device ID: {device_id}")
    print(f"  Config: {config_file}")
    print()

    os.makedirs(certs_dir, exist_ok=True)

    print("  On first run, the simulator will:")
    print("    1. Download Amazon Root CA certificate")
    print("    2. Generate a device key pair (via OpenSSL)")
    print("    3. Register the virtual device on nRF Cloud")
    print("    4. Connect via MQTT and start sending data")
    print()
    print("  Ready! Run: python device_simulator.py")
    print()


def _create_config(template_path, config_path):
    """Create config.json from template with user input."""
    with open(template_path, "r") as f:
        config = json.load(f)

    print()
    print("  Configure your simulator:")
    print()

    api_key = input("  nRF Cloud API key: ").strip()
    if api_key:
        config["nrf_cloud"]["api_key"] = api_key

    device_id = input("  Device ID [kid-gps-sim-001]: ").strip()
    if device_id:
        config["nrf_cloud"]["device_id"] = device_id

    with open(config_path, "w") as f:
        json.dump(config, f, indent=4)

    print(f"\n  Saved: {config_path}")
    return config


if __name__ == "__main__":
    main()
