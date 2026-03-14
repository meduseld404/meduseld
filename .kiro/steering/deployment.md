---
description: Server infrastructure, systemd service, domains, API endpoints, and environment variables for the Meduseld project
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

### discord-oidc-worker Repository

Cloudflare Worker for Discord OAuth authentication

- Handles Discord OAuth flow for user authentication
- Returns JWT tokens for authenticated sessions
- Used by panel.meduseld.io for access control

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
- Logs: `app/logs/webserver.log`
- Game server: `/srv/games/icarus`

### Server Directory Structure

```
/srv
├── ai-cli
├── backups
├── compatibilitytools
│   └── GE-Proton10-32
│       ├── files
│       └── protonfixes
├── games
│   └── icarus
│       ├── Engine
│       ├── Icarus
│       ├── prefix
│       └── steamapps
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
│   ├── appcache
│   │   └── httpcache
│   ├── config
│   ├── depotcache
│   ├── logs
│   ├── steamapps
│   └── userdata
│       └── anonymous
└── steamcmd
    ├── linux32
    │   └── logs
    ├── linux64
    ├── package
    ├── public
    └── siteserverui
        ├── css
        ├── images
        ├── js
        └── win32
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

The webhook at `/webhook/deploy.sh` references Docker commands, but these are NOT used in production.

Actual deployment: push to main, then on the server pull and restart the service.

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

## Environment Variables

- `MEDUSELD_ENV`: Set to "production" in production (defaults to "production")
- `FLASK_SECRET_KEY`: Session encryption key (set in systemd service)
- `JWT_SECRET`: JWT signing key for Discord OIDC auth (set in systemd service)
- `GOOGLE_CLIENT_SECRET`: Google OAuth client secret for Drive backup (set in systemd service)
- `GOOGLE_CLIENT_ID`: Google OAuth client ID (hardcoded fallback in config.py)
- `OIDC_WORKER_URL`: Discord OIDC worker URL
