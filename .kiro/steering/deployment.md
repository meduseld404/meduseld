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
  - Calls `panel.meduseld.io/api/admin/users` for data

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

### herugrim Repository (Discord OIDC Worker)

Cloudflare Worker (`worker.js`) that acts as an OIDC identity provider bridging Discord OAuth to Cloudflare Access.

- Handles Discord OAuth flow: `/authorize`, `/token`, `/userinfo`, `/jwks.json`
- Returns JWT id_tokens containing a `discord_user` object with real Discord profile data (id, username, global_name, avatar, discriminator)
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

| Column       | Type        | Notes                                                                  |
| ------------ | ----------- | ---------------------------------------------------------------------- |
| id           | Integer     | Primary key                                                            |
| discord_id   | String(64)  | Unique, indexed. Real Discord snowflake ID (e.g. `000000000000000000`) |
| username     | String(128) | Discord username                                                       |
| display_name | String(128) | Discord global_name                                                    |
| avatar_hash  | String(256) | Discord avatar hash for CDN URL                                        |
| email        | String(256) | Email from Cloudflare Access JWT                                       |
| role         | String(32)  | Default `"user"`, also supports `"admin"`                              |
| is_active    | Boolean     | Default true                                                           |
| created_at   | DateTime    | UTC                                                                    |
| last_login   | DateTime    | Updated on each login                                                  |

Key methods:

- `User.get_or_create(discord_id, username, ...)` — Looks up by `discord_id` first, falls back to `email` to prevent duplicate accounts when Cloudflare UUID changes
- `user.to_dict()` — Serializes user for API responses and session storage
- `user.avatar_url` — Property that builds Discord CDN avatar URL

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
   - The JWT `sub` is a Cloudflare UUID (NOT the Discord ID) — custom OIDC claims are not passed through
   - `User.get_or_create()` creates/finds user by discord_id or email fallback
   - User stored in Flask session
6. On static pages (services, system via meduseld-site):
   - `auth.js` decodes `CF_Authorization` cookie client-side for basic user info
   - `auth.js` calls `/cdn-cgi/access/get-identity` to get full identity including `custom.discord_user`
   - `auth.js` POSTs to `https://panel.meduseld.io/api/sync-identity` with real Discord data
   - `auth.js` calls `https://panel.meduseld.io/api/me` to get role and DB-synced user info

### Important Cloudflare Access Quirks

- `Cf-Access-Jwt-Assertion` header does NOT contain custom OIDC claims — only email, sub (Cloudflare UUID), iat, etc.
- Real Discord data is only available via `/cdn-cgi/access/get-identity` endpoint (browser-only, not server-side)
- Discord user data lives under `identity.custom.discord_user`, NOT `identity.oidc_fields`
- The Cloudflare UUID (`sub`) changes per-session, so email is used as fallback lookup to prevent duplicate user records

### Auth Files

- `meduseld/app/webserver.py` — `authenticate_request()` middleware, `@require_auth` and `@require_role` decorators, `/api/me`, `/api/sync-identity`
- `meduseld-site/static/auth.js` — Client-side auth: `MeduseldAuth.getUser()`, `.isAuthenticated()`, `.getRole()`, `.hasRole()`, `.syncUser()`
- `herugrim/worker.js` — Discord OIDC bridge worker

### Role-Based Access Control

Two roles exist: `user` (default) and `admin`.

- Roles are stored in the `users` table `role` column
- `@require_role("admin")` decorator on Flask endpoints checks `g.user.role`
- `MeduseldAuth.hasRole("admin")` on the client side (checks after `syncUser()` completes)
- Admin-only pages: SSH Terminal (`ssh.meduseld.io`), System Monitor (`system.meduseld.io`), Admin Panel (`admin.meduseld.io`)
- Admin-only service cards (SSH, System Monitor) are hidden by default on the services page (`display: none`), shown via JS after auth sync confirms admin role
- Non-admin users navigating directly to admin-only pages are redirected to `services.meduseld.io?restricted=<page-name>`, which shows a toast banner
- Server-side: SSH terminal route returns 403 for non-admin users; admin API endpoints use `@require_auth` + `@require_role("admin")`
- Self-protection: admins cannot demote or deactivate their own account via the API

### Bootstrap: Setting the First Admin

Since the admin page requires admin role, the first admin must be set directly in the database:

```bash
# Check existing users
sudo -u postgres psql -d meduseld_db -c "SELECT id, discord_id, username, role FROM users;"

# Promote a user to admin
sudo -u postgres psql -d meduseld_db -c "UPDATE users SET role = 'admin' WHERE discord_id = 'YOUR_DISCORD_ID';"
```

After the first admin is set, subsequent admins can be promoted from the admin page UI.

### Public Paths (No Auth Required)

- `/health`, `/health-bypass`
- `/api/check-service/<service>`
- Host: `health.meduseld.io`

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
- Logs: `app/logs/webserver.log`
- Game server: `/srv/games/icarus`

### Server Directory Structure

```
/srv
├── ai-cli
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

### Common Issues

When the server "goes offline" after pressing start:

1. Check actual logs on production server: `tail -100 /srv/apps/meduseld/logs/webserver.log`
2. Check if process is running: `ps aux | grep webserver.py`
3. Check systemd status: `systemctl status meduseld`
4. Run manually to see errors: `cd /srv/apps/meduseld && venv/bin/python app/webserver.py`

## API Endpoints (panel.meduseld.io)

### Public Endpoints

- `GET /health` - Public health check (no auth required)
- `GET /api/check-service/<service>` - Check if service is online

### Auth Endpoints

- `GET /api/me` - Return current authenticated user info (or `{authenticated: false}`)
- `POST /api/sync-identity` - Accept Discord user data from client-side `auth.js` and update DB record

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

## Environment Variables

- `MEDUSELD_ENV`: Set to "production" in production (defaults to "production")
- `FLASK_SECRET_KEY`: Session encryption key (set in systemd service)
- `JWT_SECRET`: JWT signing key for Discord OIDC auth (set in systemd service)
- `DATABASE_URL`: PostgreSQL connection string (set in systemd service)
- `GOOGLE_CLIENT_SECRET`: Google OAuth client secret for Drive backup (set in systemd service)
- `GOOGLE_CLIENT_ID`: Google OAuth client ID (hardcoded fallback in config.py)
- `OIDC_WORKER_URL`: Discord OIDC worker URL (defaults to `https://discord-oidc.404-41f.workers.dev/`)

## Python Dependencies (Key Additions)

- `Flask-SQLAlchemy` — ORM for PostgreSQL
- `Flask-Migrate` — Alembic-based DB migrations
- `psycopg2-binary` — PostgreSQL driver
- `PyJWT` — JWT decoding for Cloudflare Access tokens
