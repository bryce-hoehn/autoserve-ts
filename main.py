#!/usr/bin/env python3
import os
import sys
import docker
import requests
import subprocess
from dotenv import load_dotenv

load_dotenv()

# Validate environment
REQUIRED_ENV = ["TAILNET_ID", "DEVICE_ID", "TS_KEY"]
missing = [e for e in REQUIRED_ENV if not os.getenv(e)]
if missing:
    print(f"ERROR: Missing environment variables: {missing}")
    sys.exit(1)

tailnet = os.getenv("TAILNET_ID")
device_id = os.getenv("DEVICE_ID")
key = os.getenv("TS_KEY")

client = docker.from_env()

def run_tailscale(container, stop=False):
    svc = container.labels.get("tailscale.name")
    if not svc:
        return
    port = container.labels.get("tailscale.port", "443")

    payload = {
        "name": f"svc:{svc}",
        "ports": ["tcp:443"],
    }

    # Tailscale API: create or update service
    payload = {"name": f"svc:{svc}", "ports": ["tcp:443"]}
    url = f"https://api.tailscale.com/api/v2/tailnet/{tailnet}/services/svc:{svc}"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}

    try:
        resp = requests.put(url, headers=headers, json=payload)
        resp.raise_for_status()
        print(f"Tailscale API: created/updated service svc:{svc}")
    except Exception as e:
        print(f"API error (PUT): {e}")
        return

    # Approve service for this device
    approve_url = f"{url}/device/{device_id}/approved"
    try:
        resp2 = requests.post(approve_url, headers=headers, json={"approved": True})
        resp2.raise_for_status()
        print(f"Tailscale API: approved service for device {device_id}")
    except Exception as e:
        print(f"API error (POST approve): {e}")
        return

    cmd = [
        "tailscale",
        "serve",
        f"--service=svc:{svc}",
        "--https=443",
        f"127.0.0.1:{port}",
    ]

    if stop:
        cmd.append("off")
    print(f"Running: {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"tailscale error: {result.stderr}")
    else:
        print(f"tailscale success: {result.stdout}")


# Handle existing containers
print("Checking existing containers...", flush=True)
for container in client.containers.list(filters={"label": "tailscale.name"}):
    if container.status == "running":
        print(f"Found running container: {container.name}", flush=True)
        run_tailscale(container, stop=False)

# Watch for future events
event_filters = {
    "type": "container",
    "event": ["start", "stop"],
    "label": ["tailscale.name=.*"],
}
print(f"Watching for Docker events with filters: {event_filters}", flush=True)

for event in client.events(decode=True, filters=event_filters):
    action = event.get("Action")
    print(f"Received event: {action} for container {event.get('id')}", flush=True)
    cid = event.get("Actor", {}).get("ID")
    if not cid:
        continue
    try:
        container = client.containers.get(cid)
        if action == "start":
            run_tailscale(container, stop=False)
        elif action == "stop":
            run_tailscale(container, stop=True)
    except docker.errors.NotFound:
        print(f"Container {cid} not found, skipping", flush=True)
    except Exception as e:
        print(f"Error handling event: {e}", flush=True)
