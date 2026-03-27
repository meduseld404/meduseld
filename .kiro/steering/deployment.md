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

- **picker.meduseld.io** (picker/index.html)
  - Party Game Picker — spin wheel to select the weekly party game
  - Canvas-based animated wheel, Game of the Week banner, history, game pool management
  - Any authenticated user can spin once per week; admins can re-spin and manage the game pool
  - Data from `health.meduseld.io/check/picker-*` endpoints

- **remote.meduseld.io** (remote/index.html)
  - Peer-to-peer Remote Desktop using WebRTC for screen sharing and remote control
  - Host shares screen via `getDisplayMedia()`, viewers connect via session code
  - WebRTC signaling via Flask-SocketIO `/remote` namespace on `panel.meduseld.io`
  - Sessions are ephemeral (in-memory, 30-minute idle timeout), no DB persistence
  - Session list from `health.meduseld.io/check/remote-sessions`

- **fame.meduseld.io** (fame/index.html)
  - Hall of Fame — community screenshots & clips gallery
  - Authenticated users submit entries (file upload or external link), vote on entries
  - Gallery with filtering (screenshots/clips), sorting (votes/newest/oldest), pagination
  - File uploads stored at `/srv/media/fame/`, served via `/check/fame-media/<filename>`
  - Data from `health.meduseld.io/check/fame*` endpoints

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
- **Files**: `app/database.py` (init + `db.create_all()`), `app/models.py` (models)
- **Auto-create**: `db.create_all()` runs on every app startup, creating any missing tables automatically. Existing tables are untouched.
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

### GameVote Model (`app/models.py`)

Table: `game_votes`

| Column      | Type       | Notes                                 |
| ----------- | ---------- | ------------------------------------- |
| id          | Integer    | Primary key                           |
| user_id     | Integer    | FK to `users.id`                      |
| game_app_id | String(32) | Steam app ID of the game              |
| rank        | Integer    | User's preference rank (1 = top pick) |
| updated_at  | DateTime   | UTC, auto-set on creation/update      |

Unique constraint: `(user_id, game_app_id)` — one vote per user per game. Scoring uses Borda count: rank 1 = N points (N = total games), rank 2 = N-1, etc.

### GameListEntry Model (`app/models.py`)

Table: `game_list_entries`

| Column     | Type        | Notes                                       |
| ---------- | ----------- | ------------------------------------------- |
| id         | Integer     | Primary key                                 |
| app_id     | String(32)  | Steam app ID, unique                        |
| name       | String(256) | Game display name                           |
| url        | String(512) | Steam store URL (auto-generated if omitted) |
| tooltip    | String(512) | Optional note/tooltip text                  |
| added_by   | Integer     | FK to `users.id`                            |
| created_at | DateTime    | UTC, auto-set on creation                   |

### TriviaWin Model (`app/models.py`)

Table: `trivia_wins`

| Column          | Type        | Notes                                        |
| --------------- | ----------- | -------------------------------------------- |
| id              | Integer     | Primary key                                  |
| user_id         | Integer     | FK to `users.id`                             |
| score           | Integer     | Number of correct answers                    |
| total_questions | Integer     | Total questions in the game                  |
| category        | String(128) | Category name from Open Trivia DB (optional) |
| played_at       | DateTime    | UTC, auto-set on creation                    |

No unique constraint — multiple wins per user are expected (one row per completed game).

### TriviaLobby Model (`app/models.py`)

Table: `trivia_lobbies`

| Column        | Type        | Notes                                           |
| ------------- | ----------- | ----------------------------------------------- |
| id            | Integer     | Primary key                                     |
| code          | String(8)   | Unique, indexed. 6-char alphanumeric lobby code |
| host_user_id  | Integer     | FK to `users.id`                                |
| status        | String(16)  | `waiting`, `playing`, or `finished`             |
| num_questions | Integer     | Default 10                                      |
| difficulty    | String(16)  | Empty string = any                              |
| category      | String(8)   | Open Trivia DB category ID, empty = any         |
| category_name | String(128) | Display name of category                        |
| max_players   | Integer     | Default 8                                       |
| created_at    | DateTime    | UTC, auto-set on creation                       |
| started_at    | DateTime    | Set when game starts                            |
| finished_at   | DateTime    | Set when game ends                              |

Active game state (players, questions, scores, answers) is held in-memory in `trivia_ws.lobby_games` dict for speed. Only the lobby metadata and final results (via `TriviaWin`) are persisted to the database.

### UserAchievement Model (`app/models.py`)

Table: `user_achievements`

| Column         | Type       | Notes                      |
| -------------- | ---------- | -------------------------- |
| id             | Integer    | Primary key                |
| user_id        | Integer    | FK to `users.id`           |
| achievement_id | String(64) | Key from ACHIEVEMENTS dict |
| unlocked_at    | DateTime   | UTC, auto-set on creation  |

Unique constraint: `(user_id, achievement_id)` — one unlock per user per achievement.

Achievement definitions are hardcoded in `ACHIEVEMENTS` dict in `models.py` (not stored in DB). Custom achievements created by admins are stored in the `custom_achievements` table. `get_all_achievements()` merges both sources. The `check_achievements(user)` function in `webserver.py` evaluates all criteria and awards new ones. Called each time a user visits their profile page.

### UserActionCount Model (`app/models.py`)

Table: `user_action_counts`

| Column  | Type       | Notes                                                          |
| ------- | ---------- | -------------------------------------------------------------- |
| id      | Integer    | Primary key                                                    |
| user_id | Integer    | FK to `users.id`                                               |
| action  | String(64) | Action key (e.g. `server_start`, `server_stop`, `server_kill`) |
| count   | Integer    | Cumulative count, default 0                                    |

Unique constraint: `(user_id, action)` — one counter per user per action. Incremented via `UserActionCount.increment(user_id, action)` in the server start/stop/kill handlers.

### CustomAchievement Model (`app/models.py`)

Table: `custom_achievements`

| Column         | Type        | Notes                                    |
| -------------- | ----------- | ---------------------------------------- |
| id             | Integer     | Primary key                              |
| achievement_id | String(64)  | Unique, auto-generated from name         |
| name           | String(128) | Display name                             |
| description    | String(256) | Description text                         |
| icon           | String(64)  | Bootstrap icon class, default `bi-award` |
| category       | String(32)  | Category, default `custom`               |
| created_by     | Integer     | FK to `users.id`                         |
| created_at     | DateTime    | UTC, auto-set on creation                |

Admin-created achievements. Merged with hardcoded `ACHIEVEMENTS` via `get_all_achievements()`. Can be manually awarded to users via the `/check/custom-achievements-award` endpoint.

### PickerGame Model (`app/models.py`)

Table: `picker_games`

| Column     | Type        | Notes                                   |
| ---------- | ----------- | --------------------------------------- |
| id         | Integer     | Primary key                             |
| name       | String(256) | Game display name                       |
| image_url  | String(512) | Optional cover art URL                  |
| is_active  | Boolean     | Default true, soft-delete sets to false |
| added_by   | Integer     | FK to `users.id`                        |
| created_at | DateTime    | UTC, auto-set on creation               |

### WeeklyPick Model (`app/models.py`)

Table: `weekly_picks`

| Column     | Type     | Notes                                                  |
| ---------- | -------- | ------------------------------------------------------ |
| id         | Integer  | Primary key                                            |
| game_id    | Integer  | FK to `picker_games.id`                                |
| spun_by    | Integer  | FK to `users.id`                                       |
| spun_at    | DateTime | UTC, auto-set on creation                              |
| week_start | Date     | Unique. Monday of the week (defines one pick per week) |

"Week" is defined as Monday 00:00 UTC to Sunday 23:59 UTC. The `week_start` unique constraint ensures only one pick per week. Admins can override by updating the existing row.

### FameEntry Model (`app/models.py`)

Table: `fame_entries`

| Column      | Type        | Notes                                                       |
| ----------- | ----------- | ----------------------------------------------------------- |
| id          | Integer     | Primary key                                                 |
| user_id     | Integer     | FK to `users.id`                                            |
| title       | String(256) | Entry title                                                 |
| caption     | Text        | Optional caption/description                                |
| media_type  | String(16)  | `image` or `video`                                          |
| source_type | String(16)  | `upload` or `link`                                          |
| file_path   | String(512) | Server path for uploads (e.g. `/srv/media/fame/<uuid>.jpg`) |
| url         | String(512) | External URL for link entries                               |
| vote_count  | Integer     | Denormalized vote count, default 0                          |
| tag         | String(64)  | Optional game tag (e.g. `PEAK`, `R.E.P.O.`, `Icarus`)       |
| created_at  | DateTime    | UTC, auto-set on creation                                   |

### FameVote Model (`app/models.py`)

Table: `fame_votes`

| Column   | Type    | Notes                                      |
| -------- | ------- | ------------------------------------------ |
| id       | Integer | Primary key                                |
| user_id  | Integer | FK to `users.id`                           |
| entry_id | Integer | FK to `fame_entries.id`, CASCADE on delete |

Unique constraint: `(user_id, entry_id)` — one vote per user per entry. Vote count is denormalized on `FameEntry.vote_count` for sort performance.

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
- Trivia multiplayer: `app/trivia_ws.py`
- Remote desktop signaling: `app/remote_ws.py`
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
│   ├── fame
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

Flask proxy routing (`check_service()` in `webserver.py`): requests to `health.meduseld.io/check/stats` → `127.0.0.1:5004/stats`, `/check/history` → `127.0.0.1:5004/history`, `/check/backup` → `127.0.0.1:5003/backup`, `/check/backup-status` → `127.0.0.1:5003/status`, `/check/reboot` → `127.0.0.1:5002/reboot`, `/check/system-logs` → Flask's own `api_server_logs()`, `/check/media-auth` → Jellyfin SSO auth (authenticated, calls `_jellyfin_auth_inner()` to auto-provision and authenticate a Jellyfin account, returns `{token, user_id, server_id}`), `/check/seerr-auth` → Jellyseerr SSO auth (authenticated, provisions Jellyfin account then serves HTML page that POSTs credentials to Jellyseerr from the browser so `connect.sid` is set on the correct domain), `/check/team-roster` → admin users list with trivia stats (authenticated via CF_Authorization JWT passed as `cf_token` query param or `_cf_token` in body; each user includes a `trivia` object with `games_played`, `total_correct`, `total_wrong`, `total_questions`, `best_score`, `accuracy`), `/check/team-roster-<id>` → admin user update (PUT), `/check/calendar` → calendar events list (GET) and create (POST, admin only), `/check/calendar-<id>` → delete calendar event (DELETE, admin only) or edit calendar event (PUT with `title`/`event_date`, admin only) or RSVP (PUT with `status`, any authenticated user), `/check/game-votes` → game voting (GET returns aggregated scores + user's rankings, PUT submits user's ranked list), `/check/games` → games list (GET returns all games, POST adds a game — authenticated), `/check/games-<app_id>` → delete game (DELETE, admin only — also removes associated votes), `/check/trivia-lobbies` → list active multiplayer trivia lobbies (GET, public — returns lobbies with status `waiting`), `/check/trivia-leaderboard` → trivia leaderboard (GET, public — returns aggregated wins per user sorted by win count), `/check/trivia-record-win` → record a trivia game result (POST, authenticated — body: `{score, total_questions, category?, _cf_token}`), `/check/profile` → user profile with achievements and trivia stats (GET, authenticated — runs achievement checks and returns full profile data with all achievements and their locked/unlocked status), `/check/picker-current` → current week's game pick (GET, public), `/check/picker-spin` → spin the wheel to pick a game (POST, authenticated — admins can re-spin), `/check/picker-history` → past weekly picks (GET, public), `/check/picker-games` → game pool list (GET, public) and add game (POST, admin only), `/check/picker-games-<id>` → soft-delete game from pool (DELETE, admin only), `/check/fellowsync-rooms` → FellowSync active rooms (GET, public — proxies to `127.0.0.1:5050/api/rooms/active`, returns `{rooms, count}`, gracefully returns empty list if FellowSync is down), `/check/remote-sessions` → list active remote desktop sessions (GET, public — returns `{sessions}` from in-memory state, cleans up expired sessions on each call), `/check/fame` → Hall of Fame entries (GET public with optional auth for vote status, POST authenticated — file upload or JSON link), `/check/fame-<id>` → delete fame entry (DELETE, owner or admin), `/check/fame-<id>-vote` → toggle vote on entry (POST, authenticated), `/check/fame-media/<filename>` → serve uploaded fame media files from `/srv/media/fame/` (GET, public, dedicated Flask route with 24h cache). All authenticated endpoints use `_authenticate_from_cookie()` which reads the CF_Authorization JWT from cookie, header, `cf_token` query param, or `_cf_token` in JSON body.

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

### Trivia Endpoints (via health proxy)

- `GET /check/trivia-lobbies` - (Public) Returns active multiplayer lobbies with status `waiting`: `{lobbies: [{code, status, settings, players, player_count, host_user_id, host_name, ...}]}`.
- `GET /check/trivia-leaderboard` - (Public) Returns aggregated trivia wins per user: `{leaderboard: [{user_id, discord_id, display_name, avatar_url, wins, total_score, total_questions}]}`. Sorted by win count descending, then total score.
- `POST /check/trivia-record-win` - (Authenticated) Records a completed trivia game. Body: `{score, total_questions, category?, _cf_token}`. Creates a `TriviaWin` row. Every completed game is recorded regardless of score.

### Trivia WebSocket (Flask-SocketIO)

Namespace: `/trivia` on `panel.meduseld.io`. Uses `flask-socketio` with `gevent` async mode. Lobby game state is held in-memory (`trivia_ws.lobby_games` dict); only final results are persisted to DB.

Auth: Client passes `CF_Authorization` cookie value as `token` query parameter on connect. Server decodes the JWT (without signature verification) and looks up the user by `discord_user.id`. Unauthenticated connections are rejected.

Module: `app/trivia_ws.py` — all lobby logic is isolated here. `socketio` is initialized in `trivia_ws.py` and attached to the Flask app via `socketio.init_app(app)` in `webserver.py`. The app is started with `socketio.run()` instead of `app.run()`.

Events (client → server):

- `create_lobby` — Create a new lobby. Data: `{num_questions, difficulty, category, category_name, max_players}`. Generates a 6-char code, persists `TriviaLobby` to DB, joins the SocketIO room.
- `join_lobby` — Join an existing lobby. Data: `{code}`. Validates lobby exists, is waiting, not full, user not in another lobby.
- `leave_lobby` — Leave a lobby. Data: `{code}`. If host leaves during waiting, lobby closes for all.
- `start_game` — Host-only. Fetches questions from Open Trivia DB, starts countdown, then delivers questions.
- `submit_answer` — Submit answer for current question. Data: `{code, answer}`. When all players answer (or 20s timer expires), server reveals answer and advances.
- `kick_player` — Host-only, waiting state only. Data: `{code, user_id}`. Removes player from lobby.
- `end_game` — Host-only, playing/countdown states. Data: `{code}`. Ends the game early without persisting any `TriviaWin` rows.
- `play_again` — Host-only, results state. Data: `{code, settings?}`. Resets the lobby to waiting state with optional new settings (`num_questions`, `difficulty`, `category`, `category_name`, `max_players`). Clears scores/answers, removes disconnected players, cancels pending cleanup. Emits `lobby_reset` to all players.

Events (server → client):

- `welcome` — Sent on connect. Data: `{user_id}` (DB user ID).
- `lobby_created` / `lobby_joined` — Lobby state after create/join. Data: `{lobby}`.
- `player_joined` / `player_left` — Player list updates. Data: `{user_id, display_name, lobby}`.
- `lobby_closed` — Lobby was closed. Data: `{reason}`.
- `kicked` — You were kicked. Data: `{reason}`.
- `game_starting` — Countdown before first question. Data: `{countdown, total_questions}`.
- `question` — New question. Data: `{index, question, category, difficulty, answers, time_limit}`.
- `player_answered` — Someone answered (not what). Data: `{user_id, question_index, lobby}`.
- `answer_reveal` — Correct answer + per-player results. Data: `{correct_answer, player_results, question_index}`.
- `game_over` — Final standings. Data: `{standings}`. Server persists all player results as `TriviaWin` rows.
- `game_aborted` — Game ended early by host. Data: `{standings}`. No stats persisted.
- `lobby_reset` — Lobby reset for play again. Data: `{lobby}`. Sent to all players when host triggers `play_again`. Lobby returns to waiting state with updated settings.

### Profile & Achievements Endpoint (via health proxy)

- `GET /check/profile` - (Authenticated) Returns the user's full profile including trivia stats and all achievements with locked/unlocked status. Runs `check_achievements()` on each call to award any newly earned achievements. Response includes `achievements` array (all defined achievements with `unlocked` boolean), `trivia` stats object, and `new_achievements` array (IDs unlocked on this request).
- `POST /check/easter-egg` - (Authenticated) Awards the "Secret Passage" easter egg achievement. Body: `{_cf_token}`. Returns `{ok, unlocked}` on first unlock, `{ok, already}` if already unlocked.

### Custom Achievements Endpoints (via health proxy)

- `GET /check/custom-achievements` - (Authenticated) List all admin-created custom achievements.
- `POST /check/custom-achievements` - (Admin only) Create a custom achievement. Body: `{name, description, icon?, category?}`. Auto-generates `achievement_id` from name with `custom_` prefix.
- `POST /check/custom-achievements-award` - (Admin only) Award a custom achievement to a user. Body: `{achievement_id, user_id, _cf_token}`.
- `DELETE /check/custom-achievements-<id>` - (Admin only) Delete a custom achievement and remove all user unlocks for it.

### Party Game Picker Endpoints (via health proxy)

- `GET /check/picker-current` - (Public) Returns the current week's pick or `{pick: null}` if no spin yet. Week starts Monday 00:00 UTC.
- `POST /check/picker-spin` - (Authenticated) Spin the wheel. Server randomly selects a game from the active pool. Returns `{pick}`. Rejects with 409 if already spun this week (unless admin, who can re-spin to override). Body: `{_cf_token}`.
- `GET /check/picker-history` - (Public) Returns last 20 weekly picks, newest first. Response: `{history: [{game_name, game_image, spun_by_name, spun_at, week_start, ...}]}`.
- `DELETE /check/picker-history` - (Admin only) Deletes all past picks. Auth via `cf_token` query param. Response: `{ok: true, deleted: <count>}`.
- `GET /check/picker-games` - (Public) Returns all active games in the pool. Response: `{games: [{id, name, image_url, added_by_name, ...}]}`.
- `POST /check/picker-games` - (Authenticated) Add a game to the pool. Body: `{name, image_url?, _cf_token}`.
- `DELETE /check/picker-games-<id>` - (Admin only) Soft-delete a game from the pool (sets `is_active = false`). Auth via `cf_token` query param.

### FellowSync Rooms Endpoint (via health proxy)

- `GET /check/fellowsync-rooms` - (Public) Proxies to FellowSync backend at `127.0.0.1:5050/api/rooms/active`. Returns `{rooms: [{room_id, host_name, participant_count, current_track, current_artist, is_playing, group_id}], count}`. `group_id` is the host's BYOK sync group ID (may be null if the host has no group). Returns `{rooms: [], count: 0}` if FellowSync is unreachable. Used by the services page active room banner.

### Remote Desktop Endpoint (via health proxy)

- `GET /check/remote-sessions` - (Public) Returns all active (non-expired) remote desktop sessions. Response: `{sessions: [{code, host_user_id, host_name, host_avatar, viewer_count, viewers}]}`. Cleans up expired sessions on each call.

### Remote Desktop WebSocket (Flask-SocketIO)

Namespace: `/remote` on `health.meduseld.io` (same Flask app as `panel.meduseld.io`, but without Cloudflare Access protection — avoids cross-origin session issues). All session state is held in-memory (`remote_ws.remote_sessions` dict); nothing is persisted to the database. Sessions auto-expire after 30 minutes of inactivity.

Module: `app/remote_ws.py` — all signaling logic is isolated here. Event handlers are registered via `register_remote_ws(socketio)` called from `webserver.py`.

Auth: Client passes `CF_Authorization` cookie value as `token` query parameter on connect. Server decodes the JWT (without signature verification) and looks up the user by `discord_user.id`. Unauthenticated connections are rejected.

Events (client → server):

- `create_session` — Create a new hosting session. Generates a 6-char code, stores in-memory. Host must already have a display stream (captured client-side before emitting).
- `join_session` — Request to join a session. Data: `{code}`. Adds user to pending list; host must approve.
- `approve_viewer` — Host-only. Data: `{code, user_id}`. Moves viewer from pending to connected, joins SocketIO room.
- `deny_viewer` — Host-only. Data: `{code, user_id}`. Removes from pending, notifies viewer.
- `toggle_control` — Host-only. Data: `{code, user_id}`. Toggles mouse+keyboard control permission for a viewer.
- `signal` — Relay WebRTC signaling data. Data: `{code, target_user_id, signal}`. Signal contains SDP offers/answers or ICE candidates.
- `input_event` — Relay mouse/keyboard input from viewer to host. Data: `{code, event}`. Only relayed if viewer has control granted.
- `end_session` — Host-only. Data: `{code}`. Ends session, notifies all viewers.
- `leave_session` — Viewer leaves. Data: `{code}`. Removes from viewers/pending.

Events (server → client):

- `welcome` — Sent on connect. Data: `{user_id}` (DB user ID).
- `session_created` — Session created successfully. Data: `{session}`.
- `viewer_request` — Sent to host when a viewer requests to join. Data: `{user_id, display_name, avatar_url}`.
- `join_pending` — Sent to viewer after requesting to join. Data: `{code, message}`.
- `join_approved` — Sent to viewer when host approves. Data: `{session}`.
- `join_denied` — Sent to viewer when host denies. Data: `{reason}`.
- `viewer_joined` / `viewer_left` — Broadcast to room. Data includes `{user_id, display_name, session}`.
- `session_updated` — Broadcast when session state changes (e.g. control toggled). Data: `{session}`.
- `session_ended` — Broadcast when session ends. Data: `{reason}`.
- `sessions_list` — Broadcast to all `/remote` clients when the active sessions list changes. Data: `{sessions}`.
- `signal` — Relayed signaling data. Data: `{from_user_id, signal}`.
- `control_toggled` — Sent to viewer when control permission changes. Data: `{granted}`.
- `input_event` — Relayed input from viewer to host. Data: `{from_user_id, event}`.

### Hall of Fame Endpoints (via health proxy)

- `GET /check/fame` - (Public, optional auth for vote status) List fame entries, paginated. Query params: `page` (default 1), `per_page` (default 20, max 50), `sort` (`votes`/`newest`/`oldest`, default `votes`), `type` (`image`/`video`/empty for all), `tag` (game tag, case-insensitive filter). Response: `{entries: [{id, user_id, display_name, avatar_url, title, caption, media_type, source_type, vote_count, tag, created_at, voted, media_url}], total, page, per_page}`. If authenticated, each entry includes `voted` boolean.
- `POST /check/fame` - (Authenticated) Create a fame entry. Supports two content types:
  - `multipart/form-data`: file upload with `title`, `caption`, `tag`, `file` fields. Images (JPEG/PNG/GIF/WebP) and videos (MP4/WebM) up to 250MB. Files saved to `/srv/media/fame/` with UUID filenames.
  - `application/json`: external link with `{title, url, caption?, tag?, media_type?, _cf_token}`. `media_type` defaults to `video`.
- `GET /check/fame-tags` - (Public) Returns all unique tags from existing entries. Response: `{tags: ["PEAK", "R.E.P.O.", ...]}`. Sorted alphabetically (case-insensitive). Frontend merges with hardcoded default tags (PEAK, R.E.P.O., RV There Yet?, Icarus, Battlefield 6, YAPYAP) with case-insensitive dedup.
- `DELETE /check/fame-<id>` - (Authenticated) Delete a fame entry. Owner or admin only. Removes associated votes and deletes uploaded file if applicable. Auth via `cf_token` query param.
- `POST /check/fame-<id>-vote` - (Authenticated) Toggle vote on an entry. Body: `{_cf_token}`. Returns `{voted, vote_count}`. Creates or removes a `FameVote` row and updates the denormalized `vote_count` on `FameEntry`.
- `GET /check/fame-media/<filename>` - (Public) Dedicated Flask route (not in `check_service`). Serves uploaded fame media files from `/srv/media/fame/`. Returns file with correct MIME type and 24-hour cache header. Returns 404 for missing files or path traversal attempts.

### Jellyfin Auto-Login

- `GET /api/jellyfin-auth` - (Authenticated) Auto-provisions a Jellyfin account for the user if one doesn't exist, authenticates via the Jellyfin API, and returns `{token, user_id, server_id}`. Stores `jellyfin_user_id` and `jellyfin_password` in the users table. Handles password resets if credentials get out of sync.
- `GET jellyfin.meduseld.io/sso-login?token=&userId=&serverId=` - Served via the Jellyfin catch-all proxy. Uses iframe-first approach: loads Jellyfin web client in a hidden iframe, waits for its ConnectionManager to initialize `jellyfin_credentials` in localStorage, then patches in `AccessToken`/`UserId` and redirects to the Jellyfin home page. Falls back to direct credential write after 15-second timeout.

### Jellyseerr Auto-Login

- `GET /check/seerr-auth` - (Authenticated via `_authenticate_from_cookie()`) Provisions the user's Jellyfin account (reuses `_jellyfin_auth_inner()`), then serves an HTML page that uses `fetch` to POST the user's Jellyfin credentials directly to `https://requests.meduseld.io/api/v1/auth/jellyfin` from the browser. This ensures Jellyseerr sets its own `connect.sid` cookie on the correct domain (`requests.meduseld.io`). On success, redirects to Jellyseerr. The page also clears any stale `connect.sid` cookie from `.meduseld.io`. Credentials are auto-generated (not user-chosen) and the page is served over HTTPS with `Cache-Control: no-store`.
- On the services page, the "Request" button in the Edoras modal calls `openSeerr()` which navigates to `health.meduseld.io/check/seerr-auth?cf_token=<token>`. The endpoint handles everything and redirects the user to Jellyseerr.

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
- `JELLYSEERR_INTERNAL_URL`: Internal Jellyseerr server URL (defaults to `http://localhost:5055`)

## Python Dependencies (Key Additions)

- `Flask-SQLAlchemy` — ORM for PostgreSQL
- `Flask-Migrate` — Alembic-based DB migrations
- `psycopg2-binary` — PostgreSQL driver
- `PyJWT` — JWT decoding for Cloudflare Access tokens
- `flask-socketio` — WebSocket support for trivia multiplayer lobbies
- `gevent` — Async worker for Flask-SocketIO
- `gevent-websocket` — WebSocket transport for gevent

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
