"""
Standalone system monitoring microservice for Meduseld.
Runs independently of the main Flask app so the system monitor page
can display server stats even when the panel is down.

Listens on port 5004.
Serves GET /stats (live system metrics) and GET /history (30-min rolling data).
Runs as its own systemd service: meduseld-monitoring.service

Reads real data from:
- psutil for CPU, RAM, disk, temperature
- RAPL (Running Average Power Limit) for CPU package power
- nvidia-smi for GPU power draw
Estimates the rest from static config values.
"""

import json
import logging
import os
import time
import threading
import subprocess
import psutil
from collections import deque
from http.server import HTTPServer, BaseHTTPRequestHandler

logging.basicConfig(
    level=logging.INFO,
    format="[monitoring] %(asctime)s %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("stats_server")

PORT = 5004

# ===== Power estimate config (watts) =====
RAM_WATTS_PER_STICK = 3
RAM_STICKS = 4
NVME_WATTS = 5
MOTHERBOARD_WATTS = 15
AIO_PUMP_WATTS = 4
FAN_WATTS = 4
COST_PER_KWH = float(os.environ.get("ELECTRICITY_COST_PER_KWH", "0.245"))

# ===== Rolling history (30 min at 30s intervals = 60 entries) =====
history = deque(maxlen=60)

# ===== Power cache =====
_power_cache = {"data": None, "ts": 0}


def get_cpu_temperature():
    """Get CPU temperature in Celsius."""
    try:
        if hasattr(psutil, "sensors_temperatures"):
            temps = psutil.sensors_temperatures()
            if temps:
                for name in ["coretemp", "cpu_thermal", "k10temp", "zenpower"]:
                    if name in temps and temps[name]:
                        return round(temps[name][0].current, 1)
        # Fallback: /sys/class/thermal
        for zone in [
            "/sys/class/thermal/thermal_zone0/temp",
            "/sys/class/thermal/thermal_zone1/temp",
        ]:
            if os.path.exists(zone):
                with open(zone, "r") as f:
                    return round(int(f.read().strip()) / 1000.0, 1)
    except Exception as e:
        logger.warning("Failed to read CPU temperature: %s", e)
    return None


def get_power_stats():
    """Get power consumption from sensors + estimates. Cached for 5 seconds."""
    global _power_cache
    now = time.time()
    if _power_cache["data"] and (now - _power_cache["ts"]) < 5:
        return _power_cache["data"]

    power = {
        "cpu_watts": None,
        "gpu_watts": None,
        "ram_watts": None,
        "storage_watts": None,
        "other_watts": None,
        "total_watts": None,
    }

    try:
        # CPU power via RAPL
        for rapl_path in [
            "/sys/class/powercap/intel-rapl:0/energy_uj",
            "/sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj",
        ]:
            if os.path.exists(rapl_path):
                try:
                    with open(rapl_path, "r") as f:
                        e1 = int(f.read().strip())
                    time.sleep(0.1)
                    with open(rapl_path, "r") as f:
                        e2 = int(f.read().strip())
                    power["cpu_watts"] = round(max((e2 - e1) / (0.1 * 1_000_000), 0), 1)
                except Exception as e:
                    logger.warning("Failed to read RAPL energy: %s", e)
                break

        # Fallback: estimate from CPU usage and TDP
        if power["cpu_watts"] is None:
            try:
                pct = psutil.cpu_percent(interval=0)
                power["cpu_watts"] = round(20 + (45 * pct / 100), 1)
            except Exception as e:
                logger.warning("Failed to estimate CPU power from usage: %s", e)
                power["cpu_watts"] = 0

        # GPU power via nvidia-smi
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=power.draw", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                power["gpu_watts"] = round(float(result.stdout.strip().split("\n")[0]), 1)
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
            power["gpu_watts"] = 0

        # Static estimates
        power["ram_watts"] = round(RAM_WATTS_PER_STICK * RAM_STICKS, 1)
        power["storage_watts"] = NVME_WATTS
        power["other_watts"] = round(MOTHERBOARD_WATTS + AIO_PUMP_WATTS + FAN_WATTS, 1)

        components = [
            power["cpu_watts"],
            power["gpu_watts"],
            power["ram_watts"],
            power["storage_watts"],
            power["other_watts"],
        ]
        power["total_watts"] = round(sum(w for w in components if w is not None), 1)
        power["cost_per_kwh"] = COST_PER_KWH
    except Exception as e:
        logger.error("Power stats error: %s", e)

    _power_cache["data"] = power
    _power_cache["ts"] = time.time()
    return power


def get_system_stats():
    """Collect all system metrics."""
    try:
        cpu = psutil.cpu_percent(interval=0.3)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")

        total_disk = 0
        used_disk = 0
        try:
            for part in psutil.disk_partitions():
                if part.fstype and part.fstype not in ["squashfs", "tmpfs", "devtmpfs"]:
                    try:
                        u = psutil.disk_usage(part.mountpoint)
                        total_disk += u.total
                        used_disk += u.used
                    except Exception as e:
                        logger.warning("Failed to read disk usage for %s: %s", part.mountpoint, e)
        except Exception as e:
            logger.warning("Failed to enumerate disk partitions: %s", e)
            total_disk = disk.total
            used_disk = disk.used

        return {
            "cpu": cpu,
            "cpu_temp": get_cpu_temperature(),
            "ram_percent": mem.percent,
            "ram_used": round(mem.used / (1024**3), 2),
            "ram_total": round(mem.total / (1024**3), 2),
            "disk_percent": disk.percent,
            "disk_used": round(used_disk / (1024**3), 2),
            "disk_total": round(total_disk / (1024**3), 2),
            "power": get_power_stats(),
        }
    except Exception as e:
        logger.error("Stats collection error: %s", e)
        return {
            "cpu": 0,
            "cpu_temp": None,
            "ram_percent": 0,
            "ram_used": 0,
            "ram_total": 0,
            "disk_percent": 0,
            "disk_used": 0,
            "disk_total": 0,
            "power": {
                "cpu_watts": None,
                "gpu_watts": None,
                "ram_watts": None,
                "storage_watts": None,
                "other_watts": None,
                "total_watts": None,
            },
        }


def collect_stats_loop():
    """Background thread: collect stats every 30 seconds into rolling history."""
    while True:
        try:
            stats = get_system_stats()
            history.append(
                {
                    "timestamp": time.strftime("%H:%M"),
                    "system_cpu": stats["cpu"],
                    "system_ram": stats["ram_used"],
                    "power_total": stats["power"]["total_watts"] if stats["power"] else 0,
                    "power_cpu": stats["power"]["cpu_watts"] if stats["power"] else 0,
                    "power_gpu": stats["power"]["gpu_watts"] if stats["power"] else 0,
                }
            )
        except Exception as e:
            logger.error("History collection loop error: %s", e)
        time.sleep(30)


class MonitoringHandler(BaseHTTPRequestHandler):
    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _respond(self, code, data):
        self.send_response(code)
        self._cors_headers()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self._respond(200, {"status": "ok"})
        elif self.path == "/stats":
            self._respond(200, get_system_stats())
        elif self.path == "/history":
            self._respond(200, list(history))
        else:
            self._respond(404, {"error": "Not found"})

    def log_message(self, format, *args):
        logger.info(args[0])


if __name__ == "__main__":
    # Start background stats collection
    t = threading.Thread(target=collect_stats_loop, daemon=True)
    t.start()
    logger.info("Stats collection thread started (30s interval)")

    server = HTTPServer(("0.0.0.0", PORT), MonitoringHandler)
    logger.info("Listening on 0.0.0.0:%d", PORT)
    server.serve_forever()
