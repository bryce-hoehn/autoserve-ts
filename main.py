#!/usr/bin/env python3
import docker
import requests
import subprocess
from dotenv import load_dotenv

load_dotenv()

client = docker.from_env()
label_filter = {"label": ["tailscale.name"]}

tailnet = os.getenv("TAILNET_ID")
device_id = os.getenv("DEVICE_ID")
key = os.getenv("TS_KEY")

def run_tailscale(container, stop=False):
    svc = container.labels.get("tailscale.name")
    if not svc:
        return
    port = container.labels.get("tailscale.port", "443")

    payload = {
        "name": f"svc:{svc}",
        "ports": ["tcp:443"],
    }

    response = requests.put(
        f"https://api.tailscale.com/api/v2/tailnet/{tailnet}/services/svc:{svc}",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        json=payload,
    )

    response2 = requests.post(
        f"https://api.tailscale.com/api/v2/tailnet/{tailnet}/services/svc:{svc}/device/{device_id}/approved",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        json={"approved": True},
    )

    print(response.status_code)
    print(response2.status_code)

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
    subprocess.run(cmd, check=False)


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
    "label": ["tailscale.name"],
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
