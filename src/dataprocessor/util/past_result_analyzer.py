"""
past_result_analyzer.py
-----------------------
Score spider.py's report-URL filter against a set of ALREADY-CONFIRMED annual
reports (e.g. temp_resources/Agg_pilot.xlsx), to answer two tuning questions:

  1. KEYWORDS -- what recall `spider.is_report_url` gets, measured against the
     legacy keyword list and other candidate filters as baselines.
  2. DEPTH    -- how deep in a site's URL tree the confirmed reports live, as
     evidence for `--depth` / MAX_DEPTH.

It also flags reports hosted on a DIFFERENT registered domain than the org's
site, since `spider.URLFilter.same_site` drops those regardless of keywords.

Two caveats on the evidence, both reported in the output:
  - Rows whose `pdf_url` is a bare filename ("cato 2004.pdf") were renamed by
    hand, so they are NOT evidence about real web URLs. Keyword recall is
    scored only on rows that carry a resolvable URL.
  - Directory depth is a PROXY for the crawler's link-hop depth, not the same
    number. A CMS upload path (/wp-content/uploads/2011/11/x.pdf) is 4 dirs
    deep but usually one hop off a listing page, so this over-states hop
    depth; treat it as an upper bound.

Usage:
    python3 past_result_analyzer.py <results.xlsx|results.csv> [--sheet NAME]
                                    [--url-col pdf_url] [--org-col org_id]

Example:
    python3 past_result_analyzer.py temp_resources/Agg_pilot.xlsx
"""

from __future__ import annotations

import argparse
import collections
import os
import re
import sys
from urllib.parse import urlparse, unquote

import pandas as pd

from dataprocessor.Spider_section.spider import (AR_TOKEN, REPORT_KEYWORDS,
                                                 is_report_url, registered_domain)

# Wayback wraps the original URL as /web/<timestamp>[id_|im_|if_]/<original>.
WAYBACK_RE = re.compile(r"/web/\d{8,17}(?:id_|im_|if_)?/(https?://.*)$", re.I)

# The keyword list spider.py carried before this analysis, kept frozen as the
# baseline the current filter is measured against. Its apparent recall is an
# artifact: bare "ar"/"rep" match almost any URL, so it filtered nothing.
LEGACY_KEYWORDS = (
    "report", "reports", "reprt", "reprts", "rprt", "rprts",
    "rept", "repts", "rpt", "rpts", "rep", "reps",
    "annual report", "annualreport", "annual-report", "annual_report",
    "annual.report", "annual%20report", "annual+report",
    "ann report", "annreport", "ann-report", "ann_report",
    "annual reprt", "annualreprt", "annual-reprt", "annual_reprt",
    "annual rprt", "annualrprt", "annual-rprt", "annual_rprt",
    "annual rept", "annualrept", "annual-rept", "annual_rept",
    "annual rpt", "annualrpt", "annual-rpt", "annual_rpt",
    "annrpt", "ann-rpt", "ann_rpt",
    "annual rep", "annualrep", "annual-rep", "annual_rep",
    "annrep", "ann-rep", "ann_rep", "ar", "ar2", "ar1",
)


def _fname(path: str) -> str:
    return path.rsplit("/", 1)[-1]


# Filters to score, as (label, predicate over (path, full_url)). `path` is the
# lowercased, unquoted URL path; `full` is the whole lowercased URL.
CANDIDATES: list[tuple[str, object]] = [
    ("legacy keywords, full URL",
     lambda p, full: any(k in full for k in LEGACY_KEYWORDS)),
    ("legacy keywords, path",
     lambda p, full: any(k in p for k in LEGACY_KEYWORDS)),
    ("annual|report, filename only",
     lambda p, full: "annual" in _fname(p) or "report" in _fname(p)),
    ("annual|report, path",
     lambda p, full: "annual" in p or "report" in p),
    ("annual|report|<ar>, filename only",
     lambda p, full: bool("annual" in _fname(p) or "report" in _fname(p)
                          or AR_TOKEN.search(_fname(p)))),
    ("spider.is_report_url (LIVE)",
     lambda p, full: is_report_url(full)),
]


# ── loading / normalization ────────────────────────────────────────────────
def load_results(path: str, sheet: str | None) -> pd.DataFrame:
    if path.lower().endswith((".xlsx", ".xlsm", ".xls")):
        xl = pd.ExcelFile(path)
        name = sheet or next((s for s in xl.sheet_names
                              if not xl.parse(s, nrows=1).empty), xl.sheet_names[0])
        return xl.parse(name)
    return pd.read_csv(path)


def unwrap(url: str) -> str:
    """Wayback URL -> the original URL it archived. '' if not a real URL."""
    u = str(url).strip()
    if not u.lower().startswith(("http://", "https://")):
        # Some rows drop the scheme off a Wayback link; others are bare
        # filenames a human assigned, which carry no URL evidence at all.
        if not u.lower().startswith("web.archive.org"):
            return ""
        u = "https://" + u
    m = WAYBACK_RE.search(u)
    return m.group(1) if m else u


# ── report sections ────────────────────────────────────────────────────────
def report_keywords(docs: pd.DataFrame) -> None:
    print("\n" + "=" * 72)
    print("KEYWORDS -- recall against confirmed reports")
    print("=" * 72)
    n = len(docs)
    for label, pred in CANDIDATES:
        hits = sum(bool(pred(p, f)) for p, f in zip(docs.path, docs.full))
        print(f"  {label:<38} {100 * hits / n:5.1f}%  ({hits}/{n})")

    print(f"\n  spider.REPORT_KEYWORDS is now {REPORT_KEYWORDS} + the AR_TOKEN regex.")
    print("\n  Per-keyword contribution of the LEGACY list (why it was dropped)")
    print("  (matched against the path; 'unique' = reports ONLY this key catches)")
    print(f"    {'keyword':<18} {'hits':>5} {'unique':>7}")
    paths = docs.path.tolist()
    for k in LEGACY_KEYWORDS:
        hits = sum(k in p for p in paths)
        if not hits:
            continue
        others = [o for o in LEGACY_KEYWORDS if o != k]
        uniq = sum(1 for p in paths if k in p and not any(o in p for o in others))
        print(f"    {k!r:<18} {hits:>5} {uniq:>7}")

    dead = [k for k in LEGACY_KEYWORDS if not any(k in p for p in paths)]
    print(f"\n  Legacy keywords with ZERO hits ({len(dead)} of {len(LEGACY_KEYWORDS)}): "
          f"{', '.join(repr(k) for k in dead) or 'none'}")

    live = CANDIDATES[-1][1]
    missed = docs[[not live(p, f) for p, f in zip(docs.path, docs.full)]]
    print(f"\n  Missed by the live filter ({len(missed)}):")
    for org, url in zip(missed.org, missed.orig):
        print(f"    [{org}] {url[:110]}")


def report_depth(docs: pd.DataFrame) -> None:
    print("\n" + "=" * 72)
    print("DEPTH -- how deep confirmed reports sit in the URL tree")
    print("=" * 72)
    print("  (directory depth, a PROXY for link-hop depth -- see module docstring)")
    n = len(docs)
    vc = docs.dir_depth.value_counts().sort_index()
    cum = 0
    for depth, count in vc.items():
        cum += count
        print(f"    depth {depth}: {count:>4} reports    cumulative {100 * cum / n:5.1f}%")

    print("\n  Deepest directories seen (these set the ceiling):")
    for p in docs.sort_values("dir_depth", ascending=False).path.head(5):
        print(f"    {p[:110]}")


def report_hosts(docs: pd.DataFrame) -> None:
    print("\n" + "=" * 72)
    print("SAME-SITE -- reports the crawler's same_site() check would drop")
    print("=" * 72)
    flagged = False
    for org, grp in docs.groupby("org"):
        doms = collections.Counter(registered_domain(h) for h in grp.host)
        if len(doms) > 1:
            flagged = True
            main, _ = doms.most_common(1)[0]
            off = {d: c for d, c in doms.items() if d != main}
            print(f"    {org:<22} main={main:<24} off-domain={off}")
    if not flagged:
        print("    None -- every report is on its org's own registered domain.")


# ── main ───────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[3],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", help="xlsx/csv of confirmed annual reports")
    ap.add_argument("--sheet", default=None, help="Worksheet name (xlsx only)")
    ap.add_argument("--url-col", default="pdf_url", help="Column holding the PDF URL")
    ap.add_argument("--org-col", default="org_id", help="Column holding the org id")
    args = ap.parse_args()

    if not os.path.exists(args.results):
        sys.exit(f"No such file: {args.results}")

    df = load_results(args.results, args.sheet)
    for col in (args.url_col, args.org_col):
        if col not in df.columns:
            sys.exit(f"Column {col!r} not in {list(df.columns)}")

    # One row per distinct document: the source grades each PDF once per LLM,
    # so the raw rows over-count every report by the number of models.
    docs = df.drop_duplicates(subset=[args.org_col, args.url_col]).copy()
    docs = docs.rename(columns={args.url_col: "pdf_url", args.org_col: "org"})
    docs["orig"] = docs.pdf_url.map(unwrap)

    total = len(docs)
    real = docs[docs.orig != ""].copy()
    print(f"Loaded {len(df)} rows -> {total} distinct documents")
    print(f"  {len(real)} carry a resolvable URL (scored below)")
    print(f"  {total - len(real)} are hand-renamed filenames -- no URL evidence, excluded")
    if real.empty:
        sys.exit("No resolvable URLs to analyze.")

    real["path"] = [unquote(urlparse(u).path).lower() for u in real.orig]
    real["full"] = real.orig.str.lower()
    real["host"] = [urlparse(u).netloc.lower() for u in real.orig]
    real["dir_depth"] = [len([s for s in p.split("/")[:-1] if s]) for p in real.path]

    report_keywords(real)
    report_depth(real)
    report_hosts(real)


if __name__ == "__main__":
    main()
