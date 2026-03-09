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
    if os.name == 'nt':
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
        SERVER_DIR = "/home/vertebra/games/icarus"
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
    VERSION_FILE = f"{SERVER_DIR}/version.txt"

# Steam App ID for Icarus Dedicated Server
STEAM_APP_ID = "2089300"

# Server launch arguments
SERVER_ARGS = [
    "-SteamServerName=404localserver",
    "-Port=17777",
    "-QueryPort=27015",
    "-Log"
]

# ================= SECURITY =================
ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    "meduseld.io",
    "panel.meduseld.io",
    "ssh.meduseld.io"
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
LOG_LEVEL = "INFO"  # DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_FILE_PATH = "logs/webserver.log"
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
LOG_BACKUP_COUNT = 5

# ================= FLASK =================
FLASK_HOST = "0.0.0.0"
FLASK_PORT = 5001 if IS_DEV else 5000  # Use 5001 in dev to avoid macOS AirPlay conflict
FLASK_DEBUG = IS_DEV  # Auto-enable debug in dev mode
SECRET_KEY = "dev-secret-key-change-in-production" if IS_DEV else os.environ.get("FLASK_SECRET_KEY", "change-me-in-production")

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
        with open(LOG_FILE, 'w') as f:
            f.write("[2026-03-02 22:00:00] LogInit: Display: Running engine for game: Icarus\n")
            f.write("[2026-03-02 22:00:01] LogNet: Display: Game Engine Initialized\n")
            f.write("[2026-03-02 22:00:02] LogWorld: Display: Bringing World /Game/Maps/DedicatedServer up for play\n")
            f.write("[2026-03-02 22:00:03] LogNet: Display: Server is listening on port 17777\n")
            f.write("[2026-03-02 22:00:04] LogOnline: Display: STEAM: Server logged in successfully\n")
            f.write("[2026-03-02 22:00:05] LogLoad: Display: Game class is 'IcarusGameMode'\n")
            f.write("[2026-03-02 22:00:06] LogNet: Display: Server ready for connections\n")
    
    # Create dummy version file
    if not os.path.exists(VERSION_FILE):
        with open(VERSION_FILE, 'w') as f:
            f.write("15000000")  # Dummy build ID
    
    # Create dummy server executable that appends to log when running
    if not os.path.exists(LAUNCH_EXE):
        with open(LAUNCH_EXE, 'w') as f:
            f.write("#!/bin/bash\n")
            f.write("# Dummy Icarus server for development\n")
            f.write(f"echo '[' $(date '+%Y-%m-%d %H:%M:%S') '] LogNet: Display: Dummy server started' >> {LOG_FILE}\n")
            f.write(f"echo '[' $(date '+%Y-%m-%d %H:%M:%S') '] LogOnline: Display: Server is now accepting players' >> {LOG_FILE}\n")
            f.write("# Sleep to simulate running server\n")
            f.write("sleep 3600\n")
        os.chmod(LAUNCH_EXE, 0o755)
    
    # Create dummy update script
    if not os.path.exists(UPDATE_SCRIPT):
        with open(UPDATE_SCRIPT, 'w') as f:
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

