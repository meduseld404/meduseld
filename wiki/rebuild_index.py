#!/usr/bin/env python3
"""Rebuild the wiki index.html from existing scraped pages."""
import os
from pathlib import Path

WIKI_DIR = Path(os.environ.get("WIKI_DIR", "/srv/wiki/icarus"))

page_files = sorted(
    [f.stem for f in WIKI_DIR.glob("*.html") if f.name != "index.html"],
    key=lambda x: x.lower(),
)

page_links = "\n".join(
    f'<li class="wiki-link" data-name="{name.lower()}"><a href="{name}.html">{name.replace("_", " ")}</a></li>'
    for name in page_files
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
.mirror-nav .brand {{ font-weight: 600; }}
.mirror-nav .back {{ margin-left: auto; }}
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
.featured {{ background: #1e1e38; border: 1px solid #e6c65c33; border-radius: 8px; padding: 16px; margin-bottom: 20px; }}
.featured a {{ color: #e6c65c; text-decoration: none; font-size: 1.1rem; font-weight: 600; }}
</style>
</head>
<body>
<nav class="mirror-nav">
  <span class="brand">\U0001f4d6 Icarus Wiki</span>
  <a href="https://services.meduseld.io" class="back">\u2190 Back to Services</a>
</nav>
<div class="content">
<h1>Icarus Wiki</h1>
<div class="featured">
  <a href="Main_Page.html">\U0001f3e0 Open Main Page</a>
</div>
<input type="text" class="search-box" placeholder="Search pages..." id="search" autocomplete="off">
<div class="page-count" id="count">{len(page_files)} pages</div>
<ul class="wiki-list" id="pages">
{page_links}
</ul>
</div>
<script>
document.getElementById('search').addEventListener('input', function() {{
  var q = this.value.toLowerCase();
  var items = document.querySelectorAll('.wiki-link');
  var shown = 0;
  items.forEach(function(li) {{
    var match = !q || li.getAttribute('data-name').indexOf(q) !== -1;
    li.classList.toggle('hidden', !match);
    if (match) shown++;
  }});
  document.getElementById('count').textContent = shown + ' / {len(page_files)} pages';
}});
</script>
</body>
</html>"""

(WIKI_DIR / "index.html").write_text(html, encoding="utf-8")
print(f"Rebuilt index.html with {len(page_files)} pages")
