#!/bin/bash
# Scrape/mirror the Icarus wiki from wiki.gg for local hosting.
# Designed to be run by a systemd timer (weekly) or manually.

WIKI_URL="https://icarus.wiki.gg"
WIKI_DIR="/srv/wiki/icarus"
TEMP_DIR="/srv/wiki/.scrape-tmp"
LOG_FILE="/srv/wiki/scrape.log"
LOCK_FILE="/tmp/wiki-scrape.lock"

# Prevent concurrent runs
if [ -f "$LOCK_FILE" ]; then
    pid=""
    pid=$(cat "$LOCK_FILE" 2>/dev/null) || true
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') Scrape already in progress (PID $pid), skipping." >> "$LOG_FILE" 2>/dev/null
        exit 0
    fi
    rm -f "$LOCK_FILE"
fi
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG_FILE" 2>/dev/null
    echo "$1"
}

log "=== Starting wiki scrape from ${WIKI_URL} ==="

# Verify wget is available
if ! command -v wget > /dev/null 2>&1; then
    log "ERROR: wget is not installed. Install with: sudo apt install wget"
    exit 1
fi

# Create directories
mkdir -p "$WIKI_DIR" "$TEMP_DIR" 2>/dev/null
if [ ! -d "$TEMP_DIR" ]; then
    log "ERROR: Cannot create temp directory ${TEMP_DIR}"
    exit 1
fi

# Clean temp dir from any previous failed run
rm -rf "${TEMP_DIR:?}/"* 2>/dev/null || true

# Test connectivity first
log "Testing connectivity to ${WIKI_URL}..."
HTTP_CODE=$(curl -sI -o /dev/null -w '%{http_code}' --max-time 15 "${WIKI_URL}/wiki/Main_Page" 2>/dev/null) || true
log "HTTP response code: ${HTTP_CODE:-timeout/error}"

if [ -z "$HTTP_CODE" ] || [ "$HTTP_CODE" = "000" ]; then
    log "ERROR: Cannot reach ${WIKI_URL} at all (DNS failure, firewall, or site down)"
    rm -rf "$TEMP_DIR"
    exit 1
fi

if [ "$HTTP_CODE" = "403" ] || [ "$HTTP_CODE" = "429" ]; then
    log "ERROR: ${WIKI_URL} returned ${HTTP_CODE} — server IP may be blocked or rate-limited"
    rm -rf "$TEMP_DIR"
    exit 1
fi

# Use wget to mirror the wiki.
# wiki.gg wikis are MediaWiki-based with mostly server-rendered HTML.
# wget returns non-zero on 404s, robots blocks, etc. — that's expected.
log "Starting wget mirror..."
wget \
    --recursive \
    --level=inf \
    --convert-links \
    --adjust-extension \
    --page-requisites \
    --no-parent \
    --domains=icarus.wiki.gg \
    --reject-regex='(Special:|action=|oldid=|diff=|printable=|User:|User_talk:|Talk:|File:|Template:|Category:.*&|index\.php\?)' \
    --exclude-directories="/w/,/wiki/Special:,/wiki/User:,/wiki/Talk:,/wiki/User_talk:,/wiki/Template:" \
    --wait=1 \
    --random-wait \
    --limit-rate=500K \
    --timeout=30 \
    --tries=3 \
    --user-agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" \
    --directory-prefix="$TEMP_DIR" \
    --execute robots=off \
    --no-verbose \
    "${WIKI_URL}/wiki/Main_Page" \
    >> "$LOG_FILE" 2>&1 || true

# wget creates TEMP_DIR/icarus.wiki.gg/...
SCRAPED_DIR="${TEMP_DIR}/icarus.wiki.gg"
if [ ! -d "$SCRAPED_DIR" ]; then
    SCRAPED_DIR="$TEMP_DIR"
fi

HTML_COUNT=0
HTML_COUNT=$(find "$SCRAPED_DIR" -name "*.html" -o -name "*.htm" 2>/dev/null | wc -l) || true
log "Scraped ${HTML_COUNT} HTML pages"

# Log what's actually in the temp dir for debugging
log "Contents of temp dir:"
ls -la "$TEMP_DIR" >> "$LOG_FILE" 2>&1 || true
ls -la "$SCRAPED_DIR" >> "$LOG_FILE" 2>&1 || true

if [ "$HTML_COUNT" -lt 1 ]; then
    log "ERROR: No pages scraped. Check log above for wget output."
    rm -rf "$TEMP_DIR"
    exit 1
fi

# Post-process: strip edit buttons, login links, tracking scripts
log "Post-processing HTML files..."
find "$SCRAPED_DIR" -name "*.html" -print0 2>/dev/null | xargs -0 -r sed -i \
    -e 's|<script[^>]*google[^>]*>.*</script>||g' \
    -e 's|<script[^>]*analytics[^>]*>.*</script>||g' \
    -e 's|<script[^>]*tracking[^>]*>.*</script>||g' \
    -e '/<li[^>]*id="ca-edit"[^>]*>/,/<\/li>/d' \
    -e '/<li[^>]*id="ca-viewsource"[^>]*>/,/<\/li>/d' \
    -e '/<div[^>]*id="p-login"[^>]*>/,/<\/div>/d' \
    -e '/<div[^>]*class="mw-indicators"[^>]*>/,/<\/div>/d' \
    2>/dev/null || true

# Inject a local mirror banner using perl (sed struggles with complex HTML)
if command -v perl > /dev/null 2>&1; then
    find "$SCRAPED_DIR" -name "*.html" -print0 2>/dev/null | xargs -0 -r perl -pi -e '
        s{(<body[^>]*>)}{$1<style>.mw-mirror-banner{background:#1a1a2e;color:#e6c65c;text-align:center;padding:6px 12px;font-size:0.8rem;border-bottom:1px solid #e6c65c33;position:sticky;top:0;z-index:1000}.mw-mirror-banner a{color:#e6c65c}</style><div class="mw-mirror-banner">\x{1f4d6} Local mirror hosted by <a href="https://services.meduseld.io">Meduseld</a></div>}i;
    ' 2>/dev/null || true
fi

# Create a simple index.html redirect if one doesn't exist
if [ ! -f "${SCRAPED_DIR}/index.html" ]; then
    MAIN_PAGE=""
    MAIN_PAGE=$(find "$SCRAPED_DIR" -path "*/wiki/Main_Page*" -name "*.html" 2>/dev/null | head -1) || true
    if [ -n "$MAIN_PAGE" ]; then
        REL_PATH=$(realpath --relative-to="$SCRAPED_DIR" "$MAIN_PAGE" 2>/dev/null) || true
        if [ -n "$REL_PATH" ]; then
            cat > "${SCRAPED_DIR}/index.html" << INDEXEOF
<!DOCTYPE html>
<html><head><meta http-equiv="refresh" content="0;url=${REL_PATH}"><title>Icarus Wiki</title></head>
<body><a href="${REL_PATH}">Go to wiki</a></body></html>
INDEXEOF
        fi
    fi
fi

# Swap in the new mirror
BACKUP_DIR="/srv/wiki/.icarus-backup"
rm -rf "$BACKUP_DIR" 2>/dev/null || true
if [ -d "$WIKI_DIR" ]; then
    mv "$WIKI_DIR" "$BACKUP_DIR" 2>/dev/null || true
fi
mv "$SCRAPED_DIR" "$WIKI_DIR" 2>/dev/null || true
rm -rf "$BACKUP_DIR" 2>/dev/null || true

# Write sync timestamp
date -u '+%Y-%m-%dT%H:%M:%SZ' > "${WIKI_DIR}/.last-sync" 2>/dev/null || true

# Cleanup temp dir
rm -rf "$TEMP_DIR" 2>/dev/null || true

FINAL_COUNT=0
FINAL_COUNT=$(find "$WIKI_DIR" -name "*.html" 2>/dev/null | wc -l) || true
TOTAL_SIZE=$(du -sh "$WIKI_DIR" 2>/dev/null | cut -f1) || true
log "Wiki scrape complete: ${FINAL_COUNT} pages, ${TOTAL_SIZE:-unknown} total"
log "=== Scrape finished ==="
