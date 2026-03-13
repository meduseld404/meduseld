# Changelog

All notable changes to the Meduseld Server Control Panel project.

## [0.3.0-alpha] - 2026-03-11

### Server Stability & Diagnostics

#### Enhanced Startup & Monitoring

- **Improved Start Script**: Complete rewrite with comprehensive error handling and diagnostics
- **Startup Log Persistence**: All server start/stop/crash events now persist in `/srv/games/icarus/startup.log`
- **Process Health Monitoring**: Automatic health checks every 5 minutes (CPU, RAM, threads)
- **Wine Error Logging**: Captures Wine errors while filtering out harmless warnings
- **Exit Code Detection**: Identifies crash types (SIGKILL, segfault, SIGTERM, clean shutdown)
- **Process Monitor**: Detects when server dies unexpectedly and logs the event
- **Stale Session Cleanup**: Automatically removes dead tmux sessions before starting

#### Startup Script Logs Panel

- New dedicated panel for startup/shutdown/crash history
- Color-coded separators matching control buttons (green=start, red=stop, purple=kill, dark red=crash)
- Shows Wine configuration, process health checks, and exit codes
- Clear logs button with archiving to `.old` file
- Real-time updates every 5 seconds

#### Bug Fixes

- **Fixed Wine Crash**: Resolved "Read access denied for device L:\\??\\Z:\\" error by setting `WINEDEBUG=-all`
- **Fixed systemd Killing Server**: Changed `KillMode=mixed` to `KillMode=process` to prevent webserver restarts from killing game server
- **Fixed Process Detection**: Enhanced `is_running()` to properly detect Wine-wrapped server process
- **Fixed Button Loading Delay**: Pass `server_state` to template for instant button state on page load

### UI/UX Improvements

#### Control Panel Enhancements

- **Player Count Display**: Shows current online players (X/8) via Steam Query Protocol
- **Download Backup Button**: Download save file (`Expedition 404.json`) with timestamp
- **Backup to Cloud Button**: Added (disabled/coming soon)
- **Button Layout**: Moved SSH Terminal and Download Backup to top right header
- **Responsive Header**: Added flex-wrap for better mobile button layout
- **Button Sizing**: Fixed control buttons to match server status panel height
- **Cursor Improvements**: Buttons now show pointer cursor on hover

#### Visual Updates

- **Graph Colors**: Server metrics in green, system metrics in yellow (both CPU and RAM graphs)
- **Update Badge**: Changed to blue background for "Update Available"
- **Up to Date Badge**: Changed to green background
- **Health Badge**: Color-coded text (Good=green, Warning=orange, Critical=red)
- **Log Separators**: Color-coded separators in game server logs for version changes, restarts, stops

#### Menu Page Updates

- **VPN Access Card**: Added coming soon card for OpenVPN integration
- **Game Prices**: Shows Steam prices with sale badges and discount percentages
- **Production Badge**: Changed text color to white for better visibility
- **News Badge Click**: Fixed to not toggle panel when clicking badge tooltip

#### SSH Terminal Page

- **Mobile Responsive**: Fixed button layout on mobile devices
- Buttons now properly align to top right on all screen sizes

### Technical Improvements

- Added Steam Query Protocol (A2S_INFO) implementation for player count
- Enhanced logging throughout startup process
- Better error messages and diagnostic information
- Improved process validation before reporting success
- Added socket and struct imports for network queries

## [0.2.0-alpha] - 2026-03-10

### Major Updates

#### Authentication & Security

- **Discord SSO Integration**: Replaced email OTP with Discord authentication via custom OIDC worker
- **Cloudflare Access**: Configured with Discord as identity provider
- **CORS Support**: Added proper CORS headers for cross-origin requests with credential support
- **Session Management**: Fixed cross-subdomain authentication issues

#### New Services

- **Menu Page** (services.meduseld.io): Central hub for all services with status indicators
- **Health Monitoring** (health.meduseld.io): Dedicated health check system with Cloudflare Worker
- **Jellyfin Integration** (jellyfin.meduseld.io): Media streaming proxy through Flask app
- **User Profiles**: Discord-based user profile system with authentication state

#### Health & Monitoring

- Implemented health check worker to monitor all services
- Added `/health-check-b8f3a9c2` endpoint with Cloudflare Access bypass
- Created `/check/<service>` endpoints for service-specific health checks
- Real-time service status on menu page (online/offline/tunnel down)
- Health check API at meduseld-health.404-41f.workers.dev

#### UI/UX Improvements

- Added SSH Terminal button to control panel
- Improved stat displays: CPU shown as cores used, RAM/disk shown as GB used/total
- Added detailed tooltips to all metrics and badges
- Visual log separators when server state changes
- Better cursor handling (pointer for buttons, text for logs, default elsewhere)
- Enhanced process detection to skip wrapper processes (tmux, wine, xvfb)

#### Bug Fixes

- Fixed panel.meduseld.io routing and 404 errors
- Fixed catch-all route to properly handle non-Jellyfin subdomains
- Resolved Cloudflare Access redirect loops
- Fixed server process detection for Wine-wrapped executables
- Improved disk usage calculation to sum all mounted partitions
- Fixed development mode detection and banner display

#### API & Endpoints

- Added `/me` endpoint for authentication status
- Added `/api/auth/profile` endpoint with CORS support
- Added OPTIONS handlers for preflight requests
- Improved error handling and logging

#### Configuration

- Added health.meduseld.io to allowed hosts
- Updated Cloudflare Tunnel config for all subdomains
- Configured Discord OIDC worker with environment variables

## [0.1.0-alpha] - 2026-03-09

### Alpha Release - Testing Phase

**Note**: This is an alpha release. All functionality needs to be verified before v1.0.0.

#### Infrastructure

- **Cloudflare Tunnel**: HTTPS, authentication, and routing
- **Cloudflare Access**: Email-based OTP authentication
- **ttyd**: Lightweight web terminal for SSH access
- **systemd**: Service management for Flask app and ttyd
- **Native Python**: Direct execution on Ubuntu Server

#### Features

- Web-based control panel at panel.meduseld.io
- Web-based SSH terminal at ssh.meduseld.io using ttyd
- Real-time server monitoring (CPU, RAM, disk, uptime)
- Historical metrics graphs (30-minute CPU/RAM charts)
- Game server control (start, stop, restart, force kill)
- Live log streaming from game server
- Update detection via Steam API
- Crash detection and automatic state management
- Activity logging for user actions
- Rate limiting on control endpoints
- Restart cooldown protection
- Thread health monitoring

#### Control Panel

- Server status with state machine (offline, starting, running, stopping, crashed)
- System metrics with color-coded health indicators
- Server-specific CPU and RAM usage
- Control buttons with state-aware enabling/disabling
- Live game server logs with auto-scroll
- Historical graphs using Chart.js
- Update availability notifications
- Uptime tracking

#### SSH Terminal

- Browser-based terminal access
- Login authentication (username/password)
- Full bash session with all commands
- Navigation buttons to return to menu or panel
- Secure access through Cloudflare Tunnel

#### Security

- Cloudflare Access email-based authentication
- Rate limiting to prevent abuse
- Host validation for approved domains
- Restart cooldown to prevent spam
- Activity logging with IP tracking

#### Configuration

- Single config.py file for all settings
- Auto-detection of production vs development mode
- Environment-specific paths and settings
- Configurable monitoring thresholds
- Adjustable timeouts and intervals

#### API Endpoints

- POST /start - Start game server
- POST /stop - Stop game server
- POST /restart - Restart with update check
- POST /kill - Force kill server
- GET /api/stats - Server stats and metrics
- GET /api/logs - Game server logs
- GET /api/console - Console output
- GET /api/history - Historical metrics
- GET /api/check-update - Check for updates
- GET /api/activity - User activity log

#### Tech Stack

- Python 3.12 + Flask
- Bootstrap 5 + Chart.js
- ttyd (C-based terminal emulator)
- Ubuntu Server 24.04 LTS
- Cloudflare Tunnel (cloudflared)
- psutil for process monitoring
- Icarus Dedicated Server (via Wine)

#### Known Issues / To Verify

- [ ] Server start/stop/restart functionality
- [ ] Update detection and application
- [ ] Crash detection accuracy
- [ ] Historical metrics data collection
- [ ] SSH terminal stability
- [ ] Rate limiting effectiveness
- [ ] All API endpoints
- [ ] Cross-browser compatibility
- [ ] Mobile responsiveness
