"""
prep_seed_urls.py
-----------------
Turn a crawl manifest (e.g. combined.csv) into a seed CSV for spider.py's
`--seed-urls` mode, so a follow-up crawl can explore DEEPER from where the last
run found reports -- without re-crawling homepages (depths 0-3).

For every manifest row:
  - unwrap the Wayback prefix (.../<ts>id_/<original>) back to the real URL,
  - reduce it to the directory that CONTAINS the report (drop the filename),
  - record (org, domain, directory-url).

Duplicate directories are collapsed, so N reports in one folder seed it once.

Note: seeds point at the LIVE original site (the Wayback wrapper is dropped,
since a live crawl can't follow archive.org's rewritten links). Directories on
sites that have since changed will 404 and are skipped by the crawler -- this
mainly extends coverage for orgs whose sites are still live.

Usage:
    python3 prep_seed_urls.py <manifest.csv> [seeds.csv]

Defaults output to `seeds.csv` next to the input.
"""

import os
import sys
from urllib.parse import urlparse, urlunparse

import pandas as pd

WAYBACK_MARKER = "id_/"
URL_COLUMN = "url"
ORG_COLUMN = "org"


def unwrap(url: str) -> str:
    """Return the original URL embedded after the Wayback `id_/` marker."""
    i = url.find(WAYBACK_MARKER)
    return url[i + len(WAYBACK_MARKER):] if i != -1 else url


def registered_domain(host: str) -> str:
    """Naive eTLD+1 (last two labels) -- mirrors spider.registered_domain."""
    host = (host or "").lower().split(":")[0]
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def directory_url(url: str) -> str | None:
    """scheme://host + path up to (and including) the last '/'.

    Returns None if the URL has no usable scheme/host.
    """
    p = urlparse(url)
    if not p.scheme or not p.netloc:
        return None
    path = p.path
    dir_path = path if path.endswith("/") else path.rsplit("/", 1)[0] + "/"
    return urlunparse((p.scheme, p.netloc, dir_path, "", "", ""))


# ── resolve paths ───────────────────────────────────────────────────────────
if len(sys.argv) < 2:
    print("Usage:  python3 prep_seed_urls.py <manifest.csv> [seeds.csv]")
    sys.exit(1)

INPUT_PATH = sys.argv[1]
if not os.path.isfile(INPUT_PATH):
    print(f"Error: file not found -> {INPUT_PATH}")
    sys.exit(1)

folder = os.path.dirname(INPUT_PATH) or "."
OUTPUT_PATH = sys.argv[2] if len(sys.argv) > 2 else os.path.join(folder, "seeds.csv")


# ── build seeds ─────────────────────────────────────────────────────────────
df = pd.read_csv(INPUT_PATH, dtype=str).fillna("")
print(f"Loaded : {INPUT_PATH}  ({len(df)} rows)")

if URL_COLUMN not in df.columns:
    print(f"Error: no '{URL_COLUMN}' column. Columns are: {list(df.columns)}")
    sys.exit(1)

seeds: list[dict] = []
for _, row in df.iterrows():
    raw = (row.get(URL_COLUMN) or "").strip()
    if not raw:
        continue
    original = unwrap(raw)
    seed = directory_url(original)
    if not seed:
        continue
    netloc = urlparse(original).netloc
    domain = registered_domain(netloc)
    org = (row.get(ORG_COLUMN) or "").strip() or domain
    seeds.append({"org": org, "domain": domain, "url": seed})

out = pd.DataFrame(seeds, columns=["org", "domain", "url"])
before = len(out)
out = out.drop_duplicates(subset=["org", "url"]).reset_index(drop=True)

out.to_csv(OUTPUT_PATH, index=False)
print(f"Collapsed: {before} -> {len(out)} unique seed directories")
print(f"Saved    : {OUTPUT_PATH}")
