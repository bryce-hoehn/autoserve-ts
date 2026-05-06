#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod

import requests
from dotenv import load_dotenv

load_dotenv()

REQUIRED_ENV = ["TAILNET_ID", "TS_KEY"]
missing = [e for e in REQUIRED_ENV if not os.getenv(e)]
if missing:
    print(f"ERROR: Missing environment variables: {missing}")
    sys.exit(1)

tailnet = os.getenv("TAILNET_ID")
key = os.getenv("TS_KEY")


def detect_device_id():
    r = subprocess.run(
        ["tailscale", "ip", "-4"], capture_output=True, text=True
    )
    if r.returncode != 0:
        print(f"ERROR: Failed to get tailscale IP: {r.stderr.strip()}")
        sys.exit(1)
    ts_ip = r.stdout.strip()
    if not ts_ip:
        print("ERROR: No tailscale IPv4 address found")
        sys.exit(1)

    r = subprocess.run(
        ["tailscale", "whois", "--json", ts_ip], capture_output=True, text=True
    )
    if r.returncode != 0:
        print(f"ERROR: Failed to resolve device ID: {r.stderr.strip()}")
        sys.exit(1)
    data = json.loads(r.stdout)
    stable_id = data.get("Node", {}).get("StableID")
    if not stable_id:
        print("ERROR: Could not find StableID in tailscale whois output")
        sys.exit(1)
    print(f"Auto-detected device ID: {stable_id}")
    return stable_id


device_id = os.getenv("DEVICE_ID") or detect_device_id()


class ContainerBackend(ABC):
    @abstractmethod
    def list_running(self, label):
        ...

    @abstractmethod
    def inspect(self, cid):
        ...

    @abstractmethod
    def events(self, filters):
        ...


class DockerBackend(ContainerBackend):
    def __init__(self):
        import docker as _docker

        self.client = _docker.from_env()
        self.errors = _docker.errors

    def list_running(self, label):
        return [
            c
            for c in self.client.containers.list(filters={"label": label})
            if c.status == "running"
        ]

    def inspect(self, cid):
        return self.client.containers.get(cid)

    def events(self, filters):
        return self.client.events(decode=True, filters=filters)


class PodmanBackend(ContainerBackend):
    def _run(self, cmd, **kwargs):
        return subprocess.run(cmd, capture_output=True, text=True, **kwargs)

    def list_running(self, label):
        r = self._run(
            ["podman", "ps", "--format", "json", "--filter", f"label={label}"]
        )
        if r.returncode != 0:
            return []
        containers = json.loads(r.stdout)
        return [c for c in containers if c.get("State") == "running"]

    def inspect(self, cid):
        r = self._run(["podman", "inspect", cid])
        if r.returncode != 0:
            return None
        data = json.loads(r.stdout)
        return data[0] if data else None

    def events(self, filters):
        cmd = ["podman", "events", "--format", "json"]
        for k, v in filters.items():
            values = v if isinstance(v, list) else [v]
            for item in values:
                cmd += ["--filter", f"{k}={item}"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
        for line in proc.stdout:
            if line.strip():
                yield json.loads(line)


def detect_backend():
    podman_prefixed = os.getenv("CONTAINER_BACKEND", "").lower() == "podman"
    docker_prefixed = os.getenv("CONTAINER_BACKEND", "").lower() == "docker"
    if docker_prefixed:
        return DockerBackend()
    if podman_prefixed:
        return PodmanBackend()
    if shutil.which("podman") and not shutil.which("docker"):
        return PodmanBackend()
    if shutil.which("docker"):
        return DockerBackend()
    if shutil.which("podman"):
        return PodmanBackend()
    print("ERROR: Neither docker nor podman found on PATH")
    sys.exit(1)


def get_label(container, label, backend):
    if isinstance(backend, DockerBackend):
        return container.labels.get(label)
    return container.get("Config", {}).get("Labels", {}).get(label)


def get_name(container, backend):
    if isinstance(backend, DockerBackend):
        return container.name
    return container.get("Names", ["unknown"])[0].lstrip("/")


def run_tailscale(container, backend, stop=False):
    svc = get_label(container, "tailscale.name", backend)
    if not svc:
        return
    port = get_label(container, "tailscale.port", backend) or "443"

    url = f"https://api.tailscale.com/api/v2/tailnet/{tailnet}/services/svc:{svc}"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    }
    payload = {"name": f"svc:{svc}", "ports": ["tcp:443"]}

    try:
        resp = requests.put(url, headers=headers, json=payload)
        resp.raise_for_status()
        print(f"Tailscale API: created/updated service svc:{svc}")
    except Exception as e:
        print(f"API error (PUT): {e}")
        return

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


backend = detect_backend()
kind = "podman" if isinstance(backend, PodmanBackend) else "docker"
print(f"Using container backend: {kind}")

print("Checking existing containers...", flush=True)
for container in backend.list_running("tailscale.name"):
    name = get_name(container, backend)
    print(f"Found running container: {name}", flush=True)
    run_tailscale(container, backend, stop=False)

event_filters = {
    "type": "container",
    "event": ["start", "stop"],
    "label": ["tailscale.name"],
}
print(f"Watching for {kind} events...", flush=True)

for event in backend.events(event_filters):
    action = event.get("Action")
    if isinstance(backend, DockerBackend):
        cid = event.get("Actor", {}).get("ID")
    else:
        cid = event.get("ID")
    if not cid:
        continue
    print(f"Received event: {action} for container {cid}", flush=True)
    try:
        container = backend.inspect(cid)
        if container is None:
            print(f"Container {cid} not found, skipping", flush=True)
            continue
        if action == "start":
            run_tailscale(container, backend, stop=False)
        elif action == "stop":
            run_tailscale(container, backend, stop=True)
    except Exception as e:
        if isinstance(backend, DockerBackend) and isinstance(
            e, backend.errors.NotFound
        ):
            print(f"Container {cid} not found, skipping", flush=True)
        else:
            print(f"Error handling event: {e}", flush=True)
