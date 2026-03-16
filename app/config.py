# Meduseld Configuration
# Auto-detects environment: production (Ubuntu server) or development (local machine)

import os
import platform

# ================= ENVIRONMENT DETECTION =================
# Check if we're on the production server
# Set MEDUSELD_ENV=production in environment to enable production mode
MEDUSELD_ENV = os.environ.get("MEDUSELD_ENV", "production")
IS_DEV = MEDUSELD_ENV == "development"
IS_PRODUCTION = not IS_DEV

# ================= SERVER PATHS =================
if IS_PRODUCTION:
    # Production paths
    if os.name == "nt":
        # Windows production
        SERVER_DIR = r"C:\icarusserver"
        LAUNCH_EXE = f"{SERVER_DIR}\\IcarusServer.exe"
        LAUNCH_SCRIPT = "launch_server.bat"
        PROCESS_NAME = "IcarusServer-Win64-Shipping.exe"
        LOG_FILE = f"{SERVER_DIR}\\Icarus\\Saved\\Logs\\Icarus.log"
        UPDATE_SCRIPT = f"{SERVER_DIR}\\updateserver.bat"
        VERSION_FILE = f"{SERVER_DIR}\\version.txt"
    else:
        # Linux production (default)
        SERVER_DIR = "/srv/games/icarus"
        LAUNCH_EXE = f"{SERVER_DIR}/start.sh"
        LAUNCH_SCRIPT = f"{SERVER_DIR}/start.sh"
        PROCESS_NAME = "IcarusServer-Win64-Shipping.exe"
        LOG_FILE = f"{SERVER_DIR}/Icarus/Saved/Logs/Icarus.log"
        UPDATE_SCRIPT = f"{SERVER_DIR}/updateserver.sh"
        VERSION_FILE = f"{SERVER_DIR}/version.txt"
else:
    # Development paths (dummy paths for testing)
    SERVER_DIR = "/tmp/icarus_dev"
    LAUNCH_EXE = f"{SERVER_DIR}/dummy_server"
    LAUNCH_SCRIPT = "launch_server.sh"
    PROCESS_NAME = "IcarusServer-Linux-Shipping"
    LOG_FILE = f"{SERVER_DIR}/icarus.log"
    UPDATE_SCRIPT = f"{SERVER_DIR}/updateserver.sh"
    VERSION_FILE = f"{SERVER_DIR}/version.txt"

# Steam App ID for Icarus Dedicated Server
STEAM_APP_ID = "2089300"

# Server launch arguments
SERVER_ARGS = ["-SteamServerName=404localserver", "-Port=17777", "-QueryPort=27015", "-Log"]

# ================= SECURITY =================
ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    "meduseld.io",
    "panel.meduseld.io",
    "ssh.meduseld.io",
    "snowmane.meduseld.io",
]

# ================= RATE LIMITING =================
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX_REQUESTS = 10  # max requests per window
RESTART_COOLDOWN = 30  # seconds between restarts

# ================= TIMEOUTS =================
START_TIMEOUT = 60  # seconds to wait for server to start
STOP_TIMEOUT = 30  # seconds to wait for graceful shutdown
UPDATE_TIMEOUT = 600  # seconds to wait for update to complete

# ================= MONITORING =================
UPDATE_CHECK_INTERVAL = 3600  # seconds between update checks (1 hour)
STATS_COLLECTION_INTERVAL = 30  # seconds between stats collection
MONITOR_INTERVAL = 5  # seconds between server state checks

# ================= HEALTH THRESHOLDS =================
WARNING_CPU = 80  # percent
CRITICAL_CPU = 95  # percent
WARNING_RAM = 80  # percent
CRITICAL_RAM = 95  # percent
WARNING_DISK = 85  # percent
CRITICAL_DISK = 95  # percent

# ================= LOGGING =================
LOG_LEVEL = "DEBUG"  # DEBUG, INFO, WARNING, ERROR, CRITICAL

# Get the base directory (where this config.py is located)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_ROOT = os.path.dirname(BASE_DIR)  # Parent of app/ directory

# Use absolute paths for log files
if IS_PRODUCTION:
    LOG_FILE_PATH = "/srv/meduseld/logs/webserver.log"
    SYSTEM_LOG_FILE_PATH = "/var/log/syslog"  # Ubuntu system log
else:
    LOG_FILE_PATH = os.path.join(APP_ROOT, "logs", "webserver.log")
    SYSTEM_LOG_FILE_PATH = LOG_FILE_PATH  # In dev, use webserver log

LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
LOG_BACKUP_COUNT = 5

# ================= FLASK =================
FLASK_HOST = "0.0.0.0"
FLASK_PORT = 5001 if IS_DEV else 5000  # Use 5001 in dev to avoid macOS AirPlay conflict
FLASK_DEBUG = IS_DEV  # Auto-enable debug in dev mode
SECRET_KEY = (
    "dev-secret-key-change-in-production"
    if IS_DEV
    else os.environ.get("FLASK_SECRET_KEY", "change-me-in-production")
)

# ================= OIDC / AUTHENTICATION =================
OIDC_WORKER_URL = os.environ.get("OIDC_WORKER_URL", "https://discord-oidc.404-41f.workers.dev/")
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-jwt-secret-change-in-production")

# ================= DATABASE =================
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    (
        "postgresql://meduseld:meduseld@localhost:5432/meduseld_db"
        if not IS_DEV
        else "sqlite:///"
        + os.path.join(os.path.dirname(os.path.abspath(__file__)), "meduseld_dev.db")
    ),
)

# ================= GOOGLE DRIVE BACKUP =================
GOOGLE_CLIENT_ID = os.environ.get(
    "GOOGLE_CLIENT_ID", "282219108850-al0ddv2us3ovig0lg18lhae7m7ocemev.apps.googleusercontent.com"
)
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", None)
GOOGLE_REDIRECT_URI = "https://panel.meduseld.io/oauth2callback"
GOOGLE_DRIVE_PARENT_FOLDER_ID = "10Q0jIUL64QG8jitw4INtTjE7oHH-aBeQ"  # Main backup folder
GOOGLE_DRIVE_GAME_FOLDER_NAME = "icarus"  # Subfolder name for this game
GOOGLE_TOKEN_FILE = os.path.join(os.path.dirname(__file__), "google_token.json")

# ================= DEVELOPMENT MODE SETUP =================
if IS_DEV:
    # Create dummy files and directories for development testing
    os.makedirs(SERVER_DIR, exist_ok=True)

    # Create dummy log directory
    log_dir = os.path.dirname(LOG_FILE)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    # Create dummy log file with sample content
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w") as f:
            f.write("[2026-03-02 22:00:00] LogInit: Display: Running engine for game: Icarus\n")
            f.write("[2026-03-02 22:00:01] LogNet: Display: Game Engine Initialized\n")
            f.write(
                "[2026-03-02 22:00:02] LogWorld: Display: Bringing World /Game/Maps/DedicatedServer up for play\n"
            )
            f.write("[2026-03-02 22:00:03] LogNet: Display: Server is listening on port 17777\n")
            f.write(
                "[2026-03-02 22:00:04] LogOnline: Display: STEAM: Server logged in successfully\n"
            )
            f.write("[2026-03-02 22:00:05] LogLoad: Display: Game class is 'IcarusGameMode'\n")
            f.write("[2026-03-02 22:00:06] LogNet: Display: Server ready for connections\n")

    # Create dummy version file
    if not os.path.exists(VERSION_FILE):
        with open(VERSION_FILE, "w") as f:
            f.write("15000000")  # Dummy build ID

    # Create dummy server executable that appends to log when running
    if not os.path.exists(LAUNCH_EXE):
        with open(LAUNCH_EXE, "w") as f:
            f.write("#!/bin/bash\n")
            f.write("# Dummy Icarus server for development\n")
            f.write(
                f"echo '[' $(date '+%Y-%m-%d %H:%M:%S') '] LogNet: Display: Dummy server started' >> {LOG_FILE}\n"
            )
            f.write(
                f"echo '[' $(date '+%Y-%m-%d %H:%M:%S') '] LogOnline: Display: Server is now accepting players' >> {LOG_FILE}\n"
            )
            f.write("# Sleep to simulate running server\n")
            f.write("sleep 3600\n")
        os.chmod(LAUNCH_EXE, 0o755)

    # Create dummy update script
    if not os.path.exists(UPDATE_SCRIPT):
        with open(UPDATE_SCRIPT, "w") as f:
            f.write("#!/bin/bash\n")
            f.write("echo 'Connecting to Steam servers...'\n")
            f.write("sleep 1\n")
            f.write("echo 'Checking for updates...'\n")
            f.write("sleep 1\n")
            f.write("echo 'Downloading update...'\n")
            f.write("sleep 2\n")
            f.write("echo 'Success! App 2089300 fully installed.'\n")
            f.write(f"echo '22078137' > {VERSION_FILE}\n")
            f.write("exit 0\n")
        os.chmod(UPDATE_SCRIPT, 0o755)

    print(f"[DEV MODE] Running in development mode")
    print(f"[DEV MODE] Server directory: {SERVER_DIR}")
    print(f"[DEV MODE] Game server controls will use dummy processes")
    print(f"[DEV MODE] You can test all buttons - they will start/stop dummy processes")
else:
    print(f"[PRODUCTION] Running in production mode")
    print(f"[PRODUCTION] Server directory: {SERVER_DIR}")
