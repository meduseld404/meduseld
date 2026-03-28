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
from datetime import datetime, timezone, date, timedelta
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


# Validate that an Origin header belongs to *.meduseld.io
def _is_valid_meduseld_origin(origin):
    """Check if origin is a valid meduseld.io subdomain using URL parsing."""
    if not origin:
        return False
    try:
        from urllib.parse import urlparse

        parsed = urlparse(origin)
        host = parsed.hostname or ""
        return host == "meduseld.io" or host.endswith(".meduseld.io")
    except Exception:
        return False


# Add CORS headers for API endpoints
@app.after_request
def add_cors_headers(response):
    # Allow system.meduseld.io and services.meduseld.io to access API
    origin = request.headers.get("Origin")
    if _is_valid_meduseld_origin(origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, Authorization, X-CF-Authorization"
        )
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Max-Age"] = "3600"
    return response


# Explicit OPTIONS handler for cross-origin API endpoints.
# Cloudflare Access may block preflight requests that don't carry cookies,
# so this only helps if the request actually reaches Flask.
@app.route("/api/me", methods=["OPTIONS"])
@app.route("/api/sync-identity", methods=["OPTIONS"])
@app.route("/api/jellyfin-auth", methods=["OPTIONS"])
@app.route("/api/admin/users", methods=["OPTIONS"])
@app.route("/api/admin/users/<int:user_id>", methods=["OPTIONS"])
def cors_preflight(**kwargs):
    response = make_response("", 204)
    origin = request.headers.get("Origin")
    if _is_valid_meduseld_origin(origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, Authorization, X-CF-Authorization"
        )
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Max-Age"] = "3600"
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
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "change-me-in-production")
    DATABASE_URL = os.environ.get(
        "DATABASE_URL", "postgresql://meduseld:meduseld@localhost:5432/meduseld_db"
    )
    JELLYFIN_INTERNAL_URL = os.environ.get("JELLYFIN_INTERNAL_URL", "http://localhost:8096")
    JELLYFIN_API_KEY = os.environ.get("JELLYFIN_API_KEY", None)

# Set Flask secret key for sessions (required for OAuth)
app.secret_key = SECRET_KEY

# ================= DATABASE =================
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

from database import init_db

init_db(app)

# ================= SOCKETIO (Trivia Multiplayer) =================
from trivia_ws import socketio, register_trivia_rest

socketio.init_app(app)
register_trivia_rest(app)


# ================= SOCKETIO (Picker Live Updates) =================
@socketio.on("connect", namespace="/picker")
def picker_on_connect():
    logger.info("Picker client connected: %s", request.sid)


@socketio.on("disconnect", namespace="/picker")
def picker_on_disconnect():
    logger.info("Picker client disconnected: %s", request.sid)


# ================= SOCKETIO (Remote Desktop Signaling) =================
from remote_ws import register_remote_ws

register_remote_ws(socketio)


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
    except Exception:
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
    # Priority: Cf-Access-Jwt-Assertion header > CF_Authorization cookie
    cf_token = request.headers.get("Cf-Access-Jwt-Assertion")
    if not cf_token:
        # Fallback: try the CF_Authorization cookie. This cookie is set by
        # Cloudflare Access on .meduseld.io, so it's available on cross-origin
        # requests from services.meduseld.io, system.meduseld.io, etc.
        # This is the key mechanism that lets /api/me and /api/sync-identity
        # work when called from static pages on other subdomains.
        cf_token = request.cookies.get("CF_Authorization")

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

        # Custom OIDC claims from herugrim are available under the
        # 'custom' key in the JWT. This contains the real Discord
        # profile data (id, username, global_name, avatar, is_admin).
        custom = payload.get("custom", {})
        discord_user = custom.get("discord_user", {}) if isinstance(custom, dict) else {}

        if discord_user and discord_user.get("id"):
            # Use real Discord data from the JWT
            discord_id = str(discord_user["id"])
            username = discord_user.get("username", "")
            display_name = discord_user.get("global_name", "") or username
            avatar_hash = discord_user.get("avatar", "")
            logger.info(f"Using Discord data from JWT: {username} ({discord_id})")
        else:
            # Fallback: use CF JWT fields (email-derived)
            username = payload.get("preferred_username", "") or (
                email.split("@")[0] if email else "unknown"
            )
            display_name = payload.get("name", "") or username
            avatar_hash = None
            discord_id = cf_user_id
            logger.info(f"No discord_user in JWT, using fallback: {username}")

        if not discord_id:
            logger.warning("JWT decoded but no user identifier found in claims")
            g.user = None
            return

        from models import User

        user = User.get_or_create(
            discord_id=discord_id,
            username=username,
            display_name=display_name,
            avatar_hash=avatar_hash,
            email=email,
        )

        # Sync admin role from Discord — is_admin in the JWT is set by
        # herugrim based on the Discord admin role, so it's authoritative.
        jwt_is_admin = discord_user.get("is_admin", False) if discord_user else False
        if jwt_is_admin and user.role != "admin":
            user.role = "admin"
            from database import db

            db.session.commit()
            logger.info(f"Auto-promoted {username} to admin from Discord role")
        elif not jwt_is_admin and user.role == "admin":
            user.role = "user"
            from database import db

            db.session.commit()
            logger.info(f"Auto-demoted {username} to user (Discord role removed)")

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
    Creates or updates the user's DB record with real Discord ID, username, and avatar.
    """
    data = request.get_json()
    if not data or not data.get("discord_id"):
        return jsonify({"error": "Missing discord_id"}), 400

    from models import User
    from database import db

    discord_id = str(data["discord_id"])
    username = data.get("username", "")
    display_name = data.get("display_name", "")
    avatar_hash = data.get("avatar_hash", "")

    # Get email from the authenticated user context (JWT) if available
    email = None
    if hasattr(g, "user") and g.user is not None:
        email = g.user.email
    elif "user" in session:
        email = session["user"].get("email")

    # Use get_or_create so first-time users visiting only static pages
    # still get a DB record created via this sync call.
    user = User.get_or_create(
        discord_id=discord_id,
        username=username,
        display_name=display_name,
        avatar_hash=avatar_hash,
        email=email,
    )

    # Sync admin role from Discord — the is_admin flag comes from herugrim's
    # check of the Discord admin role, so it's authoritative.
    is_admin = data.get("is_admin", False)
    if is_admin and user.role != "admin":
        user.role = "admin"
        db.session.commit()
        logger.info(f"Auto-promoted {user.username} to admin from Discord role")
    elif not is_admin and user.role == "admin":
        user.role = "user"
        db.session.commit()
        logger.info(f"Auto-demoted {user.username} to user (Discord role removed)")

    try:
        session["user"] = user.to_dict()
        logger.info(f"Synced Discord identity: {user.discord_id} ({user.username})")
        return jsonify({"synced": True, "user": user.to_dict()}), 200
    except Exception as e:
        logger.error(f"Error syncing identity: {e}")
        return jsonify({"error": "Sync failed"}), 500


# ================= JELLYFIN AUTH =================


@app.route("/api/jellyfin-auth")
@require_auth
def api_jellyfin_auth():
    """Auto-provision a Jellyfin account and return an auth token.
    Creates a Jellyfin user on first access, then authenticates and redirects."""
    return _jellyfin_auth_inner()


def _jellyfin_auth_inner(retry=False):
    """Inner implementation with retry support."""
    import secrets
    from models import User
    from database import db

    user = g.user
    jellyfin_url = config.JELLYFIN_INTERNAL_URL
    api_key = config.JELLYFIN_API_KEY

    if not api_key:
        return jsonify({"error": "Jellyfin integration not configured"}), 503

    jf_headers = {"X-Emby-Token": api_key}
    display_name = user.display_name or user.username
    jf_username = None  # The actual Jellyfin username (may differ in case)

    try:
        # If we don't have a Jellyfin user ID stored, try to find or create one
        if not user.jellyfin_user_id:
            # Search for existing Jellyfin user by name (case-insensitive)
            resp = requests.get(f"{jellyfin_url}/Users", headers=jf_headers, timeout=10)
            resp.raise_for_status()
            jf_users = resp.json()

            existing = next(
                (u for u in jf_users if u["Name"].lower() == display_name.lower()), None
            )

            if existing:
                # Found existing user — store their ID and use their actual username
                user.jellyfin_user_id = existing["Id"]
                jf_username = existing["Name"]
                # Generate a new password and reset it
                new_pw = secrets.token_urlsafe(24)
                requests.post(
                    f"{jellyfin_url}/Users/{existing['Id']}/Password",
                    headers={**jf_headers, "Content-Type": "application/json"},
                    json={"NewPw": new_pw},
                    timeout=10,
                )
                user.jellyfin_password = new_pw
                db.session.commit()
            else:
                # Create new Jellyfin user
                new_pw = secrets.token_urlsafe(24)
                resp = requests.post(
                    f"{jellyfin_url}/Users/New",
                    headers={**jf_headers, "Content-Type": "application/json"},
                    json={"Name": display_name, "Password": new_pw},
                    timeout=10,
                )
                resp.raise_for_status()
                jf_user = resp.json()
                user.jellyfin_user_id = jf_user["Id"]
                jf_username = jf_user.get("Name", display_name)
                user.jellyfin_password = new_pw
                db.session.commit()

        # Look up the actual Jellyfin username from the stored user ID
        if not jf_username:
            try:
                resp = requests.get(
                    f"{jellyfin_url}/Users/{user.jellyfin_user_id}",
                    headers=jf_headers,
                    timeout=10,
                )
                resp.raise_for_status()
                jf_username = resp.json().get("Name", display_name)
            except Exception:
                jf_username = display_name

        # Authenticate to get a Jellyfin access token
        auth_header = (
            'MediaBrowser Client="Meduseld", Device="Web", DeviceId="meduseld-'
            + str(user.id)
            + '", Version="1.0.0"'
        )
        resp = requests.post(
            f"{jellyfin_url}/Users/AuthenticateByName",
            headers={"Content-Type": "application/json", "Authorization": auth_header},
            json={"Username": jf_username, "Pw": user.jellyfin_password},
            timeout=10,
        )

        if resp.status_code == 401:
            # Password out of sync — reset it
            new_pw = secrets.token_urlsafe(24)
            reset_resp = requests.post(
                f"{jellyfin_url}/Users/{user.jellyfin_user_id}/Password",
                headers={**jf_headers, "Content-Type": "application/json"},
                json={"NewPw": new_pw},
                timeout=10,
            )

            if reset_resp.ok:
                user.jellyfin_password = new_pw
                db.session.commit()

                # Retry auth
                resp = requests.post(
                    f"{jellyfin_url}/Users/AuthenticateByName",
                    headers={"Content-Type": "application/json", "Authorization": auth_header},
                    json={"Username": jf_username, "Pw": new_pw},
                    timeout=10,
                )

            # If still failing, the stored user ID is stale — clear and retry from scratch
            if resp.status_code == 401 and not retry:
                logger.warning("Jellyfin auth: stored user ID stale, re-linking account")
                user.jellyfin_user_id = None
                user.jellyfin_password = None
                db.session.commit()
                return _jellyfin_auth_inner(retry=True)

        resp.raise_for_status()
        auth_result = resp.json()
        token = auth_result.get("AccessToken", "")
        auth_server_id = auth_result.get("ServerId", "")

        # The Jellyfin web client validates stored credentials by comparing
        # the ServerId against /System/Info/Public → Id. These can differ
        # (AuthenticateByName returns an internal ID). Always use the public one.
        server_id = auth_server_id
        try:
            info_resp = requests.get(f"{jellyfin_url}/System/Info/Public", timeout=5)
            if info_resp.ok:
                public_id = info_resp.json().get("Id", "")
                if public_id and public_id != auth_server_id:
                    logger.info(
                        f"Jellyfin auth: using public ServerId {public_id} (AuthenticateByName returned {auth_server_id})"
                    )
                    server_id = public_id
        except Exception:
            pass  # Fall back to auth_server_id

        logger.info(f"Jellyfin auth success: user={jf_username}, server_id={server_id}")
        return jsonify({"token": token, "user_id": user.jellyfin_user_id, "server_id": server_id})

    except requests.Timeout:
        logger.error("Jellyfin auth: timeout connecting to Jellyfin")
        return jsonify({"error": "Jellyfin server timeout"}), 504
    except requests.RequestException as e:
        logger.error(f"Jellyfin auth error: {e}")
        return jsonify({"error": f"Jellyfin unavailable: {e}"}), 503
    except Exception as e:
        logger.error(f"Jellyfin auth unexpected error: {e}")
        return jsonify({"error": f"Internal error: {e}"}), 500


# ================= ADMIN API =================


@app.route("/api/admin/users")
@require_auth
@require_role("admin")
def api_admin_users():
    """Return all users for admin management."""
    from models import User

    users = User.query.order_by(User.created_at.desc()).all()
    return jsonify({"users": [u.to_dict() for u in users]}), 200


@app.route("/api/admin/users/<int:user_id>", methods=["PUT"])
@require_auth
@require_role("admin")
def api_admin_update_user(user_id):
    """Update a user's role or active status."""
    from models import User
    from database import db

    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    # Prevent admins from demoting themselves
    if user.id == g.user.id and data.get("role") and data["role"] != "admin":
        return jsonify({"error": "Cannot change your own role"}), 400

    if "role" in data and data["role"] in ("admin", "user"):
        user.role = data["role"]
    if "is_active" in data and isinstance(data["is_active"], bool):
        # Prevent admins from deactivating themselves
        if user.id == g.user.id and not data["is_active"]:
            return jsonify({"error": "Cannot deactivate your own account"}), 400
        user.is_active = data["is_active"]

    try:
        db.session.commit()
        logger.info(
            f"Admin {g.user.username} updated user {user.username}: role={user.role}, active={user.is_active}"
        )
        return jsonify({"user": user.to_dict()}), 200
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating user: {e}")
        return jsonify({"error": "Update failed"}), 500


# ================= CALENDAR EVENTS =================


@app.route("/api/calendar/events")
@require_auth
def api_calendar_events():
    """Return upcoming calendar events."""
    from models import CalendarEvent

    events = (
        CalendarEvent.query.filter(CalendarEvent.event_date >= datetime.now(timezone.utc))
        .order_by(CalendarEvent.event_date.asc())
        .all()
    )
    return jsonify({"events": [e.to_dict() for e in events]}), 200


@app.route("/api/calendar/events", methods=["POST"])
@require_auth
@require_role("admin")
def api_calendar_create_event():
    """Create a new calendar event (admin only)."""
    from models import CalendarEvent
    from database import db

    data = request.get_json()
    if not data or not data.get("title") or not data.get("event_date"):
        return jsonify({"error": "Title and event_date are required"}), 400

    try:
        event_date = datetime.fromisoformat(data["event_date"].replace("Z", "+00:00"))
    except (ValueError, AttributeError) as e:
        logger.error("Invalid date format for calendar event: %s", e)
        return jsonify({"error": "Invalid date format"}), 400

    event = CalendarEvent(
        title=data["title"],
        description=data.get("description", ""),
        event_date=event_date,
        created_by=g.user.id,
    )

    try:
        db.session.add(event)
        db.session.commit()
        logger.info(f"Admin {g.user.username} created calendar event: {event.title}")
        return jsonify({"event": event.to_dict()}), 201
    except Exception as e:
        db.session.rollback()
        logger.error("Error creating calendar event: %s", e)
        return jsonify({"error": "Failed to create event"}), 500


@app.route("/api/calendar/events/<int:event_id>", methods=["DELETE"])
@require_auth
@require_role("admin")
def api_calendar_delete_event(event_id):
    """Delete a calendar event (admin only)."""
    from models import CalendarEvent
    from database import db

    event = CalendarEvent.query.get(event_id)
    if not event:
        return jsonify({"error": "Event not found"}), 404

    try:
        db.session.delete(event)
        db.session.commit()
        logger.info(f"Admin {g.user.username} deleted calendar event: {event.title}")
        return jsonify({"success": True}), 200
    except Exception as e:
        db.session.rollback()
        logger.error("Error deleting calendar event: %s", e)
        return jsonify({"error": "Failed to delete event"}), 500


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

                # Kill any stale tmux session from a previous run
                # This prevents "session already exists" errors when the server
                # crashed or was stopped but the tmux session wasn't cleaned up
                try:
                    tmux_check = subprocess.run(
                        ["tmux", "has-session", "-t", "icarus"],
                        capture_output=True,
                        timeout=5,
                    )
                    if tmux_check.returncode == 0:
                        logger.warning(
                            "Stale tmux session 'icarus' found — killing it before start"
                        )
                        subprocess.run(
                            ["tmux", "kill-session", "-t", "icarus"],
                            capture_output=True,
                            timeout=5,
                        )
                except Exception as e:
                    logger.warning(f"Could not check/kill stale tmux session: {e}")

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
                    except Exception:
                        pass  # Skip inaccessible partitions (permission denied, etc.)
        except Exception:
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

                        # Write idle shutdown marker to startup log so the panel
                        # can distinguish this from an unexpected process death
                        try:
                            startup_log = os.path.join(SERVER_DIR, "startup.log")
                            with open(startup_log, "a") as f:
                                from datetime import datetime

                                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                f.write(
                                    f"[{ts}] Idle shutdown: Server empty for {IDLE_SHUTDOWN_MINUTES} minutes, stopping automatically\n"
                                )
                        except Exception as e:
                            logger.warning(f"Could not write idle shutdown marker: {e}")

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

    jellyfin_base = config.JELLYFIN_INTERNAL_URL.rstrip("/")

    try:
        # Always proxy to the Jellyfin server with the exact path
        jellyfin_url = f"{jellyfin_base}/{path}"

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
                    value = value.replace(jellyfin_base, "https://jellyfin.meduseld.io")
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
                text_content = text_content.replace(jellyfin_base, "https://jellyfin.meduseld.io")
                # Also replace without protocol in case of partial matches
                jellyfin_host = jellyfin_base.replace("http://", "").replace("https://", "")
                text_content = text_content.replace(jellyfin_host, "jellyfin.meduseld.io")

                # Inject auto-login script into Jellyfin HTML pages.
                # This uses a deferred approach: waits for Jellyfin's own
                # ConnectionManager to finish initializing localStorage, then
                # patches in our AccessToken/UserId. This avoids the race
                # condition where Jellyfin overwrites our credentials.
                if "text/html" in content_type and "</head>" in text_content:
                    auto_login_script = """<script>
(function() {
    // Skip if already logged in
    try {
        var creds = JSON.parse(localStorage.getItem("jellyfin_credentials") || "{}");
        if (creds.Servers && creds.Servers.length && creds.Servers[0].AccessToken) return;
    } catch(e) {}
    // One attempt per session to prevent loops
    if (sessionStorage.getItem("meduseld_sso_attempted")) return;
    sessionStorage.setItem("meduseld_sso_attempted", "1");
    // Fetch auth token, then wait for Jellyfin to initialize before patching
    fetch("/api/jellyfin-auth", {credentials:"include"})
        .then(function(r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function(d) {
            if (!d.token || !d.user_id) return;
            // Poll until Jellyfin's ConnectionManager has set up credentials
            var elapsed = 0, interval = 250, maxWait = 15000;
            var poll = setInterval(function() {
                elapsed += interval;
                try {
                    var raw = localStorage.getItem("jellyfin_credentials");
                    if (raw) {
                        var c = JSON.parse(raw);
                        if (c.Servers && c.Servers.length && c.Servers[0].Id) {
                            c.Servers[0].AccessToken = d.token;
                            c.Servers[0].UserId = d.user_id;
                            c.Servers[0].ManualAddress = "https://jellyfin.meduseld.io";
                            c.Servers[0].LastConnectionMode = 2;
                            c.Servers[0].manualAddressOnly = true;
                            c.Servers[0].DateLastAccessed = new Date().getTime();
                            localStorage.setItem("jellyfin_credentials", JSON.stringify(c));
                            localStorage.setItem("enableAutoLogin", "true");
                            clearInterval(poll);
                            window.location.replace("/web/index.html#!/home.html");
                            return;
                        }
                    }
                } catch(e) {}
                if (elapsed >= maxWait) {
                    clearInterval(poll);
                    // Timeout fallback: write credentials directly
                    var c = {Servers:[{AccessToken:d.token,UserId:d.user_id,Id:d.server_id||"",ManualAddress:"https://jellyfin.meduseld.io",LastConnectionMode:2,manualAddressOnly:true,DateLastAccessed:new Date().getTime()}]};
                    localStorage.setItem("jellyfin_credentials", JSON.stringify(c));
                    localStorage.setItem("enableAutoLogin", "true");
                    window.location.replace("/web/index.html#!/home.html");
                }
            }, interval);
        }).catch(function(e){ console.log("Meduseld SSO: " + e); });
})();
</script>"""
                    text_content = text_content.replace("</head>", auto_login_script + "</head>")

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

    # If accessed via ssh subdomain, show terminal wrapper (admin only)
    if host == "ssh.meduseld.io":
        if not hasattr(g, "user") or g.user is None or (g.user.role != "admin" and not IS_DEV):
            return jsonify({"error": "Insufficient permissions"}), 403
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

    # Track action for achievements
    try:
        from models import UserActionCount
        from database import db

        if hasattr(g, "user") and g.user:
            UserActionCount.increment(g.user.id, "server_start")
            db.session.commit()
    except Exception as e:
        logger.error("Failed to track server_start action: %s", e)

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

    # Track action for achievements
    try:
        from models import UserActionCount
        from database import db

        if hasattr(g, "user") and g.user:
            UserActionCount.increment(g.user.id, "server_stop")
            db.session.commit()
    except Exception as e:
        logger.error("Failed to track server_stop action: %s", e)

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

    # Write stop marker to startup log so the panel knows this was user-initiated
    try:
        startup_log = os.path.join(SERVER_DIR, "startup.log")
        with open(startup_log, "a") as f:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{ts}] User stop: Server stopped by user\n")
    except Exception as e:
        logger.warning(f"Could not write stop marker: {e}")

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

    # Track action for achievements
    try:
        from models import UserActionCount
        from database import db

        if hasattr(g, "user") and g.user:
            UserActionCount.increment(g.user.id, "server_kill")
            db.session.commit()
    except Exception as e:
        logger.error("Failed to track server_kill action: %s", e)

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

    # Write kill marker to startup log so the panel knows this was user-initiated
    try:
        startup_log = os.path.join(SERVER_DIR, "startup.log")
        with open(startup_log, "a") as f:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{ts}] User kill: Server force killed by user\n")
    except Exception as e:
        logger.warning(f"Could not write kill marker: {e}")

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
        logger.error("Error reading log file: %s", e)
        return jsonify({"error": "Failed to read logs"})


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
                    "error": "Internal server error",
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
        return jsonify({"success": False, "error": "Failed to clear logs"}), 500


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
            pass  # journalctl not available or timed out; fall through to error response

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
        return jsonify({"logs": [], "error": "Failed to read logs"}), 500


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


def _authenticate_from_cookie():
    """Authenticate a user from the CF_Authorization cookie on public hosts.
    Used by health.meduseld.io proxy routes that need auth but bypass Cloudflare Access.
    Checks multiple sources for the token:
    1. CF_Authorization cookie (direct requests)
    2. X-CF-Authorization header (custom header approach)
    3. cf_token query parameter (avoids CORS preflight for GET requests)
    4. _cf_token in JSON body (for PUT/POST requests)
    Returns a User object or None."""
    cf_token = request.cookies.get("CF_Authorization")
    if not cf_token:
        cf_token = request.headers.get("X-CF-Authorization")
    if not cf_token:
        cf_token = request.args.get("cf_token")
    if not cf_token:
        # Check JSON body for _cf_token (used by PUT requests to avoid preflight)
        try:
            body = request.get_json(silent=True)
            if body and isinstance(body, dict):
                cf_token = body.get("_cf_token")
        except Exception:
            pass  # Request body may not be JSON; fall through to next check
    if not cf_token:
        # Check form data for _cf_token (used by multipart/form-data uploads)
        try:
            cf_token = request.form.get("_cf_token")
        except Exception:
            pass  # Request may not have form data; fall through to failure
    if not cf_token:
        logger.warning(
            "_authenticate_from_cookie: No auth token found in cookie, header, query, or body"
        )
        return None

    try:
        payload = jwt.decode(cf_token, options={"verify_signature": False})
        email = payload.get("email", "")
        custom = payload.get("custom", {})
        discord_user = custom.get("discord_user", {}) if isinstance(custom, dict) else {}

        if discord_user and discord_user.get("id"):
            discord_id = str(discord_user["id"])
            username = discord_user.get("username", "")
            display_name = discord_user.get("global_name", "") or username
            avatar_hash = discord_user.get("avatar", "")
            is_admin = discord_user.get("is_admin", False)
        else:
            logger.warning("_authenticate_from_cookie: No discord_user in JWT custom claims")
            return None

        from models import User

        # Use get_or_create so users visiting the admin page for the first
        # time (without having visited a Flask page) still get a DB record.
        user = User.get_or_create(
            discord_id=discord_id,
            username=username,
            display_name=display_name,
            avatar_hash=avatar_hash,
            email=email,
        )

        # Sync admin role from Discord
        if is_admin and user.role != "admin":
            user.role = "admin"
            from database import db

            db.session.commit()
        elif not is_admin and user.role == "admin":
            user.role = "user"
            from database import db

            db.session.commit()

        return user
    except Exception as e:
        logger.error("Failed to authenticate from CF_Authorization cookie: %s", e)
        return None


def check_achievements(user):
    """Check and award any new achievements for the given user.
    Returns a list of newly unlocked achievement IDs."""
    from database import db

    try:
        return _check_achievements_inner(user)
    except Exception as e:
        logger.error("Achievement check failed for user %s: %s", user.username, e)
        try:
            db.session.rollback()
        except Exception as e:
            logger.error("Failed to rollback after achievement check error: %s", e)
        return []


def _check_achievements_inner(user):
    from models import (
        UserAchievement,
        TriviaWin,
        EventRSVP,
        GameVote,
        UserActionCount,
        get_all_achievements,
    )
    from database import db
    from sqlalchemy import func, extract

    all_achs = get_all_achievements()
    existing = set(a.achievement_id for a in UserAchievement.query.filter_by(user_id=user.id).all())
    newly_unlocked = []

    def _award(aid):
        if aid not in existing and aid in all_achs:
            try:
                ua = UserAchievement(user_id=user.id, achievement_id=aid)
                db.session.add(ua)
                db.session.flush()
                newly_unlocked.append(aid)
                existing.add(aid)
            except Exception as e:
                logger.error("Failed to award achievement %s to user %s: %s", aid, user.username, e)

    # First Login
    _award("first_login")

    # Trivia achievements
    trivia_count = TriviaWin.query.filter_by(user_id=user.id).count()
    if trivia_count >= 1:
        _award("trivia_rookie")
    if trivia_count >= 10:
        _award("trivia_veteran")
    if trivia_count >= 50:
        _award("trivia_master")

    # Perfect Score
    perfect_count = TriviaWin.query.filter(
        TriviaWin.user_id == user.id,
        TriviaWin.score == TriviaWin.total_questions,
        TriviaWin.total_questions > 0,
    ).count()
    if perfect_count >= 1:
        _award("perfect_score")
    if perfect_count >= 3:
        _award("trivia_streak_3")

    # Big Brain — 80%+ on hard difficulty
    hard_win = TriviaWin.query.filter(
        TriviaWin.user_id == user.id,
        TriviaWin.total_questions > 0,
        TriviaWin.category.like("%hard%"),
    ).all()
    # Category from Open Trivia DB doesn't include difficulty, so check score ratio
    # We store category name, not difficulty. Need to check via score threshold on any game.
    # Actually, we don't store difficulty. Let's check for 80%+ score on any game with 10+ questions.
    high_score_game = TriviaWin.query.filter(
        TriviaWin.user_id == user.id,
        TriviaWin.total_questions >= 10,
        (TriviaWin.score * 100 / TriviaWin.total_questions) >= 80,
    ).first()
    if high_score_game:
        _award("trivia_hard_win")

    # Renaissance Mind — 10 different categories
    distinct_cats = (
        db.session.query(func.count(func.distinct(TriviaWin.category)))
        .filter(
            TriviaWin.user_id == user.id, TriviaWin.category != "", TriviaWin.category.isnot(None)
        )
        .scalar()
    )
    if distinct_cats and distinct_cats >= 10:
        _award("trivia_all_categories")

    # Night Owl
    night_game = TriviaWin.query.filter(
        TriviaWin.user_id == user.id,
        extract("hour", TriviaWin.played_at) < 5,
    ).first()
    if night_game:
        _award("night_owl")

    # Media Explorer
    if user.jellyfin_user_id:
        _award("media_explorer")

    # RSVP King
    rsvp_count = (
        db.session.query(func.count(func.distinct(EventRSVP.event_id)))
        .filter(EventRSVP.user_id == user.id)
        .scalar()
    )
    if rsvp_count and rsvp_count >= 5:
        _award("rsvp_king")

    # Game Critic
    has_votes = GameVote.query.filter_by(user_id=user.id).first()
    if has_votes:
        _award("game_critic")

    # Server action achievements (from persistent counters)
    try:
        start_count = UserActionCount.query.filter_by(
            user_id=user.id, action="server_start"
        ).first()
        if start_count and start_count.count >= 5:
            _award("server_starter")

        stop_count = UserActionCount.query.filter_by(user_id=user.id, action="server_stop").first()
        if stop_count and stop_count.count >= 10:
            _award("server_stopper")

        kill_count = UserActionCount.query.filter_by(user_id=user.id, action="server_kill").first()
        if kill_count and kill_count.count >= 5:
            _award("server_killer")
    except Exception as e:
        logger.error("Failed to check server action achievements: %s", e)

    # Easter egg is awarded via a separate endpoint, not checked here

    if newly_unlocked:
        try:
            db.session.commit()
            logger.info("User %s unlocked achievements: %s", user.username, newly_unlocked)
        except Exception as e:
            db.session.rollback()
            logger.error("Failed to commit achievements for user %s: %s", user.username, e)
            newly_unlocked = []

    return newly_unlocked


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
        logger.error("Error checking service health: %s", e)
        return jsonify({"error": "Service check failed"}), 502


@app.route("/check/<service>", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
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

    # Proxy to monitoring microservice
    if service == "stats":
        return _proxy_microservice("http://127.0.0.1:5004/stats")

    if service == "history":
        return _proxy_microservice("http://127.0.0.1:5004/history")

    # Calendar API — proxied through health so static pages can manage events
    # without Cloudflare Access intercepting the request.
    if service == "calendar" or service.startswith("calendar-"):

        def _cal_cors(resp, status=200):
            if isinstance(resp, tuple):
                response = make_response(resp[0], resp[1])
            else:
                response = make_response(resp, status)
            origin = request.headers.get("Origin")
            if _is_valid_meduseld_origin(origin):
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
                response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            return response

        if request.method == "OPTIONS":
            return _cal_cors("", 204)

        user = _authenticate_from_cookie()
        if not user:
            return _cal_cors(jsonify({"error": "Authentication required"}), 401)

        if service == "calendar":
            # GET = list events, POST = create event (admin only)
            if request.method == "GET":
                from models import CalendarEvent

                events = (
                    CalendarEvent.query.filter(CalendarEvent.event_date >= datetime.utcnow())
                    .order_by(CalendarEvent.event_date.asc())
                    .all()
                )
                return _cal_cors(
                    jsonify({"events": [e.to_dict() for e in events]}),
                    200,
                )

            if request.method == "POST":
                if user.role != "admin":
                    return _cal_cors(jsonify({"error": "Insufficient permissions"}), 403)
                data = request.get_json()
                if not data or not data.get("title") or not data.get("event_date"):
                    return _cal_cors(jsonify({"error": "Title and date required"}), 400)
                try:
                    from models import CalendarEvent
                    from database import db

                    event = CalendarEvent(
                        title=data["title"],
                        description=data.get("description", ""),
                        event_date=datetime.strptime(data["event_date"][:19], "%Y-%m-%dT%H:%M:%S"),
                        created_by=user.id,
                    )
                    db.session.add(event)
                    db.session.commit()
                    logger.info("Admin %s created calendar event: %s", user.username, data["title"])
                    return _cal_cors(jsonify({"id": event.id}), 201)
                except Exception as e:
                    logger.error("Failed to create calendar event: %s (data=%s)", e, data)
                    return _cal_cors(jsonify({"error": "Create failed"}), 500)

            return _cal_cors(jsonify({"error": "Method not allowed"}), 405)

        # DELETE /check/calendar-<id> or PUT /check/calendar-<id> (RSVP)
        if service.startswith("calendar-"):
            try:
                event_id = int(service.split("calendar-")[1])
            except (ValueError, IndexError) as e:
                logger.error("Invalid calendar proxy path: %s", e)
                return _cal_cors(jsonify({"error": "Invalid event ID"}), 400)

            if request.method == "DELETE":
                if user.role != "admin":
                    return _cal_cors(jsonify({"error": "Insufficient permissions"}), 403)
                from models import CalendarEvent
                from database import db

                event = CalendarEvent.query.get(event_id)
                if not event:
                    return _cal_cors(jsonify({"error": "Event not found"}), 404)
                db.session.delete(event)
                db.session.commit()
                logger.info("Admin %s deleted calendar event %d", user.username, event_id)
                return _cal_cors(jsonify({"ok": True}), 200)

            if request.method == "PUT":
                data = request.get_json()
                if not data:
                    return _cal_cors(jsonify({"error": "No data provided"}), 400)

                # Edit event (admin only) — if title or event_date is present
                if data.get("title") or data.get("event_date"):
                    if user.role != "admin":
                        return _cal_cors(jsonify({"error": "Insufficient permissions"}), 403)
                    from models import CalendarEvent
                    from database import db

                    event = CalendarEvent.query.get(event_id)
                    if not event:
                        return _cal_cors(jsonify({"error": "Event not found"}), 404)
                    try:
                        if data.get("title"):
                            event.title = data["title"]
                        if data.get("event_date"):
                            date_str = data["event_date"]
                            # Strip trailing Z and parse as naive UTC datetime
                            if date_str.endswith("Z"):
                                date_str = date_str[:-1]
                            # Handle milliseconds if present
                            if "." in date_str:
                                date_str = date_str.split(".")[0]
                            event.event_date = datetime.strptime(date_str[:19], "%Y-%m-%dT%H:%M:%S")
                        if "description" in data:
                            event.description = data.get("description", "")
                        db.session.commit()
                        db.session.refresh(event)
                        logger.info(
                            "Admin %s edited calendar event %d: %s",
                            user.username,
                            event_id,
                            event.title,
                        )
                        return _cal_cors(jsonify({"ok": True, "event": event.to_dict()}), 200)
                    except Exception as e:
                        db.session.rollback()
                        logger.error("Failed to edit calendar event: %s", e)
                        return _cal_cors(jsonify({"error": "Edit failed"}), 500)

                # RSVP — status field required
                if not data.get("status"):
                    return _cal_cors(jsonify({"error": "Status required"}), 400)
                status = data["status"]
                if status not in ("going", "maybe", "not_going"):
                    return _cal_cors(jsonify({"error": "Invalid status"}), 400)
                from models import CalendarEvent, EventRSVP
                from database import db

                event = CalendarEvent.query.get(event_id)
                if not event:
                    return _cal_cors(jsonify({"error": "Event not found"}), 404)
                try:
                    rsvp = EventRSVP.query.filter_by(event_id=event_id, user_id=user.id).first()
                    if rsvp:
                        if rsvp.status == status:
                            # Toggle off — remove RSVP
                            db.session.delete(rsvp)
                            db.session.commit()
                            logger.info(
                                "User %s removed RSVP for event %d", user.username, event_id
                            )
                            return _cal_cors(jsonify({"ok": True, "removed": True}), 200)
                        rsvp.status = status
                        rsvp.updated_at = datetime.utcnow()
                    else:
                        rsvp = EventRSVP(event_id=event_id, user_id=user.id, status=status)
                        db.session.add(rsvp)
                    db.session.commit()
                    logger.info("User %s RSVP'd '%s' for event %d", user.username, status, event_id)
                    return _cal_cors(jsonify({"ok": True, "status": status}), 200)
                except Exception as e:
                    db.session.rollback()
                    logger.error("Failed to save RSVP: %s", e)
                    return _cal_cors(jsonify({"error": "RSVP failed"}), 500)

            return _cal_cors(jsonify({"error": "Method not allowed"}), 405)

    # Jellyfin auth — proxied through health so the services page can call
    # /api/jellyfin-auth without needing a Cloudflare Access session for
    # panel.meduseld.io. Uses _authenticate_from_cookie() for auth.
    if service == "media-auth":

        def _media_cors(resp, status=200):
            if isinstance(resp, tuple):
                response = make_response(resp[0], resp[1])
            else:
                response = make_response(resp, status)
            origin = request.headers.get("Origin")
            if _is_valid_meduseld_origin(origin):
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
                response.headers["Access-Control-Allow-Headers"] = "Content-Type"
                response.headers["Access-Control-Allow-Credentials"] = "true"
            return response

        if request.method == "OPTIONS":
            return _media_cors("", 204)

        user = _authenticate_from_cookie()
        if not user:
            return _media_cors(jsonify({"error": "Authentication required"}), 401)

        if request.method == "GET":
            g.user = user
            try:
                result = _jellyfin_auth_inner()
            except Exception as e:
                logger.error("media-auth: _jellyfin_auth_inner raised: %s", e)
                return _media_cors(jsonify({"error": f"Internal error: {e}"}), 500)
            if isinstance(result, tuple):
                return _media_cors(result[0], result[1])
            return _media_cors(result, 200)

        return _media_cors(jsonify({"error": "Method not allowed"}), 405)

    # Jellyseerr auth — provisions the user's Jellyfin account, then serves
    # an HTML page that POSTs the credentials to Jellyseerr's auth endpoint
    # directly from the browser. This ensures Jellyseerr sets its own
    # connect.sid cookie on the correct domain (requests.meduseld.io).
    if service == "seerr-auth":

        def _seerr_cors(resp, status=200):
            if isinstance(resp, tuple):
                response = make_response(resp[0], resp[1])
            else:
                response = make_response(resp, status)
            origin = request.headers.get("Origin")
            if _is_valid_meduseld_origin(origin):
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
                response.headers["Access-Control-Allow-Headers"] = "Content-Type"
                response.headers["Access-Control-Allow-Credentials"] = "true"
            return response

        if request.method == "OPTIONS":
            return _seerr_cors("", 204)

        user = _authenticate_from_cookie()
        if not user:
            return _seerr_cors(jsonify({"error": "Authentication required"}), 401)

        if request.method == "GET":
            g.user = user

            # Ensure Jellyfin account exists and credentials are current
            try:
                jf_result = _jellyfin_auth_inner()
                # Check if _jellyfin_auth_inner returned an error
                if isinstance(jf_result, tuple) and len(jf_result) == 2:
                    status_code = jf_result[1]
                    if status_code >= 400:
                        logger.error(
                            "seerr-auth: Jellyfin provisioning failed with status %d", status_code
                        )
                        return _seerr_cors(
                            jsonify({"error": "Failed to prepare Jellyfin account"}), 502
                        )
            except Exception as e:
                logger.error("seerr-auth: Failed to provision Jellyfin account: %s", e)
                return _seerr_cors(jsonify({"error": "Failed to prepare Jellyfin account"}), 500)

            # Reload user to get fresh jellyfin credentials
            from models import User as UserModel

            user = UserModel.query.get(user.id)
            if not user or not user.jellyfin_password:
                return _seerr_cors(jsonify({"error": "No Jellyfin credentials available"}), 500)

            jf_username = user.display_name or user.username

            # Look up actual Jellyfin username from stored user ID
            if user.jellyfin_user_id:
                try:
                    jf_resp = requests.get(
                        f"{config.JELLYFIN_INTERNAL_URL}/Users/{user.jellyfin_user_id}",
                        headers={"X-Emby-Token": config.JELLYFIN_API_KEY},
                        timeout=10,
                    )
                    if jf_resp.ok:
                        jf_username = jf_resp.json().get("Name", jf_username)
                except Exception as e:
                    logger.warning("seerr-auth: Could not look up Jellyfin username: %s", e)

            # Verify credentials work against Jellyfin directly before sending to Jellyseerr
            try:
                auth_header = (
                    'MediaBrowser Client="Meduseld", Device="Web", DeviceId="meduseld-seerr-'
                    + str(user.id)
                    + '", Version="1.0.0"'
                )
                jf_verify = requests.post(
                    f"{config.JELLYFIN_INTERNAL_URL}/Users/AuthenticateByName",
                    headers={"Content-Type": "application/json", "Authorization": auth_header},
                    json={"Username": jf_username, "Pw": user.jellyfin_password},
                    timeout=10,
                )
                if not jf_verify.ok:
                    logger.error(
                        "seerr-auth: Jellyfin credential verification failed for '%s': %d %s",
                        jf_username,
                        jf_verify.status_code,
                        jf_verify.text[:200],
                    )
                    return _seerr_cors(jsonify({"error": "Jellyfin credentials invalid"}), 502)
                logger.info("seerr-auth: Jellyfin credentials verified OK for '%s'", jf_username)
            except Exception as e:
                logger.error("seerr-auth: Jellyfin credential verification request failed: %s", e)
                return _seerr_cors(jsonify({"error": "Could not verify Jellyfin credentials"}), 502)

            # Authenticate with Jellyseerr server-side to avoid CORS issues.
            # We POST the Jellyfin credentials to Jellyseerr's internal API,
            # extract the connect.sid session cookie from the response, set it
            # on .meduseld.io (covers requests.meduseld.io), and redirect.
            try:
                logger.info(
                    "seerr-auth: Authenticating user '%s' with Jellyseerr at %s",
                    jf_username,
                    config.JELLYSEERR_INTERNAL_URL,
                )
                seerr_resp = requests.post(
                    f"{config.JELLYSEERR_INTERNAL_URL}/api/v1/auth/jellyfin",
                    json={"username": jf_username, "password": user.jellyfin_password},
                    headers={"Content-Type": "application/json"},
                    timeout=10,
                )
            except requests.ConnectionError as e:
                logger.error(
                    "seerr-auth: Cannot connect to Jellyseerr at %s: %s",
                    config.JELLYSEERR_INTERNAL_URL,
                    e,
                )
                return _seerr_cors(jsonify({"error": "Could not reach Jellyseerr"}), 502)
            except Exception as e:
                logger.error("seerr-auth: Jellyseerr auth request failed: %s", e)
                return _seerr_cors(jsonify({"error": "Could not reach Jellyseerr"}), 502)

            if not seerr_resp.ok:
                logger.error(
                    "seerr-auth: Jellyseerr auth returned %d for user '%s': %s",
                    seerr_resp.status_code,
                    jf_username,
                    seerr_resp.text[:500],
                )
                return _seerr_cors(jsonify({"error": "Jellyseerr authentication failed"}), 502)

            # Extract connect.sid from Jellyseerr's Set-Cookie response
            connect_sid = None
            for cookie in seerr_resp.cookies:
                if cookie.name == "connect.sid":
                    connect_sid = cookie.value
                    break

            if not connect_sid:
                logger.warning("seerr-auth: No connect.sid cookie in Jellyseerr response")

            # Build redirect response with the session cookie
            resp = make_response("", 302)
            resp.headers["Location"] = "https://requests.meduseld.io"
            resp.headers["Cache-Control"] = "no-store"

            if connect_sid:
                # Set connect.sid on .meduseld.io so it's sent to requests.meduseld.io
                resp.set_cookie(
                    "connect.sid",
                    connect_sid,
                    domain=".meduseld.io",
                    path="/",
                    secure=True,
                    httponly=True,
                    samesite="Lax",
                )

            return resp

        return _seerr_cors(jsonify({"error": "Method not allowed"}), 405)

    # Games list API — manages the "Games Up Next" list entries.
    # GET returns all games (public). POST adds a game (authenticated).
    # DELETE via /check/games-<app_id> removes a game (admin only) and its votes.
    if service == "games" or service.startswith("games-"):

        def _games_cors(resp, status=200):
            if isinstance(resp, tuple):
                response = make_response(resp[0], resp[1])
            else:
                response = make_response(resp, status)
            origin = request.headers.get("Origin")
            if _is_valid_meduseld_origin(origin):
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
                response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            return response

        if request.method == "OPTIONS":
            return _games_cors("", 204)

        if service == "games":
            if request.method == "GET":
                from models import GameListEntry

                entries = GameListEntry.query.order_by(GameListEntry.created_at.asc()).all()
                return _games_cors(jsonify({"games": [e.to_dict() for e in entries]}), 200)

            # POST — add a game (authenticated)
            user = _authenticate_from_cookie()
            if not user:
                return _games_cors(jsonify({"error": "Authentication required"}), 401)

            if request.method == "POST":
                data = request.get_json()
                if not data or not data.get("app_id") or not data.get("name"):
                    return _games_cors(jsonify({"error": "app_id and name required"}), 400)

                from models import GameListEntry
                from database import db

                app_id = str(data["app_id"]).strip()
                name = data["name"].strip()

                # Check for duplicate
                existing = GameListEntry.query.filter_by(app_id=app_id).first()
                if existing:
                    return _games_cors(jsonify({"error": "Game already in list"}), 409)

                url = data.get("url", "").strip()
                if not url:
                    url = f"https://store.steampowered.com/app/{app_id}/"

                entry = GameListEntry(
                    app_id=app_id,
                    name=name,
                    url=url,
                    tooltip=data.get("tooltip", "").strip() or None,
                    added_by=user.id,
                )
                try:
                    db.session.add(entry)
                    db.session.commit()
                    logger.info("User %s added game: %s (%s)", user.username, name, app_id)
                    return _games_cors(jsonify({"game": entry.to_dict()}), 201)
                except Exception as e:
                    db.session.rollback()
                    logger.error("Failed to add game: %s", e)
                    return _games_cors(jsonify({"error": "Add failed"}), 500)

            return _games_cors(jsonify({"error": "Method not allowed"}), 405)

        # DELETE /check/games-<app_id> — admin only
        if service.startswith("games-"):
            target_app_id = service.split("games-", 1)[1]
            if not target_app_id:
                return _games_cors(jsonify({"error": "Invalid app ID"}), 400)

            user = _authenticate_from_cookie()
            if not user:
                return _games_cors(jsonify({"error": "Authentication required"}), 401)
            if user.role != "admin":
                return _games_cors(jsonify({"error": "Insufficient permissions"}), 403)

            if request.method == "DELETE":
                from models import GameListEntry, GameVote
                from database import db

                entry = GameListEntry.query.filter_by(app_id=target_app_id).first()
                if not entry:
                    return _games_cors(jsonify({"error": "Game not found"}), 404)

                try:
                    # Delete associated votes first
                    GameVote.query.filter_by(game_app_id=target_app_id).delete()
                    db.session.delete(entry)
                    db.session.commit()
                    logger.info(
                        "Admin %s removed game: %s (%s)",
                        user.username,
                        entry.name,
                        target_app_id,
                    )
                    return _games_cors(jsonify({"ok": True}), 200)
                except Exception as e:
                    db.session.rollback()
                    logger.error("Failed to remove game: %s", e)
                    return _games_cors(jsonify({"error": "Delete failed"}), 500)

            return _games_cors(jsonify({"error": "Method not allowed"}), 405)

    # Game voting API — lets users rank games in the "Games Up Next" list.
    # GET returns aggregated scores + the current user's votes.
    # PUT submits the user's ranked list (replaces all previous votes).
    if service == "game-votes":

        def _vote_cors(resp, status=200):
            if isinstance(resp, tuple):
                response = make_response(resp[0], resp[1])
            else:
                response = make_response(resp, status)
            origin = request.headers.get("Origin")
            if _is_valid_meduseld_origin(origin):
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Methods"] = "GET, PUT, OPTIONS"
                response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            return response

        if request.method == "OPTIONS":
            return _vote_cors("", 204)

        user = _authenticate_from_cookie()
        if not user:
            return _vote_cors(jsonify({"error": "Authentication required"}), 401)

        if request.method == "GET":
            from models import GameVote, GameListEntry
            from sqlalchemy import func

            # Aggregate scores: each rank-1 vote = N points, rank-2 = N-1, etc.
            # N = total number of games in the list (dynamic)
            N = GameListEntry.query.count() or 7
            all_votes = GameVote.query.all()

            # Build per-game scores
            scores = {}  # app_id -> {score, voters, vote_count}
            for v in all_votes:
                if v.game_app_id not in scores:
                    scores[v.game_app_id] = {"score": 0, "vote_count": 0, "voters": []}
                points = max(N - v.rank + 1, 1)
                scores[v.game_app_id]["score"] += points
                scores[v.game_app_id]["vote_count"] += 1
                voter_name = v.user.display_name or v.user.username if v.user else "Unknown"
                scores[v.game_app_id]["voters"].append({"display_name": voter_name, "rank": v.rank})

            # Current user's votes
            my_votes = GameVote.query.filter_by(user_id=user.id).order_by(GameVote.rank.asc()).all()
            my_rankings = {v.game_app_id: v.rank for v in my_votes}

            # Total unique voters
            total_voters = len(set(v.user_id for v in all_votes))

            return _vote_cors(
                jsonify(
                    {"scores": scores, "my_rankings": my_rankings, "total_voters": total_voters}
                ),
                200,
            )

        if request.method == "PUT":
            from models import GameVote, GameListEntry
            from database import db

            data = request.get_json()
            if not data or not isinstance(data.get("rankings"), dict):
                return _vote_cors(jsonify({"error": "rankings dict required"}), 400)

            rankings = data["rankings"]  # {app_id: rank}
            max_rank = GameListEntry.query.count() or len(rankings)

            # Validate ranks
            ranks_seen = set()
            for app_id, rank in rankings.items():
                if not isinstance(rank, int) or rank < 1 or rank > max_rank:
                    return _vote_cors(jsonify({"error": f"Invalid rank {rank} for {app_id}"}), 400)
                if rank in ranks_seen:
                    return _vote_cors(jsonify({"error": f"Duplicate rank {rank}"}), 400)
                ranks_seen.add(rank)

            try:
                # Delete existing votes for this user
                GameVote.query.filter_by(user_id=user.id).delete()

                # Insert new votes
                for app_id, rank in rankings.items():
                    vote = GameVote(
                        user_id=user.id,
                        game_app_id=str(app_id),
                        rank=rank,
                        updated_at=datetime.utcnow(),
                    )
                    db.session.add(vote)

                db.session.commit()
                logger.info("User %s submitted game votes: %s", user.username, rankings)
                return _vote_cors(jsonify({"ok": True}), 200)
            except Exception as e:
                db.session.rollback()
                logger.error("Failed to save game votes: %s", e)
                return _vote_cors(jsonify({"error": "Save failed"}), 500)

        return _vote_cors(jsonify({"error": "Method not allowed"}), 405)

    # Profile & Achievements API — returns user profile data with achievements.
    # GET /check/profile returns the authenticated user's profile + achievements.
    # Runs achievement checks on each call to award any newly earned ones.
    if service == "profile":

        def _profile_cors(resp, status=200):
            if isinstance(resp, tuple):
                response = make_response(resp[0], resp[1])
            else:
                response = make_response(resp, status)
            origin = request.headers.get("Origin")
            if _is_valid_meduseld_origin(origin):
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
                response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            return response

        if request.method == "OPTIONS":
            return _profile_cors("", 204)

        user = _authenticate_from_cookie()
        if not user:
            return _profile_cors(jsonify({"error": "Authentication required"}), 401)

        if request.method == "GET":
            from models import UserAchievement, TriviaWin, get_all_achievements
            from database import db
            from sqlalchemy import func

            # Run achievement checks (awards any new ones)
            new_achievements = check_achievements(user)

            # Get all unlocked achievements
            unlocked = UserAchievement.query.filter_by(user_id=user.id).all()
            unlocked_list = [a.to_dict() for a in unlocked]
            unlocked_ids = set(a.achievement_id for a in unlocked)

            # Build full achievement list with locked/unlocked status
            all_achievements = []
            all_achs = get_all_achievements()
            for aid, defn in all_achs.items():
                entry = {
                    "achievement_id": aid,
                    "name": defn["name"],
                    "description": defn["description"],
                    "icon": defn["icon"],
                    "category": defn["category"],
                    "unlocked": aid in unlocked_ids,
                }
                # Add unlock date if unlocked
                for ua in unlocked:
                    if ua.achievement_id == aid:
                        entry["unlocked_at"] = (
                            ua.unlocked_at.isoformat() if ua.unlocked_at else None
                        )
                        break
                all_achievements.append(entry)

            # Trivia stats summary
            trivia_stats = {}
            trivia_rows = (
                db.session.query(
                    func.count(TriviaWin.id).label("games_played"),
                    func.sum(TriviaWin.score).label("total_correct"),
                    func.sum(TriviaWin.total_questions).label("total_questions"),
                    func.max(TriviaWin.score).label("best_score"),
                )
                .filter(TriviaWin.user_id == user.id)
                .first()
            )
            if trivia_rows and trivia_rows.games_played:
                total_q = trivia_rows.total_questions or 0
                total_c = trivia_rows.total_correct or 0
                trivia_stats = {
                    "games_played": trivia_rows.games_played,
                    "total_correct": total_c,
                    "total_wrong": total_q - total_c,
                    "total_questions": total_q,
                    "best_score": trivia_rows.best_score or 0,
                    "accuracy": round((total_c / total_q) * 100, 1) if total_q else 0,
                }

            profile = user.to_dict()
            profile["achievements"] = all_achievements
            profile["trivia"] = trivia_stats
            profile["new_achievements"] = new_achievements

            return _profile_cors(jsonify(profile), 200)

        return _profile_cors(jsonify({"error": "Method not allowed"}), 405)

    # Easter egg achievement — awarded when user clicks the hidden logo link
    if service == "easter-egg":

        def _egg_cors(resp, status=200):
            if isinstance(resp, tuple):
                response = make_response(resp[0], resp[1])
            else:
                response = make_response(resp, status)
            origin = request.headers.get("Origin")
            if _is_valid_meduseld_origin(origin):
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
                response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            return response

        if request.method == "OPTIONS":
            return _egg_cors("", 204)
        if request.method != "POST":
            return _egg_cors(jsonify({"error": "Method not allowed"}), 405)

        user = _authenticate_from_cookie()
        if not user:
            return _egg_cors(jsonify({"error": "Authentication required"}), 401)

        from models import UserAchievement, get_all_achievements
        from database import db

        all_achs = get_all_achievements()
        existing = UserAchievement.query.filter_by(
            user_id=user.id, achievement_id="easter_egg"
        ).first()
        if existing:
            return _egg_cors(jsonify({"ok": True, "already": True}), 200)

        if "easter_egg" in all_achs:
            try:
                ua = UserAchievement(user_id=user.id, achievement_id="easter_egg")
                db.session.add(ua)
                db.session.commit()
                logger.info("User %s found the easter egg!", user.username)
                return _egg_cors(jsonify({"ok": True, "unlocked": True}), 201)
            except Exception as e:
                db.session.rollback()
                logger.error("Failed to award easter egg to user %s: %s", user.username, e)
                return _egg_cors(jsonify({"error": "Save failed"}), 500)

        return _egg_cors(jsonify({"error": "Achievement not found"}), 404)

    # Custom achievements — admin CRUD + manual award to users
    if service == "custom-achievements" or service.startswith("custom-achievements-"):

        def _ca_cors(resp, status=200):
            if isinstance(resp, tuple):
                response = make_response(resp[0], resp[1])
            else:
                response = make_response(resp, status)
            origin = request.headers.get("Origin")
            if _is_valid_meduseld_origin(origin):
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
                response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            return response

        if request.method == "OPTIONS":
            return _ca_cors("", 204)

        user = _authenticate_from_cookie()
        if not user:
            return _ca_cors(jsonify({"error": "Authentication required"}), 401)

        if service == "custom-achievements":
            # GET = list all custom achievements, POST = create new (admin only)
            if request.method == "GET":
                from models import CustomAchievement

                cas = CustomAchievement.query.order_by(CustomAchievement.created_at.desc()).all()
                return _ca_cors(jsonify({"achievements": [ca.to_dict() for ca in cas]}), 200)

            if request.method == "POST":
                if user.role != "admin":
                    return _ca_cors(jsonify({"error": "Insufficient permissions"}), 403)
                from models import CustomAchievement
                from database import db

                data = request.get_json()
                if not data or not data.get("name") or not data.get("description"):
                    return _ca_cors(jsonify({"error": "name and description required"}), 400)

                # Generate achievement_id from name
                aid = "custom_" + data["name"].lower().replace(" ", "_")[:40]
                existing = CustomAchievement.query.filter_by(achievement_id=aid).first()
                if existing:
                    return _ca_cors(
                        jsonify({"error": "Achievement with this name already exists"}), 409
                    )

                try:
                    ca = CustomAchievement(
                        achievement_id=aid,
                        name=data["name"],
                        description=data["description"],
                        icon=data.get("icon", "bi-award"),
                        category=data.get("category", "custom"),
                        created_by=user.id,
                    )
                    db.session.add(ca)
                    db.session.commit()
                    logger.info("Admin %s created custom achievement: %s", user.username, aid)
                    return _ca_cors(jsonify({"achievement": ca.to_dict()}), 201)
                except Exception as e:
                    db.session.rollback()
                    logger.error("Failed to create custom achievement: %s", e)
                    return _ca_cors(jsonify({"error": "Create failed"}), 500)

            return _ca_cors(jsonify({"error": "Method not allowed"}), 405)

        # /check/custom-achievements-award — POST to award a custom achievement to a user
        if service == "custom-achievements-award":
            if request.method != "POST":
                return _ca_cors(jsonify({"error": "Method not allowed"}), 405)
            if user.role != "admin":
                return _ca_cors(jsonify({"error": "Insufficient permissions"}), 403)

            from models import UserAchievement, CustomAchievement
            from database import db

            data = request.get_json()
            if not data or not data.get("achievement_id") or not data.get("user_id"):
                return _ca_cors(jsonify({"error": "achievement_id and user_id required"}), 400)

            ca = CustomAchievement.query.filter_by(achievement_id=data["achievement_id"]).first()
            if not ca:
                return _ca_cors(jsonify({"error": "Custom achievement not found"}), 404)

            existing = UserAchievement.query.filter_by(
                user_id=data["user_id"], achievement_id=data["achievement_id"]
            ).first()
            if existing:
                return _ca_cors(jsonify({"ok": True, "already": True}), 200)

            try:
                ua = UserAchievement(user_id=data["user_id"], achievement_id=data["achievement_id"])
                db.session.add(ua)
                db.session.commit()
                logger.info(
                    "Admin %s awarded %s to user %d",
                    user.username,
                    data["achievement_id"],
                    data["user_id"],
                )
                return _ca_cors(jsonify({"ok": True}), 201)
            except Exception as e:
                db.session.rollback()
                logger.error("Failed to award custom achievement: %s", e)
                return _ca_cors(jsonify({"error": "Award failed"}), 500)

        # /check/custom-achievements-<id> — DELETE to remove a custom achievement (admin only)
        if service.startswith("custom-achievements-") and service != "custom-achievements-award":
            aid = service.split("custom-achievements-", 1)[1]
            if request.method != "DELETE":
                return _ca_cors(jsonify({"error": "Method not allowed"}), 405)
            if user.role != "admin":
                return _ca_cors(jsonify({"error": "Insufficient permissions"}), 403)

            from models import CustomAchievement, UserAchievement
            from database import db

            ca = CustomAchievement.query.filter_by(achievement_id=aid).first()
            if not ca:
                return _ca_cors(jsonify({"error": "Not found"}), 404)
            try:
                # Remove all user unlocks for this achievement
                UserAchievement.query.filter_by(achievement_id=aid).delete()
                db.session.delete(ca)
                db.session.commit()
                logger.info("Admin %s deleted custom achievement: %s", user.username, aid)
                return _ca_cors(jsonify({"ok": True}), 200)
            except Exception as e:
                db.session.rollback()
                logger.error("Failed to delete custom achievement: %s", e)
                return _ca_cors(jsonify({"error": "Delete failed"}), 500)

    # Trivia lobbies API — list active multiplayer lobbies
    if service == "trivia-lobbies":

        def _tl_cors(resp, status=200):
            if isinstance(resp, tuple):
                response = make_response(resp[0], resp[1])
            else:
                response = make_response(resp, status)
            origin = request.headers.get("Origin")
            if _is_valid_meduseld_origin(origin):
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
                response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            return response

        if request.method == "OPTIONS":
            return _tl_cors("", 204)

        if request.method == "GET":
            from trivia_ws import lobby_games

            active = []
            for code, lobby in lobby_games.items():
                if lobby.status == "waiting":
                    info = lobby.to_dict()
                    info["player_count"] = lobby.player_count()
                    active.append(info)
            return _tl_cors(jsonify({"lobbies": active}), 200)

        return _tl_cors(jsonify({"error": "Method not allowed"}), 405)

    # Trivia API — leaderboard and win recording for the trivia game page.
    if service == "trivia-leaderboard" or service == "trivia-record-win":

        def _trivia_cors(resp, status=200):
            if isinstance(resp, tuple):
                response = make_response(resp[0], resp[1])
            else:
                response = make_response(resp, status)
            origin = request.headers.get("Origin")
            if _is_valid_meduseld_origin(origin):
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
                response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            response.headers["Cache-Control"] = "no-store"
            return response

        if request.method == "OPTIONS":
            return _trivia_cors("", 204)

        if service == "trivia-leaderboard":
            if request.method == "GET":
                from models import TriviaWin, User as UserModel
                from database import db
                from sqlalchemy import func

                # Aggregate wins per user (only count rows where won=True)
                results = (
                    db.session.query(
                        TriviaWin.user_id,
                        func.count(TriviaWin.id).label("wins"),
                        func.sum(TriviaWin.score).label("total_score"),
                        func.sum(TriviaWin.total_questions).label("total_questions"),
                    )
                    .filter(TriviaWin.won == True)
                    .group_by(TriviaWin.user_id)
                    .order_by(func.count(TriviaWin.id).desc(), func.sum(TriviaWin.score).desc())
                    .all()
                )

                leaderboard = []
                for row in results:
                    u = UserModel.query.get(row.user_id)
                    if u:
                        leaderboard.append(
                            {
                                "user_id": u.id,
                                "discord_id": u.discord_id,
                                "display_name": u.display_name or u.username,
                                "avatar_url": u.avatar_url,
                                "wins": row.wins,
                                "total_score": row.total_score,
                                "total_questions": row.total_questions,
                            }
                        )

                return _trivia_cors(jsonify({"leaderboard": leaderboard}), 200)
            return _trivia_cors(jsonify({"error": "Method not allowed"}), 405)

        if service == "trivia-record-win":
            if request.method != "POST":
                return _trivia_cors(jsonify({"error": "Method not allowed"}), 405)

            user = _authenticate_from_cookie()
            if not user:
                return _trivia_cors(jsonify({"error": "Authentication required"}), 401)

            data = request.get_json()
            if (
                not data
                or not isinstance(data.get("score"), int)
                or not isinstance(data.get("total_questions"), int)
            ):
                return _trivia_cors(jsonify({"error": "score and total_questions required"}), 400)

            from models import TriviaWin
            from database import db

            try:
                win = TriviaWin(
                    user_id=user.id,
                    score=data["score"],
                    total_questions=data["total_questions"],
                    category=data.get("category", ""),
                )
                db.session.add(win)
                db.session.commit()
                logger.info(
                    "User %s recorded trivia win: %d/%d",
                    user.username,
                    data["score"],
                    data["total_questions"],
                )
                return _trivia_cors(jsonify({"ok": True}), 201)
            except Exception as e:
                db.session.rollback()
                logger.error("Failed to record trivia win: %s", e)
                return _trivia_cors(jsonify({"error": "Save failed"}), 500)

    # Admin users API — proxied through health so static pages don't need
    # a Cloudflare Access session for panel.meduseld.io.
    # Endpoint named "team-roster" to avoid ad-blocker false positives
    # (URLs containing "admin" are commonly blocked by filter lists).
    if service == "team-roster" or service.startswith("team-roster-"):

        def _cors_response(resp, status=200):
            """Add CORS headers to admin API responses."""
            if isinstance(resp, tuple):
                response = make_response(resp[0], resp[1])
            else:
                response = make_response(resp, status)
            origin = request.headers.get("Origin")
            if _is_valid_meduseld_origin(origin):
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Methods"] = "GET, PUT, OPTIONS"
                response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            return response

        if request.method == "OPTIONS":
            return _cors_response("", 204)

        user = _authenticate_from_cookie()
        if not user:
            return _cors_response(jsonify({"error": "Authentication required"}), 401)
        if user.role != "admin":
            return _cors_response(jsonify({"error": "Insufficient permissions"}), 403)

        if service == "team-roster":
            if request.method == "GET":
                from models import User as UserModel, TriviaWin
                from database import db
                from sqlalchemy import func

                users = UserModel.query.order_by(UserModel.created_at.desc()).all()

                # Aggregate trivia stats per user
                trivia_stats = {}
                stats_rows = (
                    db.session.query(
                        TriviaWin.user_id,
                        func.count(TriviaWin.id).label("games_played"),
                        func.sum(TriviaWin.score).label("total_correct"),
                        func.sum(TriviaWin.total_questions).label("total_questions"),
                        func.max(TriviaWin.score).label("best_score"),
                    )
                    .group_by(TriviaWin.user_id)
                    .all()
                )
                for row in stats_rows:
                    total_wrong = (row.total_questions or 0) - (row.total_correct or 0)
                    accuracy = (
                        round((row.total_correct / row.total_questions) * 100, 1)
                        if row.total_questions
                        else 0
                    )
                    trivia_stats[row.user_id] = {
                        "games_played": row.games_played,
                        "total_correct": row.total_correct or 0,
                        "total_wrong": total_wrong,
                        "total_questions": row.total_questions or 0,
                        "best_score": row.best_score or 0,
                        "accuracy": accuracy,
                    }

                user_list = []
                for u in users:
                    d = u.to_dict()
                    d["trivia"] = trivia_stats.get(
                        u.id,
                        {
                            "games_played": 0,
                            "total_correct": 0,
                            "total_wrong": 0,
                            "total_questions": 0,
                            "best_score": 0,
                            "accuracy": 0,
                        },
                    )
                    user_list.append(d)

                return _cors_response(jsonify({"users": user_list}), 200)
            return _cors_response(jsonify({"error": "Method not allowed"}), 405)

        # Extract user ID from service name: team-roster-<id>
        try:
            target_user_id = int(service.split("team-roster-")[1])
        except (ValueError, IndexError) as e:
            logger.error("Invalid team-roster proxy path: %s", e)
            return _cors_response(jsonify({"error": "Invalid user ID"}), 400)

        if request.method == "PUT":
            from models import User as UserModel
            from database import db

            target = UserModel.query.get(target_user_id)
            if not target:
                return _cors_response(jsonify({"error": "User not found"}), 404)

            data = request.get_json()
            if not data:
                return _cors_response(jsonify({"error": "No data provided"}), 400)

            if target.id == user.id and data.get("role") and data["role"] != "admin":
                return _cors_response(jsonify({"error": "Cannot change your own role"}), 400)

            if "role" in data and data["role"] in ("admin", "user"):
                target.role = data["role"]
            if "is_active" in data and isinstance(data["is_active"], bool):
                if target.id == user.id and not data["is_active"]:
                    return _cors_response(
                        jsonify({"error": "Cannot deactivate your own account"}), 400
                    )
                target.is_active = data["is_active"]

            try:
                db.session.commit()
                logger.info(
                    "Admin %s updated user %s via health proxy: role=%s, active=%s",
                    user.username,
                    target.username,
                    target.role,
                    target.is_active,
                )
                return _cors_response(jsonify({"user": target.to_dict()}), 200)
            except Exception as e:
                db.session.rollback()
                logger.error("Error updating user via health proxy: %s", e)
                return _cors_response(jsonify({"error": "Update failed"}), 500)

        return _cors_response(jsonify({"error": "Method not allowed"}), 405)

    # Party Game Picker API — manages the game pool and weekly spins.
    if (
        service == "picker-current"
        or service == "picker-spin"
        or service == "picker-history"
        or service == "picker-games"
        or service.startswith("picker-games-")
    ):

        def _picker_cors(resp, status=200):
            if isinstance(resp, tuple):
                response = make_response(resp[0], resp[1])
            else:
                response = make_response(resp, status)
            origin = request.headers.get("Origin")
            if _is_valid_meduseld_origin(origin):
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
                response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            response.headers["Cache-Control"] = "no-store"
            return response

        if request.method == "OPTIONS":
            return _picker_cors("", 204)

        # GET /check/picker-current — public, no auth needed
        if service == "picker-current":
            if request.method == "GET":
                from models import WeeklyPick

                today = date.today()
                monday = today - timedelta(days=today.weekday())
                pick = WeeklyPick.query.filter_by(week_start=monday).first()
                if pick:
                    return _picker_cors(jsonify({"pick": pick.to_dict()}), 200)
                return _picker_cors(jsonify({"pick": None}), 200)
            return _picker_cors(jsonify({"error": "Method not allowed"}), 405)

        # POST /check/picker-spin — authenticated, one spin per week (admins can re-spin)
        if service == "picker-spin":
            if request.method != "POST":
                return _picker_cors(jsonify({"error": "Method not allowed"}), 405)

            user = _authenticate_from_cookie()
            if not user:
                return _picker_cors(jsonify({"error": "Authentication required"}), 401)

            from models import WeeklyPick, PickerGame
            from database import db
            import random

            today = date.today()
            monday = today - timedelta(days=today.weekday())
            existing = WeeklyPick.query.filter_by(week_start=monday).first()

            if existing and user.role != "admin":
                return _picker_cors(
                    jsonify({"error": "Already spun this week", "pick": existing.to_dict()}), 409
                )

            active_games = PickerGame.query.filter_by(is_active=True).all()
            if not active_games:
                return _picker_cors(jsonify({"error": "No games in the pool"}), 400)

            chosen = random.choice(active_games)

            try:
                if existing:
                    existing.game_id = chosen.id
                    existing.spun_by = user.id
                    existing.spun_at = datetime.now(timezone.utc)
                else:
                    pick = WeeklyPick(
                        game_id=chosen.id,
                        spun_by=user.id,
                        week_start=monday,
                    )
                    db.session.add(pick)
                db.session.commit()
                result = WeeklyPick.query.filter_by(week_start=monday).first()
                logger.info("User %s spun the wheel: %s", user.username, chosen.name)

                # Broadcast spin to all connected picker clients
                try:
                    from trivia_ws import socketio

                    pick_data = result.to_dict()
                    # Include the game index in the active games list for wheel animation
                    game_ids = [g.id for g in active_games]
                    winner_index = game_ids.index(chosen.id) if chosen.id in game_ids else 0
                    socketio.emit(
                        "spin_result",
                        {"pick": pick_data, "winner_index": winner_index},
                        namespace="/picker",
                    )
                except Exception as e:
                    logger.error("Failed to broadcast picker spin: %s", e)

                return _picker_cors(jsonify({"pick": result.to_dict()}), 201)
            except Exception as e:
                db.session.rollback()
                logger.error("Failed to save weekly pick: %s", e)
                return _picker_cors(jsonify({"error": "Spin failed"}), 500)

        # GET /check/picker-history — public, DELETE — admin only (clear all history)
        if service == "picker-history":
            if request.method == "GET":
                from models import WeeklyPick

                picks = WeeklyPick.query.order_by(WeeklyPick.week_start.desc()).limit(20).all()
                return _picker_cors(jsonify({"history": [p.to_dict() for p in picks]}), 200)

            if request.method == "DELETE":
                user = _authenticate_from_cookie()
                if not user:
                    return _picker_cors(jsonify({"error": "Authentication required"}), 401)
                if user.role != "admin":
                    return _picker_cors(jsonify({"error": "Insufficient permissions"}), 403)

                from models import WeeklyPick
                from database import db

                try:
                    count = WeeklyPick.query.delete()
                    db.session.commit()
                    logger.info("Admin %s cleared picker history (%d picks)", user.username, count)

                    # Broadcast to all connected picker clients
                    try:
                        from trivia_ws import socketio

                        socketio.emit("history_cleared", {}, namespace="/picker")
                    except Exception as e:
                        logger.error("Failed to broadcast picker history clear: %s", e)

                    return _picker_cors(jsonify({"ok": True, "deleted": count}), 200)
                except Exception as e:
                    db.session.rollback()
                    logger.error("Failed to clear picker history: %s", e)
                    return _picker_cors(jsonify({"error": "Clear failed"}), 500)

            return _picker_cors(jsonify({"error": "Method not allowed"}), 405)

        # GET/POST /check/picker-games — list or add games
        if service == "picker-games":
            if request.method == "GET":
                from models import PickerGame

                games = (
                    PickerGame.query.filter_by(is_active=True).order_by(PickerGame.name.asc()).all()
                )
                return _picker_cors(jsonify({"games": [g.to_dict() for g in games]}), 200)

            user = _authenticate_from_cookie()
            if not user:
                return _picker_cors(jsonify({"error": "Authentication required"}), 401)

            if request.method == "POST":
                from models import PickerGame
                from database import db

                data = request.get_json()
                if not data or not data.get("name"):
                    return _picker_cors(jsonify({"error": "name required"}), 400)

                try:
                    game = PickerGame(
                        name=data["name"].strip(),
                        image_url=data.get("image_url", "").strip() or None,
                        added_by=user.id,
                    )
                    db.session.add(game)
                    db.session.commit()
                    logger.info("User %s added picker game: %s", user.username, game.name)

                    # Broadcast pool change
                    try:
                        from trivia_ws import socketio

                        socketio.emit("pool_updated", {}, namespace="/picker")
                    except Exception as e:
                        logger.error("Failed to broadcast picker pool update: %s", e)

                    return _picker_cors(jsonify({"game": game.to_dict()}), 201)
                except Exception as e:
                    db.session.rollback()
                    logger.error("Failed to add picker game: %s", e)
                    return _picker_cors(jsonify({"error": "Add failed"}), 500)

            return _picker_cors(jsonify({"error": "Method not allowed"}), 405)

        # DELETE /check/picker-games-<id> — admin only, soft delete
        if service.startswith("picker-games-"):
            try:
                game_id = int(service.split("picker-games-")[1])
            except (ValueError, IndexError) as e:
                logger.error("Invalid picker-games proxy path: %s", e)
                return _picker_cors(jsonify({"error": "Invalid game ID"}), 400)

            user = _authenticate_from_cookie()
            if not user:
                return _picker_cors(jsonify({"error": "Authentication required"}), 401)
            if user.role != "admin":
                return _picker_cors(jsonify({"error": "Insufficient permissions"}), 403)

            if request.method == "DELETE":
                from models import PickerGame
                from database import db

                game = PickerGame.query.get(game_id)
                if not game:
                    return _picker_cors(jsonify({"error": "Game not found"}), 404)

                try:
                    game.is_active = False
                    db.session.commit()
                    logger.info("Admin %s removed picker game: %s", user.username, game.name)

                    # Broadcast pool change
                    try:
                        from trivia_ws import socketio

                        socketio.emit("pool_updated", {}, namespace="/picker")
                    except Exception as e:
                        logger.error("Failed to broadcast picker pool update: %s", e)

                    return _picker_cors(jsonify({"ok": True}), 200)
                except Exception as e:
                    db.session.rollback()
                    logger.error("Failed to remove picker game: %s", e)
                    return _picker_cors(jsonify({"error": "Delete failed"}), 500)

            return _picker_cors(jsonify({"error": "Method not allowed"}), 405)

    # Remote Desktop sessions — list active sessions (public, authenticated users only)
    if service == "remote-sessions":

        def _remote_cors(resp, status=200):
            if isinstance(resp, tuple):
                response = make_response(resp[0], resp[1])
            else:
                response = make_response(resp, status)
            origin = request.headers.get("Origin")
            if _is_valid_meduseld_origin(origin):
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
                response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            return response

        if request.method == "OPTIONS":
            return _remote_cors("", 204)

        if request.method == "GET":
            from remote_ws import remote_sessions as rs, cleanup_expired_sessions

            cleanup_expired_sessions(socketio)
            sessions_list = [s.to_dict() for s in rs.values() if not s.is_expired()]
            return _remote_cors(jsonify({"sessions": sessions_list}), 200)

        return _remote_cors(jsonify({"error": "Method not allowed"}), 405)

    # Hall of Fame API — community screenshots & clips gallery
    if service == "fame" or service.startswith("fame-"):

        FAME_UPLOAD_DIR = "/srv/media/fame"
        FAME_MAX_IMAGE_SIZE = 250 * 1024 * 1024  # 250MB
        FAME_MAX_VIDEO_SIZE = 250 * 1024 * 1024  # 250MB
        FAME_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
        FAME_ALLOWED_VIDEO_TYPES = {"video/mp4", "video/webm"}

        def _fame_cors(resp, status=200):
            if isinstance(resp, tuple):
                response = make_response(resp[0], resp[1])
            else:
                response = make_response(resp, status)
            origin = request.headers.get("Origin")
            if _is_valid_meduseld_origin(origin):
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
                response.headers["Access-Control-Allow-Headers"] = "Content-Type"
                response.headers["Access-Control-Allow-Credentials"] = "true"
            return response

        if request.method == "OPTIONS":
            return _fame_cors("", 204)

        if service == "fame":
            # GET /check/fame — list entries (paginated, sortable)
            if request.method == "GET":
                from models import FameEntry, FameVote
                from database import db

                page = request.args.get("page", 1, type=int)
                per_page = request.args.get("per_page", 20, type=int)
                per_page = min(per_page, 50)
                sort = request.args.get("sort", "votes")  # votes, newest, oldest
                filter_type = request.args.get("type", "")  # image, video, or empty
                filter_tag = request.args.get("tag", "").strip()

                query = FameEntry.query
                if filter_type in ("image", "video"):
                    query = query.filter_by(media_type=filter_type)
                if filter_tag:
                    query = query.filter(db.func.lower(FameEntry.tag) == filter_tag.lower())

                if sort == "newest":
                    query = query.order_by(FameEntry.created_at.desc())
                elif sort == "oldest":
                    query = query.order_by(FameEntry.created_at.asc())
                else:
                    query = query.order_by(FameEntry.vote_count.desc(), FameEntry.created_at.desc())

                total = query.count()
                entries = query.offset((page - 1) * per_page).limit(per_page).all()

                # Check which entries the current user has voted on
                voted_ids = set()
                user = _authenticate_from_cookie()
                if user:
                    user_votes = FameVote.query.filter(
                        FameVote.user_id == user.id,
                        FameVote.entry_id.in_([e.id for e in entries]),
                    ).all()
                    voted_ids = {v.entry_id for v in user_votes}

                result = []
                for e in entries:
                    d = e.to_dict()
                    d["voted"] = e.id in voted_ids
                    # Build media URL for uploads
                    if e.source_type == "upload" and e.file_path:
                        d["media_url"] = (
                            "https://health.meduseld.io/check/fame-media/"
                            + os.path.basename(e.file_path)
                        )
                    else:
                        d["media_url"] = e.url
                    result.append(d)

                return _fame_cors(
                    jsonify(
                        {"entries": result, "total": total, "page": page, "per_page": per_page}
                    ),
                    200,
                )

            # POST /check/fame — create entry (file upload or URL)
            if request.method == "POST":
                user = _authenticate_from_cookie()
                if not user:
                    return _fame_cors(jsonify({"error": "Authentication required"}), 401)

                # Check if it's a file upload (multipart) or JSON (link)
                if request.content_type and "multipart/form-data" in request.content_type:
                    title = request.form.get("title", "").strip()
                    caption = request.form.get("caption", "").strip()
                    tag = request.form.get("tag", "").strip() or None
                    file = request.files.get("file")

                    if not title:
                        return _fame_cors(jsonify({"error": "Title required"}), 400)
                    if not file:
                        return _fame_cors(jsonify({"error": "File required"}), 400)

                    content_type = file.content_type or ""
                    if content_type in FAME_ALLOWED_IMAGE_TYPES:
                        media_type = "image"
                        max_size = FAME_MAX_IMAGE_SIZE
                    elif content_type in FAME_ALLOWED_VIDEO_TYPES:
                        media_type = "video"
                        max_size = FAME_MAX_VIDEO_SIZE
                    else:
                        return _fame_cors(
                            jsonify(
                                {
                                    "error": "Unsupported file type. Use JPEG, PNG, GIF, WebP, MP4, or WebM."
                                }
                            ),
                            400,
                        )

                    # Read file and check size
                    file_data = file.read()
                    if len(file_data) > max_size:
                        limit_mb = max_size // (1024 * 1024)
                        return _fame_cors(
                            jsonify(
                                {"error": f"File too large. Max {limit_mb}MB for {media_type}s."}
                            ),
                            400,
                        )

                    # Save file
                    try:
                        os.makedirs(FAME_UPLOAD_DIR, exist_ok=True)
                        import uuid as _uuid

                        ext = os.path.splitext(file.filename or "")[1] or (
                            ".jpg" if media_type == "image" else ".mp4"
                        )
                        # Sanitize extension to prevent path injection
                        allowed_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".webm"}
                        ext = ext.lower()
                        if ext not in allowed_exts:
                            ext = ".jpg" if media_type == "image" else ".mp4"
                        filename = f"{_uuid.uuid4().hex}{ext}"
                        filepath = os.path.normpath(os.path.join(FAME_UPLOAD_DIR, filename))
                        if not filepath.startswith(FAME_UPLOAD_DIR):
                            return _fame_cors(jsonify({"error": "Invalid filename"}), 400)
                        with open(filepath, "wb") as f:
                            f.write(file_data)
                    except Exception as e:
                        logger.error("Failed to save fame upload: %s", e)
                        return _fame_cors(jsonify({"error": "File save failed"}), 500)

                    from models import FameEntry
                    from database import db

                    try:
                        entry = FameEntry(
                            user_id=user.id,
                            title=title,
                            caption=caption or None,
                            media_type=media_type,
                            source_type="upload",
                            file_path=filepath,
                            tag=tag,
                        )
                        db.session.add(entry)
                        db.session.commit()
                        logger.info("User %s uploaded fame entry: %s", user.username, title)
                        d = entry.to_dict()
                        d["voted"] = False
                        d["media_url"] = "https://health.meduseld.io/check/fame-media/" + filename
                        return _fame_cors(jsonify({"entry": d}), 201)
                    except Exception as e:
                        db.session.rollback()
                        logger.error("Failed to create fame entry: %s", e)
                        return _fame_cors(jsonify({"error": "Create failed"}), 500)

                else:
                    # JSON body — external link
                    data = request.get_json()
                    if not data or not data.get("title") or not data.get("url"):
                        return _fame_cors(jsonify({"error": "title and url required"}), 400)

                    title = data["title"].strip()
                    url = data["url"].strip()
                    caption = data.get("caption", "").strip()
                    tag = data.get("tag", "").strip() or None
                    media_type = data.get("media_type", "video")
                    if media_type not in ("image", "video"):
                        media_type = "video"

                    from models import FameEntry
                    from database import db

                    try:
                        entry = FameEntry(
                            user_id=user.id,
                            title=title,
                            caption=caption or None,
                            media_type=media_type,
                            source_type="link",
                            url=url,
                            tag=tag,
                        )
                        db.session.add(entry)
                        db.session.commit()
                        logger.info("User %s added fame link: %s", user.username, title)
                        d = entry.to_dict()
                        d["voted"] = False
                        d["media_url"] = url
                        return _fame_cors(jsonify({"entry": d}), 201)
                    except Exception as e:
                        db.session.rollback()
                        logger.error("Failed to create fame link entry: %s", e)
                        return _fame_cors(jsonify({"error": "Create failed"}), 500)

            return _fame_cors(jsonify({"error": "Method not allowed"}), 405)

        # GET /check/fame-tags — return all unique tags
        if service == "fame-tags":
            if request.method == "GET":
                from models import FameEntry
                from database import db

                rows = (
                    db.session.query(FameEntry.tag)
                    .filter(FameEntry.tag.isnot(None), FameEntry.tag != "")
                    .distinct()
                    .all()
                )
                tags = sorted(set(r[0] for r in rows), key=str.lower)
                return _fame_cors(jsonify({"tags": tags}), 200)
            return _fame_cors(jsonify({"error": "Method not allowed"}), 405)

        # DELETE /check/fame-<id> — delete entry (owner or admin)
        if (
            service.startswith("fame-")
            and service != "fame-media"
            and not service.startswith("fame-media/")
        ):
            # Vote toggle: POST /check/fame-<id>-vote
            if service.endswith("-vote"):
                try:
                    entry_id = int(service.replace("-vote", "").split("fame-")[1])
                except (ValueError, IndexError) as e:
                    logger.error("Invalid fame vote path: %s", e)
                    return _fame_cors(jsonify({"error": "Invalid entry ID"}), 400)

                if request.method != "POST":
                    return _fame_cors(jsonify({"error": "Method not allowed"}), 405)

                user = _authenticate_from_cookie()
                if not user:
                    return _fame_cors(jsonify({"error": "Authentication required"}), 401)

                from models import FameEntry, FameVote
                from database import db

                entry = FameEntry.query.get(entry_id)
                if not entry:
                    return _fame_cors(jsonify({"error": "Entry not found"}), 404)

                existing = FameVote.query.filter_by(user_id=user.id, entry_id=entry_id).first()
                try:
                    if existing:
                        db.session.delete(existing)
                        entry.vote_count = max(0, entry.vote_count - 1)
                        db.session.commit()
                        return _fame_cors(
                            jsonify({"voted": False, "vote_count": entry.vote_count}), 200
                        )
                    else:
                        vote = FameVote(user_id=user.id, entry_id=entry_id)
                        db.session.add(vote)
                        entry.vote_count = entry.vote_count + 1
                        db.session.commit()
                        return _fame_cors(
                            jsonify({"voted": True, "vote_count": entry.vote_count}), 200
                        )
                except Exception as e:
                    db.session.rollback()
                    logger.error("Failed to toggle fame vote: %s", e)
                    return _fame_cors(jsonify({"error": "Vote failed"}), 500)

            # DELETE /check/fame-<id>
            try:
                entry_id = int(service.split("fame-")[1])
            except (ValueError, IndexError) as e:
                logger.error("Invalid fame delete path: %s", e)
                return _fame_cors(jsonify({"error": "Invalid entry ID"}), 400)

            if request.method != "DELETE":
                return _fame_cors(jsonify({"error": "Method not allowed"}), 405)

            user = _authenticate_from_cookie()
            if not user:
                return _fame_cors(jsonify({"error": "Authentication required"}), 401)

            from models import FameEntry, FameVote
            from database import db

            entry = FameEntry.query.get(entry_id)
            if not entry:
                return _fame_cors(jsonify({"error": "Entry not found"}), 404)

            # Owner or admin can delete
            if entry.user_id != user.id and user.role != "admin":
                return _fame_cors(jsonify({"error": "Insufficient permissions"}), 403)

            try:
                # Delete file if it was an upload
                if entry.source_type == "upload" and entry.file_path:
                    try:
                        os.remove(entry.file_path)
                    except OSError as e:
                        logger.error("Failed to delete fame file %s: %s", entry.file_path, e)

                FameVote.query.filter_by(entry_id=entry_id).delete()
                db.session.delete(entry)
                db.session.commit()
                logger.info("User %s deleted fame entry %d", user.username, entry_id)
                return _fame_cors(jsonify({"ok": True}), 200)
            except Exception as e:
                db.session.rollback()
                logger.error("Failed to delete fame entry: %s", e)
                return _fame_cors(jsonify({"error": "Delete failed"}), 500)

    # FellowSync active rooms — public, no auth needed
    if service == "fellowsync-rooms":

        def _fs_cors(resp, status=200):
            if isinstance(resp, tuple):
                response = make_response(resp[0], resp[1])
            else:
                response = make_response(resp, status)
            origin = request.headers.get("Origin")
            if _is_valid_meduseld_origin(origin):
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
                response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            return response

        if request.method == "OPTIONS":
            return _fs_cors("", 204)

        if request.method == "GET":
            try:
                resp = requests.get("http://127.0.0.1:5050/api/rooms/active", timeout=5)
                resp.raise_for_status()
                return _fs_cors(jsonify(resp.json()), 200)
            except Exception as e:
                logger.error("FellowSync rooms proxy error: %s", e)
                return _fs_cors(jsonify({"rooms": [], "count": 0}), 200)

        return _fs_cors(jsonify({"error": "Method not allowed"}), 405)

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
        logger.error("Error checking service status: %s", e)
        return jsonify({"status": "error", "message": "Service check failed"}), 502


# ================= FAME MEDIA =================


@app.route("/check/fame-media/<filename>", methods=["GET", "OPTIONS"])
def serve_fame_media(filename):
    """Serve uploaded fame media files from /srv/media/fame/"""
    host = request.host.split(":")[0]
    if host != "health.meduseld.io":
        abort(404)

    if not filename or "/" in filename or ".." in filename:
        abort(404)

    base_path = "/srv/media/fame"
    filepath = os.path.normpath(os.path.join(base_path, filename))
    if not filepath.startswith(base_path + os.sep):
        abort(404)
    if not os.path.isfile(filepath):
        abort(404)

    import mimetypes

    mime = mimetypes.guess_type(filepath)[0] or "application/octet-stream"
    try:
        with open(filepath, "rb") as f:
            data = f.read()
        resp = make_response(data)
        resp.headers["Content-Type"] = mime
        resp.headers["Cache-Control"] = "public, max-age=86400"
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp
    except Exception as e:
        logger.error("Failed to serve fame media %s: %s", filename, e)
        return abort(500)


# ================= JELLYFIN PROXY =================


@app.route("/<path:path>", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
def jellyfin_catch_all(path):
    """Catch-all for Jellyfin subdomain paths"""
    host = request.host.split(":")[0]

    # Serve auto-login page on Jellyfin domain.
    # Uses iframe-first approach: loads Jellyfin web client in a hidden iframe
    # so its ConnectionManager initializes jellyfin_credentials in localStorage,
    # then patches in our AccessToken/UserId. This prevents the race condition
    # where Jellyfin's JS overwrites our credentials after redirect.
    if host == "jellyfin.meduseld.io" and path == "sso-login":
        token = request.args.get("token", "")
        user_id = request.args.get("userId", "")
        server_id = request.args.get("serverId", "")

        # Escape values for safe inline script embedding (prevent </script> injection)
        def _js_safe(val):
            return json.dumps(val).replace("<", "\\u003c").replace(">", "\\u003e")

        _safe_token = _js_safe(token)
        _safe_user_id = _js_safe(user_id)
        _safe_server_id = _js_safe(server_id)
        if not token or not user_id:
            return redirect("https://jellyfin.meduseld.io")
        return (
            f"""<!DOCTYPE html>
<html>
<head><title>Connecting to Edoras...</title></head>
<body style="background:#0b1f14;color:#e6c65c;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">
<div id="status" style="text-align:center;">
<div style="margin-bottom:16px;"><div style="width:40px;height:40px;border:3px solid #e6c65c;border-top-color:transparent;border-radius:50%;animation:spin 1s linear infinite;margin:0 auto;"></div></div>
<p>Connecting to Edoras...</p>
</div>
<style>@keyframes spin{{from{{transform:rotate(0deg)}}to{{transform:rotate(360deg)}}}}</style>
<script>
(function() {{
    var token = {_safe_token};
    var userId = {_safe_user_id};
    var serverId = {_safe_server_id};
    var POLL_INTERVAL = 250;
    var MAX_WAIT = 15000;
    var elapsed = 0;

    // Load Jellyfin web client in a hidden iframe so its ConnectionManager
    // initializes localStorage (jellyfin_credentials, _deviceId2, etc.)
    var iframe = document.createElement("iframe");
    iframe.style.cssText = "position:absolute;width:0;height:0;border:0;visibility:hidden;";
    iframe.src = "/web/index.html";
    document.body.appendChild(iframe);

    // Poll localStorage until Jellyfin has initialized its credentials
    var poll = setInterval(function() {{
        elapsed += POLL_INTERVAL;
        try {{
            var raw = localStorage.getItem("jellyfin_credentials");
            if (raw) {{
                var creds = JSON.parse(raw);
                // Wait until Jellyfin has set up Servers[0].Id (server discovery done)
                if (creds.Servers && creds.Servers.length > 0 && creds.Servers[0].Id) {{
                    // Patch in our auth token and user ID
                    creds.Servers[0].AccessToken = token;
                    creds.Servers[0].UserId = userId;
                    creds.Servers[0].ManualAddress = "https://jellyfin.meduseld.io";
                    creds.Servers[0].LastConnectionMode = 2;
                    creds.Servers[0].manualAddressOnly = true;
                    creds.Servers[0].DateLastAccessed = new Date().getTime();
                    localStorage.setItem("jellyfin_credentials", JSON.stringify(creds));
                    localStorage.setItem("enableAutoLogin", "true");
                    clearInterval(poll);
                    // Remove iframe and redirect
                    iframe.remove();
                    window.location.replace("/web/index.html#!/home.html");
                    return;
                }}
            }}
        }} catch(e) {{}}

        if (elapsed >= MAX_WAIT) {{
            // Timeout — fall back to direct credential write
            clearInterval(poll);
            iframe.remove();
            try {{
                var c = {{"Servers":[{{"AccessToken":token,"UserId":userId,"Id":serverId,"ManualAddress":"https://jellyfin.meduseld.io","LastConnectionMode":2,"manualAddressOnly":true,"DateLastAccessed":new Date().getTime()}}]}};
                localStorage.setItem("jellyfin_credentials", JSON.stringify(c));
                localStorage.setItem("enableAutoLogin", "true");
            }} catch(e) {{}}
            window.location.replace("/web/index.html#!/home.html");
        }}
    }}, POLL_INTERVAL);
}})();
</script>
</body>
</html>""",
            200,
            {"Content-Type": "text/html"},
        )

    # Handle jellyfin-auth on the jellyfin subdomain so the auto-login
    # script can call it same-origin (avoids Cloudflare Access CORS issues)
    if host == "jellyfin.meduseld.io" and path == "api/jellyfin-auth":
        return api_jellyfin_auth()

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
    return abort(404)


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
    socketio.run(app, host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG)
