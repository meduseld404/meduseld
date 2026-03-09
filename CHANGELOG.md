# Changelog

All notable changes to the Meduseld Server Control Panel project.

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
