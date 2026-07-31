# Llama Cockpit Hub

A dark modern web dashboard designed for headless servers running containerized `llama.cpp` inference engines (via Podman/Docker).

## Features
- **Preset Management:** Save, edit, and launch custom model startup commands (`podman run ...`).
- **Persistent State:** Model commands are stored on disk (`commands.json`).
- **Headless Background Execution:** Models launch in detached mode (`-d`) and remain active even if you close the web browser.
- **Live Terminal Output:** Centered, auto-refreshing log viewer displaying real-time stdout/stderr from `podman logs`.
- **Systemd Autostart:** Automatically launches on system boot.

---

## Directory Structure

```text
/opt/model-manager/
├── app.py                 # Flask backend API & container process runner
├── commands.json          # Persistent storage for model commands
├── .gitignore             # Git ignore rules
├── README.md              # Documentation
└── templates/
    └── index.html         # Dark Modern Tailwind CSS single-page app
```

## Quick Installation

### 1. Prerequisites

Ensure Python 3, Flask, and Podman are installed on your Linux server:

```bash
sudo apt update && sudo apt install -y python3-flask python3-pip podman git
```

### 2. Setup Files

Clone this repository to `/opt/model-manager`:

```bash
sudo mkdir -p /opt/model-manager
sudo chown -R $USER:$USER /opt/model-manager
git clone <YOUR-GITHUB-REPO-URL> /opt/model-manager
```

### 3. Create Systemd Boot Service

Create `/etc/systemd/system/model-manager.service`:

```ini
[Unit]
Description=Model Manager Web Dashboard
After=network.target podman.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/model-manager
ExecStart=/usr/bin/python3 /opt/model-manager/app.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

### 4. Enable and Start

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now model-manager.service
```

Access the dashboard at `http://<YOUR-SERVER-IP>:5000`
