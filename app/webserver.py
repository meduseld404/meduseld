from flask import Flask, g, render_template, request, jsonify, abort, make_response, redirect
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
from collections import deque
from functools import wraps
from datetime import datetime
import jwt

# Get the directory where this script is located
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    static_folder=os.path.join(BASE_DIR, "static"),
    template_folder=os.path.join(BASE_DIR, "templates")
)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

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
    SERVER_DIR = "/home/vertebra/games/icarus"
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
    LOG_FILE_PATH = "webserver.log"
    LOG_LEVEL = "INFO"
    FLASK_HOST = "0.0.0.0"
    FLASK_PORT = 5000
    FLASK_DEBUG = False

# ================= LOGGING =================

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE_PATH),
        logging.StreamHandler()
    ]
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

# Session-based dev mode tracking (keyed by session ID or IP)
session_dev_mode = {}

# Rate limiting
request_history = deque(maxlen=100)

# Thread health tracking
thread_health = {
    "monitor": {"alive": False, "last_heartbeat": 0},
    "stats": {"alive": False, "last_heartbeat": 0},
    "updates": {"alive": False, "last_heartbeat": 0}
}

ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    "192.168.1.175",
    "meduseld.io",
    "menu.meduseld.io",
    "panel.meduseld.io",
    "ssh.meduseld.io",
    "terminal.meduseld.io",
    "jellyfin.meduseld.io",
    "health.meduseld.io"
]

# Valid state transitions
VALID_TRANSITIONS = {
    "offline": ["starting", "crashed"],
    "starting": ["running", "offline", "crashed"],
    "running": ["stopping", "restarting", "crashed"],
    "stopping": ["offline", "crashed"],
    "restarting": ["running", "offline", "crashed"],
    "crashed": ["starting", "offline"]
}

# ================= UTILITIES =================

def is_dev_mode():
    """Check if current request is in development mode"""
    try:
        return IS_DEV or (request and request.args.get('env') == 'development')
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
    
    activity_log.append({
        "timestamp": timestamp,
        "ip": ip,
        "action": action
    })
    
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

@app.before_request
def load_user():
    """Load user authentication - dev mode uses fake Discord user"""
    
    # Development mode: Use hardcoded Discord user
    if IS_DEV:
        g.user = "dev@meduseld.io"
        g.discord_user = {
            "id": "123456789012345678",
            "username": "DevUser",
            "discriminator": "0001",
            "email": "dev@meduseld.io",
            "verified": True,
            "global_name": "Development User",
            "avatar": None,
            "guilds": ["924788704529252353"],  # Your Discord server ID
            "roles": {
                "roles:924788704529252353": ["1234567890", "0987654321"]
            }
        }
        logger.debug(f"Dev mode: Using fake Discord user: {g.discord_user['username']}")
        return
    
    # Production: Check Cloudflare Access header
    email = request.headers.get("Cf-Access-Authenticated-User-Email")
    
    if email:
        g.user = email
        # TODO: Fetch full Discord user data from database/session
        # For now, just store basic info from email
        g.discord_user = {
            "email": email,
            "username": email.split('@')[0],
            "verified": True
        }
        logger.debug(f"Authenticated user: {email}")
    else:
        g.user = None
        g.discord_user = None

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
        for proc in psutil.process_iter(["name", "cmdline", "pid", "exe"]):
            # For Wine processes, check the command line for the exe name
            if proc.info["cmdline"]:
                try:
                    # cmdline is a list, convert each element to string and join
                    cmdline_str = " ".join(str(arg) for arg in proc.info["cmdline"])
                    
                    # Skip tmux, xvfb-run, wine launcher processes - we want the actual game server
                    if proc.info["name"] in ["tmux", "tmux: server", "xvfb-run", "sh", "bash", "wine", "wine64", "wineserver", "start.exe"]:
                        continue
                    
                    if "IcarusServer-Win64-Shipping.exe" in cmdline_str:
                        logger.debug(f"Found server by cmdline: PID {proc.info['pid']}, name: {proc.info['name']}, exe: {proc.info.get('exe', 'N/A')}")
                        return True
                except Exception as e:
                    # Skip processes we can't access
                    continue
        
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
            if 'LAUNCH_SCRIPT' in globals() and os.path.exists(LAUNCH_SCRIPT):
                # Use the script which uses proper process isolation
                subprocess.Popen(
                    [LAUNCH_SCRIPT],
                    shell=True,
                    cwd=os.path.dirname(os.path.abspath(LAUNCH_SCRIPT)) if os.path.dirname(LAUNCH_SCRIPT) else ".",
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                logger.info("Server launched via script (independent process)")
            else:
                # Fallback to direct launch
                subprocess.Popen(
                    [LAUNCH_EXE] + SERVER_ARGS,
                    cwd=SERVER_DIR,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                logger.info("Server launched directly (independent process)")
    except Exception as e:
        logger.error(f"Failed to launch server: {e}")
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
            if os.name == 'nt':
                subprocess.call(f'taskkill /IM "{PROCESS_NAME}" /F', shell=True)
            else:
                # On Linux, find and kill the Wine process running the server
                killed = False
                try:
                    for proc in psutil.process_iter(["name", "cmdline", "pid"]):
                        # Check if this is the Icarus server process
                        if proc.info["cmdline"]:
                            for arg in proc.info["cmdline"]:
                                if arg and ("IcarusServer-Win64-Shipping.exe" in arg or "IcarusServer.exe" in arg):
                                    logger.info(f"Killing process {proc.info['pid']}: {proc.info['name']}")
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
                if partition.fstype and partition.fstype not in ['squashfs', 'tmpfs', 'devtmpfs']:
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

        return {
            "cpu": cpu,
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
                "ram": round(random.uniform(2.5, 4.5), 2)
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
                        
                        logger.debug(f"Using cached process {_icarus_process_cache['pid']}: CPU={cpu_raw}%, RAM={ram_gb}GB")
                        
                        return {
                            "cpu": cpu_norm,
                            "cpu_raw": cpu_raw,
                            "ram": ram_gb
                        }
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
                            logger.debug(f"Checking PID {proc.info['pid']}: {proc.info['name']} - {cmdline_str[:100]}")
                        
                        # Skip tmux, xvfb-run, wine launcher processes - we want the actual game server
                        if proc.info["name"] in ["tmux", "tmux: server", "xvfb-run", "sh", "bash", "wine", "wine64", "wineserver", "start.exe"]:
                            continue
                        
                        if "IcarusServer-Win64-Shipping.exe" in cmdline_str:
                            logger.info(f"Found Icarus server process: PID {proc.info['pid']}, name: {proc.info['name']}, exe: {proc.info.get('exe', 'N/A')}")
                            p = psutil.Process(proc.info['pid'])
                            
                            # Initialize CPU measurement (non-blocking)
                            p.cpu_percent(interval=None)
                            
                            # Cache the process
                            _icarus_process_cache = {
                                "pid": proc.info['pid'],
                                "process": p,
                                "last_check": now
                            }
                            
                            # Get RAM immediately
                            ram_gb = round(p.memory_info().rss / (1024**3), 2)
                            
                            logger.info(f"Initialized stats for PID {proc.info['pid']}: RAM={ram_gb}GB (CPU will be available on next call)")
                            
                            return {
                                "cpu": 0.0,  # Will be accurate on next call
                                "cpu_raw": 0.0,
                                "ram": ram_gb
                            }
                    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                        continue
                    except Exception as e:
                        logger.error(f"Error getting process stats for PID {proc.info.get('pid')}: {e}")
                        continue
            
            logger.warning("Icarus server process not found in process list")
    except Exception as e:
        logger.error(f"Error iterating processes: {e}", exc_info=True)
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
                    if proc.info["name"] in ["tmux", "tmux: server", "xvfb-run", "sh", "bash", "wine", "wine64", "wineserver", "start.exe"]:
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
    if stats["cpu"] > CRITICAL_CPU or stats["ram_percent"] > CRITICAL_RAM or stats["disk_percent"] > CRITICAL_DISK:
        return "critical"
    if stats["cpu"] > WARNING_CPU or stats["ram_percent"] > WARNING_RAM or stats["disk_percent"] > WARNING_DISK:
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
                    match = re.search(r'Version:\s+(.+?)\s+<====', line)
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
    global thread_health
    
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
                logger.warning("Server crashed - process not found")
                set_server_state("crashed")
                log_buffer.extend(read_log())
            
            elif current_state == "crashed" and running:
                logger.info("Server recovered from crash")
                set_server_state("running")
        
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
            if key.lower() not in ['host', 'connection', 'accept-encoding']:
                headers[key] = value
        
        # Make request to Jellyfin
        resp = requests.request(
            method=request.method,
            url=jellyfin_url,
            headers=headers,
            data=request.get_data(),
            cookies=request.cookies,
            allow_redirects=False,
            timeout=30
        )
        
        # Prepare response headers and rewrite Location header
        excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        response_headers = []
        
        for name, value in resp.headers.items():
            if name.lower() not in excluded_headers:
                # Rewrite Location header to use proxy domain
                if name.lower() == 'location':
                    value = value.replace('http://71.191.152.254:8096', 'https://jellyfin.meduseld.io')
                response_headers.append((name, value))
        
        # Rewrite content for HTML/JS/JSON responses
        content = resp.content
        content_type = resp.headers.get('content-type', '').lower()
        
        if any(ct in content_type for ct in ['text/html', 'application/javascript', 'application/json', 'text/javascript']):
            try:
                text_content = content.decode('utf-8')
                # Replace backend URL references
                text_content = text_content.replace('http://71.191.152.254:8096', 'https://jellyfin.meduseld.io')
                text_content = text_content.replace('71.191.152.254:8096', 'jellyfin.meduseld.io')
                content = text_content.encode('utf-8')
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

# ================= AUTH ENDPOINTS =================

@app.route('/api/auth/profile', methods=['OPTIONS', 'GET'])
def get_profile():
    """Get authenticated user profile with Discord data"""
    if request.method == 'OPTIONS':
        return '', 204
    
    if hasattr(g, 'user') and g.user:
        return jsonify({
            'authenticated': True,
            'user': g.discord_user if hasattr(g, 'discord_user') and g.discord_user else {
                'email': g.user,
                'username': g.user.split('@')[0]
            }
        })
    else:
        return jsonify({
            'authenticated': False
        })

@app.route('/me', methods=['OPTIONS', 'GET'])
def auth_me():
    """Auth endpoint for checking authentication status"""
    if request.method == 'OPTIONS':
        return '', 204
    
    if hasattr(g, 'user') and g.user:
        return jsonify({
            'authenticated': True,
            'user': g.discord_user if hasattr(g, 'discord_user') and g.discord_user else {
                'email': g.user,
                'username': g.user.split('@')[0]
            }
        })
    else:
        return jsonify({
            'authenticated': False
        })

# ================= ROUTES =================

@app.route("/", methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS', 'HEAD'])
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
        dev_mode=IS_DEV or dev_mode_active
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
    
    launch_server()

    def wait():
        for _ in range(START_TIMEOUT):
            time.sleep(1)
            if is_running():
                set_server_state("running")
                return
        
        # Failed to start
        logger.error("Server failed to start within timeout")
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
        return jsonify({
            "error": "Cooldown active",
            "remaining": remaining
        }), 429

    current_state = get_server_state()
    
    if current_state in ["starting", "stopping", "restarting"]:
        return "", 204
    
    if not set_server_state("restarting"):
        return jsonify({"error": "Invalid state transition"}), 400

    last_restart_time = now

    def restart_sequence():
        global last_update_status, last_update_time, last_update_output, current_build_id
        
        # Run update script (it handles killing the server and updating)
        if os.path.exists(UPDATE_SCRIPT):
            try:
                logger.info("Running update script")
                result = subprocess.run(
                    [UPDATE_SCRIPT],
                    cwd=SERVER_DIR,
                    shell=True,
                    timeout=UPDATE_TIMEOUT,
                    capture_output=True,
                    text=True
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
            # If update script doesn't exist, manually kill the server
            last_update_status = "script not found"
            last_update_time = time.time()
            logger.warning("Update script not found, manually killing server")
            
            kill_server()
            for _ in range(15):
                time.sleep(1)
                if not is_running():
                    break
            if is_running():
                kill_server()
                time.sleep(2)
        
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
        logger.error(f"Server failed to start after restart (timeout after {START_TIMEOUT} seconds)")
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
        if os.name == 'nt':
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
        if os.name == 'nt':
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
            
            return jsonify({
                "state": current_state,
                "stats": {
                    "cpu": round(random.uniform(10, 30), 1),
                    "ram_percent": round(random.uniform(40, 60), 1),
                    "ram_used": round(random.uniform(16, 24), 1),
                    "ram_total": 32.0,
                    "disk_percent": round(random.uniform(50, 70), 1)
                },
                "icarus": {
                    "cpu": round(random.uniform(5, 15), 2),
                    "cpu_raw": round(random.uniform(20, 60), 2),
                    "ram": round(random.uniform(2.5, 4.5), 2)
                } if running else None,
                "uptime": int(time.time() - dev_server_start_time) if running and dev_server_start_time > 0 else 0,
                "health": "good",
                "last_update": None,
                "version": {
                    "current": "15000000",
                    "latest": "15000000",
                    "update_available": False
                },
                "thread_health": thread_health
            })
        
        # Production mode
        stats = get_system_stats()
        running = is_running()
        current_state = get_server_state()
        
        # Get game version from logs if server is running
        game_version = get_game_version_from_logs() if running else None

        return jsonify({
            "state": current_state,
            "stats": stats,
            "icarus": get_icarus_usage() if running else None,
            "uptime": get_uptime() if running else 0,
            "health": get_health(stats),
            "last_update": {
                "status": last_update_status,
                "time": last_update_time
            } if last_update_status else None,
            "version": {
                "current": current_build_id,
                "latest": latest_build_id,
                "update_available": current_build_id != latest_build_id if (current_build_id and latest_build_id) else None,
                "game_version": game_version
            },
            "thread_health": thread_health
        })
    except Exception as e:
        logger.error(f"Error in /api/stats: {e}", exc_info=True)
        return jsonify({
            "error": str(e),
            "state": "offline",
            "stats": {"cpu": 0, "ram_percent": 0, "ram_used": 0, "ram_total": 0, "disk_percent": 0},
            "icarus": None,
            "uptime": 0,
            "health": "unknown"
        }), 500

@app.route("/api/check-update")
def api_check_update():
    """Manually trigger an update check"""
    update_available = check_for_updates()
    
    return jsonify({
        "current_build": current_build_id,
        "latest_build": latest_build_id,
        "update_available": update_available
    })

@app.route("/api/update-output")
def api_update_output():
    """Get the output from the last update"""
    return jsonify({
        "output": last_update_output if last_update_output else "No update output available",
        "status": last_update_status,
        "time": last_update_time
    })

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
            f"[{now}] [DEV MODE] All server operations are simulated in development mode"
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

@app.route("/check/<service>")
def check_service(service):
    """
    Health check endpoint for specific services on health.meduseld.io
    Checks if the target service is reachable
    """
    host = request.host.split(":")[0]
    
    # Only allow from health subdomain
    if host != "health.meduseld.io":
        abort(404)
    
    # Map service names to their URLs
    service_urls = {
        'panel': 'https://panel.meduseld.io/health-check-b8f3a9c2',
        'ssh': 'https://ssh.meduseld.io/health-check-b8f3a9c2',
        'jellyfin': 'https://jellyfin.meduseld.io/health-check-b8f3a9c2'
    }
    
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

@app.route("/<path:path>", methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS', 'HEAD'])
def jellyfin_catch_all(path):
    """Catch-all for Jellyfin subdomain paths"""
    host = request.host.split(":")[0]
    
    # Only proxy if on jellyfin subdomain
    if host == "jellyfin.meduseld.io":
        return jellyfin_proxy(path)
    
    # For other subdomains, redirect to home
    if host in ["panel.meduseld.io", "ssh.meduseld.io", "health.meduseld.io"]:
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

            history.append({
                "timestamp": time.strftime("%H:%M"),
                "system_cpu": stats["cpu"],
                "system_ram": stats["ram_used"],
                "icarus_cpu": icarus["cpu"] if icarus else 0,
                "icarus_ram": icarus["ram"] if icarus else 0
            })

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
                        logger.error(f"Thread '{thread_name}' appears to be dead (no heartbeat for {time_since_heartbeat:.0f}s)")
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
