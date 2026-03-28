"""
Standalone wiki server microservice for Meduseld.
Serves a static mirror of the Icarus wiki from /srv/wiki/icarus.
Runs independently of the main Flask app.

Listens on port 5005.
Serves static files from the wiki mirror directory.
Runs as its own systemd service: meduseld-wiki.service
"""

import json
import logging
import mimetypes
import os
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import unquote

logging.basicConfig(
    level=logging.INFO,
    format="[wiki] %(asctime)s %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("wiki_server")

PORT = 5005
WIKI_DIR = os.environ.get("WIKI_DIR", "/srv/wiki/icarus")
SYNC_TIMESTAMP_FILE = os.path.join(WIKI_DIR, ".last-sync")


def get_last_sync():
    """Read the last sync timestamp from the marker file."""
    try:
        if os.path.exists(SYNC_TIMESTAMP_FILE):
            with open(SYNC_TIMESTAMP_FILE, "r") as f:
                return f.read().strip()
    except Exception as e:
        logger.warning("Failed to read sync timestamp: %s", e)
    return None


class WikiHandler(BaseHTTPRequestHandler):
    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, format, *args):
        # Suppress default access logs for static files, keep errors
        pass

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        # Health check endpoint
        if self.path == "/health":
            page_count = 0
            try:
                for root, dirs, files in os.walk(WIKI_DIR):
                    page_count += sum(1 for f in files if f.endswith(".html"))
            except Exception:
                pass

            data = {
                "status": "ok",
                "wiki_dir": WIKI_DIR,
                "pages": page_count,
                "last_sync": get_last_sync(),
            }
            self.send_response(200)
            self._cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())
            return

        # Serve static wiki files
        path = unquote(self.path.split("?")[0])  # Strip query params

        # Default to index.html for directory requests
        if path == "/" or path == "":
            path = "/index.html"
        elif path.endswith("/"):
            path = path + "index.html"

        # If no extension, try appending .html (wiki pages often lack extensions)
        if "." not in os.path.basename(path):
            path = path + ".html"

        # Security: prevent path traversal
        safe_path = os.path.normpath(path.lstrip("/"))
        if safe_path.startswith("..") or os.path.isabs(safe_path):
            self.send_response(403)
            self.end_headers()
            return

        file_path = os.path.join(WIKI_DIR, safe_path)

        if not os.path.isfile(file_path):
            # Try without .html extension as fallback
            alt_path = file_path.rstrip(".html")
            if os.path.isfile(alt_path):
                file_path = alt_path
            else:
                self.send_response(404)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<h1>404 - Page Not Found</h1>")
                return

        # Determine content type
        content_type, _ = mimetypes.guess_type(file_path)
        if content_type is None:
            content_type = "application/octet-stream"

        try:
            with open(file_path, "rb") as f:
                content = f.read()

            self.send_response(200)
            self._cors_headers()
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            # Cache static assets aggressively, HTML pages less so
            if any(
                content_type.startswith(t) for t in ["image/", "text/css", "application/javascript"]
            ):
                self.send_header("Cache-Control", "public, max-age=86400")
            else:
                self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            logger.error("Error serving %s: %s", file_path, e)
            self.send_response(500)
            self.end_headers()


def main():
    if not os.path.isdir(WIKI_DIR):
        logger.warning("Wiki directory %s does not exist yet. Creating it.", WIKI_DIR)
        os.makedirs(WIKI_DIR, exist_ok=True)

    server = HTTPServer(("127.0.0.1", PORT), WikiHandler)
    logger.info("Wiki server listening on port %d, serving from %s", PORT, WIKI_DIR)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Wiki server shutting down")
        server.server_close()


if __name__ == "__main__":
    main()
