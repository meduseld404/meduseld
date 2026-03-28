#!/bin/bash
# Scrape/mirror the Icarus wiki from wiki.gg for local hosting.
# Designed to be run by a systemd timer (weekly) or manually.
#
# Usage: ./scrape_wiki.sh [--force]
#   --force: re-scrape even if a recent sync exists

set -euo pipefail

WIKI_URL="https://icarus.wiki.gg"
WIKI_DIR="/srv/wiki/icarus"
TEMP_DIR="/srv/wiki/.scrape-tmp"
TIMESTAMP_FILE="${WIKI_DIR}/.last-sync"
LOG_FILE="/srv/wiki/scrape.log"
LOCK_FILE="/tmp/wiki-scrape.lock"

# Prevent concurrent runs
if [ -f "$LOCK_FILE" ]; then
    pid=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') Scrape already in progress (PID $pid), skipping." | tee -a "$LOG_FILE"
        exit 0
    fi
    rm -f "$LOCK_FILE"
fi
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a "$LOG_FILE"
}

log "Starting wiki scrape from ${WIKI_URL}"

# Create directories
mkdir -p "$WIKI_DIR" "$TEMP_DIR"

# Check robots.txt first
log "Checking robots.txt..."
if ! curl -sf "${WIKI_URL}/robots.txt" > /dev/null 2>&1; then
    log "WARNING: Could not fetch robots.txt, proceeding anyway"
fi

# Use wget to mirror the wiki
# --mirror: recursive, timestamping, infinite depth
# --convert-links: rewrite links for local browsing
# --adjust-extension: add .html to pages
# --page-requisites: download CSS, JS, images
# --no-parent: don't ascend above the wiki path
# --reject: skip unnecessary files
# --domains: stay on wiki.gg domain
# --wait/--random-wait: be polite to the server
log "Starting wget mirror..."
wget \
    --mirror \
    --convert-links \
    --adjust-extension \
    --page-requisites \
    --no-parent \
    --domains=icarus.wiki.gg \
    --reject="*.action,*Special:*,*action=*,*oldid=*,*diff=*,*printable=*" \
    --exclude-directories="/w/,/wiki/Special:,/wiki/User:,/wiki/Talk:,/wiki/User_talk:" \
    --wait=0.5 \
    --random-wait \
    --limit-rate=1M \
    --timeout=30 \
    --tries=3 \
    --user-agent="Mozilla/5.0 (compatible; MeduseldWikiMirror/1.0; +https://meduseld.io)" \
    --directory-prefix="$TEMP_DIR" \
    --no-host-directories \
    --cut-dirs=0 \
    --execute robots=off \
    "$WIKI_URL/wiki/Main_Page" \
    2>&1 | tail -20 | tee -a "$LOG_FILE" || true

# Check if we got content
SCRAPED_DIR="${TEMP_DIR}/icarus.wiki.gg"
if [ ! -d "$SCRAPED_DIR" ]; then
    SCRAPED_DIR="$TEMP_DIR"
fi

HTML_COUNT=$(find "$SCRAPED_DIR" -name "*.html" 2>/dev/null | wc -l)
log "Scraped ${HTML_COUNT} HTML pages"

if [ "$HTML_COUNT" -lt 5 ]; then
    log "ERROR: Too few pages scraped (${HTML_COUNT}), aborting to preserve existing mirror"
    rm -rf "$TEMP_DIR"
    exit 1
fi

# Post-process: strip edit buttons, login links, tracking scripts
log "Post-processing HTML files..."
find "$SCRAPED_DIR" -name "*.html" -exec sed -i \
    -e 's|<script[^>]*google[^>]*>.*</script>||g' \
    -e 's|<script[^>]*analytics[^>]*>.*</script>||g' \
    -e 's|<script[^>]*tracking[^>]*>.*</script>||g' \
    -e '/<li[^>]*id="ca-edit"[^>]*>/,/<\/li>/d' \
    -e '/<li[^>]*id="ca-viewsource"[^>]*>/,/<\/li>/d' \
    -e '/<div[^>]*id="p-login"[^>]*>/,/<\/div>/d' \
    -e '/<div[^>]*class="mw-indicators"[^>]*>/,/<\/div>/d' \
    {} +

# Inject a banner indicating this is a local mirror
BANNER_CSS='<style>.meduseld-mirror-banner{background:#1a1a2e;color:#e6c65c;text-align:center;padding:6px 12px;font-size:0.8rem;border-bottom:1px solid #e6c65c33;position:sticky;top:0;z-index:1000;}.meduseld-mirror-banner a{color:#e6c65c;}</style>'
BANNER_HTML='<div class="meduseld-mirror-banner">📖 Local mirror hosted by <a href="https://services.meduseld.io">Meduseld</a> · <span id="mirror-sync-date"></span></div>'

find "$SCRAPED_DIR" -name "*.html" -exec sed -i \
    -e "s|</head>|${BANNER_CSS}</head>|" \
    -e "s|<body[^>]*>|&${BANNER_HTML}|" \
    {} +

# Swap in the new mirror (atomic-ish)
if [ -d "$WIKI_DIR" ] && [ "$HTML_COUNT" -gt 0 ]; then
    BACKUP_DIR="/srv/wiki/.icarus-backup"
    rm -rf "$BACKUP_DIR"
    mv "$WIKI_DIR" "$BACKUP_DIR" 2>/dev/null || true
    mv "$SCRAPED_DIR" "$WIKI_DIR"
    rm -rf "$BACKUP_DIR"
else
    mv "$SCRAPED_DIR" "$WIKI_DIR"
fi

# Write sync timestamp
date -u '+%Y-%m-%dT%H:%M:%SZ' > "${WIKI_DIR}/.last-sync"

# Cleanup
rm -rf "$TEMP_DIR"

FINAL_COUNT=$(find "$WIKI_DIR" -name "*.html" 2>/dev/null | wc -l)
TOTAL_SIZE=$(du -sh "$WIKI_DIR" 2>/dev/null | cut -f1)
log "Wiki scrape complete: ${FINAL_COUNT} pages, ${TOTAL_SIZE} total"
