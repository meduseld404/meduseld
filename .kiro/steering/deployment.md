---
description: Server infrastructure, systemd service, domains, API endpoints, database, authentication flow, and environment variables for the Meduseld project
---

# Meduseld Deployment Information

## Production Environment

The Meduseld control panel runs on a Linux server (Ubuntu) without Docker.

## Site Structure & Domains

### meduseld-site Repository (Static Pages)

Served via Cloudflare Pages at `/srv/meduseld-site`

- **meduseld.io** (root index.html)
  - Landing page with "404 Server Not Found" theme
  - Title: "Meduseld - 404 Server Not Found"
  - Single button linking to menu.meduseld.io

- **menu.meduseld.io** (menu/index.html)
  - Main navigation hub - "The Great Hall"
  - Title: "The Great Hall - Meduseld"
  - Service cards for: Game Server Panel, SSH Access, Jellyfin, System Monitor
  - Coming soon cards: Game Wiki, VPN Access, Trivia Game, Hall of Fame
  - Game news panel (fetches from Steam)
  - "Games Up Next" list with Steam pricing
  - Server specifications display
  - Quick links to external services

- **system.meduseld.io** (system/index.html)
  - System monitoring and server logs viewer
  - Title: "Meduseld - System Info"
  - Displays server logs from panel.meduseld.io API
  - Linux commands help modal
  - Auto-refreshes logs every 30 seconds
  - Admin-only: non-admin users are redirected to services

- **admin.meduseld.io** (admin/index.html)
  - Admin user management page (static, served by Cloudflare Pages)
  - Title: "Meduseld - User Management"
  - Admin-only: non-admin users are redirected to services
  - Lists all users with avatar, name, Discord ID, role, status, last login
  - Promote/demote users between admin and user roles
  - Activate/deactivate user accounts
  - Shows "Backend Offline" state if Flask is down
  - Calls `health.meduseld.io/check/team-roster` for data (routed through health to bypass Cloudflare Access session requirement). The endpoint is named "team-roster" instead of "admin-users" to avoid ad-blocker false positives. The admin page reads the `CF_Authorization` cookie via JS and passes its value as a `cf_token` query parameter (GET) or `_cf_token` in the JSON body (PUT) — this avoids both Cloudflare cookie interception and CORS preflight issues. Flask's `_authenticate_from_cookie()` checks cookie, header, query param, and body for the token.

- **herugrim.meduseld.io** (herugrim/index.html)
  - Public landing page for the Herugrim open-source project
  - Centered logo, title, subtitle, and "View on GitHub" link
  - No authentication required

- **status.meduseld.io** (status/index.html)
  - Server status page shown when the tunnel/server is down
  - Polls health Worker for live service status
  - No authentication required

### meduseld Repository (Flask Backend)

Python Flask application at `/srv/meduseld`

- **panel.meduseld.io** (app/webserver.py)
  - Game server control panel (Flask backend)
  - Start/stop/restart Icarus dedicated server
  - Real-time server stats and monitoring
  - Player count tracking
  - Update checking and management
  - API endpoints for system stats, logs, console output
  - Jellyfin proxy at /jellyfin/\*
  - Health check endpoints

- **ssh.meduseld.io** / **terminal.meduseld.io**
  - Web-based SSH terminal access
  - Proxied through Flask app

- **jellyfin.meduseld.io**
  - Media streaming service
  - Proxied through Flask app at /jellyfin/\*

- **health.meduseld.io**
  - Public health check endpoint
  - Returns server status without authentication

### Health Worker (Cloudflare Worker)

Cloudflare Worker at `https://meduseld-health.404-41f.workers.dev` that checks the status of all Meduseld services. Used by the services page and admin page to show Online/Offline/Tunnel Down badges.

- First checks tunnel status by fetching `panel.meduseld.io/health` (bypasses Cloudflare Access). If the response is JSON, the tunnel is up. If the response is an HTML error page (e.g., error 1033), the tunnel is down and all services are reported as `tunnel-down`. If the request times out or fails, also reported as tunnel down.
- If the tunnel is up, performs individual `HEAD` requests to each service URL (`panel.meduseld.io`, `ssh.meduseld.io`, `jellyfin.meduseld.io`) with a 5-second timeout.
- Returns JSON with per-service status: `online` (green), `offline` (red), `tunnel-down` (orange), `timeout` (orange), `error` (red).
- CORS: `Access-Control-Allow-Origin: *`, no caching.
- Important: Worker-to-Worker fetches within Cloudflare's network get intercepted by Cloudflare Access (returns login page as 200 OK), so the tunnel check must use a path that bypasses Access. The `/health` path is configured as a bypass in the Cloudflare Access application for `panel.meduseld.io`.
- No env vars required.

### ExSpire (Standalone Node.js App)

Express application at `/srv/apps/exspire`

- **exspire.meduseld.io** — Expiry tracking app
  - Standalone Express backend on port 3001, serves its own React frontend from `../frontend/dist`
  - Routed through Cloudflare Tunnel (not proxied through Flask). Tunnel ingress is managed via the Cloudflare Dashboard (remote management), not the local `config.yml` — the dashboard config takes precedence.
  - No Cloudflare Access protection — ExSpire has its own auth system (email/password with JWT), independent of Meduseld's Discord OIDC auth
  - systemd: `exspire.service`
  - Database: sql.js (SQLite via WebAssembly)
  - Env: `/etc/exspire.env` (root:vertebra, 640) — `PORT`, `JWT_SECRET`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `NOTIFICATION_FROM`, `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`
  - Deployment: `cd /srv/apps/exspire && git pull && cd backend && npm install --production && cd ../frontend && npm install && npm run build && sudo systemctl restart exspire`
  - Auto-deploy: `exspire-deploy.timer` runs every 5 minutes, checks for new commits on `main`, pulls + installs dependencies (backend and frontend) + rebuilds + restarts if changes detected. Logs: `journalctl -u exspire-deploy.service`
  - Repo: separate `exspire` workspace folder
  - Note: The old static demo page at `meduseld-site/exspire/index.html` has been removed. ExSpire is served entirely through the Cloudflare Tunnel via its Express backend.

### herugrim Repository (Discord OIDC Worker)

Cloudflare Worker (`worker.js`) that acts as an OIDC identity provider bridging Discord OAuth to Cloudflare Access.

- Handles Discord OAuth flow: `/authorize`, `/token`, `/jwks.json`, `/health`
- All configuration via Wrangler environment variables (`CLIENT_ID`, `CLIENT_SECRET`, `REDIRECT_URI`, `ALLOWED_GUILDS`, `ADMIN_GUILD_ID`, `ADMIN_ROLE_ID`) — no hardcoded values in source. `CLIENT_SECRET` should be set via `wrangler secret put` for production.
- OAuth scopes are dynamic: `identify email guilds` by default, adds `guilds.members.read` only when `ADMIN_ROLE_ID` is configured
- Returns JWT id_tokens containing a `discord_user` object with real Discord profile data (id, username, global_name, avatar, discriminator, is_admin)
- JWT issuer is set dynamically from the request origin (not hardcoded)
- `is_admin` detection is optional — enabled by setting `ADMIN_ROLE_ID` env var. When enabled, checks if the user has that role in the configured `ADMIN_GUILD_ID` (defaults to `ALLOWED_GUILDS[0]` if not set) via `GET /users/@me/guilds/{guild_id}/member`. When disabled, `is_admin` is always `false`.
- Error handling on all Discord API calls (token exchange, user fetch, guilds fetch, member fetch) with descriptive error responses
- `GET /health` endpoint returns `{ "status": "ok" }` for uptime monitoring
- Cloudflare Access is configured with this worker as an OIDC identity provider
- Claims config in Cloudflare Access: `["id", "preferred_username", "name", "discord_user"]`
- The `discord_user` claim appears under the `custom` key in the Cloudflare Access identity response (NOT `oidc_fields`)

## Database

### PostgreSQL Setup

- **Database**: `meduseld_db`
- **User**: `meduseld`
- **Host**: localhost:5432
- **ORM**: Flask-SQLAlchemy with Flask-Migrate
- **Files**: `app/database.py` (init), `app/models.py` (models)
- **Dev mode**: Uses SQLite at `app/meduseld_dev.db` instead of PostgreSQL

### User Model (`app/models.py`)

Table: `users`

| Column            | Type        | Notes                                                                  |
| ----------------- | ----------- | ---------------------------------------------------------------------- |
| id                | Integer     | Primary key                                                            |
| discord_id        | String(64)  | Unique, indexed. Real Discord snowflake ID (e.g. `000000000000000000`) |
| username          | String(128) | Discord username                                                       |
| display_name      | String(128) | Discord global_name                                                    |
| avatar_hash       | String(256) | Discord avatar hash for CDN URL                                        |
| email             | String(256) | Email from Cloudflare Access JWT                                       |
| role              | String(32)  | Default `"user"`, also supports `"admin"`                              |
| is_active         | Boolean     | Default true                                                           |
| created_at        | DateTime    | UTC                                                                    |
| last_login        | DateTime    | Updated on each login                                                  |
| jellyfin_user_id  | String(64)  | Jellyfin user ID, set on first Edoras login                            |
| jellyfin_password | String(256) | Auto-generated Jellyfin password, managed by `/api/jellyfin-auth`      |

Key methods:

- `User.get_or_create(discord_id, username, ...)` — Looks up by `discord_id` first, falls back to `email` to prevent duplicate accounts when Cloudflare UUID changes. When found by email fallback with real Discord data (indicated by avatar_hash), updates discord_id and profile. When found by email fallback without real Discord data, skips profile updates to preserve existing Discord data.
- `user.to_dict()` — Serializes user for API responses and session storage. Includes `has_jellyfin` boolean derived from `jellyfin_user_id`.
- `user.avatar_url` — Property that builds Discord CDN avatar URL

### CalendarEvent Model (`app/models.py`)

Table: `calendar_events`

| Column      | Type        | Notes                     |
| ----------- | ----------- | ------------------------- |
| id          | Integer     | Primary key               |
| title       | String(256) | Event title               |
| description | Text        | Optional description      |
| event_date  | DateTime    | When the event occurs     |
| created_by  | Integer     | FK to `users.id`          |
| created_at  | DateTime    | UTC, auto-set on creation |

### EventRSVP Model (`app/models.py`)

Table: `event_rsvps`

| Column     | Type       | Notes                                               |
| ---------- | ---------- | --------------------------------------------------- |
| id         | Integer    | Primary key                                         |
| event_id   | Integer    | FK to `calendar_events.id`, CASCADE on delete       |
| user_id    | Integer    | FK to `users.id`                                    |
| status     | String(16) | `going`, `maybe`, or `not_going`                    |
| updated_at | DateTime   | UTC, auto-set on creation, updated on status change |

Unique constraint: `(event_id, user_id)` — one RSVP per user per event.

### Database Commands

```bash
# Connect to production DB
sudo -u postgres psql -d meduseld_db

# Check users
sudo -u postgres psql -d meduseld_db -c "SELECT discord_id, username, avatar_hash FROM users;"

# Tables are auto-created by db.create_all() on app startup
```

## Authentication Flow

### How Login Works (End-to-End)

1. User visits a Cloudflare Access-protected page (panel, services, system, ssh)
2. Cloudflare Access redirects to herugrim worker → Discord OAuth consent
3. Herugrim returns JWT id_token with `discord_user` claim to Cloudflare Access
4. Cloudflare Access sets `CF_Authorization` cookie and `Cf-Access-Jwt-Assertion` header
5. On Flask backend (panel.meduseld.io):
   - `authenticate_request()` middleware decodes the `Cf-Access-Jwt-Assertion` JWT
   - Fallback: if no header, decodes the `CF_Authorization` cookie instead (enables cross-origin auth from static pages)
   - The JWT `sub` is a Cloudflare UUID (NOT the Discord ID), but custom OIDC claims ARE available under the `custom` key (including `discord_user` with real Discord ID, username, global_name, avatar, is_admin)
   - `authenticate_request()` extracts `custom.discord_user` from the JWT when available, falling back to email-derived username if not present
   - `User.get_or_create()` creates/finds user by discord_id or email fallback
   - User stored in Flask session
6. On static pages (services, system via meduseld-site):
   - `auth.js` decodes `CF_Authorization` cookie client-side for basic user info
   - `auth.js` calls `/cdn-cgi/access/get-identity` to get full identity including `custom.discord_user` (which contains `is_admin` flag from herugrim)
   - `auth.js` sets admin role immediately from `discord_user.is_admin` — no backend dependency
   - `auth.js` POSTs to `https://panel.meduseld.io/api/sync-identity` with real Discord data (best-effort, non-blocking). Uses `redirect: 'manual'` to prevent Cloudflare Access login redirects from navigating the page away when the cookie is missing or expired.
   - `auth.js` calls `https://panel.meduseld.io/api/me` to get DB-synced user info (best-effort, non-blocking). Also uses `redirect: 'manual'`.

### Cross-Origin Auth (Static Pages → Flask API)

- Static pages on `services.meduseld.io` etc. call Flask API endpoints cross-origin via `fetch()` with `credentials: 'include'`
- The Flask session cookie is scoped to `panel.meduseld.io` and is NOT sent on these cross-origin requests
- The `Cf-Access-Jwt-Assertion` header is only added by Cloudflare on direct requests to the protected origin, not on JS fetch calls
- Solution: `authenticate_request()` falls back to the `CF_Authorization` cookie, which Cloudflare Access sets on `.meduseld.io` (all subdomains), so it IS available on cross-origin requests
- The `/api/sync-identity` endpoint uses `get_or_create` to ensure users are created even on cross-origin calls from static pages
- CORS is configured to allow `GET, POST, PUT, DELETE, OPTIONS` with credentials for `*.meduseld.io` origins

### Important Cloudflare Access Quirks

- Both `Cf-Access-Jwt-Assertion` header and `CF_Authorization` cookie contain custom OIDC claims under the `custom` key, including `custom.discord_user` with full Discord profile data (id, username, global_name, avatar, is_admin)
- `CF_Authorization` cookie is set on `.meduseld.io` domain — available across all subdomains, used as auth fallback for cross-origin API calls
- Discord user data is available both server-side (from JWT `custom.discord_user`) and client-side (from `/cdn-cgi/access/get-identity` under `identity.custom.discord_user`)
- Discord user data lives under `custom.discord_user`, NOT `oidc_fields`
- The Cloudflare UUID (`sub`) changes per-session, so email is used as fallback lookup to prevent duplicate user records

### Auth Files

- `meduseld/app/webserver.py` — `authenticate_request()` middleware, `_authenticate_from_cookie()` helper (for public-host routes that need auth; reads CF_Authorization cookie, X-CF-Authorization header, cf_token query param, or \_cf_token in JSON body), `@require_auth` and `@require_role` decorators, `/api/me`, `/api/sync-identity`
- `meduseld-site/static/auth.js` — Client-side auth: `MeduseldAuth.getUser()`, `.isAuthenticated()`, `.getRole()`, `.hasRole()`, `.syncUser()`
- `herugrim/worker.js` — Discord OIDC bridge worker

### Role-Based Access Control

Two roles exist: `user` (default) and `admin`.

- Admin status is primarily determined by Discord role: herugrim checks for role `1481870667015127144` in guild `924788704529252353` during OAuth and sets `discord_user.is_admin` in the JWT
- `auth.js` reads `is_admin` from the `discord_user` claim via `/cdn-cgi/access/get-identity` — no backend call needed for client-side admin detection
- This means admin UI (service cards, page access) works even when the Flask backend is offline
- Roles are also stored in the `users` table `role` column for server-side checks
- DB role auto-syncs from Discord: `authenticate_request()` promotes/demotes based on `discord_user.is_admin` in the JWT, and `sync-identity` does the same from the client-posted `is_admin` flag. This means adding/removing the Discord admin role automatically updates the DB role on next login.
- `auth.js` sends `is_admin` in the `/api/sync-identity` POST payload so static-page-only users also get their DB role synced
- `@require_role("admin")` decorator on Flask endpoints checks `g.user.role`
- `MeduseldAuth.hasRole("admin")` on the client side (reads `discord_user.is_admin` from identity, falls back to backend `syncUser()`)
- Admin-only pages: SSH Terminal (`ssh.meduseld.io`), System Monitor (`system.meduseld.io`), Admin Panel (`admin.meduseld.io`)
- Admin-only service cards (SSH, System Monitor) are hidden by default on the services page (`display: none`), shown via JS after auth confirms admin role
- Non-admin users navigating directly to admin-only pages are redirected to `services.meduseld.io?restricted=<page-name>`, which shows a toast banner
- Server-side: SSH terminal route returns 403 for non-admin users; admin API endpoints use `@require_auth` + `@require_role("admin")`
- Self-protection: admins cannot demote or deactivate their own account via the API

### Bootstrap: Setting the First Admin

The first admin is automatically set when a user with the Discord admin role (`1481870667015127144`) logs in — the DB role syncs from the JWT. Manual DB promotion is only needed if the Discord role check isn't working:

```bash
# Check existing users
sudo -u postgres psql -d meduseld_db -c "SELECT id, discord_id, username, role FROM users;"

# Promote a user to admin
sudo -u postgres psql -d meduseld_db -c "UPDATE users SET role = 'admin' WHERE discord_id = 'YOUR_DISCORD_ID';"
```

After the first admin is set, subsequent admins can be promoted from the admin page UI or by assigning the Discord admin role.

### Public Paths (No Auth Required)

- `/health`, `/health-bypass`
- `/api/check-service/<service>`
- Host: `health.meduseld.io`

### Cloudflare Access Configuration

- All subdomain apps need `options_preflight_bypass: true` to allow CORS preflight requests
- `panel.meduseld.io/health` is configured as a bypass path in Cloudflare Access so the health Worker can check tunnel status without being intercepted by the Access login page
- Cross-origin API calls from static pages to `panel.meduseld.io` (e.g. `/api/me`, `/api/sync-identity`) work because the `CF_Authorization` cookie is set on `.meduseld.io` and Cloudflare Access accepts it with `options_preflight_bypass` enabled. These calls use `redirect: 'manual'` so that if Cloudflare Access intercepts the request (expired/missing cookie), the browser returns an opaque redirect instead of navigating the page to the login flow. `auth.js` treats opaque redirects as non-critical failures.
- The Jellyfin auto-login script avoids cross-origin issues by calling `/api/jellyfin-auth` as a same-origin request on `jellyfin.meduseld.io` — the catch-all route handles it server-side

### Dev Mode Auth

- When `MEDUSELD_ENV=development`, a fake user `dev_user_000` is auto-created
- No Cloudflare Access JWT needed in dev mode
- SQLite database used instead of PostgreSQL

## Server Setup

- **Host**: Linux server (production)
- **Python**: Flask application running via virtualenv
- **Process Manager**: systemd
- **Port**: 5000 (production) / 5001 (dev)
- **User**: vertebra
- **App Directory**: `/srv/apps/meduseld`

### Application Structure

- Main app: `app/webserver.py`
- Config: `app/config.py`
- Database init: `app/database.py`
- Models: `app/models.py`
- Game server: `/srv/games/icarus`

### Server Directory Structure

```
/srv
├── ai-cli
├── apps
│   └── exspire
├── backups
├── compatibilitytools
│   └── GE-Proton10-32
├── games
│   └── icarus
├── media
│   ├── movies
│   └── tv
├── meduseld
│   ├── app
│   ├── logs
│   ├── nginx
│   └── webhook
├── meduseld-site
│   ├── menu
│   └── static
├── Steam
└── steamcmd
```

### How It Starts

Managed by systemd. Service file: `/etc/systemd/system/meduseld.service`

```ini
[Unit]
Description=Meduseld Control Panel
After=network.target

[Service]
User=vertebra
WorkingDirectory=/srv/apps/meduseld
Environment="JWT_SECRET=<redacted>"
Environment="MEDUSELD_ENV=production"
Environment="FLASK_SECRET_KEY=<redacted>"
Environment="GOOGLE_CLIENT_SECRET=<set from Google Cloud Console>"
Environment="DATABASE_URL=postgresql://meduseld:<password>@localhost:5432/meduseld_db"
ExecStart=/srv/apps/meduseld/venv/bin/python /srv/apps/meduseld/app/webserver.py
Restart=always
KillMode=process
KillSignal=SIGTERM

[Install]
WantedBy=multi-user.target
```

- Auto-restarts on failure (`Restart=always`)
- Uses virtualenv Python at `/srv/apps/meduseld/venv/bin/python`
- Manage with: `sudo systemctl start|stop|restart|status meduseld`
- After config changes: `sudo systemctl daemon-reload && sudo systemctl restart meduseld`

### Deployment Process

Push to main, then on the server pull and restart the service. The webhook at `/webhook/deploy.sh` automates this.

### Standalone Microservices

Three lightweight Python HTTP servers run independently of the Flask app so the system monitor page works even when the panel is down. All are managed by systemd and proxied through the Flask app's `check_service()` handler at `health.meduseld.io/check/<service>`.

1. **Monitoring Service** (`monitoring/stats_server.py`)
   - Port: 5004
   - systemd: `meduseld-monitoring.service`
   - Endpoints: `GET /stats` (live metrics), `GET /history` (30-min rolling data), `GET /health`
   - Reads: psutil (CPU, RAM, disk, temp), RAPL (CPU power), nvidia-smi (GPU power)
   - Env: `ELECTRICITY_COST_PER_KWH` (default 0.245, currently set to 0.145 for USD)
   - Dependency: `psutil`

2. **Reboot Service** (`reboot/reboot_server.py`)
   - Port: 5002
   - systemd: `meduseld-reboot.service`
   - Endpoints: `POST /reboot` (requires token), `GET /health`
   - Env: `REBOOT_SECRET`

3. **Backup Service** (`reboot/backup_server.py`)
   - Port: 5003
   - systemd: `meduseld-backup-api.service`
   - Endpoints: `POST /backup` (requires token), `GET /status`, `GET /health`
   - Triggers `meduseld-backup.service` via systemd
   - Env: `BACKUP_SECRET`

Flask proxy routing (`check_service()` in `webserver.py`): requests to `health.meduseld.io/check/stats` → `127.0.0.1:5004/stats`, `/check/history` → `127.0.0.1:5004/history`, `/check/backup` → `127.0.0.1:5003/backup`, `/check/backup-status` → `127.0.0.1:5003/status`, `/check/reboot` → `127.0.0.1:5002/reboot`, `/check/system-logs` → Flask's own `api_server_logs()`, `/check/media-auth` → Jellyfin SSO auth (authenticated, calls `_jellyfin_auth_inner()` to auto-provision and authenticate a Jellyfin account, returns `{token, user_id, server_id}`), `/check/team-roster` → admin users list (authenticated via CF_Authorization JWT passed as `cf_token` query param or `_cf_token` in body), `/check/team-roster-<id>` → admin user update (PUT), `/check/calendar` → calendar events list (GET) and create (POST, admin only), `/check/calendar-<id>` → delete calendar event (DELETE, admin only) or edit calendar event (PUT with `title`/`event_date`, admin only) or RSVP (PUT with `status`, any authenticated user). All authenticated endpoints use `_authenticate_from_cookie()` which reads the CF_Authorization JWT from cookie, header, `cf_token` query param, or `_cf_token` in JSON body.

### Common Issues

When the server "goes offline" after pressing start:

1. Check systemd journal (most reliable): `sudo journalctl -u meduseld --no-pager -n 100`
2. Check production log file: `tail -100 /srv/meduseld/logs/webserver.log`
3. Check if process is running: `ps aux | grep webserver.py`
4. Check systemd status: `systemctl status meduseld`
5. Run manually to see errors: `cd /srv/apps/meduseld && venv/bin/python app/webserver.py`

**Important log paths:**

- Production log file: `/srv/meduseld/logs/webserver.log` (configured in `app/config.py` as `LOG_FILE_PATH`)
- systemd journal: `sudo journalctl -u meduseld` (captures stdout/stderr, always up to date)
- Do NOT use `/srv/apps/meduseld/logs/webserver.log` — that path is from local dev sessions and will have stale data
- To search for specific errors: `sudo journalctl -u meduseld --no-pager -n 200 | grep -i "error\|calendar\|failed"`

## API Endpoints (panel.meduseld.io)

### Public Endpoints

- `GET /health` - Public health check (no auth required)
- `GET /api/check-service/<service>` - Check if service is online

### Auth Endpoints

- `GET /api/me` - Return current authenticated user info (or `{authenticated: false}`)
- `POST /api/sync-identity` - Accept Discord user data from client-side `auth.js` and create or update DB record (uses `get_or_create` so first-time users visiting only static pages still get a DB record)

### Authenticated Endpoints

- `GET /` - Main control panel UI
- `POST /start` - Start game server
- `POST /stop` - Stop game server
- `POST /restart` - Restart game server
- `POST /kill` - Force kill game server
- `GET /api/stats` - Get system and server stats
- `GET /api/console` - Get console output
- `GET /api/logs` - Get game server logs
- `GET /api/startup-logs` - Get startup logs
- `GET /api/server-logs` - Get webserver logs
- `GET /api/check-update` - Check for game updates
- `GET /api/update-output` - Get update process output
- `GET /api/history` - Get stats history
- `GET /api/activity` - Get activity log
- `GET /jellyfin/*` - Proxy to Jellyfin service

### Admin Endpoints (require admin role)

- `GET /api/admin/users` - List all users with full profile data
- `PUT /api/admin/users/<id>` - Update user role (`admin`/`user`) or active status (`true`/`false`)

### Calendar Endpoints

- `GET /api/calendar/events` - (Authenticated) List upcoming calendar events, sorted by date ascending
- `POST /api/calendar/events` - (Admin only) Create a new calendar event. Body: `{title, event_date, description?}`
- `PUT /api/calendar/events/<id>` - (Admin only) Edit a calendar event. Body: `{title?, event_date?, description?}`. If `title` or `event_date` is present, treated as an edit (admin required). Otherwise falls through to RSVP handling.
- `DELETE /api/calendar/events/<id>` - (Admin only) Delete a calendar event

### Jellyfin Auto-Login

- `GET /api/jellyfin-auth` - (Authenticated) Auto-provisions a Jellyfin account for the user if one doesn't exist, authenticates via the Jellyfin API, and returns `{token, user_id, server_id}`. Stores `jellyfin_user_id` and `jellyfin_password` in the users table. Handles password resets if credentials get out of sync.
- `GET jellyfin.meduseld.io/sso-login?token=&userId=&serverId=` - Served via the Jellyfin catch-all proxy. Uses iframe-first approach: loads Jellyfin web client in a hidden iframe, waits for its ConnectionManager to initialize `jellyfin_credentials` in localStorage, then patches in `AccessToken`/`UserId` and redirects to the Jellyfin home page. Falls back to direct credential write after 15-second timeout.

## Environment Variables

- `MEDUSELD_ENV`: Set to "production" in production (defaults to "production")
- `FLASK_SECRET_KEY`: Session encryption key (set in systemd service)
- `JWT_SECRET`: JWT signing key for Discord OIDC auth (set in systemd service)
- `DATABASE_URL`: PostgreSQL connection string (set in systemd service)
- `GOOGLE_CLIENT_SECRET`: Google OAuth client secret for Drive backup (set in systemd service)
- `GOOGLE_CLIENT_ID`: Google OAuth client ID (hardcoded fallback in config.py)
- `OIDC_WORKER_URL`: Discord OIDC worker URL (defaults to `https://discord-oidc.404-41f.workers.dev/`)
- `JELLYFIN_INTERNAL_URL`: Internal Jellyfin server URL (defaults to `http://localhost:8096`)
- `JELLYFIN_API_KEY`: Jellyfin API key for auto-provisioning user accounts (generated from Jellyfin admin dashboard → API Keys)

## Python Dependencies (Key Additions)

- `Flask-SQLAlchemy` — ORM for PostgreSQL
- `Flask-Migrate` — Alembic-based DB migrations
- `psycopg2-binary` — PostgreSQL driver
- `PyJWT` — JWT decoding for Cloudflare Access tokens

## Release Pipeline

Both `meduseld` and `meduseld-site` use `commit-and-tag-version` (maintained fork of `standard-version`) for automated releases. `herugrim` uses the same pipeline starting from v0.1.0-alpha.

### How It Works

- Reads conventional commits since the last git tag
- Bumps version in `package.json` based on commit types (`feat` → minor, `fix` → patch, breaking → major)
- Auto-generates CHANGELOG.md entries grouped by type
- Creates a git tag (e.g. `v0.6.0-alpha`)
- Makes a single "chore(release)" commit with all changes

### Release Commands

- `npm run release` — auto-bump based on commits (prerelease alpha)
- `npm run release:patch` — force patch bump (prerelease alpha)
- `npm run release:minor` — force minor bump (clean version, e.g., `0.7.0`)
- `npm run release:major` — force major bump (clean version, e.g., `1.0.0`)
- `npm run release:stable` — drop the `-alpha` suffix for stable release
- `npx commit-and-tag-version --release-as X.Y.Z-alpha` — exact version with alpha suffix (most reliable for specific alpha releases)

### Release Workflow

1. Merge PRs to `main` as usual
2. Run `npm run release`
3. `git push --follow-tags`
4. GitHub Action (`.github/workflows/release.yml`) creates a GitHub Release with changelog as release notes

### Configuration Files

- `package.json` — version field and release scripts
- `.versionrc.json` — changelog section headings and type visibility (docs/test/chore/build/ci hidden from changelog)
- `.github/workflows/release.yml` — creates GitHub Release on tag push, extracts changelog section for release notes
