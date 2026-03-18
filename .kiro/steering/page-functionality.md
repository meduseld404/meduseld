---
description: Detailed UI functionality for every page across meduseld and meduseld-site, including all buttons, interactive elements, and behaviors
---

# Page Functionality Reference

## meduseld.io — Landing Page

File: `meduseld-site/index.html`

Minimal splash page with a "404 Server Not Found" joke theme.

- "Enter the Great Hall" button → navigates to `https://services.meduseld.io`
- Footer links: quietarcade website, dynamic version badge (fetches latest release tag from GitHub API, links to release page)
- Copyright year auto-fills via JS

---

## services.meduseld.io — The Great Hall (Main Hub)

File: `meduseld-site/services/index.html`

Central navigation hub. All service cards check live status via a Cloudflare Worker health API at `https://meduseld-health.404-41f.workers.dev`. Status checks run every 5 seconds.

### Navigation & Global Elements

- "← Back to Landing" button → navigates to `https://meduseld.io`
- "Changelog" button → opens a modal with two tabs (Site and Backend). Fetches CHANGELOG.md from each GitHub repo (`raw.githubusercontent.com`) on first open, renders markdown to styled HTML. Shows spinner while loading, error message on failure.
- Discord widget (Widgetbot Crate) → embedded chat bubble in bottom-right, links to server channel `1474674474036232204`
- Speech bubble notification → appears after 3 seconds, fades after 8 seconds, says "Server suggestion or problem? Send a Discord message!"
- Profile widget (top-right, inside header nav bar) → shows avatar, display name, and Admin badge for admins. Dropdown includes: username, role, "Admin Panel" link (admin only, links to `https://admin.meduseld.io`), and Logout
- Calendar widget (top-left) → shows upcoming group events fetched from `panel.meduseld.io/api/calendar/events`. Displays event title, date/time, and optional description. Admins see a "+" button to add events (opens modal with title, date/time, description fields) and delete buttons on each event. Events auto-load on page load.
- Copyright footer with quietarcade link and dynamic version badge (fetches latest release from GitHub API, links to release page)

### Service Cards (Active)

Each active service card has a status indicator badge that shows Online/Offline/Cloudflare Offline with color coding (green/red/orange).

1. **Icarus Server (Game Server Panel)**
   - Status badge: checks panel health, shows Online/Offline/Cloudflare Tunnel Down
   - "Open Control Panel" button → links to `https://panel.meduseld.io` (disabled when offline)
   - Production/Development mode toggle badge → currently hidden. When visible, click to switch between `panel.meduseld.io` and `panel.meduseld.io?env=development`. Persists in localStorage as `panelDevMode`. Shows a toast notification on toggle.
   - Game name and description are dynamically set from `CONFIG.gameName` (currently "Icarus")

2. **Edoras (Jellyfin/Media)**
   - Status badge: checks jellyfin health
   - "Open Edoras" button → opens a modal with two options (disabled when offline):
     - "View Library" → calls `/api/jellyfin-auth` to auto-provision a Jellyfin account and get an auth token, then navigates (same tab) to `jellyfin.meduseld.io/sso-login` which sets localStorage credentials and opens Jellyfin logged in. Falls back to navigating to Jellyfin directly if auth fails. Shows "Connecting..." spinner during auth.
     - "Request" button → links to `https://overseerr.meduseld.io` (opens in new tab)
     - "Manage" button (admin only, hidden for non-admin users) → links to `https://edoras.meduseld.io` Edoras management page. Shows gold "Admin" badge.
   - SSO login page (`/sso-login`): uses an iframe-first approach to avoid a race condition where Jellyfin's ConnectionManager overwrites localStorage credentials. Loads `/web/index.html` in a hidden iframe, polls localStorage every 250ms until Jellyfin initializes `jellyfin_credentials` with `Servers[0].Id`, then patches in `AccessToken`/`UserId` and redirects. Falls back to direct credential write after 15 seconds.
   - Direct visit auto-login: when a user visits `jellyfin.meduseld.io` directly with a valid `CF_Authorization` cookie, an injected script automatically calls `/api/jellyfin-auth`, then polls localStorage waiting for Jellyfin's ConnectionManager to initialize credentials before patching in the auth token. Uses `sessionStorage` flag to prevent retry loops (one attempt per session).

3. **SSH Access** (Admin only — hidden for non-admin users)
   - Gold "Admin" badge below card title
   - Status badge: checks SSH health
   - "Open SSH Terminal" button → links to `https://ssh.meduseld.io` (disabled when offline)

4. **System Monitor** (Admin only — hidden for non-admin users)
   - Gold "Admin" badge below card title
   - Always shows "Active" badge (static page, always available)
   - "Open System Monitor" button → links to `https://system.meduseld.io`

### Service Cards (Coming Soon — Disabled)

- VPN Access — Mullvad remote access
- Game Wiki — community wiki for current game
- The Red Book — e-books and audiobooks
- Trivia Game — trivia questions
- D&D Companion — session hub, DM soundboard, and campaign wiki for Roll20 adventures
- Remote Desktop — screen sharing/collaboration
- Hall of Fame — funny moments and screenshots
- More Services — placeholder

### Game News Panel (Collapsible)

- Header is clickable to expand/collapse (chevron rotates)
- "Refresh" button → re-fetches news from Steam API
- Fetches from `https://api.steampowered.com/ISteamNews/GetNewsForApp/v0002/` via allorigins proxy
- Uses `CONFIG.gameAppId` (currently `1149460` for Icarus)
- Shows up to 5 news items with title, date, truncated content, and "Read More" link to Steam
- Status badge shows count of updates or error state

### Games Up Next List

- Ranked list of upcoming games with Steam store links
- "Refresh prices" button → re-fetches prices from Steam API with `forceRefresh=true`
- Prices fetched from `https://store.steampowered.com/api/appdetails` via allorigins proxy
- Auto-detects user country via `https://ipapi.co/json/` for localized currency (USD/GBP/EUR)
- 24-hour price cache in localStorage (`gamePricesCache`)
- Shows sale badges with discount percentage when on sale
- Retries up to 3 times per game with 2-second delay
- Games listed: Raft, Dawn of Defiance (has info tooltip about dedicated server support), Soulmask, 7 Days to Die, LOTR: Return to Moria, Fellowship, VEIN

### Server Specifications

Static display of hardware specs: AMD Ryzen 7 2700, 32GB DDR4 3600, RTX 3060, 1TB NVMe SSD, GIGABYTE B550M K, ARCTIC Liquid Freezer III Pro 240, JONSBO Z20 case, Ubuntu Server 24.04

### Quick Links Bar

Admin-only links (hidden for non-admin users):

- Cloudflare Dashboard → `https://dash.cloudflare.com`
- Backend Repo → `https://github.com/meduseld404/meduseld`
- Site Repo → `https://github.com/meduseld404/meduseld-site`
- Herugrim Repo → same as site repo link (likely needs updating)

Visible to all users (always shown, displayed below admin links):

- Gmail → `https://mail.google.com`
- Drive → Google Drive backups folder

### Restricted Access Toast

When a non-admin user is redirected from an admin-only page (SSH, System Monitor, Admin), the services page shows a warning toast banner: "[Page Name] is not available for your account type." Auto-dismisses after 8 seconds. The `?restricted=` URL parameter is cleaned after display. Built with DOM APIs (not innerHTML) to prevent XSS.

---

## system.meduseld.io — System Monitor (Admin Only)

File: `meduseld-site/system/index.html`

Server logs viewer and system management page. Non-admin users are redirected to `services.meduseld.io?restricted=system-monitor`.

### Navigation

- Header nav bar (top of page, inside page flow):
  - Left side: "Back to Services" button → navigates to `https://services.meduseld.io`
  - Right side: Profile widget (same as services page)

### Server Logs Panel

- Fetches logs from `https://health.meduseld.io/check/system-logs?lines=100`
- "Refresh" button → manually re-fetches logs
- Auto-refreshes every 30 seconds
- Color-coded log lines: red for ERROR, yellow for WARNING, blue for INFO
- Status badge shows line count or error state
- Auto-scrolls to bottom on load

### Action Buttons Row

1. **SSH Terminal** → opens `https://ssh.meduseld.io` in new tab
2. **Trello Board** → opens `https://trello.com/b/9hN0SOQP/meduseld` in new tab
3. **System Backup** → opens backup confirmation modal
4. **Reboot Server** → opens reboot confirmation modal

### System Backup Modal

- Warning text: "This will create a backup of the game server save data and upload it to Google Drive."
- "Start Backup" button → POSTs to `https://health.meduseld.io/check/backup` with a token
- Shows progress spinner during backup
- Polls `https://health.meduseld.io/check/backup-status` every 5 seconds until complete
- Handles "already in progress" state
- On success: shows green checkmark with "Backup uploaded successfully" and the backup filename (if available from `/tmp/meduseld-last-backup-name`), hides Start Backup button, Cancel becomes a green "Close" button. Resets when modal is reopened.
- On failure: button resets after 5 seconds
- "Cancel" button → closes modal

### Reboot Server Modal

- Danger warning: explains all services will go offline (game server, Jellyfin, SSH)
- "Confirm Reboot" button → POSTs to `https://health.meduseld.io/check/reboot` with a token
- Shows spinner during reboot request
- Updates logs status badge to "Rebooting..." on success
- Button resets after 3 seconds on failure
- "Cancel" button → closes modal

### System Monitoring Section

All system stats fetched from standalone monitoring microservice at `https://health.meduseld.io/check/stats` every 5 seconds. Charts history from `https://health.meduseld.io/check/history` every 30 seconds. Game server stats fetched from `https://panel.meduseld.io/api/stats` (best-effort, fails gracefully if panel is down) every 10 seconds.

#### Status Cards Row

6 cards: Status (Online/Offline), CPU %, CPU Temp (color-coded: green <60°C, yellow 60-75°C, red 75°C+), RAM (used/total GB), Disk (used/total GB), Power (watts with monthly cost estimate)

#### Power Breakdown Card

Shows per-component wattage: CPU, GPU, RAM, Storage (NVMe), Other (MB/Fans/AIO), and Total. Values come from the monitoring service (RAPL for CPU, nvidia-smi for GPU, static estimates for the rest).

#### Cost Estimate Card (24/7)

Displays electricity cost in USD ($): current draw, daily, monthly (30d), yearly, and rate per kWh. Rate defaults to $0.145/kWh (configurable via `ELECTRICITY_COST_PER_KWH` env var on the monitoring service).

#### Charts (3 columns)

- CPU Usage — system CPU % over time (gold line)
- RAM Usage — system RAM in GB over time (gold line)
- Power Draw — total watts (gold), CPU watts (green), GPU watts (purple)
- All use Chart.js line graphs with 30 minutes of rolling history

#### Game Server Card

Shows: State (color-coded), Players (X/8), Uptime, Server CPU (cores), Server RAM (GB), Health (good/warning/critical). Shows "Unavailable" if the Flask backend is down.

---

## panel.meduseld.io — Game Server Control Panel

Files: `meduseld/app/templates/panel.html` (extends `base.html`)

Authenticated Flask page for controlling the Icarus dedicated game server. Requires Discord OIDC login.

### Navigation Bar

- "Back to Services" button → navigates to `https://services.meduseld.io`
- "SSH Terminal" button (admin only — hidden for non-admin users) → opens `https://ssh.meduseld.io` in new tab
- "Backup" dropdown:
  - "Download Backup" → `GET /download-backup` (downloads file)
  - "Backup to Cloud" → `GET /backup-to-cloud` (triggers Google Drive upload)
- Profile widget (top-right) → same shared `auth.js` `renderProfile()` widget used on all pages. Shows avatar, display name, Admin badge for admins. Dropdown includes: username, role, "Admin Panel" link (admin only), and Logout.

### Development Mode

- Activated via `?env=development` URL parameter
- Shows purple "DEVELOPMENT MODE" badge below logo
- Prepends "🔧 DEV |" to page title
- All API calls append `?env=development` parameter

### System Status Cards (Top Row)

All stats update every 5 seconds via `GET /api/stats`.

1. **System Status** — shows Online (green) or Offline (red) based on API reachability
2. **System CPU** — total CPU usage percentage across all cores
3. **CPU Temp** — temperature in Celsius, card color changes: green (<60°C), yellow (60-75°C), red (75-85°C), dark red (85°C+)
4. **System RAM** — used / total in GB
5. **Disk Usage** — used / total in GB

### Icarus Server Status Card (Left)

- Server state display: Running (green), Offline (red), Crashed (dark red), Starting/Stopping/Restarting (gold)
- Health badge: Good (green), Warning (yellow, 80%+ resource usage), Critical (red, 95%+ resource usage)
- Update badge:
  - "Up to Date" (green) — when current build matches latest
  - "Update Available (Click to Update)" (blue, clickable) — triggers `/restart` and shows "Updating..." spinner
  - "Updating..." (gold with spinner) — during update process
- Uptime display — formatted as Xd Xh Xm Xs, ticks every second client-side
- Player count — shows "Players: X/8" when running, green when players > 0
- Server CPU Usage — shows cores used and raw percentage
- Server RAM Usage — shows GB used / total

### Dynamic Tab Title

Changes based on server state:

- 🟢 Panel | Meduseld (running)
- 🔴 Offline | Meduseld (offline)
- 💥 CRASHED | Meduseld (crashed)
- 🟡 Restarting/Starting | Meduseld (transitional)
- 🟠 Stopping | Meduseld (stopping)

### Server Control Buttons (Right, 2x2 Grid)

Each is a clickable card. Buttons enable/disable based on server state:

1. **Start** (green when active) → `POST /start`
   - Enabled when: offline, crashed
   - Disabled when: running, starting, stopping, restarting

2. **Stop** (red when active) → `POST /stop`
   - Enabled when: running
   - Disabled when: offline, starting, stopping, crashed

3. **Restart** (gold when active) → `POST /restart`
   - Enabled when: running
   - Disabled when: offline, starting, stopping, crashed, restarting

4. **Kill** (purple when active) → `POST /kill`
   - Enabled when: running, restarting
   - Disabled when: offline, starting, stopping, crashed

After any action: polling increases to 1-second intervals for 30 seconds, then returns to 5-second intervals. A 429 response shows a cooldown alert with remaining seconds.

### Game Server Logs Panel

- Fetches from `GET /api/logs` every 5 seconds
- Auto-scrolls to bottom only if user was already at bottom
- Adds visual separators on state transitions (start → running, running → stopped) with timestamps
- Version change separators when game updates
- Single info line shown in gold color

### Startup Script Logs Panel

- Fetches from `GET /api/startup-logs` every 5 seconds
- "Clear Logs" button → `POST /api/clear-startup-logs` (with confirmation dialog, archives current logs)
- Color-coded entries:
  - Green: success messages (✓, "Server started successfully", "Clean shutdown")
  - Red: errors (ERROR:), crashes, kills, process deaths
  - Yellow: warnings (WARNING:)
  - Purple: kill events (SIGKILL, force kill)
  - Gray: process health checks
  - Gold: Wine errors (only err: level, warn: and fixme: filtered out)
- Visual separators for: SERVER START, SERVER STOPPED, PROCESS DIED, SERVER KILLED, SERVER CRASHED

### CPU & RAM Charts

- Two Chart.js line graphs side by side
- Each has two datasets: System (gold) and Server/Icarus (green)
- CPU chart: System CPU % and Server CPU %
- RAM chart: System RAM (GB) and Server RAM (GB)
- History loaded from `GET /api/history` (30 minutes of data)
- Refreshes every 30 seconds

### Logo Easter Eggs

- Top logo → links to YouTube video `7lwJOxN_gXc` (Rohan theme)
- Bottom logo → links to YouTube video `WtO3AHMBePY`

---

## ssh.meduseld.io — SSH Terminal Wrapper (Admin Only)

File: `meduseld/app/templates/terminal.html`

Wrapper page that embeds the ttyd web terminal in an iframe with a navigation bar and help modal. Non-admin users are blocked server-side (403) and redirected client-side to `services.meduseld.io?restricted=ssh-terminal`.

### Navigation Bar

- "Back to Services" button → navigates to `https://services.meduseld.io`
- "Server Panel" button → navigates to `https://panel.meduseld.io`
- Title text: "SSH Terminal | Meduseld Server"

### Terminal

- Embedded iframe pointing to `https://terminal.meduseld.io` (ttyd instance)
- Full-height, no border, fills remaining viewport

### Help Button (Floating)

- Fixed position bottom-right corner, round gold button with question mark icon
- Opens the Linux Commands Cheat Sheet modal

### Linux Commands Cheat Sheet Modal

Sections with command examples and descriptions:

1. Navigation: `cd /srv/games/icarus`, `ls -lah`, `pwd`
2. File Operations: `cat`, `tail -f`, `grep`
3. Disk Usage: `du -sh *`, `df -h`
4. Process Management: `ps aux | grep icarus`, `top`
5. Permissions: `ls -l`, `chmod +x`
6. Server Configuration: `nano ServerSettings.ini`, `nano start.sh` (with info box about start.sh purpose)
7. Caution warning about destructive commands

---

## admin.meduseld.io — User Management (Admin Only)

File: `meduseld-site/admin/index.html`

Static admin page for managing user roles and account status. Served by Cloudflare Pages independently of the Flask backend. Non-admin users are redirected to `services.meduseld.io?restricted=user-management`.

### Navigation

- "Back to Services" button → navigates to `https://services.meduseld.io`

### Users Table

- Fetches from `GET https://panel.meduseld.io/api/admin/users` with credentials
- "Refresh" button → re-fetches user list
- Columns: User (avatar + display name), Discord ID, Role, Services, Session Status, Last Login, Actions
- Services column shows a purple Jellyfin icon if the user has Jellyfin credentials (`has_jellyfin`), dash otherwise
- Current user row shows a "You" badge
- Inactive users shown at 50% opacity

### Actions Per User (not available on own account)

- Promote/Demote button → `PUT /api/admin/users/<id>` with `{role: "admin"}` or `{role: "user"}`
- Activate/Deactivate button → `PUT /api/admin/users/<id>` with `{is_active: true}` or `{is_active: false}`
- Toast notifications on success/failure

### Backend Offline State

- If the Flask backend is unreachable, the table shows "Backend is offline. Unable to load users."
- User count badge shows "Backend Offline" in red
- If the `CF_Authorization` cookie is invalid or expired (401/403), shows "Authentication failed" with a suggestion to log out and back in. User count badge shows "Auth Error" in yellow.
- API calls are routed through `health.meduseld.io/check/admin-users` to bypass Cloudflare Access session requirements. Auth is handled by decoding the `CF_Authorization` cookie server-side via `_authenticate_from_cookie()`.

---

## edoras.meduseld.io — Edoras Management (Admin Only)

File: `meduseld-site/edoras/index.html`

Admin page for managing entertainment and media services. Non-admin users are redirected to `services.meduseld.io?restricted=edoras-management`.

### Navigation

- "Back to Services" button → navigates to `https://services.meduseld.io`
- Profile widget (top-right, fixed position)

### Service Cards (3-column grid)

Each card has an icon, title, description, and "Open" button that opens the service in a new tab.

1. **Overseerr** → `https://overseerr.meduseld.io` — Media request management
2. **Sonarr** → `https://sonarr.meduseld.io` — TV show management and automation
3. **Radarr** → `https://radarr.meduseld.io` — Movie management and automation
4. **Prowlarr** → `https://prowlarr.meduseld.io` — Indexer management for Sonarr and Radarr
5. **qBittorrent** → `https://qbit.meduseld.io` — Download client management

---

## health.meduseld.io — Service Health Dashboard

File: `meduseld/app/templates/health.html` (extends `base.html`)

Public health monitoring page showing real-time status of all Meduseld services.

### Service Status Cards (3 columns)

Each card shows a spinner while checking, then displays Online (green check), Degraded (yellow warning), or Down (red X) with response time or error details.

1. Control Panel — checks `panel.meduseld.io`
2. SSH Terminal — checks `ssh.meduseld.io`
3. Jellyfin Media — checks `jellyfin.meduseld.io`

### Auto-Refresh

- Checks all services on page load via `fetch(/<service>)`
- Re-checks every 30 seconds
- "Last updated" timestamp at the bottom
