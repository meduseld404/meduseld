#!/usr/bin/env python3
"""Rebuild the wiki index.html from existing scraped pages and categories."""
import json
import os
from collections import defaultdict
from pathlib import Path

WIKI_DIR = Path(os.environ.get("WIKI_DIR", "/srv/wiki/icarus"))

# Load categories if available
categories_file = WIKI_DIR / "categories.json"
page_categories = {}
if categories_file.exists():
    try:
        page_categories = json.loads(categories_file.read_text())
    except Exception:
        pass

# Collect all page files
page_files = sorted(
    [f.stem for f in WIKI_DIR.glob("*.html") if f.name != "index.html"],
    key=lambda x: x.lower(),
)

# Skip internal/meta categories
SKIP_CATS = {
    "Animated Images",
    "Blog posts",
    "Candidates for deletion",
    "Candidates for merging",
    "Candidates for moving",
    "Candidates for splitting",
    "CS1 errors",
    "CS1 errors: ISBN date",
    "CS1 location test",
}

# Build category -> pages mapping using first non-skipped category per page
cat_to_pages = defaultdict(list)
uncategorized = []

for name in page_files:
    title = name.replace("_", " ")
    cats = page_categories.get(title, [])
    # Filter out asset/meta categories
    cats = [c for c in cats if c not in SKIP_CATS and not c.startswith("Assets")]
    if cats:
        # Use the first category as the primary grouping
        cat_to_pages[cats[0]].append(name)
    else:
        uncategorized.append(name)

# Sort categories alphabetically
sorted_cats = sorted(cat_to_pages.keys(), key=lambda x: x.lower())

# Build category sections HTML
sections_html = ""
for cat in sorted_cats:
    pages = cat_to_pages[cat]
    links = "\n".join(
        f'<li class="wiki-link" data-name="{n.lower()}"><a href="{n}.html">{n.replace("_", " ")}</a></li>'
        for n in pages
    )
    sections_html += f"""
<div class="category-section" data-cat="{cat.lower()}">
  <h2 class="cat-header" onclick="this.parentElement.classList.toggle('collapsed')">{cat} <span class="cat-count">{len(pages)}</span></h2>
  <ul class="wiki-list">{links}</ul>
</div>"""

# Uncategorized section
if uncategorized:
    links = "\n".join(
        f'<li class="wiki-link" data-name="{n.lower()}"><a href="{n}.html">{n.replace("_", " ")}</a></li>'
        for n in uncategorized
    )
    sections_html += f"""
<div class="category-section" data-cat="uncategorized">
  <h2 class="cat-header" onclick="this.parentElement.classList.toggle('collapsed')">Other <span class="cat-count">{len(uncategorized)}</span></h2>
  <ul class="wiki-list">{links}</ul>
</div>"""

# Build all-pages flat list for search results
all_links = "\n".join(
    f'<li class="wiki-link" data-name="{n.lower()}"><a href="{n}.html">{n.replace("_", " ")}</a></li>'
    for n in page_files
)

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Icarus Wiki - Local Mirror</title>
<style>
body {{ background: #1a1a2e; color: #e0e0e0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; }}
.mirror-nav {{ background: #0f0f23; border-bottom: 2px solid #e6c65c33; padding: 8px 16px; display: flex; align-items: center; gap: 12px; position: sticky; top: 0; z-index: 1000; }}
.mirror-nav a {{ color: #e6c65c; text-decoration: none; font-size: 0.85rem; }}
.mirror-nav a:hover {{ text-decoration: underline; }}
.content {{ max-width: 960px; margin: 0 auto; padding: 20px; }}
h1 {{ color: #e6c65c; }}
.search-box {{ width: 100%; padding: 10px 14px; font-size: 1rem; border: 1px solid #e6c65c44; border-radius: 6px; background: #0f0f23; color: #e0e0e0; margin-bottom: 16px; box-sizing: border-box; }}
.search-box:focus {{ outline: none; border-color: #e6c65c; }}
.page-count {{ color: #e6c65c88; font-size: 0.85rem; margin-bottom: 12px; }}
.wiki-list {{ list-style: none; padding: 0; columns: 2; column-gap: 24px; }}
@media (max-width: 600px) {{ .wiki-list {{ columns: 1; }} }}
.wiki-link {{ padding: 3px 0; break-inside: avoid; }}
.wiki-link a {{ color: #e6c65c; text-decoration: none; font-size: 0.9rem; }}
.wiki-link a:hover {{ text-decoration: underline; }}
.wiki-link.hidden {{ display: none; }}
.category-section {{ margin-bottom: 8px; }}
.cat-header {{ color: #e6c65c; font-size: 1rem; cursor: pointer; user-select: none; padding: 6px 0; margin: 0; border-bottom: 1px solid #e6c65c22; }}
.cat-header:hover {{ color: #fff; }}
.cat-count {{ font-size: 0.75rem; color: #e6c65c66; font-weight: normal; margin-left: 6px; }}
.cat-header::before {{ content: "▾ "; font-size: 0.8rem; }}
.collapsed .cat-header::before {{ content: "▸ "; }}
.collapsed .wiki-list {{ display: none; }}
#search-results {{ display: none; }}
#search-results.active {{ display: block; }}
#categories.hidden {{ display: none; }}
</style>
</head>
<body>
<nav class="mirror-nav">
  <a href="https://services.meduseld.io">\u2190 Back to Services</a>
</nav>
<div class="content">
<h1>Icarus Wiki</h1>
<input type="text" class="search-box" placeholder="Search {len(page_files)} pages..." id="search" autocomplete="off">
<div class="page-count" id="count">{len(page_files)} pages in {len(sorted_cats)} categories</div>
<div id="categories">
{sections_html}
</div>
<div id="search-results">
<ul class="wiki-list" id="search-list">
{all_links}
</ul>
</div>
</div>
<script>
var searchBox = document.getElementById('search');
var categories = document.getElementById('categories');
var searchResults = document.getElementById('search-results');
var countEl = document.getElementById('count');
var totalPages = {len(page_files)};

searchBox.addEventListener('input', function() {{
  var q = this.value.toLowerCase().trim();
  if (!q) {{
    categories.classList.remove('hidden');
    searchResults.classList.remove('active');
    countEl.textContent = totalPages + ' pages in {len(sorted_cats)} categories';
    return;
  }}
  categories.classList.add('hidden');
  searchResults.classList.add('active');
  var items = searchResults.querySelectorAll('.wiki-link');
  var shown = 0;
  items.forEach(function(li) {{
    var match = li.getAttribute('data-name').indexOf(q) !== -1;
    li.classList.toggle('hidden', !match);
    if (match) shown++;
  }});
  countEl.textContent = shown + ' / ' + totalPages + ' pages';
}});
</script>
</body>
</html>"""

(WIKI_DIR / "index.html").write_text(html, encoding="utf-8")
print(f"Rebuilt index.html with {len(page_files)} pages in {len(sorted_cats)} categories")
