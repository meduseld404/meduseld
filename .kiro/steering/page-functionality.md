---
description: Detailed UI functionality for every page across meduseld and meduseld-site, including all buttons, interactive elements, and behaviors
---

# Page Functionality Reference

## meduseld.io — Landing Page

File: `meduseld-site/index.html`

Minimal splash page with a "404 Server Not Found" joke theme.

- "Enter the Great Hall" button → navigates to `https://services.meduseld.io`
- "Looking for Herugrim?" link → navigates to `https://herugrim.meduseld.io`
- "Looking for FellowSync?" link → navigates to `https://fellowsync.meduseld.io`
- "Looking for ExSpire?" link → navigates to `https://exspire.meduseld.io`
- Footer links: quietarcade website, dynamic version badge (fetches latest release tag from GitHub API, links to release page)
- Copyright year auto-fills via JS

---

## services.meduseld.io — The Great Hall (Main Hub)

File: `meduseld-site/services/index.html`

Central navigation hub. All service cards check live status via a Cloudflare Worker health API at `https://meduseld-health.404-41f.workers.dev`. The worker first checks Cloudflare Tunnel health via the Cloudflare API, then performs individual service checks only if the tunnel is healthy. Status checks run every 5 seconds.

### Navigation & Global Elements

- "← Back to Landing" button → navigates to `https://meduseld.io`
- "Changelog" button → opens a modal with two tabs (Site and Backend). Fetches CHANGELOG.md from each GitHub repo (`raw.githubusercontent.com`) on first open, renders markdown to styled HTML. Shows spinner while loading, error message on failure.
- Discord widget (Widgetbot Crate) → embedded chat bubble in bottom-right, links to server channel `1474674474036232204`
- Speech bubble notification → appears after 3 seconds, fades after 8 seconds, says "Server suggestion or problem? Send a Discord message!"
- Profile widget (top-right, inside header nav bar) → shows avatar, display name, and Admin badge for admins. Dropdown includes: username, role, "My Profile" link (links to `https://profile.meduseld.io`), "Admin Panel" link (admin only, links to `https://admin.meduseld.io`), and Logout (redirects to `https://meduseld.io`)
- Calendar widget (centered, between page title and news panel) → shows upcoming group events fetched from `health.meduseld.io/check/calendar` (proxied through health to bypass Cloudflare Access). Displays event title, date/time, and optional description. Each event has RSVP buttons (going/maybe/not going) with counts — clicking sets your status, clicking the same status again removes it, hovering shows names of users who selected that option. Admins see a "+" button to add events (opens modal with title, date/time, description fields), edit buttons (pencil icon, opens edit modal pre-filled with event data), and delete buttons on each event. Events auto-load on page load. Auth via `cf_token` query param (GET/DELETE) or `_cf_token` in JSON body (POST/PUT).
- FellowSync active room banner (centered, between page title and calendar widget) → polls `health.meduseld.io/check/fellowsync-rooms` every 30 seconds (proxied to FellowSync backend at `127.0.0.1:5050/api/rooms/active`). When one or more listening rooms are active, shows a clickable card with the FellowSync icon, room count, total listener count badge, and currently playing track/artist. Links to `https://fellowsync.meduseld.io`. Hidden when no rooms are active. No authentication required.
- Copyright footer with quietarcade link and dynamic version badge (fetches latest release from GitHub API, links to release page)

### Service Cards (Active)

Each active service card has a status indicator badge that shows Online/Offline/Cloudflare Offline with color coding (green/red/orange). When the Cloudflare tunnel is down, the service button becomes a link to `https://status.meduseld.io` instead of being disabled.

1. **Icarus Server (Game Server Panel)**
   - Status badge: checks panel health, shows Online/Offline/Cloudflare Tunnel Down
   - "Open Control Panel" button → links to `https://panel.meduseld.io` (disabled when offline, links to status page when tunnel is down)
   - Production/Development mode toggle badge → currently hidden. When visible, click to switch between `panel.meduseld.io` and `panel.meduseld.io?env=development`. Persists in localStorage as `panelDevMode`. Shows a toast notification on toggle.
   - Game name and description are dynamically set from `CONFIG.gameName` (currently "Icarus")

2. **Edoras (Jellyfin/Media)**
   - Status badge: checks Jellyfin health via Cloudflare Worker health API, shows Online/Offline/Cloudflare Tunnel Down
   - "Open Edoras" button → opens a modal with options (disabled when offline, links to status page when tunnel is down):
     - "View Library" → calls `/api/jellyfin-auth` to auto-provision a Jellyfin account and get an auth token, then navigates (same tab) to `jellyfin.meduseld.io/sso-login` which sets localStorage credentials and opens Jellyfin logged in. Falls back to navigating to Jellyfin directly if auth fails. Shows "Connecting..." spinner during auth.
     - "Request" button → calls `openSeerr()` which navigates to `health.meduseld.io/check/seerr-auth?cf_token=<token>`. The backend provisions the user's Jellyfin account, authenticates against Jellyseerr's API with those credentials, sets the `connect.sid` session cookie on `.meduseld.io`, and redirects to `https://requests.meduseld.io` (user arrives logged in).
     - "Manage" button (admin only) → links to `https://edoras.meduseld.io` Edoras management page. Shows gold "Admin" badge.
   - Description: "Stream movies, TV shows, and media from our Edoras server."
   - SSO login page (`/sso-login`): uses an iframe-first approach to avoid a race condition where Jellyfin's ConnectionManager overwrites localStorage credentials. Loads `/web/index.html` in a hidden iframe, polls localStorage every 250ms until Jellyfin initializes `jellyfin_credentials` with `Servers[0].Id`, then patches in `AccessToken`/`UserId` and redirects. Falls back to direct credential write after 15 seconds.
   - Direct visit auto-login: when a user visits `jellyfin.meduseld.io` directly with a valid `CF_Authorization` cookie, an injected script automatically calls `/api/jellyfin-auth`, then polls localStorage waiting for Jellyfin's ConnectionManager to initialize credentials before patching in the auth token. Uses `sessionStorage` flag to prevent retry loops (one attempt per session).

3. **FellowSync (Spotify Listening Rooms)**
   - Custom icon: `fellowsync-bi.png`
   - "Open FellowSync" button → links to `https://fellowsync.meduseld.io`
   - Description: "One does not simply listen alone..."
   - No status badge (external app, not health-checked)

4. **The Red Book (E-books & Audiobooks)**
   - "Open The Red Book" button → links to `https://redbook.meduseld.io`
   - Description: "Browse and listen to e-books and audiobooks from our collection."
   - No status badge (external app, not health-checked)

5. **Trivia Game**
   - "Play Trivia" button → links to `https://trivia.meduseld.io`
   - Description: "Test your knowledge with trivia questions."
   - No status badge (static page, not health-checked)

6. **Game Picker**
   - "Open Game Picker" button → links to `https://picker.meduseld.io`
   - Description: "Spin the wheel to pick this week's party game."
   - No status badge (static page, not health-checked)

### Service Cards (Coming Soon — Disabled)

- VPN Access — Mullvad remote access
- Game Wiki — community wiki for current game
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

- Dynamically rendered ranked list of upcoming games with Steam store links
- Games stored in database (`game_list_entries` table) and fetched from `health.meduseld.io/check/games` API. Falls back to hardcoded default list if API is unreachable.
- "Add Game" button (+) → opens modal for any authenticated user to add a game by Steam App ID, name, optional URL (auto-generated from App ID if omitted), and optional note/tooltip.
- Admin delete buttons (x icon, red) on each game → confirms then DELETE to `health.meduseld.io/check/games-<app_id>`. Removes the game and all associated votes.
- Games sorted by weighted vote score (Borda count) when votes exist, otherwise creation order
- "Vote" button (thumbs-up icon) → enters vote mode where users can drag-and-drop to rank games by preference (#1 = most wanted). Shows instruction bar with Save/Cancel buttons. Requires authentication.
- Vote mode initializes with user's existing rankings if they've voted before, otherwise uses current display order
- "Save Votes" → PUT to `health.meduseld.io/check/game-votes` with `{rankings: {appId: rank}}`. Replaces all previous votes for that user. Each user gets one set of rankings (unique constraint per user+game).
- Scoring: Borda count — rank 1 = 7 points, rank 2 = 6, ..., rank 7 = 1 point. Games sorted by total score descending, vote count as tiebreaker.
- Vote count badges shown next to each game (thumbs-up icon + count). Hovering shows voter names and their rank for that game via Bootstrap tooltip.
- Total voter count badge shown in card header.
- Backend: `GameVote` model (`game_votes` table) with `user_id`, `game_app_id`, `rank`, `updated_at`. Unique constraint on `(user_id, game_app_id)`.
- "Refresh prices" button → re-fetches prices from Steam API with `forceRefresh=true`
- Prices fetched from `https://store.steampowered.com/api/appdetails` via allorigins proxy
- Auto-detects user country via `https://ipapi.co/json/` for localized currency (USD/GBP/EUR)
- 24-hour price cache in localStorage (`gamePricesCache`)
- Shows sale badges with discount percentage when on sale
- Retries up to 3 times per game with 2-second delay

### Server Specifications

Static display of hardware specs: AMD Ryzen 7 2700, 32GB DDR4 3600, RTX 3060, 1TB NVMe SSD, GIGABYTE B550M K, ARCTIC Liquid Freezer III Pro 240, JONSBO Z20 case, Ubuntu Server 24.04

### Quick Links Bar

Admin-only links (hidden for non-admin users):

- Cloudflare Dashboard → `https://dash.cloudflare.com`
- Backend Repo → `https://github.com/meduseld404/meduseld`
- Site Repo → `https://github.com/meduseld404/meduseld-site`
- Herugrim Repo → `https://github.com/meduseld-io/herugrim`

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
- Profile widget (top-right) → same shared `auth.js` `renderProfile()` widget used on all pages. Shows avatar, display name, Admin badge for admins. Dropdown includes: username, role, "My Profile" link (links to `https://profile.meduseld.io`), "Admin Panel" link (admin only), and Logout (redirects to `https://meduseld.io`).

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
  - Red: errors (ERROR:), crashes, unexpected process deaths
  - Yellow: warnings (WARNING:)
  - Purple: kill events (User kill, SIGKILL, force kill)
  - Gold: idle shutdown events, Wine errors (only err: level, warn: and fixme: filtered out)
  - Gray: process health checks, expected process monitor messages (after user stop/kill/idle shutdown)
- Visual separators for: SERVER START, SERVER STOP INITIATED, SERVER STOPPED, IDLE SHUTDOWN, SERVER KILLED, SERVER CRASHED, PROCESS DIED UNEXPECTEDLY
- The stop, kill, and idle shutdown actions write markers to `startup.log` ("User stop:", "User kill:", "Idle shutdown:") so the panel can distinguish intentional shutdowns from unexpected process deaths. When the process monitor detects the server is gone, it checks the preceding log lines for these markers — if found, the event is shown as a quiet gray line; if not, it shows the red "💥 PROCESS DIED UNEXPECTEDLY" separator.

### CPU & RAM Charts

- Two Chart.js line graphs side by side
- Each has two datasets: System (gold) and Server/Icarus (green)
- CPU chart: System CPU % and Server CPU %
- RAM chart: System RAM (GB) and Server RAM (GB)
- History loaded from `GET /api/history` (30 minutes of data)
- Refreshes every 30 seconds

### Logo Easter Eggs

- Top logo → links to YouTube video `7lwJOxN_gXc` (Rohan theme). Also silently fires `POST /check/easter-egg` to award the "Secret Passage" achievement on click.
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
- Profile widget (top-right, inside header nav bar)

### Users Table

- Fetches from `GET https://panel.meduseld.io/api/admin/users` with credentials
- "Refresh" button → re-fetches user list
- Columns: User (avatar + display name), Discord ID, Role, Services, Trivia, Last Login, Actions
- Services column shows a purple Jellyfin icon if the user has Jellyfin credentials (`has_jellyfin`), dash otherwise
- Trivia column shows a gold badge with games played count and accuracy percentage. Hovering shows a Bootstrap tooltip with full stats: games played, total correct, total wrong, best single-game score, and accuracy %. Users with no trivia games show a dash.
- Current user row shows a "You" badge
- Inactive users shown at 50% opacity
- Users sorted: admins first, then alphabetically by display name
- Users without a custom Discord avatar show Discord's default avatar (colored Discord logo) instead of a generic icon

### Actions Per User (not available on own account)

- Promote/Demote button → `PUT /api/admin/users/<id>` with `{role: "admin"}` or `{role: "user"}`
- Activate/Deactivate button → `PUT /api/admin/users/<id>` with `{is_active: true}` or `{is_active: false}`
- Toast notifications on success/failure

### Backend Offline State

- If the Flask backend is unreachable, the table shows "Backend is offline. Unable to load users."
- User count badge shows "Backend Offline" in red
- If the `CF_Authorization` cookie is invalid or expired (401/403), shows "Authentication failed" with a suggestion to log out and back in. User count badge shows "Auth Error" in yellow.
- API calls are routed through `health.meduseld.io/check/team-roster` to bypass Cloudflare Access session requirements. The endpoint is named "team-roster" instead of "admin-users" to avoid ad-blocker false positives (filter lists block URLs containing "admin"). Auth is handled by reading the `CF_Authorization` cookie via JS and passing its value as a `cf_token` query parameter (GET) or `_cf_token` in the JSON body (PUT). This avoids both Cloudflare cookie interception and CORS preflight issues. Flask's `_authenticate_from_cookie()` decodes the token from cookie, header, query param, or body.

### Admin Tools Section

Below the users table, an "Admin Tools" section displays service cards for admin-only services. These were moved here from the services page since they are only relevant to admins.

1. **SSH Access**
   - Status badge: checks SSH health via Cloudflare Worker health API (`https://meduseld-health.404-41f.workers.dev`), polls every 5 seconds
   - Shows Online/Offline/Cloudflare Tunnel Down with color coding (green/red/orange)
   - "Open SSH Terminal" button → links to `https://ssh.meduseld.io` (disabled when offline, links to status page when tunnel is down)

2. **System Monitor**
   - Always shows "Online" badge (static page, always available)
   - "Open System Monitor" button → links to `https://system.meduseld.io`

3. **Edoras Management**
   - Always shows "Online" badge (static page, always available)
   - "Open Edoras" button → links to `https://edoras.meduseld.io`

### Custom Achievements Section

Below the Admin Tools, a "Custom Achievements" card allows admins to manage custom achievements.

- "Create" button → opens modal with name, description, and Bootstrap icon class fields. Creates via `POST /check/custom-achievements`.
- Lists all custom achievements with icon, name, and description
- Each achievement has:
  - "Award" button (gift icon) → opens modal with user dropdown to award the achievement to a specific user via `POST /check/custom-achievements-award`
  - "Delete" button (trash icon) → confirms then deletes the achievement and removes all user unlocks via `DELETE /check/custom-achievements-<id>`
- Toast notifications for create/award/delete success and errors

---

## edoras.meduseld.io — Edoras Management (Admin Only)

File: `meduseld-site/edoras/index.html`

Admin page for managing entertainment and media services. Non-admin users are redirected to `services.meduseld.io?restricted=edoras-management`.

### Navigation

- "Back to Services" button → navigates to `https://services.meduseld.io`
- Profile widget (top-right, inside header nav bar)

### Service Cards (3-column grid)

Each card has an icon, title, description, and "Open" button that opens the service in a new tab.

1. **Overseerr** → `https://requests.meduseld.io` — Media request management
2. **Sonarr** → `https://sonarr.meduseld.io` — TV show management and automation
3. **Radarr** → `https://radarr.meduseld.io` — Movie management and automation
4. **Prowlarr** → `https://prowlarr.meduseld.io` — Indexer management for Sonarr and Radarr
5. **qBittorrent** → `https://qb.meduseld.io` — Download client management
6. **Chaptarr** → `https://chaptarr.meduseld.io` — Book management and automation
7. **Bazarr** → `https://bazarr.meduseld.io` — Subtitle management and automation
8. **Maintainerr** → `https://maintainerr.meduseld.io` — Media collection maintenance and cleanup

---

## herugrim.meduseld.io — Herugrim Landing Page

File: `meduseld-site/herugrim/index.html`

Public landing page for the Herugrim open-source project.

- Centered Herugrim logo (fade-in animation)
- Title "Herugrim" and subtitle describing it as a one-click Discord OIDC provider for Cloudflare Access
- "View on GitHub" button → opens `https://github.com/meduseld-io/herugrim` in new tab
- "Deploy to Cloudflare Workers" button → opens Cloudflare's one-click deploy flow for the herugrim repo
- "Contact" button → opens a small centered modal with a mailto link to `admin@meduseld.io` for questions, issues, or feedback
- Dark radial gradient background
- Footer with copyright year (auto-filled via JS), "meduseld.io" link to GitHub org (`https://github.com/meduseld-io`), and dynamic version badge (fetches latest release tag from herugrim GitHub repo, falls back to `v0.1.0-alpha` on error, links to releases page)
- No authentication required

---

## trivia.meduseld.io — Trivia Game

File: `meduseld-site/trivia/index.html`

Multiplayer trivia game with lobby system using WebSocket (Flask-SocketIO). Users can host or join lobbies to play together in real-time, or play solo. Questions from Open Trivia Database API. All game results (solo and multiplayer) are recorded to the backend leaderboard.

### Navigation

- "Back to Services" button → navigates to `https://services.meduseld.io`
- Profile widget (top-right, inside header nav bar)

### Leaderboard (Podium)

- Fetches aggregated win data from `GET https://health.meduseld.io/check/trivia-leaderboard`
- Top 3 users displayed as podium cards with medal emojis (🥇🥈🥉), avatar, display name, win count, and total correct answers
- Users ranked 4+ shown in a table below the podium
- Refreshes automatically after each game completes (solo or multiplayer)

### Lobby Browser (default view)

- "Host Game" button → opens create lobby form
- "Join by Code" button → prompts for a 6-character lobby code
- "Play Solo" button → opens solo game setup (no WebSocket needed)
- Refresh button → re-fetches active lobbies from `GET https://health.meduseld.io/check/trivia-lobbies`
- Lobby cards show: host name, lobby code, player count/max, question count, difficulty, category. Clicking a card joins the lobby.

### Create Lobby Form

- Questions: dropdown (5, 10, 15, 20 — default 10)
- Difficulty: dropdown (Any, Easy, Medium, Hard — default Any)
- Max Players: dropdown (2–12, default 8)
- Category: dropdown loaded dynamically from `https://opentdb.com/api_category.php`
- "Create Lobby" button → connects WebSocket to `panel.meduseld.io/trivia` namespace, emits `create_lobby` event

### Lobby Waiting Room

- Shows lobby code (with Bootstrap tooltip "Share this code with friends"), game settings summary
- Player chips with avatars, display names, HOST badge for the host
- Host can kick players (X icon on each non-host player chip, only visible during waiting)
- Host sees "Start Game" button. Non-hosts see "Waiting for host to start..."
- "Leave" button → emits `leave_lobby`, returns to browser. If host leaves, lobby closes for all players.
- Real-time updates via WebSocket: `player_joined`, `player_left` events update the player list

### WebSocket Connection

- Connects to `panel.meduseld.io` on the `/trivia` namespace
- Auth: passes `CF_Authorization` cookie value as `token` query parameter
- Server authenticates by decoding the JWT and looking up the user by Discord ID
- On connect, server emits `welcome` with `{user_id}` (DB user ID) so the client can identify itself in lobby data
- Transports: WebSocket with polling fallback

### Multiplayer Gameplay

- Host clicks "Start Game" → server fetches questions from Open Trivia DB, emits `game_starting` with countdown (5 seconds)
- Countdown displayed as large animated number
- Each question delivered via `question` event with shuffled answers and 20-second time limit
- Timer bar animates from full (gold) → yellow (50%) → red (25%) → empty
- Answer buttons use `data-idx` attributes and click event listeners (avoids HTML entity escaping issues with inline onclick)
- Clicking an answer: disables all buttons, highlights selection, emits `submit_answer` with the raw answer string
- `player_answered` event updates player chips (green border = answered)
- When all players answer (or timer expires), server emits `answer_reveal` with correct answer and per-player results
- Correct answer highlighted green, wrong answers red. Score badge updates. Progress dots update.
- 5-second pause between questions, then next question auto-advances
- After final question, server emits `game_over` with standings sorted by score
- Host sees an "End Game" button in the question header. Clicking it (with confirm dialog) ends the game early — shows standings but no stats are recorded to the leaderboard. Server emits `game_aborted` instead of `game_over`.

### Multiplayer Results

- Ranked scoreboard with medal emojis (🥇🥈🥉) for top 3, numbered for rest
- Each row shows avatar, display name, "You" badge for current user, score/total
- Winner row has gold border highlight
- "Results recorded to leaderboard" badge (server persists all player results as TriviaWin rows)
- "Back to Lobbies" and "Play Again" buttons

### Solo Mode

- Same setup as the old trivia game: questions, difficulty, category dropdowns
- Fetches questions client-side from `https://opentdb.com/api.php` (no WebSocket needed)
- Gameplay: question with 4 answer buttons, progress dots, score badge. "Solo" badge shown in header.
- Advances to next question after 1.2 seconds on answer
- Results: score with percentage, contextual message, progress dots, "Win recorded to leaderboard" badge
- Win recording: POSTs to `https://health.meduseld.io/check/trivia-record-win` with `{score, total_questions, category, _cf_token}`

---

## picker.meduseld.io — Party Game Picker

File: `meduseld-site/picker/index.html`

Weekly game wheel where users spin to randomly select the party game for the week. Any authenticated user can spin once per week; admins can re-spin to override.

### Navigation

- "Back to Services" button → navigates to `https://services.meduseld.io`
- Profile widget (top-right, inside header nav bar)

### Game of the Week Banner

- Displayed at the top when a game has been picked for the current week
- Shows: game name, optional cover art, who spun it, and when
- Hidden if no spin has occurred this week
- Data fetched from `GET https://health.meduseld.io/check/picker-current`

### Spin Wheel

- Canvas-based wheel with colored segments, one per game in the pool
- "Spin the Wheel" button triggers animation, server picks the winner randomly (client animation is visual only)
- Button disabled once a game has been picked for the current week (non-admin users)
- Admins see "Re-Spin (Admin)" button to override the current week's pick
- Wheel hidden if no games are in the pool
- Spin POSTs to `https://health.meduseld.io/check/picker-spin` with `{_cf_token}`

### Past Picks (History)

- List of previous weekly picks showing game name, cover art, week date, and who spun
- Fetched from `GET https://health.meduseld.io/check/picker-history` (last 20 picks, newest first)
- Admins see a "Clear" button (trash icon) that deletes all past picks after confirmation via `DELETE https://health.meduseld.io/check/picker-history`

### Game Pool

- List of all active games in the pool with names and who added them
- Admins see "Add Game" button → opens modal with name field and Steam search. Clicking the search icon (or pressing Enter) queries the Steam store API via allorigins proxy, shows up to 5 results with thumbnails. Clicking a result auto-fills the game name and cover image URL with a live preview. Cover image URL can also be pasted manually.
- Admins see delete buttons (x icon) on each game → confirms then soft-deletes via `DELETE https://health.meduseld.io/check/picker-games-<id>`
- Games added via `POST https://health.meduseld.io/check/picker-games` with `{name, image_url?, _cf_token}`

---

## profile.meduseld.io — User Profile & Achievements

File: `meduseld-site/profile/index.html`

User profile page showing personal stats and achievement progress. Any authenticated user can view their own profile.

### Navigation

- "Back to Services" button → navigates to `https://services.meduseld.io`
- Profile widget (top-right, inside header nav bar)
- Accessible from the "My Profile" link in the profile dropdown on all pages

### Profile Header

- User avatar (96px, gold border), display name with admin badge if applicable, @username, and "Member since" date
- Data fetched from `GET https://health.meduseld.io/check/profile?cf_token=<token>`

### Stats Row

- Displayed only if the user has played trivia games
- 5 stat cards: Games Played, Correct Answers, Wrong Answers, Best Score, Accuracy %

### Achievements Grid

- Shows all defined achievements as cards in a grid (unlocked first, then locked)
- Unlocked achievements: gold icon, name, description, unlock date
- Locked achievements: grayed out with reduced opacity
- Newly unlocked achievements (awarded on this page load) show a green "NEW" badge
- Achievement count badge: "X / Y unlocked"
- Achievements are checked and awarded server-side each time the profile is loaded

### Achievement Definitions

| ID                    | Name             | Description                                  | Category |
| --------------------- | ---------------- | -------------------------------------------- | -------- |
| first_login           | First Steps      | Log in to Meduseld for the first time        | general  |
| trivia_rookie         | Trivia Rookie    | Complete your first trivia game              | trivia   |
| trivia_veteran        | Trivia Veteran   | Complete 10 trivia games                     | trivia   |
| trivia_master         | Trivia Master    | Complete 50 trivia games                     | trivia   |
| perfect_score         | Perfect Score    | Get 100% on a trivia game                    | trivia   |
| trivia_streak_3       | On a Roll        | Get 3 perfect scores                         | trivia   |
| trivia_hard_win       | Big Brain        | Score 80%+ on a 10+ question trivia game     | trivia   |
| trivia_all_categories | Renaissance Mind | Play trivia in 10 different categories       | trivia   |
| night_owl             | Night Owl        | Play a trivia game between midnight and 5 AM | trivia   |
| media_explorer        | Media Explorer   | Access Edoras (Jellyfin) for the first time  | media    |
| rsvp_king             | RSVP King        | RSVP to 5 different events                   | social   |
| game_critic           | Game Critic      | Vote on the Games Up Next list               | general  |
| server_starter        | Ignition         | Start the game server 5 times                | server   |
| server_stopper        | Lights Out       | Stop the game server 10 times                | server   |
| server_killer         | Chaos Agent      | Force kill the game server 5 times           | server   |
| easter_egg            | Secret Passage   | Find the hidden link on the control panel    | secret   |

Admins can also create custom achievements via the API and manually award them to users.

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

---

## status.meduseld.io — Server Status Page

File: `meduseld-site/status/index.html`

Static page shown to users when the server/tunnel is down. No authentication required (served by Cloudflare Pages independently of the Flask backend).

- Meduseld logo and "The Hall is Quiet" heading with sword emoji
- Explanation text that the server is currently offline
- Live status dots for Control Panel, SSH Terminal, and Jellyfin Media — polls the health Worker (`https://meduseld-health.404-41f.workers.dev`) every 15 seconds
- Status dots: yellow (checking), green (online), red (offline/tunnel-down) with Bootstrap tooltips showing status text
- "Last checked" timestamp updates on each poll
- "Back to The Great Hall" button → links to `https://services.meduseld.io`
- Matches the existing Meduseld dark/gold theme with custom `status.png` background image (dark overlay)
- Footer with copyright year (auto-filled via JS) and quietarcade link
