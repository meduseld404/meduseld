#!/usr/bin/env python3
"""Fetch categories for existing wiki pages without re-scraping HTML.
Generates categories.json from the MediaWiki API based on files in WIKI_DIR."""
import json
import os
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path

API_URL = "https://icarus.wiki.gg/api.php"
USER_AGENT = "MeduseldWikiMirror/1.0 (https://meduseld.io)"
WIKI_DIR = Path(os.environ.get("WIKI_DIR", "/srv/wiki/icarus"))


def api_request(params):
    params["format"] = "json"
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep((attempt + 1) * 5)
                continue
            print(f"  API error: {e}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"  API error: {e}", file=sys.stderr)
            return None
    return None


# Get all page titles from existing files
page_files = [
    f.stem.replace("_", " ") for f in WIKI_DIR.glob("*.html") if f.name not in ("index.html",)
]
print(f"Found {len(page_files)} pages to categorize")

# Fetch categories in batches of 50 (API limit)
categories = {}
batch_size = 50
for i in range(0, len(page_files), batch_size):
    batch = page_files[i : i + batch_size]
    titles_str = "|".join(batch)
    data = api_request(
        {
            "action": "query",
            "titles": titles_str,
            "prop": "categories",
            "cllimit": "max",
        }
    )
    if data and "query" in data:
        for page_id, page_data in data["query"].get("pages", {}).items():
            title = page_data.get("title", "")
            cats = [
                c["title"].replace("Category:", "")
                for c in page_data.get("categories", [])
                if not c.get("hidden")
            ]
            if cats:
                categories[title] = cats

    done = min(i + batch_size, len(page_files))
    print(f"  {done}/{len(page_files)} pages processed")
    time.sleep(1)

out_path = WIKI_DIR / "categories.json"
out_path.write_text(json.dumps(categories, indent=2), encoding="utf-8")
print(f"Saved categories for {len(categories)} pages to {out_path}")
