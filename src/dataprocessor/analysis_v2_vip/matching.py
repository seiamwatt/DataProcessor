# match pdf files back to the source csv (main dataset, e.g. ar_all.csv)
# file names follow DataProcessor_toolkit/util_scripts/dowloading_reports.py:
#   <ein>_<year>_<name>_<hash>.pdf, hash from the url
# keep target_name in sync with that script, or folder pdfs stop matching
import hashlib
import os
import re
from urllib.parse import unquote, urlparse

import pandas as pd

WAYBACK_RE = re.compile(
    r"^(https?://web\.archive\.org/web/)(\d{1,14})(?:[a-z]{2}_)?/(.+)$", re.I)

# built here rather than copied from the source csv (same as merge_analysis.py)
DERIVED_COLUMNS = ["Document_ID", "Document_type", "Source_URL"]


# NOTE: download file names

def original_url(url):
    m = WAYBACK_RE.match(url)
    return m.group(3) if m else url


def target_name(row):
    ein = re.sub(r"\D", "", row.get("ein") or "") or "noein"
    year = re.sub(r"[^0-9A-Za-z]", "", (row.get("year") or "").split(".")[0]) or "undated"
    path = urlparse(original_url(row["url"])).path
    stem = os.path.splitext(unquote(os.path.basename(path.rstrip("/"))))[0]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")[:80] or "report"
    digest = hashlib.sha1(row["url"].encode("utf-8")).hexdigest()[:8]
    return f"{ein}_{year}_{stem}_{digest}.pdf"


# NOTE: matching

def name_key(name):
    # compare without the extension: a saved webpage <ein>_<year>_<name>_<hash>.html matches too
    return os.path.splitext(str(name))[0]


def build_name_index(source):
    # download file name (no extension) -> source row positions that produce it
    index = {}
    for pos, row in enumerate(source.to_dict("records")):
        if row.get("url"):
            index.setdefault(name_key(target_name(row)), []).append(pos)
    return index


def match_folder(names, source):
    """For each pdf name return (source row position or None, how it matched).

    how: "file name"        rebuilt download name matches exactly one source row
         "file column"      the source csv has a `file` column naming the pdf
         "ambiguous"        several source rows produce the same name
         "unmatched"        nothing found
    """
    # several rows with the same url are one report, keep the first
    source = source.drop_duplicates("url")
    positions = list(source.index)
    index = build_name_index(source)

    by_file = {}
    if "file" in source.columns:
        for pos, value in zip(positions, source["file"]):
            if value:
                by_file.setdefault(value, []).append(pos)

    results = []
    for name in names:
        hits = index.get(name_key(name), [])
        if len(hits) == 1:
            results.append((positions[hits[0]], "file name"))
        elif len(hits) > 1:
            results.append((None, "ambiguous"))
        elif len(by_file.get(name, [])) == 1:
            results.append((by_file[name][0], "file column"))
        else:
            results.append((None, "unmatched"))
    return results


def attach_source(folder_df, source, name_col):
    """Left join the source csv onto the pdf list. Returns (joined df, how Series).

    Unmatched pdfs keep empty source columns so they are still analysed.
    """
    matches = match_folder(folder_df[name_col], source)
    how = pd.Series([m[1] for m in matches], index=folder_df.index)
    meta = pd.DataFrame(
        [source.loc[pos].to_dict() if pos is not None else {} for pos, _ in matches],
        columns=source.columns,
        index=folder_df.index,
    ).fillna("")
    return pd.concat([meta, folder_df], axis=1), how


def document_fields(row):
    # empty for an unmatched pdf, it has no source url
    url = row.get("url") or ""
    if not url:
        return {col: "" for col in DERIVED_COLUMNS}
    return {
        "Document_ID": f"{row.get('ein') or ''}_{row.get('year') or ''}",
        "Document_type": "Annual report",
        "Source_URL": url,
    }
