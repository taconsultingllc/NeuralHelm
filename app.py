import json
import os
import subprocess
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
COMMANDS_FILE = "/opt/model-manager/commands.json"


def load_commands():
    if not os.path.exists(COMMANDS_FILE):
        return []
    with open(COMMANDS_FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def save_commands(commands):
    with open(COMMANDS_FILE, "w") as f:
        json.dump(commands, f, indent=2)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/commands", methods=["GET"])
def get_commands():
    return jsonify(load_commands())


@app.route("/api/commands", methods=["POST"])
def add_command():
    data = request.json
    commands = load_commands()
    # Create unique string ID if not present
    import uuid

    new_cmd = {
        "id": str(uuid.uuid4())[:8],
        "name": data.get("name"),
        "command": data.get("command"),
    }
    commands.append(new_cmd)
    save_commands(commands)
    return jsonify({"status": "success", "command": new_cmd})


# --- FIXED: Edit / Update Route ---
@app.route("/api/commands/<cmd_id>", methods=["PUT", "POST"])
def update_command(cmd_id):
    data = request.json
    commands = load_commands()
    updated = False

    for cmd in commands:
        if str(cmd.get("id")) == str(cmd_id):
            cmd["name"] = data.get("name")
            cmd["command"] = data.get("command")
            updated = True
            break

    if updated:
        save_commands(commands)
        return jsonify({"status": "success"})
    else:
        return jsonify({"status": "error", "message": "Preset not found"}), 404


@app.route("/api/commands/<cmd_id>", methods=["DELETE"])
def delete_command(cmd_id):
    commands = load_commands()
    commands = [c for c in commands if str(c.get("id")) != str(cmd_id)]
    save_commands(commands)
    return jsonify({"status": "success"})


# Container controls (start, stop, status, logs)
@app.route("/api/status", methods=["GET"])
def status():
    try:
        out = subprocess.check_output(
            ["podman", "ps", "--format", "{{.Names}}|{{.Image}}|{{.Status}}"],
            text=True,
        )
        for line in out.strip().split("\n"):
            if "llama-cockpit-server" in line:
                parts = line.split("|")
                return jsonify(
                    {"running": True, "name": parts[0], "image": parts[1], "status": parts[2]}
                )
    except Exception:
        pass
    return jsonify({"running": False})


@app.route("/api/start", methods=["POST"])
def start_container():
    cmd = request.json.get("command")
    if not cmd:
        return jsonify({"status": "error", "message": "No command provided"}), 400

    # Ensure previous container is cleaned up
    subprocess.run(["podman", "rm", "-f", "llama-cockpit-server"], stderr=subprocess.DEVNULL)
    subprocess.Popen(cmd, shell=True)
    return jsonify({"status": "started"})


@app.route("/api/stop", methods=["POST"])
def stop_container():
    subprocess.run(["podman", "stop", "-t", "2", "llama-cockpit-server"])
    subprocess.run(["podman", "rm", "-f", "llama-cockpit-server"])
    return jsonify({"status": "stopped"})


@app.route("/api/logs", methods=["GET"])
def get_logs():
    try:
        logs = subprocess.check_output(
            ["podman", "logs", "--tail", "100", "llama-cockpit-server"],
            stderr=subprocess.STDOUT,
            text=True,
        )
        return jsonify({"logs": logs})
    except Exception:
        return jsonify({"logs": "No active process logs available."})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)