#!/usr/bin/env python3
"""
Scrape the Icarus wiki from wiki.gg using the MediaWiki API.

The API bypasses Cloudflare's bot detection (which blocks wget/curl on
regular page URLs). We enumerate all pages via the allpages API, fetch
rendered HTML via the parse API, and download images via imageinfo.

Each page is saved as a standalone HTML file with embedded CSS and a
navigation wrapper matching the Meduseld dark/gold theme.

Usage:
    python3 scrape_wiki.py
    WIKI_DIR=/srv/wiki/icarus python3 scrape_wiki.py
"""

import json
import logging
import os
import re
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

API_URL = "https://icarus.wiki.gg/api.php"
WIKI_BASE = "https://icarus.wiki.gg"
USER_AGENT = "MeduseldWikiMirror/1.0 (https://meduseld.io)"
CRAWL_DELAY = 1.0  # seconds between API requests (wiki.gg rate-limits aggressively)
MAX_PAGES = 2000
MAX_RETRIES = 3  # retries on 429/5xx with exponential backoff

WIKI_DIR = Path(os.environ.get("WIKI_DIR", "/srv/wiki/icarus"))
TEMP_DIR = WIKI_DIR.parent / ".scrape-tmp"
LOG_FILE = WIKI_DIR.parent / "scrape.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(LOG_FILE), mode="a"),
    ],
)
log = logging.getLogger("scrape")


def api_request(params):
    """Make a MediaWiki API request and return parsed JSON. Retries on 429/5xx."""
    params["format"] = "json"
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"

    for attempt in range(MAX_RETRIES + 1):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw)
                if not isinstance(data, dict):
                    log.error("API returned non-dict: %s", type(data))
                    return None
                return data
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < MAX_RETRIES:
                wait = (attempt + 1) * 5  # 5s, 10s, 15s backoff
                log.warning(
                    "API returned %d, retrying in %ds... (%s)", e.code, wait, params.get("page", "")
                )
                time.sleep(wait)
                continue
            log.error("API request failed: %s (url=%s)", e, url)
            return None
        except json.JSONDecodeError as e:
            log.error("API returned non-JSON (likely Cloudflare challenge): %s", e)
            return None
        except Exception as e:
            log.error("API request failed: %s (url=%s)", e, url)
            return None
    return None


def get_all_page_titles():
    """Enumerate all wiki page titles via the allpages API."""
    titles = []
    params = {"action": "query", "list": "allpages", "aplimit": "max"}
    while True:
        data = api_request(params)
        if not data:
            break
        pages = data.get("query", {}).get("allpages", [])
        for p in pages:
            titles.append(p["title"])
        cont = data.get("continue", {})
        if "apcontinue" in cont:
            params["apcontinue"] = cont["apcontinue"]
        else:
            break
        time.sleep(CRAWL_DELAY)
        if len(titles) >= MAX_PAGES:
            break
    return titles


def fetch_page_html(title):
    """Fetch rendered HTML for a wiki page via the parse API."""
    data = api_request(
        {
            "action": "parse",
            "page": title,
            "prop": "text|categories|displaytitle",
        }
    )
    if not data or "parse" not in data:
        return None, None
    parse = data["parse"]
    html = parse.get("text", {}).get("*", "")
    display_title = parse.get("displaytitle", title)
    return html, display_title


def fetch_wiki_css():
    """Fetch the wiki's main CSS for styling."""
    params = {
        "action": "parse",
        "page": "Main_Page",
        "prop": "headhtml",
    }
    data = api_request(params)
    if not data or "parse" not in data:
        return ""
    head = data["parse"].get("headhtml", {}).get("*", "")
    # Extract stylesheet links
    css_links = re.findall(r'<link[^>]+rel="stylesheet"[^>]+href="([^"]+)"', head)
    css_content = []
    for link in css_links:
        url = link if link.startswith("http") else f"{WIKI_BASE}{link}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=15) as resp:
                css_content.append(resp.read().decode("utf-8", errors="replace"))
        except Exception as e:
            log.warning("Failed to fetch CSS %s: %s", url, e)
        time.sleep(CRAWL_DELAY)
    return "\n".join(css_content)


def title_to_filename(title):
    """Convert a wiki page title to a safe filename."""
    safe = title.replace(" ", "_")
    safe = re.sub(r'[<>:"/\\|?*]', "_", safe)
    return safe + ".html"


def build_page_html(title, display_title, body_html, css, all_titles):
    """Wrap parsed wiki HTML in a full standalone page."""

    # Rewrite wiki links to local relative paths
    def rewrite_link(match):
        href = match.group(1)
        # /wiki/Page_Name -> Page_Name.html
        if href.startswith("/wiki/"):
            page = href[6:].split("#")[0].split("?")[0]
            page = urllib.parse.unquote(page)
            fragment = ""
            if "#" in match.group(1):
                fragment = "#" + match.group(1).split("#")[1]
            return f'href="{urllib.parse.quote(page.replace(" ", "_"))}.html{fragment}"'
        return match.group(0)

    body_html = re.sub(r'href="(/wiki/[^"]*)"', rewrite_link, body_html)
    # Remove edit links
    body_html = re.sub(
        r'<span class="mw-editsection">.*?</span></span>', "", body_html, flags=re.DOTALL
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{display_title} - Icarus Wiki</title>
<style>
{css}
body {{ background: #1a1a2e; color: #e0e0e0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; }}
.mirror-nav {{ background: #0f0f23; border-bottom: 2px solid #e6c65c33; padding: 8px 16px; display: flex; align-items: center; gap: 12px; position: sticky; top: 0; z-index: 1000; }}
.mirror-nav a {{ color: #e6c65c; text-decoration: none; font-size: 0.85rem; }}
.mirror-nav a:hover {{ text-decoration: underline; }}
.mirror-nav .brand {{ font-weight: 600; }}
.mirror-nav .sep {{ color: #e6c65c44; }}
.mirror-nav .back {{ margin-left: auto; }}
.mw-parser-output {{ max-width: 960px; margin: 20px auto; padding: 0 20px; }}
.mw-parser-output a {{ color: #e6c65c; }}
.mw-parser-output a:visited {{ color: #c4a84a; }}
.mw-parser-output img {{ max-width: 100%; height: auto; }}
.mw-parser-output table {{ border-collapse: collapse; }}
.mw-parser-output th, .mw-parser-output td {{ border: 1px solid #333; padding: 6px 10px; }}
.mw-parser-output th {{ background: #252540; }}
.mw-parser-output h1, .mw-parser-output h2, .mw-parser-output h3 {{ color: #e6c65c; border-bottom: 1px solid #e6c65c33; padding-bottom: 4px; }}
.infobox, .wikitable {{ background: #1e1e38; }}
</style>
</head>
<body>
<nav class="mirror-nav">
  <a href="Main_Page.html" class="brand">\U0001f4d6 Icarus Wiki</a>
  <span class="sep">|</span>
  <a href="Main_Page.html">Main Page</a>
  <a href="https://services.meduseld.io" class="back">\u2190 Back to Services</a>
</nav>
<div class="mw-parser-output">
<h1>{display_title}</h1>
{body_html}
</div>
</body>
</html>"""


def download_images(html_dir):
    """Download images referenced in the HTML files."""
    img_urls = set()
    for html_file in html_dir.glob("*.html"):
        content = html_file.read_text(errors="replace")
        # Find image src attributes pointing to wiki.gg
        for match in re.finditer(
            r'src="(https://[^"]*wiki\.gg[^"]*\.(png|jpg|jpeg|gif|svg|webp|ico))"', content, re.I
        ):
            img_urls.add(match.group(1))
        # Also find relative image paths
        for match in re.finditer(r'src="(/images/[^"]*)"', content):
            img_urls.add(f"{WIKI_BASE}{match.group(1)}")

    if not img_urls:
        return 0

    img_dir = html_dir / "images"
    img_dir.mkdir(exist_ok=True)
    count = 0

    for url in img_urls:
        try:
            # Create a safe local filename from the URL
            parsed = urllib.parse.urlparse(url)
            filename = Path(parsed.path).name
            if not filename:
                continue
            local_path = img_dir / filename
            if local_path.exists():
                continue

            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=15) as resp:
                local_path.write_bytes(resp.read())
            count += 1
            time.sleep(CRAWL_DELAY)
        except Exception as e:
            log.warning("Failed to download image %s: %s", url, e)

    # Rewrite image src in HTML files to point to local copies
    for html_file in html_dir.glob("*.html"):
        content = html_file.read_text(errors="replace")
        for url in img_urls:
            parsed = urllib.parse.urlparse(url)
            filename = Path(parsed.path).name
            if filename:
                content = content.replace(url, f"images/{filename}")
                # Also rewrite the relative /images/ path
                rel_path = parsed.path
                content = content.replace(f'src="{rel_path}"', f'src="images/{filename}"')
        html_file.write_text(content)

    return count


def main():
    log.info("=== Starting wiki scrape via MediaWiki API ===")

    # Test API connectivity
    test = api_request({"action": "query", "meta": "siteinfo", "siprop": "general"})
    if not test:
        log.error("Cannot reach wiki API at %s", API_URL)
        return 1

    site_name = test.get("query", {}).get("general", {}).get("sitename", "Unknown")
    log.info("Connected to: %s", site_name)

    # Enumerate all pages
    log.info("Enumerating wiki pages...")
    titles = get_all_page_titles()
    log.info("Found %d pages", len(titles))

    if not titles:
        log.error("No pages found")
        return 1

    # Ensure Main Page is included (it's often missing from allpages)
    # and put it first so it's fetched before any rate-limiting kicks in
    if "Main Page" in titles:
        titles.remove("Main Page")
    titles.insert(0, "Main Page")

    # Prepare temp directory
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    pages_dir = TEMP_DIR / "pages"
    if pages_dir.exists():
        import shutil

        shutil.rmtree(pages_dir)
    pages_dir.mkdir()

    # Fetch wiki CSS (once)
    log.info("Fetching wiki CSS...")
    css = fetch_wiki_css()

    # Fetch each page
    log.info("Fetching %d pages...", len(titles))
    fetched = 0
    failed = []
    for i, title in enumerate(titles):
        html, display_title = fetch_page_html(title)
        if html is None:
            log.warning("Failed to fetch: %s", title)
            failed.append(title)
            continue

        filename = title_to_filename(title)
        full_html = build_page_html(title, display_title, html, css, titles)
        out_path = pages_dir / filename
        out_path.write_text(full_html, encoding="utf-8")
        fetched += 1

        if title == "Main Page":
            log.info("Main Page saved as %s (%d bytes)", filename, out_path.stat().st_size)

        if (i + 1) % 50 == 0:
            log.info("Progress: %d/%d pages fetched", i + 1, len(titles))

        time.sleep(CRAWL_DELAY)

    log.info("Fetched %d pages (%d failed)", fetched, len(failed))
    if failed:
        log.info("Failed pages: %s", ", ".join(failed[:20]))

    if fetched < 1:
        log.error("No pages fetched successfully")
        return 1

    # Create index.html redirect — point to Main_Page.html if it exists,
    # otherwise the first available page
    index_path = pages_dir / "index.html"
    redirect_target = "Main_Page.html"
    if not (pages_dir / redirect_target).exists():
        # Find any HTML file to redirect to
        for f in sorted(pages_dir.glob("*.html")):
            if f.name != "index.html":
                redirect_target = f.name
                log.warning("Main_Page.html not found, redirecting to %s", redirect_target)
                break
    index_path.write_text(
        f'<!DOCTYPE html>\n<html><head><meta http-equiv="refresh" content="0;url={redirect_target}">'
        f"<title>Icarus Wiki</title></head><body>"
        f'<a href="{redirect_target}">Go to wiki</a></body></html>\n'
    )

    # Download images
    log.info("Downloading images...")
    img_count = download_images(pages_dir)
    log.info("Downloaded %d images", img_count)

    # Swap in the new mirror
    backup_dir = WIKI_DIR.parent / ".icarus-backup"
    if backup_dir.exists():
        import shutil

        shutil.rmtree(backup_dir)
    if WIKI_DIR.exists():
        WIKI_DIR.rename(backup_dir)
    pages_dir.rename(WIKI_DIR)
    if backup_dir.exists():
        import shutil

        shutil.rmtree(backup_dir)

    # Write sync timestamp
    (WIKI_DIR / ".last-sync").write_text(
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") + "\n"
    )

    # Cleanup
    if TEMP_DIR.exists():
        import shutil

        shutil.rmtree(TEMP_DIR)

    total_html = sum(1 for _ in WIKI_DIR.glob("*.html"))
    total_images = (
        sum(1 for _ in (WIKI_DIR / "images").glob("*")) if (WIKI_DIR / "images").exists() else 0
    )
    log.info("Wiki scrape complete: %d pages, %d images", total_html, total_images)
    log.info("=== Scrape finished ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
