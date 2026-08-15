import json
import os
import subprocess
import threading
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
COMMANDS_FILE = "/opt/model-manager/commands.json"
STATE_DIR = "/opt/model-manager"
MODEL_STATE_FILE = os.path.join(STATE_DIR, "active_model.json")
LOCAL_MODELS_DIR = "/root/models_local"
REMOTE_MODELS_DIR = "/root/models"

copy_operations = {}
copy_lock = threading.Lock()


def calculate_dir_size(path):
    total = 0
    try:
        if os.path.isfile(path):
            total = os.path.getsize(path)
        elif os.path.isdir(path):
            for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    try:
                        if not os.path.islink(fp):
                            total += os.path.getsize(fp)
                    except OSError:
                        pass
    except OSError:
        pass
    return total


def copy_model_local_progressive(src, dst, task_id):
    if not os.path.exists(LOCAL_MODELS_DIR):
        os.makedirs(LOCAL_MODELS_DIR, exist_ok=True)
    if not os.path.exists(src):
        return False, "Source not found"
    
    try:
        if os.path.isfile(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(src, 'rb') as fsrc, open(dst, 'wb') as fdst:
                while True:
                    with copy_lock:
                        if task_id in copy_operations and copy_operations[task_id]['status'] == 'cancelled':
                            if os.path.exists(dst):
                                os.remove(dst)
                            return False, "Cancelled"
                    chunk = fsrc.read(1024 * 1024)
                    if not chunk:
                        break
                    fdst.write(chunk)
            return True, None
        elif os.path.isdir(src):
            import shutil
            os.makedirs(dst, exist_ok=True)
            for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
                with copy_lock:
                    if task_id in copy_operations and copy_operations[task_id]['status'] == 'cancelled':
                        if os.path.exists(dst):
                            shutil.rmtree(dst, ignore_errors=True)
                        return False, "Cancelled"
                
                rel_path = os.path.relpath(dirpath, src)
                dst_dir = os.path.join(os.path.dirname(dst), rel_path) if rel_path != '.' else dst
                os.makedirs(dst_dir, exist_ok=True)
                
                for f in filenames:
                    with copy_lock:
                        if task_id in copy_operations and copy_operations[task_id]['status'] == 'cancelled':
                            if os.path.exists(dst):
                                shutil.rmtree(dst, ignore_errors=True)
                            return False, "Cancelled"
                    
                    src_file = os.path.join(dirpath, f)
                    dst_file = os.path.join(dst_dir, f)
                    
                    if os.path.islink(src_file):
                        link_target = os.readlink(src_file)
                        if os.path.exists(dst_file):
                            os.remove(dst_file)
                        os.symlink(link_target, dst_file)
                    else:
                        if os.path.exists(dst_file):
                            os.remove(dst_file)
                        with open(src_file, 'rb') as fsrc, open(dst_file, 'wb') as fdst:
                            while True:
                                with copy_lock:
                                    if task_id in copy_operations and copy_operations[task_id]['status'] == 'cancelled':
                                        if os.path.exists(dst_file):
                                            os.remove(dst_file)
                                        shutil.rmtree(dst, ignore_errors=True)
                                        return False, "Cancelled"
                                chunk = fsrc.read(1024 * 1024)
                                if not chunk:
                                    break
                                fdst.write(chunk)
            return True, None
    except Exception as e:
        return False, str(e)
    
    return False, "Unknown error"


def get_copy_progress(task_id):
    if task_id not in copy_operations:
        return None
    
    op = copy_operations[task_id]
    src = op['src']
    dst = op['dst']
    
    if not os.path.exists(dst):
        return 0
    
    src_size = calculate_dir_size(src)
    dst_size = calculate_dir_size(dst)
    
    if src_size == 0:
        return 0
    
    progress = (dst_size / src_size) * 100
    return min(progress, 99.9)


def copy_worker(task_id, src, dst, model_path):
    with copy_lock:
        if task_id in copy_operations:
            copy_operations[task_id]['status'] = 'copying'
    
    success, error = copy_model_local_progressive(src, dst, task_id)
    
    with copy_lock:
        if task_id in copy_operations:
            if success:
                copy_operations[task_id]['status'] = 'completed'
                copy_operations[task_id]['progress'] = 100
                copy_operations[task_id]['modified_command'] = build_local_command(
                    copy_operations[task_id]['original_command'], model_path
                )
                copy_operations[task_id]['auto_start'] = True
            else:
                copy_operations[task_id]['status'] = 'failed'
                copy_operations[task_id]['error'] = error


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


def save_model_state(model_name):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(MODEL_STATE_FILE, "w") as f:
        json.dump({"model_name": model_name}, f)


def load_model_state():
    if not os.path.exists(MODEL_STATE_FILE):
        return None
    try:
        with open(MODEL_STATE_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def clear_model_state():
    if os.path.exists(MODEL_STATE_FILE):
        os.remove(MODEL_STATE_FILE)


def cleanup_local_models():
    try:
        subprocess.run(["rm", "-rf", LOCAL_MODELS_DIR], check=True)
        os.makedirs(LOCAL_MODELS_DIR, exist_ok=True)
    except subprocess.CalledProcessError:
        pass


def model_exists_locally(model_path):
    model_subpath = get_model_parent_subpath(model_path)
    local_path = os.path.join(LOCAL_MODELS_DIR, model_subpath)
    return os.path.exists(local_path)


def needs_local_copy(model_path):
    if model_exists_locally(model_path):
        return False
    if os.path.exists(LOCAL_MODELS_DIR) and os.listdir(LOCAL_MODELS_DIR):
        cleanup_local_models()
        return True
    return True


def get_container_status():
    try:
        out = subprocess.check_output(
            ["podman", "ps", "--format", "{{.Names}}|{{.Image}}|{{.Status}}"],
            text=True,
        )
        for line in out.strip().split("\n"):
            if "llama-cockpit-server" in line:
                parts = line.split("|")
                model_state = load_model_state()
                model_name = model_state.get("model_name", "") if model_state else ""
                return {
                    "running": True,
                    "name": parts[0],
                    "image": parts[1],
                    "status": parts[2],
                    "model_name": model_name
                }
    except Exception:
        pass
    return {"running": False}


def extract_model_path(command):
    import re
    match = re.search(r'-m\s+(\S+)', command)
    if match:
        return match.group(1)
    return None


def get_model_subpath(model_path_in_container):
    container_models_prefix = "/models/"
    if model_path_in_container.startswith(container_models_prefix):
        return model_path_in_container[len(container_models_prefix):]
    return model_path_in_container.lstrip("/")


def get_model_parent_subpath(model_path_in_container):
    subpath = get_model_subpath(model_path_in_container)
    parent_dir = os.path.dirname(subpath)
    if parent_dir:
        return parent_dir
    return subpath


def build_local_command(original_command, model_path_in_container):
    subpath = get_model_subpath(model_path_in_container)
    local_path = "/models/" + subpath
    updated = original_command
    updated = updated.replace("-v " + REMOTE_MODELS_DIR + ":", "-v " + LOCAL_MODELS_DIR + ":")
    container_prefix = "-m " + model_path_in_container
    updated = updated.replace(container_prefix, "-m " + local_path)
    return updated


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
    import uuid

    new_cmd = {
        "id": str(uuid.uuid4())[:8],
        "name": data.get("name"),
        "command": data.get("command"),
        "use_local": data.get("use_local", False),
    }
    commands.append(new_cmd)
    save_commands(commands)
    return jsonify({"status": "success", "command": new_cmd})


@app.route("/api/commands/<cmd_id>", methods=["PUT", "POST"])
def update_command(cmd_id):
    data = request.json
    commands = load_commands()
    updated = False

    for cmd in commands:
        if str(cmd.get("id")) == str(cmd_id):
            cmd["name"] = data.get("name")
            cmd["command"] = data.get("command")
            cmd["use_local"] = data.get("use_local", False)
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


@app.route("/api/status", methods=["GET"])
def status():
    return jsonify(get_container_status())


@app.route("/api/copy-model", methods=["POST"])
def start_copy():
    data = request.json
    command = data.get("command")
    model_name = data.get("model_name", "")
    import uuid
    task_id = str(uuid.uuid4())[:8]
    
    model_path = extract_model_path(command)
    if not model_path:
        return jsonify({"status": "error", "message": "No model path found in command"}), 400
    
    model_subpath = get_model_parent_subpath(model_path)
    src = os.path.join(REMOTE_MODELS_DIR, model_subpath)
    dst = os.path.join(LOCAL_MODELS_DIR, model_subpath)
    
    if not needs_local_copy(model_path):
        with copy_lock:
            copy_operations[task_id] = {
                'status': 'completed',
                'progress': 100,
                'error': None,
                'original_command': command,
                'modified_command': build_local_command(command, model_path),
                'model_name': model_name,
                'model_path': model_path,
                'src': src,
                'dst': dst,
                'src_size': 0,
                'auto_start': True,
            }
        return jsonify({"status": "already_copied", "task_id": task_id})
    
    src_size = calculate_dir_size(src)
    
    with copy_lock:
        copy_operations[task_id] = {
            'status': 'pending',
            'progress': 0,
            'error': None,
            'original_command': command,
            'modified_command': None,
            'model_name': model_name,
            'model_path': model_path,
            'src': src,
            'dst': dst,
            'src_size': src_size,
            'auto_start': False,
        }
    
    t = threading.Thread(target=copy_worker, args=(task_id, src, dst, model_path), daemon=True)
    t.start()
    
    return jsonify({"status": "started", "task_id": task_id})


@app.route("/api/copy-status", methods=["GET"])
def copy_status():
    task_id = request.args.get("task_id")
    if not task_id:
        return jsonify({"status": "error", "message": "No task_id provided"}), 400
    
    with copy_lock:
        if task_id not in copy_operations:
            return jsonify({"status": "error", "message": "Task not found"}), 404
        op = copy_operations[task_id].copy()
    
    if op['status'] == 'copying':
        progress = get_copy_progress(task_id)
        op['progress'] = progress
    
    return jsonify({
        "status": op['status'],
        "progress": op['progress'],
        "error": op.get('error'),
        "modified_command": op.get('modified_command'),
        "auto_start": op.get('auto_start', False),
    })


@app.route("/api/cancel-copy", methods=["POST"])
def cancel_copy():
    data = request.json
    task_id = data.get("task_id")
    if not task_id:
        return jsonify({"status": "error", "message": "No task_id provided"}), 400
    
    with copy_lock:
        if task_id in copy_operations:
            copy_operations[task_id]['status'] = 'cancelled'
            return jsonify({"status": "cancelled"})
        return jsonify({"status": "error", "message": "Task not found"}), 404


@app.route("/api/start", methods=["POST"])
def start_container():
    data = request.json
    cmd = data.get("command")
    model_name = data.get("model_name", "")
    modified_command = data.get("modified_command")
    task_id = data.get("copy_task_id")
    if not cmd and not modified_command:
        return jsonify({"status": "error", "message": "No command provided"}), 400

    if modified_command:
        cmd = modified_command
    elif task_id:
        with copy_lock:
            if task_id not in copy_operations:
                return jsonify({"status": "error", "message": "Copy task not found"}), 404
            op = copy_operations[task_id]
            if op['status'] != 'completed':
                return jsonify({"status": "error", "message": f"Copy not completed (status: {op['status']})"}), 409
            cmd = op['modified_command']
            model_name = op.get('model_name', model_name)

    subprocess.run(["podman", "rm", "-f", "llama-cockpit-server"], stderr=subprocess.DEVNULL)
    subprocess.Popen(cmd, shell=True)
    save_model_state(model_name)
    return jsonify({"status": "started"})


@app.route("/api/auto-start", methods=["POST"])
def auto_start_container():
    data = request.json
    task_id = data.get("task_id")
    if not task_id:
        return jsonify({"status": "error", "message": "No task_id provided"}), 400
    
    with copy_lock:
        if task_id not in copy_operations:
            return jsonify({"status": "error", "message": "Task not found"}), 404
        op = copy_operations[task_id]
        if op['status'] != 'completed':
            return jsonify({"status": "error", "message": "Copy not completed"}), 409
        cmd = op['modified_command']
        model_name = op.get('model_name', '')
    
    subprocess.run(["podman", "rm", "-f", "llama-cockpit-server"], stderr=subprocess.DEVNULL)
    subprocess.Popen(cmd, shell=True)
    save_model_state(model_name)
    return jsonify({"status": "started"})


@app.route("/api/stop", methods=["POST"])
def stop_container():
    subprocess.run(["podman", "stop", "-t", "2", "llama-cockpit-server"])
    subprocess.run(["podman", "rm", "-f", "llama-cockpit-server"])
    clear_model_state()
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
