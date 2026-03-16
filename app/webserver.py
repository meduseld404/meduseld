from flask import (
    Flask,
    g,
    render_template,
    request,
    jsonify,
    abort,
    make_response,
    redirect,
    session,
    url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix
import subprocess
import psutil
import time
import os
import threading
import requests
import logging
import signal
import sys
import json
from collections import deque
from functools import wraps
from datetime import datetime
import socket
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import jwt

# Get the directory where this script is located
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    static_folder=os.path.join(BASE_DIR, "static"),
    template_folder=os.path.join(BASE_DIR, "templates"),
)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)


# Add CORS headers for API endpoints
@app.after_request
def add_cors_headers(response):
    # Allow system.meduseld.io and services.meduseld.io to access API
    origin = request.headers.get("Origin")
    if origin and "meduseld.io" in origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Allow-Credentials"] = "true"
    return response


# ================= CONFIG =================

try:
    import config
    from config import *
except ImportError as e:
    # Fallback to Linux defaults if config.py doesn't exist
    logger_temp = logging.getLogger(__name__)
    logger_temp.warning(f"config.py not found, using default Linux configuration: {e}")

    IS_DEV = False
    IS_PRODUCTION = True
    SERVER_DIR = "/srv/games/icarus"
    LAUNCH_EXE = f"{SERVER_DIR}/start.sh"
    LAUNCH_SCRIPT = f"{SERVER_DIR}/start.sh"
    PROCESS_NAME = "IcarusServer.exe"
    LOG_FILE = f"{SERVER_DIR}/Icarus/Saved/Logs/Icarus.log"
    UPDATE_SCRIPT = f"{SERVER_DIR}/updateserver.sh"
    STEAM_APP_ID = "2089300"
    VERSION_FILE = f"{SERVER_DIR}/version.txt"
    SERVER_ARGS = ["-SteamServerName=404localserver", "-Port=17777", "-QueryPort=27015", "-Log"]
    RATE_LIMIT_WINDOW = 60
    RATE_LIMIT_MAX_REQUESTS = 10
    RESTART_COOLDOWN = 30
    START_TIMEOUT = 60
    STOP_TIMEOUT = 30
    UPDATE_TIMEOUT = 600
    UPDATE_CHECK_INTERVAL = 3600
    STATS_COLLECTION_INTERVAL = 30
    MONITOR_INTERVAL = 5
    WARNING_CPU = 80
    WARNING_RAM = 80
    WARNING_DISK = 85
    CRITICAL_CPU = 95
    CRITICAL_RAM = 90
    CRITICAL_DISK = 95
    LOG_FILE_PATH = "/srv/meduseld/logs/webserver.log"
    SYSTEM_LOG_FILE_PATH = "/var/log/syslog"
    LOG_LEVEL = "INFO"
    FLASK_HOST = "0.0.0.0"
    FLASK_PORT = 5000
    FLASK_DEBUG = False
    ev = "change-me-in-production"

# Set Flask secret key for sessions (required for OAuth)
app.secret_key = SECRET_KEY

# ================= DATABASE =================
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

from database import init_db

init_db(app)

# ================= LOGGING =================

# Ensure log directory exists
log_dir = os.path.dirname(LOG_FILE_PATH)
if log_dir and not os.path.exists(log_dir):
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception as e:
        print(f"Warning: Could not create log directory {log_dir}: {e}")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(LOG_FILE_PATH), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# ================= STATE MANAGEMENT =================

state_lock = threading.Lock()
version_lock = threading.Lock()
rate_limit_lock = threading.Lock()

server_state = "offline"
last_restart_time = 0
last_update_status = None
last_update_time = None
last_update_output = None
current_build_id = None
latest_build_id = None
history = deque(maxlen=60)
log_buffer = deque(maxlen=500)
activity_log = deque(maxlen=100)  # Track user actions

# Dev mode: fake server running state
dev_server_running = False
dev_server_start_time = 0

# Idle shutdown tracking
IDLE_SHUTDOWN_MINUTES = 15
idle_since = None  # Timestamp when server first had 0 players

# Session-based dev mode tracking (keyed by session ID or IP)
session_dev_mode = {}

# Rate limiting
request_history = deque(maxlen=100)

# Thread health tracking
thread_health = {
    "monitor": {"alive": False, "last_heartbeat": 0},
    "stats": {"alive": False, "last_heartbeat": 0},
    "updates": {"alive": False, "last_heartbeat": 0},
}

ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    "192.168.1.175",
    "meduseld.io",
    "services.meduseld.io",
    "panel.meduseld.io",
    "ssh.meduseld.io",
    "snowmane.meduseld.io",
    "terminal.meduseld.io",
    "jellyfin.meduseld.io",
    "health.meduseld.io",
]

# Valid state transitions
VALID_TRANSITIONS = {
    "offline": ["starting", "crashed"],
    "starting": ["running", "offline", "crashed"],
    "running": ["stopping", "restarting", "crashed"],
    "stopping": ["offline", "crashed"],
    "restarting": ["running", "offline", "crashed"],
    "crashed": ["starting", "offline"],
}

# ================= UTILITIES =================


def is_dev_mode():
    """Check if current request is in development mode"""
    try:
        return IS_DEV or (request and request.args.get("env") == "development")
    except:
        return IS_DEV


def set_server_state(new_state, force=False):
    """Thread-safe state setter with validation"""
    global server_state

    with state_lock:
        old_state = server_state

        if new_state == old_state:
            return True

        # Validate transition (unless forced)
        if not force and new_state not in VALID_TRANSITIONS.get(old_state, []):
            logger.warning(f"Invalid state transition: {old_state} -> {new_state}")
            return False

        server_state = new_state
        if force:
            logger.info(f"State transition (forced): {old_state} -> {new_state}")
        else:
            logger.info(f"State transition: {old_state} -> {new_state}")
        return True


def get_server_state():
    """Thread-safe state getter"""
    with state_lock:
        return server_state


def rate_limit_check(ip):
    """Check if IP has exceeded rate limit"""
    with rate_limit_lock:
        now = time.time()

        # Remove old requests
        while request_history and request_history[0][1] < now - RATE_LIMIT_WINDOW:
            request_history.popleft()

        # Count requests from this IP
        ip_requests = sum(1 for req_ip, _ in request_history if req_ip == ip)

        if ip_requests >= RATE_LIMIT_MAX_REQUESTS:
            return False

        request_history.append((ip, now))
        return True


def rate_limit(f):
    """Rate limiting decorator"""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        ip = request.headers.get("CF-Connecting-IP", request.remote_addr)

        if not rate_limit_check(ip):
            logger.warning(f"Rate limit exceeded for {ip}")
            return jsonify({"error": "Rate limit exceeded"}), 429

        return f(*args, **kwargs)

    return decorated_function


def log_activity(action):
    """Log user activity"""
    global activity_log

    ip = request.headers.get("CF-Connecting-IP", request.remote_addr)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

    activity_log.append({"timestamp": timestamp, "ip": ip, "action": action})

    logger.info(f"Activity: {action} from {ip}")


# ================= STARTUP VALIDATION =================


def validate_configuration():
    """Validate configuration on startup"""
    issues = []

    if not os.path.exists(LAUNCH_EXE):
        issues.append(f"Launch executable not found: {LAUNCH_EXE}")

    if not os.path.exists(UPDATE_SCRIPT):
        logger.warning(f"Update script not found: {UPDATE_SCRIPT}")

    if not os.path.exists(os.path.dirname(VERSION_FILE)):
        try:
            os.makedirs(os.path.dirname(VERSION_FILE))
        except Exception as e:
            issues.append(f"Cannot create version file directory: {e}")

    if issues:
        for issue in issues:
            logger.error(issue)
        return False

    logger.info("Configuration validation passed")
    return True


def detect_initial_state():
    """Detect if server is already running on startup"""
    global server_state

    if is_running():
        with state_lock:
            server_state = "running"
        logger.info("Server detected as already running on startup")
    else:
        with state_lock:
            server_state = "offline"
        logger.info("Server detected as offline on startup")


# ================= HOST VALIDATION =================


@app.before_request
def validate_host():
    host = request.host.split(":")[0]

    # Log all request details for debugging
    logger.info(f"Request to {request.path} from host: {host}, headers: {dict(request.headers)}")

    if host in ALLOWED_HOSTS:
        return

    if host.startswith("192.168.") or host.startswith("10.") or host.startswith("172."):
        return

    abort(403)


# ================= AUTHENTICATION =================

# Paths that don't require authentication
PUBLIC_PATHS = [
    "/health",
    "/health-check-b8f3a9c2",
    "/api/check-service/",
]

# Hosts that don't require authentication
PUBLIC_HOSTS = [
    "health.meduseld.io",
]


def get_current_user():
    """Get the current user from the session, or None if not logged in."""
    user_data = session.get("user")
    if not user_data:
        return None
    from models import User

    return User.query.filter_by(discord_id=str(user_data["discord_id"])).first()


@app.before_request
def authenticate_request():
    """
    Check for a valid Cf-Access-Jwt-Assertion header from Cloudflare Access.
    On authenticated pages, decode the JWT, get_or_create the user, and store in session + g.
    Public paths and health endpoints are skipped.
    """
    host = request.host.split(":")[0]

    # Skip auth for public hosts
    if host in PUBLIC_HOSTS:
        return

    # Skip auth for public paths
    for path in PUBLIC_PATHS:
        if request.path.startswith(path):
            return

    # Skip auth for OPTIONS preflight requests
    if request.method == "OPTIONS":
        return

    # If user is already in session, load them into g
    if "user" in session:
        g.user = get_current_user()
        return

    # Check for Cloudflare Access JWT
    cf_token = request.headers.get("Cf-Access-Jwt-Assertion")
    if not cf_token:
        # No token and no session — in production this means Cloudflare Access
        # hasn't authenticated yet. The request shouldn't reach here if
        # Cloudflare Access is configured correctly, but just in case:
        if IS_DEV:
            # In dev mode, create a fake dev user
            from models import User

            user = User.get_or_create(
                discord_id="dev_user_000",
                username="dev_user",
                display_name="Development User",
            )
            session["user"] = user.to_dict()
            g.user = user
            return
        # In production, let the request through — Cloudflare Access handles gating.
        # The user just won't have a session/user object.
        g.user = None
        return

    # Decode the Cloudflare Access JWT
    try:
        # Cloudflare Access JWTs are signed with RS256 but we can decode
        # the payload without verification since Cloudflare Access already
        # validated the token before forwarding the request to our origin.
        payload = jwt.decode(cf_token, options={"verify_signature": False})

        # Log the full JWT payload so we can see what Cloudflare sends
        logger.info(f"CF Access JWT claims: {json.dumps(payload, default=str)}")

        # Extract what we can from the Cloudflare Access JWT
        email = payload.get("email", "")
        # Cloudflare Access uses its own UUID as 'sub', not the Discord ID
        cf_user_id = payload.get("sub", "")

        # The custom OIDC claims (discord_user) are NOT passed through
        # in the Cf-Access-Jwt-Assertion header. They're only available
        # via the /cdn-cgi/access/get-identity endpoint from the browser.
        # So we use email as the initial identifier, and the client-side
        # auth.js will call /api/sync-identity with the full Discord data.

        # Use preferred_username or email-derived username as fallback
        username = payload.get("preferred_username", "") or (
            email.split("@")[0] if email else "unknown"
        )
        display_name = payload.get("name", "") or username

        # For now, use the Cloudflare UUID as discord_id — it will be
        # updated when the client calls /api/sync-identity with real data
        discord_id = cf_user_id

        if not discord_id:
            logger.warning("JWT decoded but no user identifier found in claims")
            g.user = None
            return

        from models import User

        user = User.get_or_create(
            discord_id=discord_id,
            username=username,
            display_name=display_name,
            email=email,
        )

        session["user"] = user.to_dict()
        g.user = user
        logger.info(f"Authenticated user: {username} ({discord_id})")

    except Exception as e:
        logger.error(f"Error decoding Cloudflare Access JWT: {e}")
        g.user = None


def require_auth(f):
    """Decorator to require an authenticated user. Returns 401 if no user in session."""

    @wraps(f)
    def decorated(*args, **kwargs):
        if not hasattr(g, "user") or g.user is None:
            return jsonify({"error": "Authentication required"}), 401
        return f(*args, **kwargs)

    return decorated


def require_role(role):
    """Decorator to require a specific role. Use after @require_auth."""

    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not hasattr(g, "user") or g.user is None:
                return jsonify({"error": "Authentication required"}), 401
            if g.user.role != role and g.user.role != "admin":
                return jsonify({"error": "Insufficient permissions"}), 403
            return f(*args, **kwargs)

        return decorated

    return decorator


@app.route("/api/me")
def api_me():
    """Return the current authenticated user's info."""
    if not hasattr(g, "user") or g.user is None:
        return jsonify({"authenticated": False}), 200
    return jsonify({"authenticated": True, "user": g.user.to_dict()}), 200


@app.route("/api/sync-identity", methods=["POST"])
def api_sync_identity():
    """
    Called by client-side auth.js with Discord user data from the
    Cloudflare Access /cdn-cgi/access/get-identity endpoint.
    Updates the user's DB record with real Discord ID, username, and avatar.
    """
    data = request.get_json()
    if not data or not data.get("discord_id"):
        return jsonify({"error": "Missing discord_id"}), 400

    # We need a session user to know which DB record to update
    user_data = session.get("user")
    if not user_data:
        return jsonify({"error": "Not authenticated"}), 401

    from models import User
    from database import db

    user = User.query.filter_by(discord_id=str(user_data["discord_id"])).first()
    if not user:
        return jsonify({"error": "User not found"}), 404

    # Update with real Discord data
    old_discord_id = user.discord_id
    user.discord_id = str(data["discord_id"])
    user.username = data.get("username", user.username)
    user.display_name = data.get("display_name", user.display_name)
    user.avatar_hash = data.get("avatar_hash", user.avatar_hash)

    try:
        db.session.commit()
        session["user"] = user.to_dict()
        logger.info(
            f"Synced Discord identity: {old_discord_id} -> {user.discord_id} ({user.username})"
        )
        return jsonify({"synced": True, "user": user.to_dict()}), 200
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error syncing identity: {e}")
        return jsonify({"error": "Sync failed"}), 500


# ================= SERVER CONTROL =================


def is_running():
    """Check if server process is running"""
    global dev_server_running

    # Always use dev_server_running if it's set (for URL-based dev mode)
    if dev_server_running:
        return True

    if IS_DEV:
        # In dev mode, use fake state
        return dev_server_running

    try:
        # Production: check by process name and command line
        found_processes = []
        for proc in psutil.process_iter(["name", "cmdline", "pid", "exe"]):
            # For Wine processes, check the command line for the exe name
            if proc.info["cmdline"]:
                try:
                    # cmdline is a list, convert each element to string and join
                    cmdline_str = " ".join(str(arg) for arg in proc.info["cmdline"])

                    # Log all wine/icarus related processes for debugging
                    if "wine" in proc.info["name"].lower() or "icarus" in cmdline_str.lower():
                        found_processes.append(
                            f"PID {proc.info['pid']}: {proc.info['name']} - {cmdline_str[:100]}"
                        )

                    # Skip tmux, xvfb-run, wine launcher processes - we want the actual game server
                    if proc.info["name"] in [
                        "tmux",
                        "tmux: server",
                        "xvfb-run",
                        "sh",
                        "bash",
                        "wine",
                        "wine64",
                        "wineserver",
                    ]:
                        continue

                    if "IcarusServer-Win64-Shipping.exe" in cmdline_str:
                        logger.info(
                            f"Found server by cmdline: PID {proc.info['pid']}, name: {proc.info['name']}, exe: {proc.info.get('exe', 'N/A')}"
                        )
                        return True
                except Exception as e:
                    # Skip processes we can't access
                    continue

        if found_processes:
            logger.debug(
                f"Found {len(found_processes)} wine/icarus related processes but none matched:"
            )
            for p in found_processes[:5]:  # Log first 5
                logger.debug(f"  {p}")
        else:
            logger.debug("No wine or icarus related processes found at all")

        logger.debug("Server process not found")
    except Exception as e:
        logger.error(f"Error checking if server is running: {e}", exc_info=True)
    return False


def launch_server():
    """Launch the game server as a completely independent process"""
    global dev_server_running, dev_server_start_time

    try:
        if IS_DEV:
            # In dev mode, just set the fake state
            dev_server_running = True
            dev_server_start_time = time.time()
            logger.info("Dummy server 'started' (simulated)")
        else:
            # Production: Check if launch script exists, use it for better process isolation
            if "LAUNCH_SCRIPT" in globals() and os.path.exists(LAUNCH_SCRIPT):
                # Ensure script is executable
                try:
                    os.chmod(LAUNCH_SCRIPT, 0o755)
                except Exception as e:
                    logger.warning(f"Could not set execute permission on {LAUNCH_SCRIPT}: {e}")

                # Use bash explicitly to run the script
                logger.info(f"Launching server via script: {LAUNCH_SCRIPT}")
                logger.info(
                    f"Working directory: {os.path.dirname(os.path.abspath(LAUNCH_SCRIPT)) if os.path.dirname(LAUNCH_SCRIPT) else '.'}"
                )

                proc = subprocess.Popen(
                    ["bash", LAUNCH_SCRIPT],
                    cwd=(
                        os.path.dirname(os.path.abspath(LAUNCH_SCRIPT))
                        if os.path.dirname(LAUNCH_SCRIPT)
                        else "."
                    ),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                logger.info(f"Server launch script started with PID: {proc.pid}")

                # Give it a moment to start, then check if it's still alive
                time.sleep(2)
                poll_result = proc.poll()
                if poll_result is not None:
                    # Process already exited
                    stdout, stderr = proc.communicate()
                    logger.error(f"Launch script exited immediately with code {poll_result}")
                    logger.error(f"STDOUT: {stdout.decode('utf-8', errors='ignore')}")
                    logger.error(f"STDERR: {stderr.decode('utf-8', errors='ignore')}")
                else:
                    logger.info("Launch script is running")
            else:
                # Fallback to direct launch
                logger.info(f"Launching server directly: {LAUNCH_EXE} {' '.join(SERVER_ARGS)}")
                logger.info(f"Working directory: {SERVER_DIR}")

                proc = subprocess.Popen(
                    [LAUNCH_EXE] + SERVER_ARGS,
                    cwd=SERVER_DIR,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                logger.info(f"Server process started with PID: {proc.pid}")

                # Give it a moment to start, then check if it's still alive
                time.sleep(2)
                poll_result = proc.poll()
                if poll_result is not None:
                    # Process already exited
                    stdout, stderr = proc.communicate()
                    logger.error(f"Server process exited immediately with code {poll_result}")
                    logger.error(f"STDOUT: {stdout.decode('utf-8', errors='ignore')}")
                    logger.error(f"STDERR: {stderr.decode('utf-8', errors='ignore')}")
                else:
                    logger.info("Server process is running")
    except Exception as e:
        logger.error(f"Failed to launch server: {e}", exc_info=True)
        raise


def kill_server():
    """Kill the game server process"""
    global dev_server_running

    try:
        if IS_DEV:
            # In dev mode, just clear the fake state
            dev_server_running = False
            logger.info("Dummy server 'killed' (simulated)")
        else:
            # Production: Kill the server process
            if os.name == "nt":
                subprocess.call(f'taskkill /IM "{PROCESS_NAME}" /F', shell=True)
            else:
                # On Linux, find and kill the Wine process running the server
                killed = False
                try:
                    for proc in psutil.process_iter(["name", "cmdline", "pid"]):
                        # Check if this is the Icarus server process
                        if proc.info["cmdline"]:
                            for arg in proc.info["cmdline"]:
                                if arg and (
                                    "IcarusServer-Win64-Shipping.exe" in arg
                                    or "IcarusServer.exe" in arg
                                ):
                                    logger.info(
                                        f"Killing process {proc.info['pid']}: {proc.info['name']}"
                                    )
                                    proc.kill()
                                    killed = True
                                    break
                except Exception as e:
                    logger.error(f"Error killing process via psutil: {e}")

                # Fallback to pkill if psutil didn't work
                if not killed:
                    subprocess.call(f'pkill -9 -f "IcarusServer-Win64-Shipping.exe"', shell=True)
                    subprocess.call(f'pkill -9 -f "IcarusServer.exe"', shell=True)

                logger.info("Server kill command executed")
    except Exception as e:
        logger.error(f"Failed to kill server: {e}")


# ================= SYSTEM STATS =================


def get_cpu_temperature():
    """Get CPU temperature in Celsius"""
    try:
        # Try psutil first (works on many systems)
        if hasattr(psutil, "sensors_temperatures"):
            temps = psutil.sensors_temperatures()
            if temps:
                # Try common sensor names
                for name in ["coretemp", "cpu_thermal", "k10temp", "zenpower"]:
                    if name in temps:
                        entries = temps[name]
                        if entries:
                            # Return the first temperature reading
                            return round(entries[0].current, 1)

        # Fallback: read from /sys/class/thermal (Linux)
        thermal_zones = [
            "/sys/class/thermal/thermal_zone0/temp",
            "/sys/class/thermal/thermal_zone1/temp",
        ]

        for zone in thermal_zones:
            if os.path.exists(zone):
                with open(zone, "r") as f:
                    temp = int(f.read().strip())
                    # Temperature is in millidegrees, convert to Celsius
                    return round(temp / 1000.0, 1)

        return None
    except Exception as e:
        logger.debug(f"Could not read CPU temperature: {e}")
        return None


def get_system_stats():
    """Get system resource usage"""
    try:
        cpu = psutil.cpu_percent(interval=0.3)
        memory = psutil.virtual_memory()

        # Get disk usage for root filesystem
        disk = psutil.disk_usage("/")

        # Try to get total physical disk size by checking all partitions
        total_disk_size = 0
        used_disk_size = 0
        try:
            for partition in psutil.disk_partitions():
                # Skip special filesystems
                if partition.fstype and partition.fstype not in ["squashfs", "tmpfs", "devtmpfs"]:
                    try:
                        usage = psutil.disk_usage(partition.mountpoint)
                        total_disk_size += usage.total
                        used_disk_size += usage.used
                    except:
                        pass
        except:
            # Fallback to root filesystem only
            total_disk_size = disk.total
            used_disk_size = disk.used

        # Get CPU temperature
        cpu_temp = get_cpu_temperature()

        return {
            "cpu": cpu,
            "cpu_temp": cpu_temp,
            "ram_percent": memory.percent,
            "ram_used": round(memory.used / (1024**3), 2),
            "ram_total": round(memory.total / (1024**3), 2),
            "disk_percent": disk.percent,
            "disk_used": round(used_disk_size / (1024**3), 2),
            "disk_total": round(total_disk_size / (1024**3), 2),
        }
    except Exception as e:
        logger.error(f"Error getting system stats: {e}")
        return {
            "cpu": 0,
            "cpu_temp": None,
            "ram_percent": 0,
            "ram_used": 0,
            "ram_total": 0,
            "disk_percent": 0,
            "disk_used": 0,
            "disk_total": 0,
        }


# Cache for Icarus process object to avoid repeated lookups
_icarus_process_cache = {"pid": None, "process": None, "last_check": 0}


def get_icarus_usage():
    """Get Icarus server resource usage"""
    global _icarus_process_cache

    try:
        if IS_DEV:
            # In dev mode, return fake stats for the dummy server
            import random

            return {
                "cpu": round(random.uniform(5, 15), 2),
                "cpu_raw": round(random.uniform(20, 60), 2),
                "ram": round(random.uniform(2.5, 4.5), 2),
            }
        else:
            # Production: get real process stats
            now = time.time()

            # Try to use cached process first
            if _icarus_process_cache["pid"] and _icarus_process_cache["process"]:
                try:
                    p = _icarus_process_cache["process"]
                    if p.is_running():
                        cpu_raw = p.cpu_percent(interval=None)  # Non-blocking
                        ram_gb = round(p.memory_info().rss / (1024**3), 2)
                        cpu_norm = round(cpu_raw / psutil.cpu_count(), 2)

                        logger.debug(
                            f"Using cached process {_icarus_process_cache['pid']}: CPU={cpu_raw}%, RAM={ram_gb}GB"
                        )

                        return {"cpu": cpu_norm, "cpu_raw": cpu_raw, "ram": ram_gb}
                except Exception as e:
                    # Cache invalid, clear it
                    logger.debug(f"Cache invalid, clearing: {e}")
                    _icarus_process_cache = {"pid": None, "process": None, "last_check": 0}

            # Find the process
            logger.debug("Searching for Icarus process...")
            for proc in psutil.process_iter(["name", "cmdline", "pid", "exe"]):
                if proc.info["cmdline"]:
                    try:
                        cmdline_str = " ".join(str(arg) for arg in proc.info["cmdline"])

                        # Debug: log processes that might be relevant
                        if "icarus" in cmdline_str.lower() or "wine" in proc.info["name"].lower():
                            logger.debug(
                                f"Checking PID {proc.info['pid']}: {proc.info['name']} - {cmdline_str[:100]}"
                            )

                        # Skip tmux, xvfb-run, wine launcher processes - we want the actual game server
                        if proc.info["name"] in [
                            "tmux",
                            "tmux: server",
                            "xvfb-run",
                            "sh",
                            "bash",
                            "wine",
                            "wine64",
                            "wineserver",
                            "start.exe",
                        ]:
                            continue

                        if "IcarusServer-Win64-Shipping.exe" in cmdline_str:
                            logger.info(
                                f"Found Icarus server process: PID {proc.info['pid']}, name: {proc.info['name']}, exe: {proc.info.get('exe', 'N/A')}"
                            )
                            p = psutil.Process(proc.info["pid"])

                            # Initialize CPU measurement (non-blocking)
                            p.cpu_percent(interval=None)

                            # Cache the process
                            _icarus_process_cache = {
                                "pid": proc.info["pid"],
                                "process": p,
                                "last_check": now,
                            }

                            # Get RAM immediately
                            ram_gb = round(p.memory_info().rss / (1024**3), 2)

                            logger.info(
                                f"Initialized stats for PID {proc.info['pid']}: RAM={ram_gb}GB (CPU will be available on next call)"
                            )

                            return {
                                "cpu": 0.0,  # Will be accurate on next call
                                "cpu_raw": 0.0,
                                "ram": ram_gb,
                            }
                    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                        continue
                    except Exception as e:
                        logger.error(
                            f"Error getting process stats for PID {proc.info.get('pid')}: {e}"
                        )
                        continue

            logger.warning("Icarus server process not found in process list")
    except Exception as e:
        logger.error(f"Error iterating processes: {e}", exc_info=True)
    return None


def get_player_count():
    """Query Steam server for current player count using A2S_INFO protocol"""
    try:
        if IS_DEV:
            # Return fake player count in dev mode
            import random

            return random.randint(0, 8)

        # Steam Query Protocol A2S_INFO
        query_port = 27015
        query_host = "127.0.0.1"

        # Create UDP socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(2)

        # A2S_INFO request packet
        # Format: 0xFFFFFFFF (4 bytes) + 0x54 (1 byte) + "Source Engine Query\0"
        request = b"\xff\xff\xff\xff\x54Source Engine Query\x00"

        sock.sendto(request, (query_host, query_port))
        data, addr = sock.recvfrom(4096)
        sock.close()

        # Parse response
        # Skip header (4 bytes 0xFFFFFFFF + 1 byte type)
        if len(data) < 5:
            return None

        # Skip protocol version (1 byte)
        offset = 6

        # Skip server name (null-terminated string)
        while offset < len(data) and data[offset] != 0:
            offset += 1
        offset += 1

        # Skip map name (null-terminated string)
        while offset < len(data) and data[offset] != 0:
            offset += 1
        offset += 1

        # Skip folder (null-terminated string)
        while offset < len(data) and data[offset] != 0:
            offset += 1
        offset += 1

        # Skip game (null-terminated string)
        while offset < len(data) and data[offset] != 0:
            offset += 1
        offset += 1

        # Skip app ID (2 bytes)
        offset += 2

        # Read player count (1 byte)
        if offset < len(data):
            players = data[offset]
            logger.debug(f"Steam Query: {players} players online")
            return players

        return None

    except socket.timeout:
        logger.debug("Steam query timeout - server may not be responding to queries")
        return None
    except Exception as e:
        logger.debug(f"Error querying player count: {e}")
        return None


def get_uptime():
    """Get server uptime in seconds"""
    global dev_server_start_time

    if IS_DEV:
        # In dev mode, calculate from fake start time
        if dev_server_running and dev_server_start_time > 0:
            return int(time.time() - dev_server_start_time)
        return 0

    try:
        for proc in psutil.process_iter(["name", "create_time", "cmdline", "pid", "exe"]):
            # For Wine processes, check the command line
            if proc.info["cmdline"]:
                try:
                    cmdline_str = " ".join(str(arg) for arg in proc.info["cmdline"])

                    # Skip tmux, xvfb-run, wine launcher processes - we want the actual game server
                    if proc.info["name"] in [
                        "tmux",
                        "tmux: server",
                        "xvfb-run",
                        "sh",
                        "bash",
                        "wine",
                        "wine64",
                        "wineserver",
                        "start.exe",
                    ]:
                        continue

                    if "IcarusServer-Win64-Shipping.exe" in cmdline_str:
                        uptime = int(time.time() - proc.info["create_time"])
                        logger.debug(f"Server uptime: {uptime}s (PID {proc.info['pid']})")
                        return uptime
                except Exception as e:
                    continue
    except Exception as e:
        logger.error(f"Error getting uptime: {e}", exc_info=True)
    return 0


def get_health(stats):
    """Determine system health status"""
    if (
        stats["cpu"] > CRITICAL_CPU
        or stats["ram_percent"] > CRITICAL_RAM
        or stats["disk_percent"] > CRITICAL_DISK
    ):
        return "critical"
    if (
        stats["cpu"] > WARNING_CPU
        or stats["ram_percent"] > WARNING_RAM
        or stats["disk_percent"] > WARNING_DISK
    ):
        return "warning"
    return "good"


# ================= LOGS =================


def read_log():
    """Read game server log file"""
    if not os.path.exists(LOG_FILE):
        return []

    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
            return f.readlines()[-200:]
    except Exception as e:
        logger.error(f"Error reading log file: {e}")
        return []


def detect_crash_signature(lines):
    """Detect crash indicators in logs"""
    crash_keywords = ["Fatal", "Unhandled", "Exception", "Error"]
    return any(any(k in line for k in crash_keywords) for line in lines)


# ================= VERSION TRACKING =================

# Cache for game version from logs
_game_version_cache = {"version": None, "last_check": 0}


def get_game_version_from_logs():
    """Extract game version from server logs and cache it"""
    global _game_version_cache

    # Return cached version if available and server is running
    if _game_version_cache["version"] and is_running():
        return _game_version_cache["version"]

    # If server is not running, clear cache
    if not is_running():
        _game_version_cache = {"version": None, "last_check": 0}
        return None

    try:
        if not os.path.exists(LOG_FILE):
            return None

        # Read the log file and search for version line
        with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "LogIcarusGameInstance:" in line and "Version:" in line:
                    # Extract version using regex
                    import re

                    match = re.search(r"Version:\s+(.+?)\s+<====", line)
                    if match:
                        version = match.group(1).strip()
                        _game_version_cache["version"] = version
                        _game_version_cache["last_check"] = time.time()
                        logger.info(f"Extracted game version from logs: {version}")
                        return version

        logger.debug("Version string not found in logs")
        return None
    except Exception as e:
        logger.error(f"Error extracting version from logs: {e}")
        return None


def get_current_build_id():
    """Read the locally stored build ID"""
    with version_lock:
        if os.path.exists(VERSION_FILE):
            try:
                with open(VERSION_FILE, "r") as f:
                    build_id = f.read().strip()
                    logger.debug(f"Read current build ID: {build_id}")
                    return build_id
            except Exception as e:
                logger.error(f"Error reading version file: {e}")
        return None


def save_current_build_id(build_id):
    """Save the current build ID to file"""
    with version_lock:
        try:
            with open(VERSION_FILE, "w") as f:
                f.write(str(build_id))
            logger.info(f"Saved build ID: {build_id}")
        except Exception as e:
            logger.error(f"Error saving version file: {e}")


def get_latest_build_id(retries=3):
    """Query Steam API for the latest build ID with retry logic"""
    for attempt in range(retries):
        try:
            url = f"https://api.steamcmd.net/v1/info/{STEAM_APP_ID}"
            response = requests.get(url, timeout=5)  # Reduced timeout from 10 to 5

            if response.status_code == 200:
                data = response.json()

                if "data" in data and STEAM_APP_ID in data["data"]:
                    depots = data["data"][STEAM_APP_ID].get("depots", {})
                    branches = depots.get("branches", {})
                    public_branch = branches.get("public", {})
                    build_id = public_branch.get("buildid")

                    if build_id:
                        logger.debug(f"Retrieved latest build ID: {build_id}")
                        return build_id
                    else:
                        logger.warning("Build ID not found in API response")
                else:
                    logger.warning(f"Unexpected API response structure")
            else:
                logger.warning(f"Steam API returned status {response.status_code}")

        except requests.Timeout:
            logger.warning(f"Steam API timeout (attempt {attempt + 1}/{retries})")
        except requests.RequestException as e:
            logger.warning(f"Steam API request failed (attempt {attempt + 1}/{retries}): {e}")
        except Exception as e:
            logger.error(f"Unexpected error querying Steam API: {e}")

        if attempt < retries - 1:
            time.sleep(1)  # Reduced from exponential backoff to 1 second

    logger.warning("Failed to retrieve latest build ID after all retries")
    return None


def check_for_updates():
    """Check if an update is available"""
    global current_build_id, latest_build_id

    current_build_id = get_current_build_id()
    latest_build_id = get_latest_build_id()

    if current_build_id and latest_build_id:
        update_available = current_build_id != latest_build_id
        if update_available:
            logger.info(f"Update available: {current_build_id} -> {latest_build_id}")
        return update_available

    return False


# ================= MONITOR =================


def monitor_server():
    """Monitor server state and detect crashes"""
    global thread_health, idle_since

    logger.info("Monitor thread started")
    thread_health["monitor"]["alive"] = True

    while True:
        try:
            thread_health["monitor"]["last_heartbeat"] = time.time()
            time.sleep(MONITOR_INTERVAL)

            current_state = get_server_state()
            running = is_running()

            # Only monitor if in stable states
            if current_state == "running" and not running:
                logger.error("=" * 60)
                logger.error("SERVER CRASH DETECTED")
                logger.error("=" * 60)
                logger.error(f"Server was running but process is now gone")
                logger.error(f"Last known state: {current_state}")
                logger.error(f"Checking for crash indicators...")

                # Check system logs for OOM killer
                try:
                    result = subprocess.run(
                        ["dmesg", "-T"], capture_output=True, text=True, timeout=5
                    )
                    oom_lines = [
                        line
                        for line in result.stdout.split("\n")
                        if "oom" in line.lower() or "killed process" in line.lower()
                    ]
                    if oom_lines:
                        logger.error("OOM KILLER DETECTED - Server was killed due to low memory:")
                        for line in oom_lines[-5:]:
                            logger.error(f"  {line}")
                except Exception as e:
                    logger.error(f"Could not check dmesg: {e}")

                # Check last log lines
                try:
                    log_lines = read_log()
                    if log_lines:
                        logger.error("Last 10 lines from game log:")
                        for line in log_lines[-10:]:
                            logger.error(f"  {line.strip()}")
                except Exception as e:
                    logger.error(f"Could not read game log: {e}")

                logger.error("=" * 60)

                set_server_state("crashed")
                log_buffer.extend(read_log())

            elif current_state == "crashed" and running:
                logger.info("Server recovered from crash")
                set_server_state("running")

            # Idle shutdown: stop server after 15 min with 0 players
            if current_state == "running" and running:
                player_count = get_player_count()
                if player_count is not None and player_count == 0:
                    if idle_since is None:
                        idle_since = time.time()
                        logger.info("Idle shutdown: Server has 0 players, starting idle timer")
                    elif time.time() - idle_since >= IDLE_SHUTDOWN_MINUTES * 60:
                        logger.warning(
                            f"Idle shutdown: Server empty for {IDLE_SHUTDOWN_MINUTES} min, stopping"
                        )
                        idle_since = None
                        set_server_state("stopping")
                        kill_server()
                        # Wait for graceful stop, force kill if needed
                        stopped = False
                        for _ in range(STOP_TIMEOUT):
                            time.sleep(1)
                            if not is_running():
                                stopped = True
                                break
                        if not stopped:
                            logger.warning("Idle shutdown: Graceful stop failed, force killing")
                            kill_server()
                            time.sleep(5)
                        set_server_state("offline")
                        logger.info("Idle shutdown: Server stopped successfully")
                else:
                    if idle_since is not None:
                        logger.info("Idle shutdown: Players detected, resetting idle timer")
                    idle_since = None
            else:
                idle_since = None

        except Exception as e:
            logger.error(f"Error in monitor thread: {e}")
            time.sleep(MONITOR_INTERVAL)


# ================= ROUTES =================


def jellyfin_proxy(path=""):
    """Proxy requests to Jellyfin server"""
    from flask import Response

    try:
        # Always proxy to the Jellyfin server with the exact path
        jellyfin_url = f"http://71.191.152.254:8096/{path}"

        # Forward query parameters
        if request.query_string:
            jellyfin_url += f"?{request.query_string.decode()}"

        # Prepare headers - remove accept-encoding to prevent compression issues
        headers = {}
        for key, value in request.headers:
            if key.lower() not in ["host", "connection", "accept-encoding"]:
                headers[key] = value

        # Make request to Jellyfin
        resp = requests.request(
            method=request.method,
            url=jellyfin_url,
            headers=headers,
            data=request.get_data(),
            cookies=request.cookies,
            allow_redirects=False,
            timeout=30,
        )

        # Prepare response headers and rewrite Location header
        excluded_headers = ["content-encoding", "content-length", "transfer-encoding", "connection"]
        response_headers = []

        for name, value in resp.headers.items():
            if name.lower() not in excluded_headers:
                # Rewrite Location header to use proxy domain
                if name.lower() == "location":
                    value = value.replace(
                        "http://71.191.152.254:8096", "https://jellyfin.meduseld.io"
                    )
                response_headers.append((name, value))

        # Rewrite content for HTML/JS/JSON responses
        content = resp.content
        content_type = resp.headers.get("content-type", "").lower()

        if any(
            ct in content_type
            for ct in ["text/html", "application/javascript", "application/json", "text/javascript"]
        ):
            try:
                text_content = content.decode("utf-8")
                # Replace backend URL references
                text_content = text_content.replace(
                    "http://71.191.152.254:8096", "https://jellyfin.meduseld.io"
                )
                text_content = text_content.replace("71.191.152.254:8096", "jellyfin.meduseld.io")
                content = text_content.encode("utf-8")
            except Exception as e:
                logger.warning(f"Could not rewrite content: {e}")

        # Return proxied response
        return Response(content, resp.status_code, response_headers)

    except requests.Timeout:
        logger.error("Jellyfin proxy timeout")
        return "Jellyfin server timeout", 504
    except Exception as e:
        logger.error(f"Error proxying to Jellyfin: {e}")
        return f"Jellyfin unavailable: {e}", 503


@app.route("/", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
def home():
    """Route based on hostname"""
    host = request.host.split(":")[0]

    # If accessed via ssh subdomain, show terminal wrapper
    if host == "ssh.meduseld.io":
        return render_template("terminal.html")

    # If accessed via jellyfin subdomain, proxy to Jellyfin
    if host == "jellyfin.meduseld.io":
        return jellyfin_proxy("")

    # If accessed via health subdomain, show health dashboard
    if host == "health.meduseld.io":
        return render_template("health.html")

    # Check if dev mode is enabled via URL parameter
    dev_mode_active = is_dev_mode()

    # Otherwise show the Icarus control panel
    running = is_running()
    stats = get_system_stats()
    icarus_stats = get_icarus_usage() if running else None
    logs = read_log() if running else []

    return render_template(
        "panel.html",
        running=running,
        stats=stats,
        icarus_stats=icarus_stats,
        logs=logs,
        dev_mode=IS_DEV or dev_mode_active,
        server_state=get_server_state(),
        user=get_current_user(),
    )


@app.route("/terminal")
def terminal_proxy():
    """Proxy requests to ttyd"""
    import requests as req

    try:
        resp = req.get("http://localhost:7681", stream=True, timeout=5)
        return resp.content, resp.status_code, resp.headers.items()
    except Exception as e:
        logger.error(f"Error proxying to ttyd: {e}")
        return f"Terminal unavailable: {e}", 503


@app.route("/start", methods=["POST"])
@rate_limit
def start():
    log_activity("START server")

    # Check if dev mode via URL parameter
    dev_mode_active = is_dev_mode()

    current_state = get_server_state()

    if current_state in ["running", "starting", "restarting"]:
        return "", 204

    if not set_server_state("starting"):
        return jsonify({"error": "Invalid state transition"}), 400

    if dev_mode_active:
        # Dev mode: use dummy server
        global dev_server_running, dev_server_start_time
        dev_server_running = True
        dev_server_start_time = time.time()
        set_server_state("running")
        logger.info("Dummy server 'started' (simulated via URL parameter)")
        return "", 204

    # Check if update is available before starting
    update_available = check_for_updates()

    if update_available:
        logger.info("Update available - running update before starting server")
        # Run update script before launching
        if os.path.exists(UPDATE_SCRIPT):
            try:
                global last_update_status, last_update_time, last_update_output, current_build_id

                logger.info("Running update script")
                result = subprocess.run(
                    [UPDATE_SCRIPT],
                    cwd=SERVER_DIR,
                    shell=True,
                    timeout=UPDATE_TIMEOUT,
                    capture_output=True,
                    text=True,
                )

                last_update_time = time.time()
                last_update_output = result.stdout + "\n" + result.stderr

                if result.returncode == 0:
                    last_update_status = "success"
                    logger.info("Update script completed successfully")

                    # Update the stored build ID after successful update
                    new_build = get_latest_build_id()
                    if new_build:
                        save_current_build_id(new_build)
                        current_build_id = new_build
                else:
                    last_update_status = f"failed (exit code {result.returncode})"
                    logger.error(f"Update script failed: {last_update_status}")

            except subprocess.TimeoutExpired:
                last_update_status = "timeout"
                last_update_time = time.time()
                logger.error("Update script timed out")
            except Exception as e:
                last_update_status = f"error: {str(e)}"
                last_update_time = time.time()
                logger.error(f"Update script error: {e}")

        # Wait a moment after update
        time.sleep(2)

    launch_server()

    def wait():
        logger.info(f"Waiting up to {START_TIMEOUT} seconds for server to start...")
        for i in range(START_TIMEOUT):
            time.sleep(1)
            running = is_running()

            # Log every 10 seconds
            if i % 10 == 0 or i < 5:
                logger.info(f"Startup check {i}/{START_TIMEOUT}s - is_running(): {running}")

            if running:
                set_server_state("running")
                logger.info(f"Server detected as running after {i+1} seconds")
                return

        # Failed to start
        logger.error(f"Server failed to start within {START_TIMEOUT} seconds")
        logger.error("Checking for processes one more time...")
        is_running()  # This will log what processes it found
        set_server_state("offline")

    threading.Thread(target=wait, daemon=True).start()
    return "", 204


@app.route("/stop", methods=["POST"])
@rate_limit
def stop():
    log_activity("STOP server")

    dev_mode_active = is_dev_mode()

    current_state = get_server_state()

    if current_state in ["offline", "stopping"]:
        return "", 204

    if not set_server_state("stopping"):
        return jsonify({"error": "Invalid state transition"}), 400

    if dev_mode_active:
        # Dev mode: stop dummy server
        global dev_server_running
        dev_server_running = False
        set_server_state("offline")
        logger.info("Dummy server 'stopped' (simulated via URL parameter)")
        return "", 204

    kill_server()

    def wait():
        for _ in range(STOP_TIMEOUT):
            time.sleep(1)
            if not is_running():
                set_server_state("offline")
                return

        # Failed to stop - check actual state
        logger.error("Server failed to stop within timeout")
        if is_running():
            set_server_state("crashed")
        else:
            set_server_state("offline")

    threading.Thread(target=wait, daemon=True).start()
    return "", 204


@app.route("/restart", methods=["POST"])
@rate_limit
def restart():
    log_activity("RESTART server (with update)")

    global last_restart_time

    now = time.time()
    if now - last_restart_time < RESTART_COOLDOWN:
        remaining = int(RESTART_COOLDOWN - (now - last_restart_time))
        logger.warning(f"Restart cooldown active: {remaining}s remaining")
        return jsonify({"error": "Cooldown active", "remaining": remaining}), 429

    current_state = get_server_state()

    if current_state in ["starting", "stopping", "restarting"]:
        return "", 204

    if not set_server_state("restarting"):
        return jsonify({"error": "Invalid state transition"}), 400

    last_restart_time = now

    def restart_sequence():
        global last_update_status, last_update_time, last_update_output, current_build_id

        # First, forcefully kill any existing server processes
        logger.info("Killing any existing server processes before restart")
        kill_server()

        # Wait for processes to fully terminate
        max_wait = 20
        for i in range(max_wait):
            time.sleep(1)
            if not is_running():
                logger.info(f"Server processes terminated after {i+1} seconds")
                break
            if i == max_wait - 1:
                logger.warning("Server still running after initial kill, forcing again")
                kill_server()

        # Extra aggressive cleanup - kill any Wine processes running the server
        try:
            subprocess.run(
                ["pkill", "-9", "-f", "IcarusServer-Win64-Shipping.exe"],
                capture_output=True,
                timeout=5,
            )
            subprocess.run(
                ["pkill", "-9", "-f", "IcarusServer.exe"], capture_output=True, timeout=5
            )
            # Also kill any tmux sessions that might be holding the process
            subprocess.run(["tmux", "kill-session", "-t", "icarus"], capture_output=True, timeout=5)
        except Exception as e:
            logger.warning(f"Error during aggressive cleanup: {e}")

        # Final wait to ensure everything is dead
        time.sleep(3)

        # Verify nothing is running
        if is_running():
            logger.error("WARNING: Server process still detected after aggressive kill")
        else:
            logger.info("All server processes confirmed terminated")

        # Run update script (it handles updating)
        if os.path.exists(UPDATE_SCRIPT):
            try:
                logger.info("Running update script")
                result = subprocess.run(
                    [UPDATE_SCRIPT],
                    cwd=SERVER_DIR,
                    shell=True,
                    timeout=UPDATE_TIMEOUT,
                    capture_output=True,
                    text=True,
                )

                last_update_time = time.time()
                last_update_output = result.stdout + "\n" + result.stderr

                if result.returncode == 0:
                    last_update_status = "success"
                    logger.info("Update script completed successfully")

                    # Update the stored build ID after successful update
                    new_build = get_latest_build_id()
                    if new_build:
                        save_current_build_id(new_build)
                        current_build_id = new_build
                else:
                    last_update_status = f"failed (exit code {result.returncode})"
                    logger.error(f"Update script failed: {last_update_status}")

            except subprocess.TimeoutExpired:
                last_update_status = "timeout"
                last_update_time = time.time()
                logger.error("Update script timed out")
            except Exception as e:
                last_update_status = f"error: {str(e)}"
                last_update_time = time.time()
                logger.error(f"Update script error: {e}")
        else:
            # If update script doesn't exist, just note it
            last_update_status = "script not found"
            last_update_time = time.time()
            logger.warning("Update script not found, skipping update")

        # Ensure server is fully stopped before launching
        time.sleep(2)

        # Launch the server
        logger.info("Launching server after update")
        launch_server()

        # Wait for it to start
        logger.info(f"Waiting up to {START_TIMEOUT} seconds for server to start...")
        for i in range(START_TIMEOUT):
            time.sleep(1)
            running = is_running()
            if i % 5 == 0:  # Log every 5 seconds
                logger.info(f"Waiting for server... ({i}/{START_TIMEOUT}s) - Running: {running}")
            if running:
                set_server_state("running")
                logger.info(f"Server started successfully after restart (took {i+1} seconds)")
                return

        # If it never started, set to offline
        logger.error(
            f"Server failed to start after restart (timeout after {START_TIMEOUT} seconds)"
        )
        logger.error(f"Final check - is_running(): {is_running()}")
        set_server_state("offline")

    threading.Thread(target=restart_sequence, daemon=True).start()
    return "", 204


@app.route("/kill", methods=["POST"])
@rate_limit
def kill():
    log_activity("FORCE KILL server")

    if IS_DEV:
        # In dev mode, immediately kill and reset state
        kill_server()
        set_server_state("offline", force=True)
        logger.info("Dummy server force killed (simulated)")
        return "", 204

    if not is_running():
        set_server_state("offline", force=True)
        return "", 204

    # Force state to stopping (kill should always work)
    set_server_state("stopping", force=True)

    def kill_sequence():
        # First kill attempt
        logger.info("Executing force kill")
        if os.name == "nt":
            subprocess.call(f'taskkill /IM "{PROCESS_NAME}" /F', shell=True)
        else:
            subprocess.call(f'pkill -9 -f "{PROCESS_NAME}"', shell=True)

        # Wait for process to die
        for _ in range(15):
            time.sleep(1)
            if not is_running():
                set_server_state("offline")
                logger.info("Server killed successfully")
                return

        # If still running, try again
        logger.warning("Server still running, retrying kill")
        if os.name == "nt":
            subprocess.call(f'taskkill /IM "{PROCESS_NAME}" /F', shell=True)
        else:
            subprocess.call(f'pkill -9 -f "{PROCESS_NAME}"', shell=True)
        time.sleep(2)

        # Final check
        if not is_running():
            set_server_state("offline")
            logger.info("Server killed successfully on retry")
        else:
            logger.error("Server refused to die after multiple kill attempts")
            set_server_state("crashed")

    threading.Thread(target=kill_sequence, daemon=True).start()
    return "", 204


# ================= API =================


@app.route("/api/console")
def api_console():
    # Use the LOG_FILE from config instead of hardcoded path
    if not os.path.exists(LOG_FILE):
        return jsonify({"lines": []})

    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()[-200:]
        return jsonify({"lines": lines})
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/stats")
def api_stats():
    try:
        dev_mode_active = is_dev_mode()

        if dev_mode_active:
            # Return dummy stats for dev mode
            import random

            global dev_server_running

            current_state = get_server_state()
            running = dev_server_running

            return jsonify(
                {
                    "state": current_state,
                    "stats": {
                        "cpu": round(random.uniform(10, 30), 1),
                        "cpu_temp": round(random.uniform(45, 65), 1),
                        "ram_percent": round(random.uniform(40, 60), 1),
                        "ram_used": round(random.uniform(16, 24), 1),
                        "ram_total": 32.0,
                        "disk_percent": round(random.uniform(50, 70), 1),
                    },
                    "icarus": (
                        {
                            "cpu": round(random.uniform(5, 15), 2),
                            "cpu_raw": round(random.uniform(20, 60), 2),
                            "ram": round(random.uniform(2.5, 4.5), 2),
                        }
                        if running
                        else None
                    ),
                    "uptime": (
                        int(time.time() - dev_server_start_time)
                        if running and dev_server_start_time > 0
                        else 0
                    ),
                    "health": "good",
                    "players": random.randint(0, 8) if running else None,
                    "last_update": None,
                    "version": {
                        "current": "15000000",
                        "latest": "15000000",
                        "update_available": False,
                    },
                    "thread_health": thread_health,
                }
            )

        # Production mode
        stats = get_system_stats()
        running = is_running()
        current_state = get_server_state()

        # Get game version from logs if server is running
        game_version = get_game_version_from_logs() if running else None

        # Get player count if server is running
        player_count = get_player_count() if running else None

        return jsonify(
            {
                "state": current_state,
                "stats": stats,
                "icarus": get_icarus_usage() if running else None,
                "uptime": get_uptime() if running else 0,
                "health": get_health(stats),
                "players": player_count,
                "last_update": (
                    {"status": last_update_status, "time": last_update_time}
                    if last_update_status
                    else None
                ),
                "version": {
                    "current": current_build_id,
                    "latest": latest_build_id,
                    "update_available": (
                        current_build_id != latest_build_id
                        if (current_build_id and latest_build_id)
                        else None
                    ),
                    "game_version": game_version,
                },
                "thread_health": thread_health,
            }
        )
    except Exception as e:
        logger.error(f"Error in /api/stats: {e}", exc_info=True)
        return (
            jsonify(
                {
                    "error": str(e),
                    "state": "offline",
                    "stats": {
                        "cpu": 0,
                        "ram_percent": 0,
                        "ram_used": 0,
                        "ram_total": 0,
                        "disk_percent": 0,
                    },
                    "icarus": None,
                    "uptime": 0,
                    "health": "unknown",
                }
            ),
            500,
        )


@app.route("/api/check-update")
def api_check_update():
    """Manually trigger an update check"""
    update_available = check_for_updates()

    return jsonify(
        {
            "current_build": current_build_id,
            "latest_build": latest_build_id,
            "update_available": update_available,
        }
    )


@app.route("/api/update-output")
def api_update_output():
    """Get the output from the last update"""
    return jsonify(
        {
            "output": last_update_output if last_update_output else "No update output available",
            "status": last_update_status,
            "time": last_update_time,
        }
    )


@app.route("/api/logs")
def api_logs():
    dev_mode_active = is_dev_mode()

    if dev_mode_active:
        # Return dummy logs for dev mode
        from datetime import datetime

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        dummy_logs = [
            f"[{now}] [DEV MODE] This is a simulated log view",
            f"[{now}] [DEV MODE] No real server processes are running",
            f"[{now}] LogInit: Display: Running engine for game: Icarus (SIMULATED)",
            f"[{now}] LogNet: Display: Game Engine Initialized (SIMULATED)",
            f"[{now}] LogWorld: Display: Bringing World /Game/Maps/DedicatedServer up for play (SIMULATED)",
            f"[{now}] LogNet: Display: Server is listening on port 17777 (SIMULATED)",
            f"[{now}] LogOnline: Display: STEAM: Server logged in successfully (SIMULATED)",
            f"[{now}] LogLoad: Display: Game class is 'IcarusGameMode' (SIMULATED)",
            f"[{now}] LogNet: Display: Server ready for connections (SIMULATED)",
            f"[{now}] [DEV MODE] All server operations are simulated in development mode",
        ]
        return jsonify({"logs": dummy_logs})

    # Production mode - read real logs
    logs = read_log()

    if logs:
        crashed = detect_crash_signature(logs)

        if crashed:
            logs.insert(0, "[ERROR] ⚠ Crash signature detected in logs.\n")

        return jsonify({"logs": logs})

    return jsonify({"logs": ["[INFO] No log file found."]})


@app.route("/api/startup-logs")
def api_startup_logs():
    """Get startup script logs from the game server directory"""
    dev_mode_active = is_dev_mode()

    if dev_mode_active:
        # Return dummy startup logs for dev mode
        from datetime import datetime

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        dummy_logs = [
            f"[{now}] [DEV MODE] Simulated startup logs",
            f"[{now}] =========================================",
            f"[{now}] Starting Icarus Server",
            f"[{now}] =========================================",
            f"[{now}] All checks passed - starting server in tmux session 'icarus'",
            f"[{now}] Server started successfully in tmux session 'icarus'",
        ]
        return jsonify({"logs": dummy_logs})

    # Production mode - read startup.log from game directory
    startup_log_path = f"{SERVER_DIR}/startup.log"

    if not os.path.exists(startup_log_path):
        return jsonify({"logs": ["[INFO] No startup log file found."]})

    try:
        with open(startup_log_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = [
                line.rstrip("\n\r") for line in f.readlines()[-200:]
            ]  # Strip newlines and get last 200

        if not lines:
            return jsonify({"logs": ["[INFO] Startup log is empty."]})

        return jsonify({"logs": lines})
    except Exception as e:
        logger.error(f"Error reading startup log: {e}")
        return jsonify({"logs": [f"[ERROR] Failed to read startup log: {e}"]})


@app.route("/api/clear-startup-logs", methods=["POST"])
@rate_limit
def api_clear_startup_logs():
    """Clear the startup log file"""
    log_activity("CLEAR startup logs")

    dev_mode_active = is_dev_mode()

    if dev_mode_active:
        return jsonify({"success": True, "message": "Dev mode - no logs to clear"})

    startup_log_path = f"{SERVER_DIR}/startup.log"

    try:
        if os.path.exists(startup_log_path):
            # Archive the old log
            archive_path = f"{startup_log_path}.old"
            if os.path.exists(archive_path):
                os.remove(archive_path)
            os.rename(startup_log_path, archive_path)
            logger.info("Startup log archived and cleared")
            return jsonify({"success": True, "message": "Startup logs cleared and archived"})
        else:
            return jsonify({"success": True, "message": "No logs to clear"})
    except Exception as e:
        logger.error(f"Error clearing startup log: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/download-backup")
@rate_limit
def download_backup():
    """Download the Expedition 404 save file"""
    log_activity("DOWNLOAD backup")

    dev_mode_active = is_dev_mode()

    if dev_mode_active:
        # Return a dummy file in dev mode
        return make_response("Dev mode - no backup available", 404)

    backup_file = (
        f"{SERVER_DIR}/Icarus/Saved/PlayerData/DedicatedServer/Prospects/Expedition 404.json"
    )

    if not os.path.exists(backup_file):
        logger.warning(f"Backup file not found: {backup_file}")
        return make_response("Backup file not found", 404)

    try:
        from flask import send_file

        timestamp = time.strftime("%Y%m%d-%H%M%S")
        return send_file(
            backup_file,
            as_attachment=True,
            download_name=f"Expedition_404_backup_{timestamp}.json",
            mimetype="application/json",
        )
    except Exception as e:
        logger.error(f"Error downloading backup: {e}")
        return make_response(f"Error downloading backup: {e}", 500)


# ================= GOOGLE DRIVE BACKUP =================


def get_google_credentials():
    """Load Google OAuth credentials from token file"""
    if not os.path.exists(GOOGLE_TOKEN_FILE):
        return None

    try:
        with open(GOOGLE_TOKEN_FILE, "r") as f:
            token_data = json.load(f)

        credentials = Credentials(
            token=token_data.get("token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            scopes=["https://www.googleapis.com/auth/drive.file"],
        )

        return credentials
    except Exception as e:
        logger.error(f"Error loading Google credentials: {e}")
        return None


def save_google_credentials(credentials):
    """Save Google OAuth credentials to token file"""
    try:
        token_data = {
            "token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "token_uri": credentials.token_uri,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "scopes": credentials.scopes,
        }

        with open(GOOGLE_TOKEN_FILE, "w") as f:
            json.dump(token_data, f)

        # Secure the token file
        os.chmod(GOOGLE_TOKEN_FILE, 0o600)
        logger.info("Google credentials saved successfully")
    except Exception as e:
        logger.error(f"Error saving Google credentials: {e}")


@app.route("/backup-to-cloud")
def backup_to_cloud():
    """Initiate Google Drive backup - starts OAuth flow if needed"""
    log_activity("BACKUP to cloud initiated")

    # Check if we have valid credentials
    credentials = get_google_credentials()

    if credentials and credentials.valid:
        # We have valid credentials, proceed with backup
        return redirect(url_for("upload_to_drive"))
    elif credentials and credentials.expired and credentials.refresh_token:
        # Try to refresh the token
        try:
            from google.auth.transport.requests import Request

            credentials.refresh(Request())
            save_google_credentials(credentials)
            return redirect(url_for("upload_to_drive"))
        except Exception as e:
            logger.error(f"Error refreshing Google token: {e}")
            # Fall through to OAuth flow

    # Need to authenticate - start OAuth flow
    return redirect(url_for("google_oauth"))


@app.route("/google-oauth")
def google_oauth():
    """Start Google OAuth flow"""
    try:
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [GOOGLE_REDIRECT_URI],
                }
            },
            scopes=["https://www.googleapis.com/auth/drive.file"],
        )

        flow.redirect_uri = GOOGLE_REDIRECT_URI

        authorization_url, state = flow.authorization_url(
            access_type="offline", include_granted_scopes="true", prompt="consent"
        )

        session["oauth_state"] = state
        session["code_verifier"] = flow.code_verifier
        return redirect(authorization_url)
    except Exception as e:
        logger.error(f"Error starting OAuth flow: {e}")
        return make_response(f"Error starting OAuth: {e}", 500)


@app.route("/oauth2callback")
def oauth2callback():
    """Handle OAuth callback from Google"""
    try:
        state = session.get("oauth_state")

        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [GOOGLE_REDIRECT_URI],
                }
            },
            scopes=["https://www.googleapis.com/auth/drive.file"],
            state=state,
        )

        flow.redirect_uri = GOOGLE_REDIRECT_URI
        flow.fetch_token(
            authorization_response=request.url,
            code_verifier=session.get("code_verifier"),
        )

        credentials = flow.credentials
        save_google_credentials(credentials)

        log_activity("GOOGLE OAuth completed")

        # Now upload the backup
        return redirect(url_for("upload_to_drive"))
    except Exception as e:
        logger.error(f"Error in OAuth callback: {e}")
        return make_response(f"Error completing OAuth: {e}", 500)


@app.route("/upload-to-drive")
def upload_to_drive():
    """Upload backup file to Google Drive"""
    log_activity("UPLOAD backup to Google Drive")

    dev_mode_active = is_dev_mode()

    if dev_mode_active:
        return make_response("Dev mode - no backup available", 404)

    backup_file = (
        f"{SERVER_DIR}/Icarus/Saved/PlayerData/DedicatedServer/Prospects/Expedition 404.json"
    )

    if not os.path.exists(backup_file):
        logger.warning(f"Backup file not found: {backup_file}")
        return make_response("Backup file not found", 404)

    try:
        credentials = get_google_credentials()

        if not credentials or not credentials.valid:
            return make_response("Not authenticated with Google Drive", 401)

        # Build Drive API service
        service = build("drive", "v3", credentials=credentials)

        # Find or create the game subfolder
        game_folder_id = None

        # Search for existing "icarus" folder
        query = f"name='{GOOGLE_DRIVE_GAME_FOLDER_NAME}' and '{GOOGLE_DRIVE_PARENT_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = service.files().list(q=query, spaces="drive", fields="files(id, name)").execute()
        folders = results.get("files", [])

        if folders:
            game_folder_id = folders[0]["id"]
            logger.info(
                f"Found existing '{GOOGLE_DRIVE_GAME_FOLDER_NAME}' folder: {game_folder_id}"
            )
        else:
            # Create the subfolder
            folder_metadata = {
                "name": GOOGLE_DRIVE_GAME_FOLDER_NAME,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [GOOGLE_DRIVE_PARENT_FOLDER_ID],
            }
            folder = service.files().create(body=folder_metadata, fields="id").execute()
            game_folder_id = folder.get("id")
            logger.info(f"Created '{GOOGLE_DRIVE_GAME_FOLDER_NAME}' folder: {game_folder_id}")

        # Prepare file metadata
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        file_name = f"Expedition_404_backup_{timestamp}.json"

        file_metadata = {
            "name": file_name,
            "mimeType": "application/json",
            "parents": [game_folder_id],
        }

        # Upload file
        media = MediaFileUpload(backup_file, mimetype="application/json", resumable=True)

        file = (
            service.files()
            .create(body=file_metadata, media_body=media, fields="id,name,webViewLink")
            .execute()
        )

        logger.info(f"Backup uploaded to Google Drive: {file.get('name')} (ID: {file.get('id')})")
        log_activity(f"BACKUP uploaded to Drive: {file.get('name')}")

        # Return success page or redirect
        return f"""
        <html>
        <head>
            <title>Backup Successful</title>
            <style>
                body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; }}
                .success {{ color: #28a745; font-size: 24px; margin-bottom: 20px; }}
                .details {{ color: #666; margin-bottom: 30px; }}
                a {{ color: #007bff; text-decoration: none; }}
            </style>
        </head>
        <body>
            <div class="success">✓ Backup Uploaded Successfully!</div>
            <div class="details">
                <p><strong>File:</strong> {file.get('name')}</p>
                <p><strong>Location:</strong> {GOOGLE_DRIVE_GAME_FOLDER_NAME} folder</p>
                <p><a href="{file.get('webViewLink')}" target="_blank">View in Google Drive</a></p>
            </div>
            <a href="/">← Back to Control Panel</a>
        </body>
        </html>
        """
    except Exception as e:
        logger.error(f"Error uploading to Google Drive: {e}")
        return make_response(f"Error uploading to Google Drive: {e}", 500)


@app.route("/api/server-logs")
def api_server_logs():
    """Return recent system logs for display on system page."""
    try:
        lines = int(request.args.get("lines", 50))
        lines = min(lines, 500)  # Cap at 500 lines

        # Try reading syslog file directly
        if os.path.exists(SYSTEM_LOG_FILE_PATH) and os.access(SYSTEM_LOG_FILE_PATH, os.R_OK):
            with open(SYSTEM_LOG_FILE_PATH, "r", encoding="utf-8", errors="ignore") as f:
                all_lines = f.readlines()
                recent_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
            logs = [line.rstrip("\n") for line in recent_lines]
            return jsonify({"logs": logs, "count": len(logs), "source": "syslog"})

        # Syslog not readable, try journalctl instead
        try:
            result = subprocess.run(
                ["journalctl", "--no-pager", "-n", str(lines)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                logs = [line for line in result.stdout.strip().split("\n")]
                return jsonify({"logs": logs, "count": len(logs), "source": "journalctl"})
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        return jsonify(
            {
                "logs": [],
                "error": "Cannot read system logs. Add vertebra to adm group: sudo usermod -aG adm vertebra",
            }
        )
    except PermissionError as e:
        logger.error(f"Permission denied reading system logs: {e}")
        return (
            jsonify({"logs": [], "error": "Permission denied reading system logs"}),
            403,
        )
    except Exception as e:
        logger.error(f"Error reading server logs: {e}")
        return jsonify({"logs": [], "error": str(e)}), 500


@app.route("/api/history")
def api_history():
    return jsonify(list(history))


@app.route("/api/activity")
def api_activity():
    """Get recent user activity log"""
    return jsonify(list(activity_log))


# ================= HEALTH CHECK ENDPOINT =================


@app.route("/health-check-b8f3a9c2")
def health_check_bypass():
    """
    Simple health check endpoint for Cloudflare Worker monitoring.
    This endpoint should be configured with a Bypass policy in Cloudflare Access.
    Returns 200 if the Flask app is reachable.
    """
    return jsonify({"status": "ok"}), 200


@app.route("/health")
def health_check_public():
    """
    Public health check endpoint that doesn't require authentication.
    Can be accessed by monitoring services.
    """
    return jsonify({"status": "ok"}), 200


def _proxy_microservice(url):
    """Proxy a request to a local microservice and return its response."""
    try:
        if request.method == "GET":
            resp = requests.get(url, timeout=10)
        elif request.method == "POST":
            resp = requests.post(url, json=request.get_json(silent=True) or {}, timeout=10)
        elif request.method == "OPTIONS":
            response = make_response("", 204)
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            return response
        else:
            abort(405)
        flask_resp = make_response(resp.content, resp.status_code)
        flask_resp.headers["Content-Type"] = resp.headers.get("Content-Type", "application/json")
        flask_resp.headers["Access-Control-Allow-Origin"] = "*"
        return flask_resp
    except requests.exceptions.ConnectionError:
        return jsonify({"error": "Service unavailable"}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/check/<service>", methods=["GET", "POST", "OPTIONS"])
def check_service(service):
    """
    Health check endpoint for specific services on health.meduseld.io
    Checks if the target service is reachable
    """
    host = request.host.split(":")[0]

    # Only allow from health subdomain
    if host != "health.meduseld.io":
        abort(404)

    # System logs endpoint — return logs directly
    if service == "system-logs":
        return api_server_logs()

    # Map service names to their URLs
    service_urls = {
        "panel": "https://panel.meduseld.io/health-check-b8f3a9c2",
        "ssh": "https://ssh.meduseld.io/health-check-b8f3a9c2",
        "jellyfin": "https://jellyfin.meduseld.io/health-check-b8f3a9c2",
    }

    # Proxy to backup microservice
    if service == "backup":
        return _proxy_microservice("http://127.0.0.1:5003/backup")

    # Backup status polling
    if service == "backup-status":
        return _proxy_microservice("http://127.0.0.1:5003/status")

    # Proxy to reboot microservice
    if service == "reboot":
        return _proxy_microservice("http://127.0.0.1:5002/reboot")

    if service not in service_urls:
        return jsonify({"status": "error", "message": "Unknown service"}), 404

    try:
        # Try to reach the service
        response = requests.get(service_urls[service], timeout=5)
        if response.status_code == 200:
            return jsonify({"status": "ok"}), 200
        else:
            return jsonify({"status": "error", "code": response.status_code}), 502
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 502


# ================= JELLYFIN PROXY =================


@app.route("/<path:path>", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
def jellyfin_catch_all(path):
    """Catch-all for Jellyfin subdomain paths"""
    host = request.host.split(":")[0]

    # Only proxy if on jellyfin subdomain
    if host == "jellyfin.meduseld.io":
        return jellyfin_proxy(path)

    # For ssh and health subdomains, redirect to home
    if host in ["ssh.meduseld.io", "health.meduseld.io"]:
        return redirect("/")

    # For panel subdomain, let the home route handle it
    if host == "panel.meduseld.io":
        return redirect("/")

    # Otherwise 404
    abort(404)


# ================= BACKGROUND THREADS =================


def collect_stats():
    """Collect system stats periodically"""
    global thread_health

    logger.info("Stats collection thread started")
    thread_health["stats"]["alive"] = True

    while True:
        try:
            thread_health["stats"]["last_heartbeat"] = time.time()

            stats = get_system_stats()
            icarus = get_icarus_usage() if is_running() else None

            history.append(
                {
                    "timestamp": time.strftime("%H:%M"),
                    "system_cpu": stats["cpu"],
                    "system_ram": stats["ram_used"],
                    "icarus_cpu": icarus["cpu"] if icarus else 0,
                    "icarus_ram": icarus["ram"] if icarus else 0,
                }
            )

            time.sleep(STATS_COLLECTION_INTERVAL)

        except Exception as e:
            logger.error(f"Error in stats collection thread: {e}")
            time.sleep(STATS_COLLECTION_INTERVAL)


def check_updates_periodically():
    """Check for updates every hour"""
    global thread_health

    logger.info("Update check thread started")
    thread_health["updates"]["alive"] = True

    while True:
        try:
            thread_health["updates"]["last_heartbeat"] = time.time()
            check_for_updates()
            time.sleep(UPDATE_CHECK_INTERVAL)

        except Exception as e:
            logger.error(f"Error in update check thread: {e}")
            time.sleep(UPDATE_CHECK_INTERVAL)


def monitor_thread_health():
    """Monitor background thread health"""
    logger.info("Thread health monitor started")

    while True:
        try:
            time.sleep(60)  # Check every minute
            now = time.time()

            for thread_name, health in thread_health.items():
                if health["alive"]:
                    time_since_heartbeat = now - health["last_heartbeat"]
                    if time_since_heartbeat > 120:  # 2 minutes without heartbeat
                        logger.error(
                            f"Thread '{thread_name}' appears to be dead (no heartbeat for {time_since_heartbeat:.0f}s)"
                        )
                        health["alive"] = False

        except Exception as e:
            logger.error(f"Error in thread health monitor: {e}")
            time.sleep(60)


# ================= GRACEFUL SHUTDOWN =================


def signal_handler(sig, frame):
    """Handle shutdown signals - does NOT kill game server"""
    logger.info("Shutdown signal received")
    logger.info("Game server will continue running independently")
    logger.info("Control panel shutting down...")
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# ================= STARTUP =================


def initialize():
    """Initialize the application"""
    logger.info("=" * 50)
    logger.info("Icarus Server Control Panel Starting")
    logger.info("=" * 50)

    # Validate configuration
    if not validate_configuration():
        logger.error("Configuration validation failed")
        sys.exit(1)

    # Detect initial server state
    detect_initial_state()

    # Initialize version tracking
    logger.info("Checking for updates...")
    check_for_updates()

    # Start background threads
    threading.Thread(target=collect_stats, daemon=True).start()
    threading.Thread(target=monitor_server, daemon=True).start()
    threading.Thread(target=check_updates_periodically, daemon=True).start()
    threading.Thread(target=monitor_thread_health, daemon=True).start()

    logger.info("All background threads started")
    logger.info("Initialization complete")


# Initialize on import
initialize()

# ================= RUN =================

if __name__ == "__main__":
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG)
