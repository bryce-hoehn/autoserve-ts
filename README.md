# auto-ts

**Automatically provision HTTPS certificates for Docker containers on your Tailscale tailnet.**

When a Docker container starts with a `tailscale.name` label, `auto-ts`:
1. Creates a Tailscale service for it (via API)
2. Approves the service for your device
3. Runs `tailscale serve` to forward HTTPS to the container's port

No reverse proxy. No manual certificate management.

## Requirements

- Tailscale installed and logged in (`tailscale up`)
- Docker installed and accessible
- Python 3.8+
- Tailscale API access
- Tailscale device ID

## Setup

1. Clone the repo:
  ```bash
  git clone https://github.com/bryce-hoehn/auto-ts.git
  cd auto-ts
  ```

2. Create .venv:
  ```bash
  python -m venv .venv
  source .venv/bin/activate
  ```

3. Install dependencies:
  ```bash
  pip install -r requirements.txt
  ```

4. Create a .env file:

  ```bash
  nano .env 
  ```

  ```bash
  TAILNET_ID=your-tailnet-name
  DEVICE_ID=your-device-id
  TS_KEY=tskey-api-xxxx
  ```

5. Run the script:
  ```bash
  python auto-ts.py
  ```

  Press Ctrl+C to stop the script.

## Setting up auto-ts as a systemd service (autostart)

To run auto-ts automatically in the background and restart it if it ever fails, you can install it as a systemd service. This is the recommended way to run it on most Linux distributions.

1. Create the systemd service file

Create a new service file using your text editor of choice:

```bash
sudo nano /etc/systemd/system/auto-ts.service
```

Paste the following content. Make sure to adjust the User, WorkingDirectory, and ExecStart paths to match your setup:

```
[Unit]
Description=Automatic HTTPS for Docker containers on Tailscale
After=docker.service tailscaled.service
Requires=docker.service tailscaled.service

[Service]
Type=simple
User=my_tailscale_user              # Replace with your username
WorkingDirectory=/opt/auto-ts       # Replace with your auto-ts directory
EnvironmentFile=/opt/auto-ts/.env   # Path to your .env file
ExecStart=/opt/auto-ts/.venv/bin/python /opt/auto-ts/auto-ts.py # Path to your virtual environment
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Notes:

* `User`: the Linux user that has docker permissions and is logged into Tailscale (e.g., your own username).

* `WorkingDirectory` and `EnvironmentFile`: absolute path to where you cloned auto-ts and stored the .env file. Can be moved to any folder you have permissions for.

2. Secure your credentials

```bash
chmod 600 /opt/auto-ts/.env
```

3. Reload systemd and enable the service

```bash
sudo systemctl daemon-reload
sudo systemctl enable auto-ts.service
sudo systemctl start auto-ts.service
```

4. Check that it’s running

```bash
sudo systemctl status auto-ts.service
```

You should see output similar to:

```
  auto-ts.service - Automatic HTTPS for Docker containers on Tailscale
  Loaded: loaded (/etc/systemd/system/auto-ts.service; enabled; vendor preset: enabled)
  Active: active (running) since ...
```

5. View the logs

To see real-time output (including when containers start and stop):

```bash
sudo journalctl -u auto-ts.service -f -e
```

Press Ctrl+C to exit the log view.

6. Test the automation

Run a test Docker container with the required label:

```bash
docker run -d --label "tailscale.name=testapp" --label "tailscale.port=8080" -p 8080:80 nginx
```

Then check the logs again. You should see messages indicating that the service was created and tailscale serve was started. After a few seconds, visit https://testapp.your-tailnet.ts.net – you should see the nginx welcome page.

When you stop the container:

```bash
docker stop <container-id>
```

The logs will show that tailscale serve ... off was executed, removing the HTTPS route.

Once the service is running, auto-ts will watch for Docker containers with the tailscale.name label and automatically proxy HTTPS through Tailscale – no manual intervention required.

7. Managing the service

| Action | Command |
|--------|---------|
| Stop the service | `sudo systemctl stop auto-ts.service` |
| Start it again | `sudo systemctl start auto-ts.service` |
| Restart it | `sudo systemctl restart auto-ts.service` |
| Enable autostart | `sudo systemctl enable auto-ts.service` |
| Disable autostart | `sudo systemctl disable auto-ts.service` |
| Remove the service | `sudo systemctl stop auto-ts.service && sudo systemctl disable auto-ts.service && sudo rm /etc/systemd/system/auto-ts.service && sudo systemctl daemon-reload` |

8. Troubleshooting

| Problem | Likely cause | Solution |
|---------|--------------|----------|
| Service fails to start with “permission denied” | Wrong user or file permissions | Ensure `User` has read+execute on the script and read on `.env`. Check that the user is in the `docker` group (`groups myuser`). |
| Service starts but `tailscale serve` commands fail | Tailscale not running or not authenticated | Run `tailscale status` as the service user. If not logged in, run `sudo -u myuser tailscale up`. |
| Docker events not detected | The user cannot access the Docker socket | Add the service user to the `docker` group: `sudo usermod -aG docker myuser`. Then restart the service. |
| Environment variables not found | `.env` file path is wrong or not readable | Verify the path in `EnvironmentFile=` and run `sudo -u myuser cat /path/to/.env` to test readability. |

## Usage

Label any Docker container you want to expose. Example using `docker-compose.yml`:

```
services:
  my-app:
   image: nginx
   labels:
    - "tailscale.name=myapp"
    - "tailscale.port=8080"
   ports:
    - "8080:80"
```

Start the container. auto-ts will automatically:
- Create https://myapp.tailnet-name.ts.net
- Forward HTTPS to localhost:8080
Stop the container → service turns off automatically.