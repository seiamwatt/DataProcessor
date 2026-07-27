"""
cut_link_depth.py
-----------------
Normalize the `url` column of a manifest CSV to a fixed path depth.

For every row:
  - Unwrap the Wayback prefix. The spider stores archived links as
    `https://web.archive.org/web/<ts>id_/<original>` (see spider.py), so the
    part after the `id_/` marker is the real URL whose path we care about.
  - Count the path segments of that original URL (see FILENAME_COUNTS below).
  - If the URL has FEWER than TARGET_DEPTH segments  -> the row is DELETED.
  - Otherwise the URL is CUT to the first TARGET_DEPTH segments
    (scheme://host/seg1/seg2/seg3) and written back to the `url` column.

Depth here is URL path depth, NOT the spider's BFS crawl depth (which isn't
stored in the manifest).

Usage:
    python3 cut_link_depth.py <input.csv> [output.csv]

Defaults the output to `combined_depth3.csv` next to the input.
"""

import os
import sys
from urllib.parse import urlparse, urlunparse

import pandas as pd

# ── knobs ──────────────────────────────────────────────────────────────────
TARGET_DEPTH = 3          # keep this many leading path segments
URL_COLUMN = "url"        # column holding the links
WAYBACK_MARKER = "id_/"   # separates the Wayback wrapper from the original URL

# True  -> the filename counts as a segment: /a/b/report.pdf == depth 3
# False -> count directories only:           /a/b/c/report.pdf == depth 3
FILENAME_COUNTS = True

# True  -> keep the Wayback wrapper on rows that survive (only meaningful for
#          rows already at exactly TARGET_DEPTH, since a truncated Wayback path
#          no longer resolves). False -> emit the plain, cut original URL.
KEEP_WAYBACK = False


def unwrap(url: str) -> str:
    """Return the original URL embedded after the Wayback `id_/` marker.

    Falls back to the input unchanged when there is no wrapper.
    """
    idx = url.rfind(WAYBACK_MARKER)
    return url[idx + len(WAYBACK_MARKER):] if idx != -1 else url


def path_segments(url: str) -> list[str]:
    """Non-empty path segments of a URL, e.g. /a/b/c.pdf -> ['a', 'b', 'c.pdf']."""
    return [s for s in urlparse(url).path.split("/") if s]


def depth_of(segments: list[str]) -> int:
    """Path depth under the FILENAME_COUNTS rule."""
    if FILENAME_COUNTS:
        return len(segments)
    # Directories only: drop the trailing filename segment (has a dot / extension).
    dirs = segments[:-1] if segments and "." in segments[-1] else segments
    return len(dirs)


def cut(url: str) -> str | None:
    """Cut `url` to TARGET_DEPTH path segments, or None if it's too shallow."""
    original = unwrap(url)
    segments = path_segments(original)
    if depth_of(segments) < TARGET_DEPTH:
        return None

    p = urlparse(original)
    new_path = "/" + "/".join(segments[:TARGET_DEPTH])
    cut_original = urlunparse((p.scheme, p.netloc, new_path, "", "", ""))

    if KEEP_WAYBACK and original is not url:
        # Re-wrap with the surviving rows' original prefix (up to and incl. id_/).
        prefix = url[: url.rfind(WAYBACK_MARKER) + len(WAYBACK_MARKER)]
        return prefix + cut_original
    return cut_original


# ── resolve paths ───────────────────────────────────────────────────────────
if len(sys.argv) < 2:
    print("Usage:  python3 cut_link_depth.py <input.csv> [output.csv]")
    sys.exit(1)

INPUT_PATH = sys.argv[1]
if not os.path.isfile(INPUT_PATH):
    print(f"Error: file not found -> {INPUT_PATH}")
    sys.exit(1)

folder = os.path.dirname(INPUT_PATH) or "."
OUTPUT_PATH = sys.argv[2] if len(sys.argv) > 2 else os.path.join(folder, "combined_depth3.csv")


# ── run ─────────────────────────────────────────────────────────────────────
df = pd.read_csv(INPUT_PATH, dtype=str)
print(f"Loaded : {INPUT_PATH}  ({len(df)} rows)")

if URL_COLUMN not in df.columns:
    print(f"Error: no '{URL_COLUMN}' column. Columns are: {list(df.columns)}")
    sys.exit(1)

df[URL_COLUMN] = df[URL_COLUMN].apply(lambda u: cut(u) if isinstance(u, str) else None)

before = len(df)
df = df[df[URL_COLUMN].notna()].reset_index(drop=True)
dropped = before - len(df)

df.to_csv(OUTPUT_PATH, index=False)
print(f"Dropped: {dropped} rows below depth {TARGET_DEPTH}")
print(f"Kept   : {len(df)} rows (url cut to depth {TARGET_DEPTH})")
print(f"Saved  : {OUTPUT_PATH}")
