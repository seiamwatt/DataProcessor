#!/usr/bin/env python3
"""
nonprofit_reports.py
====================

Collect nonprofit "annual reports" from three complementary sources, going back
~20 years, and drop everything into a manifest per organization.

  1. ProPublica Nonprofit Explorer  -> IRS Form 990 filings (the financial return)
  2. Wayback Machine CDX API        -> historical published-report PDFs (since removed)
  3. Live site crawl (BFS)          -> current published-report PDFs

ProPublica and Wayback are treated as SEEDERS: they discover URLs and push them
into the same URL frontier the live crawler uses. The BFS crawler then handles
collection, dedup, (optionally) downloading + text extraction / OCR, and storage
uniformly for all three.

By default this runs in LINKS-ONLY mode: instead of downloading each PDF, it just
records the PDF's URL in the manifest. Pass --download (CLI) or links_only=False
(populate_data) to fetch and save the actual files.

Input: a JSON file describing the orgs, e.g.
  [
    {"name": "Carnegie Hall", "domain": "carnegiehall.org", "ein": "131923635"},
    {"name": "Whitney Museum of American Art", "domain": "whitney.org"}
  ]
(`ein` is optional — if omitted, the script searches ProPublica by `name`.)

Run:
  python nonprofit_reports.py --orgs orgs.json --out ./reports --years 20
  python nonprofit_reports.py --orgs orgs.json --download   # save PDFs too
  python nonprofit_reports.py --sample          # writes a sample orgs.json
"""

import json
import argparse
import requests
import pandas as pd
from dotenv import load_dotenv
from tqdm import tqdm
import PyPDF2
from io import BytesIO
import time
import os
import tempfile
from rich.console import Console
import sys, os

# OCR is optional and pulls in heavy system binaries (tesseract, ghostscript).
# Guard the import so the rest of the tool still runs on machines without it;
# OCR only ever runs when you pass --ocr.
try:
    import ocrmypdf
    OCR_AVAILABLE = True
except Exception:
    ocrmypdf = None
    OCR_AVAILABLE = False

# --- standard-library helpers needed to implement the crawler architecture ---
import re
import socket
import hashlib
import logging
import datetime as dt
from collections import deque
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.robotparser import RobotFileParser
from requests.adapters import HTTPAdapter

try:
    from urllib3.util.retry import Retry
except Exception:  # very old urllib3
    Retry = None


def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


load_dotenv(resource_path(".env"))

console = Console()

# Config (env-overridable; .env is read above) -------------------------------
USER_AGENT     = os.getenv("USER_AGENT", "nonprofit-reports/1.0 (+research; contact you@example.com)")
YEARS_BACK     = int(os.getenv("YEARS_BACK", "20"))
REQUEST_DELAY  = float(os.getenv("REQUEST_DELAY", "1.0"))   # seconds between hits to the same host
REQUEST_TIMEOUT= int(os.getenv("REQUEST_TIMEOUT", "30"))
MAX_DEPTH      = int(os.getenv("MAX_DEPTH", "3"))           # live-crawl link depth
MAX_PAGES_PER_SITE = int(os.getenv("MAX_PAGES_PER_SITE", "400"))
OUTPUT_DIR     = os.getenv("OUTPUT_DIR", "./reports")

PROPUBLICA_SEARCH = "https://projects.propublica.org/nonprofits/api/v2/search.json"
PROPUBLICA_ORG    = "https://projects.propublica.org/nonprofits/api/v2/organizations/{ein}.json"
WAYBACK_CDX       = "http://web.archive.org/cdx/search/cdx"

# Filename tokens that mark a PDF as an "annual report" (used to avoid grabbing
# every PDF on a site). Disable with --all-pdfs.
REPORT_KEYWORDS = ["annual", "report", "annualreport", "annual-report",
                   "year-in-review", "yearinreview", "impact", "ar20", "ar-20"]

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
log = logging.getLogger("nonprofit_reports")


# ---------------------------------------------------------------------------
# small utilities
# ---------------------------------------------------------------------------
def slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", (text or "").strip().lower())
    return re.sub(r"[\s_-]+", "-", text)[:80] or "org"


def window_years():
    """Return (oldest_year, this_year) for the configured lookback."""
    this_year = dt.date.today().year
    return this_year - YEARS_BACK, this_year


def registered_domain(host: str) -> str:
    """Naive eTLD+1 (good enough for *.org / *.com nonprofits)."""
    host = (host or "").lower().split(":")[0]
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def load_csv(file_path):
    """Read the org-list CSV (columns: name, domain, ein) into a DataFrame.

    Lets exceptions propagate so the caller (the UI) can show the error and
    re-prompt, instead of silently getting back None and crashing on len().
    Column names are lower-cased/stripped so downstream lookups are casing-proof.
    """
    df = pd.read_csv(file_path)
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df
    
# ===========================================================================
# URL frontier  -- BFS queue of work items
# ===========================================================================
class URLFrontier:
    """FIFO queue -> breadth-first traversal. Items are dicts so seeders can
    attach source/org/year/expand metadata alongside the URL."""

    def __init__(self):
        self._q = deque()

    def add(self, url, *, source, org, depth=0, expand=False, year=None):
        self._q.append({"url": url, "source": source, "org": org,
                        "depth": depth, "expand": expand, "year": year})

    def next(self):
        return self._q.popleft()

    def __len__(self):
        return len(self._q)


# ===========================================================================
# HTML downloader  -- polite, retrying fetch for both HTML and binary (PDF)
# ===========================================================================
class HTMLDownloader:
    def __init__(self, delay=REQUEST_DELAY, timeout=REQUEST_TIMEOUT):
        self.delay = delay
        self.timeout = timeout
        self._last_hit = {}                     # host -> last request time (rate limit)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        if Retry is not None:
            retry = Retry(total=3, backoff_factor=0.5,
                          status_forcelist=(429, 500, 502, 503, 504),
                          allowed_methods=("GET", "HEAD"))
            adapter = HTTPAdapter(max_retries=retry)
            self.session.mount("http://", adapter)
            self.session.mount("https://", adapter)

    def _throttle(self, host):
        last = self._last_hit.get(host, 0)
        wait = self.delay - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        self._last_hit[host] = time.time()

    def get(self, url):
        host = urlparse(url).netloc
        self._throttle(host)
        try:
            r = self.session.get(url, timeout=self.timeout, allow_redirects=True)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            log.warning(f"download failed {url}: {e}")
            return None


# ===========================================================================
# DNS resolver  -- cached, so we resolve each host once and skip dead ones
# ===========================================================================
class DNSResolver:
    def __init__(self):
        self._cache = {}                        # host -> ip or None

    def resolve(self, host):
        host = host.split(":")[0]
        if host not in self._cache:
            try:
                self._cache[host] = socket.gethostbyname(host)
            except socket.gaierror:
                self._cache[host] = None
                log.warning(f"DNS resolution failed: {host}")
        return self._cache[host]


# ===========================================================================
# content parser  -- HTML -> soup;  PDF bytes -> page count / text (+ OCR)
# ===========================================================================
class ContentParser:
    def parse_html(self, html_bytes, base_url):
        # Imported lazily so a missing bs4 only matters if you actually crawl.
        from bs4 import BeautifulSoup
        return BeautifulSoup(html_bytes, "html.parser")

    def pdf_info(self, pdf_bytes, do_ocr=False):
        """Return (n_pages, text_len, ocr_used). Used to flag scanned/image PDFs."""
        n_pages, text_len, ocr_used = 0, 0, False
        try:
            reader = PyPDF2.PdfReader(BytesIO(pdf_bytes))
            n_pages = len(reader.pages)
            text = "".join((p.extract_text() or "") for p in reader.pages)
            text_len = len(text.strip())
        except Exception as e:
            log.warning(f"PDF parse error: {e}")

        
        if do_ocr and text_len < 50 and OCR_AVAILABLE:
            try:
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fin, \
                     tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fout:
                    fin.write(pdf_bytes); fin.flush()
                    ocrmypdf.ocr(fin.name, fout.name, skip_text=True, progress_bar=False)
                    fout.seek(0)
                    reader = PyPDF2.PdfReader(fout.name)
                    text_len = len("".join((p.extract_text() or "") for p in reader.pages).strip())
                    ocr_used = True
            except Exception as e:
                log.warning(f"OCR failed: {e}")
        return n_pages, text_len, ocr_used


# ===========================================================================
# Content seen  -- dedup downloaded *content* by hash (same PDF from 2 sources)
# ===========================================================================
class ContentSeen:
    def __init__(self):
        self._hashes = set()

    @staticmethod
    def digest(data: bytes) -> str:
        return hashlib.sha1(data).hexdigest()

    def is_new(self, data: bytes) -> bool:
        h = self.digest(data)
        if h in self._hashes:
            return False
        self._hashes.add(h)
        return True


# ===========================================================================
# Content storage  -- dated folders per org/source + a pandas manifest
# ===========================================================================
class ContentStorage:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.rows = []
        os.makedirs(out_dir, exist_ok=True)

    def record_link(self, *, org, source, year, url):
        """Links-only mode: record the PDF's URL in the manifest without
        downloading the file or writing anything to disk."""
        self.rows.append({
            "org": org, "source": source, "year": year or "undated", "url": url,
        })

    def save_pdf(self, data, *, org, source, year, url, n_pages, text_len, ocr_used):
        year = year or "undated"
        folder = os.path.join(self.out_dir, slugify(org), source, str(year))
        os.makedirs(folder, exist_ok=True)

        base = os.path.basename(urlparse(url).path) or "document.pdf"
        if not base.lower().endswith(".pdf"):
            base += ".pdf"
        fname = f"{year}_{slugify(os.path.splitext(base)[0])}.pdf"
        path = os.path.join(folder, fname)

        # Avoid clobbering same-name different-content files.
        i = 1
        while os.path.exists(path):
            path = os.path.join(folder, f"{year}_{slugify(os.path.splitext(base)[0])}_{i}.pdf")
            i += 1

        with open(path, "wb") as f:
            f.write(data)

        self.rows.append({
            "org": org, "source": source, "year": year, "url": url,
            "saved_path": path, "bytes": len(data), "pages": n_pages,
            "text_len": text_len, "scanned_or_ocr": ocr_used,
            "sha1": ContentSeen.digest(data),
        })
        return path

    def write_manifest(self):
        if not self.rows:
            console.print("[yellow]No documents collected — manifest is empty.[/yellow]")
            return None
        df = pd.DataFrame(self.rows).sort_values(["org", "source", "year"])
        csv_path = os.path.join(self.out_dir, "manifest.csv")
        json_path = os.path.join(self.out_dir, "manifest.json")
        df.to_csv(csv_path, index=False)
        df.to_json(json_path, orient="records", indent=2)
        return df


# ===========================================================================
# URL extractor  -- pull candidate links out of an HTML page
# ===========================================================================
class URLExtractor:
    def extract(self, soup, base_url):
        urls = set()
        for a in soup.find_all("a", href=True):
            absolute = urljoin(base_url, a["href"])
            absolute, _ = urldefrag(absolute)     # drop #fragments
            if absolute.startswith(("http://", "https://")):
                urls.add(absolute)
        return urls


# ===========================================================================
# URL filter  -- decide what is worth enqueuing / keeping
# ===========================================================================
class URLFilter:
    def __init__(self, seed_domain, all_pdfs=False):
        self.seed_domain = registered_domain(seed_domain)
        self.all_pdfs = all_pdfs

    def same_site(self, url):
        return registered_domain(urlparse(url).netloc) == self.seed_domain

    @staticmethod
    def is_pdf_url(url):
        return urlparse(url).path.lower().endswith(".pdf")

    def looks_like_report(self, url):
        if self.all_pdfs:
            return True
        low = url.lower()
        return any(k in low for k in REPORT_KEYWORDS)

    def keep_pdf(self, url):
        """A PDF is worth downloading if it's on-site and looks like a report."""
        return self.same_site(url) and self.looks_like_report(url)

    def follow_html(self, url):
        """Only follow same-site, non-PDF links during the live crawl."""
        return self.same_site(url) and not self.is_pdf_url(url)


# ===========================================================================
# url seen  -- dedup URLs we've already enqueued/visited
# ===========================================================================
class URLSeen:
    def __init__(self):
        self._seen = set()

    @staticmethod
    def _norm(url):
        url, _ = urldefrag(url)
        return url.rstrip("/").lower()

    def add(self, url):
        self._seen.add(self._norm(url))

    def seen(self, url):
        return self._norm(url) in self._seen


# ===========================================================================
# Seeders  -- ProPublica + Wayback push URLs into the frontier
# ===========================================================================
def seed_propublica(org, frontier, downloader):
    """Find the org's EIN (if not given) and enqueue every Form 990 PDF in window."""
    oldest, _ = window_years()
    ein = org.get("ein")

    if not ein:
        q = org.get("name") or org.get("domain", "")
        r = downloader.get(f"{PROPUBLICA_SEARCH}?q={requests.utils.quote(q)}")
        if not r:
            return
        results = r.json().get("organizations", [])
        if not results:
            console.print(f"[yellow]ProPublica: no match for '{q}'[/yellow]")
            return
        ein = str(results[0]["ein"])
        console.print(f"  ProPublica matched '{q}' -> EIN {ein} "
                      f"({results[0].get('name','?')}) "
                      f"[dim](verify if unsure)[/dim]")

    ein = re.sub(r"\D", "", str(ein))
    r = downloader.get(PROPUBLICA_ORG.format(ein=ein))
    if not r:
        return
    data = r.json()
    filings = (data.get("filings_with_data", []) or []) + \
              (data.get("filings_without_data", []) or [])

    seen = set()
    for f in filings:
        pdf = f.get("pdf_url")
        yr = f.get("tax_prd_yr") or f.get("tax_prd")
        if not pdf or pdf in seen:
            continue
        try:
            yr_int = int(str(yr)[:4])
        except (TypeError, ValueError):
            yr_int = None
        if yr_int and yr_int < oldest:
            continue
        seen.add(pdf)
        frontier.add(pdf, source="990", org=org["name"],
                     expand=False, year=yr_int)
    console.print(f"  ProPublica: queued {len(seen)} Form 990 PDF(s)")


def seed_wayback(org, frontier, downloader, all_pdfs=False):
    """Enqueue every archived PDF on the domain within the 20-year window."""
    oldest, this_year = window_years()
    domain = org["domain"]
    params = (f"?url={requests.utils.quote(domain)}*"
              f"&filter=mimetype:application/pdf"
              f"&filter=statuscode:200"
              f"&from={oldest}0101&to={this_year}1231"
              f"&output=json&collapse=urlkey")
    r = downloader.get(WAYBACK_CDX + params)
    if not r:
        return
    try:
        rows = r.json()
    except Exception:
        console.print(f"[yellow]Wayback: bad response for {domain}[/yellow]")
        return
    if not rows or len(rows) < 2:
        console.print(f"  Wayback: nothing archived for {domain}")
        return

    header, *records = rows
    idx = {name: i for i, name in enumerate(header)}
    count = 0
    for rec in records:
        ts = rec[idx["timestamp"]]
        original = rec[idx["original"]]
        if not all_pdfs and not any(k in original.lower() for k in REPORT_KEYWORDS):
            continue
        # `id_` returns the raw archived file with no Wayback wrapper.
        archived = f"https://web.archive.org/web/{ts}id_/{original}"
        year = int(ts[:4])
        frontier.add(archived, source="wayback", org=org["name"],
                     expand=False, year=year)
        count += 1
    console.print(f"  Wayback: queued {count} archived PDF(s)")


# ===========================================================================
# Crawler -> BFS  -- the orchestrator that ties every component together
# ===========================================================================
class Crawler:
    def __init__(self, out_dir, *, sources, all_pdfs=False, do_ocr=False,
                 max_depth=MAX_DEPTH, max_pages=MAX_PAGES_PER_SITE,
                 links_only=True):
        self.frontier   = URLFrontier()
        self.downloader = HTMLDownloader()
        self.dns        = DNSResolver()
        self.parser     = ContentParser()
        self.content_seen = ContentSeen()
        self.storage    = ContentStorage(out_dir)
        self.extractor  = URLExtractor()
        self.url_seen   = URLSeen()
        self.sources    = sources
        self.all_pdfs   = all_pdfs
        self.do_ocr     = do_ocr
        self.links_only = links_only
        self.max_depth  = max_depth
        self.max_pages  = max_pages
        self._robots    = {}        # host -> RobotFileParser

    # ---- robots.txt (only enforced for the live crawl) -------------------
    def _allowed(self, url):
        host = urlparse(url).netloc
        if host not in self._robots:
            rp = RobotFileParser()
            rp.set_url(f"{urlparse(url).scheme}://{host}/robots.txt")
            try:
                rp.read()
            except Exception:
                rp = None
            self._robots[host] = rp
        rp = self._robots[host]
        return True if rp is None else rp.can_fetch(USER_AGENT, url)

    # ---- per-org seeding --------------------------------------------------
    def seed_org(self, org):
        console.print(f"[bold cyan]{org['name']}[/bold cyan] ({org['domain']})")
        if "990" in self.sources:
            seed_propublica(org, self.frontier, self.downloader)
        if not org.get("domain"):
            return  # wayback + live both need a domain; 990 can run via EIN/name
        if "wayback" in self.sources:
            seed_wayback(org, self.frontier, self.downloader, self.all_pdfs)
        if "live" in self.sources:
            self.frontier.add(f"https://{org['domain']}/", source="live",
                              org=org["name"], depth=0, expand=True)

    # ---- the BFS loop -----------------------------------------------------
    def run(self):
        pages_seen = {}     # org -> count, to cap live-crawl breadth
        pbar = tqdm(total=len(self.frontier), desc="crawling", unit="url")

        while len(self.frontier):
            item = self.frontier.next()
            pbar.total = pbar.n + len(self.frontier) + 1
            pbar.update(1)

            url = item["url"]
            if self.url_seen.seen(url):
                continue
            self.url_seen.add(url)

            # Links-only: known PDF URLs (990/wayback seeds, or .pdf links found
            # on the live crawl) go straight to the manifest — no fetch needed.
            if self.links_only and (item["source"] in ("990", "wayback")
                                    or URLFilter.is_pdf_url(url)):
                self.storage.record_link(org=item["org"], source=item["source"],
                                         year=item["year"], url=url)
                continue

            # live-crawl breadth cap
            if item["source"] == "live":
                n = pages_seen.get(item["org"], 0)
                if n >= self.max_pages:
                    continue
                pages_seen[item["org"]] = n + 1
                if not self._allowed(url):
                    continue

            # DNS gate (skip hosts that don't resolve)
            if self.dns.resolve(urlparse(url).netloc) is None:
                continue

            resp = self.downloader.get(url)
            if resp is None:
                continue

            ctype = resp.headers.get("Content-Type", "").lower()
            body = resp.content
            is_pdf = "application/pdf" in ctype or body[:5] == b"%PDF-"

            if is_pdf:
                # A live URL with no .pdf extension that turned out to be a PDF.
                if self.links_only:
                    self.storage.record_link(org=item["org"], source=item["source"],
                                             year=item["year"], url=item["url"])
                else:
                    self._handle_pdf(item, body)
                continue

            # HTML: only the live crawl expands further
            if item["expand"] and item["depth"] < self.max_depth and "html" in ctype:
                self._expand_html(item, resp)

        pbar.close()
        df = self.storage.write_manifest()
        return df

    # ---- handlers ---------------------------------------------------------
    def _handle_pdf(self, item, body):
        if not self.content_seen.is_new(body):
            return   # identical file already saved (e.g. live + wayback dupe)
        n_pages, text_len, ocr_used = self.parser.pdf_info(body, do_ocr=self.do_ocr)
        self.storage.save_pdf(
            body, org=item["org"], source=item["source"], year=item["year"],
            url=item["url"], n_pages=n_pages, text_len=text_len, ocr_used=ocr_used)

    def _expand_html(self, item, resp):
        soup = self.parser.parse_html(resp.content, resp.url)
        # derive the seed domain from this live URL for same-site filtering
        flt = URLFilter(urlparse(resp.url).netloc, all_pdfs=self.all_pdfs)
        for link in self.extractor.extract(soup, resp.url):
            if flt.is_pdf_url(link):
                if flt.keep_pdf(link):
                    self.frontier.add(link, source="live", org=item["org"],
                                      depth=item["depth"] + 1, expand=False,
                                      year=dt.date.today().year)
            elif flt.follow_html(link):
                self.frontier.add(link, source="live", org=item["org"],
                                  depth=item["depth"] + 1, expand=True)


# ===========================================================================
# Programmatic entry point  -- used by the questionary UI (spider_UI.py)
# ===========================================================================
def _row_to_org(row):
    """Turn a DataFrame row (name, domain, ein) into the dict the crawler wants.

    pandas fills missing cells with NaN; we convert those (and blanks) to None
    so seed_propublica's `org.get("ein")` falls through to search-by-name, and
    we strip any scheme/trailing slash off the domain.
    """
    def val(key):
        if key not in row.index:
            return None
        v = row[key]
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        s = str(v).strip()
        return s or None

    domain = re.sub(r"^https?://", "", val("domain") or "", flags=re.I).strip("/").lower()
    # Fall back to the domain when no name column is present, so each org still
    # gets its own folder/manifest label instead of collapsing into "org".
    return {"name": val("name") or domain or "", "domain": domain, "ein": val("ein")}


def populate_data(orgs_df, out_dir, sources, years=YEARS_BACK, depth=MAX_DEPTH,
                  start_row=0, end_row=None, *, all_pdfs=False, do_ocr=False,
                  links_only=True):
    """Run the crawler over a slice of an orgs DataFrame; return the manifest df.

    This is the entry point the UI calls. It mirrors main() but takes an
    in-memory DataFrame (from load_csv) and a [start_row, end_row) window
    instead of a JSON file and an argparse namespace.

    links_only=True (default) records PDF URLs in the manifest without
    downloading them. Set links_only=False to fetch and save the actual files.
    """
    global YEARS_BACK
    YEARS_BACK = years  # window_years() reads this global, so set it before seeding

    if orgs_df is None or len(orgs_df) == 0:
        console.print("[yellow]No orgs to process.[/yellow]")
        return None

    n = len(orgs_df)
    start = max(0, int(start_row))
    end = n if end_row is None else min(int(end_row), n)
    rows = orgs_df.iloc[start:end]
    if rows.empty:
        console.print(f"[yellow]Row range {start}:{end} is empty — nothing to do.[/yellow]")
        return None

    oldest, this_year = window_years()
    console.print(
        f"[bold]Collecting {oldest}-{this_year} for rows {start}:{end} "
        f"from sources: {', '.join(sources)}[/bold]"
    )

    crawler = Crawler(out_dir, sources=sources, all_pdfs=all_pdfs,
                      do_ocr=do_ocr, max_depth=depth, links_only=links_only)
    for _, row in rows.iterrows():
        org = _row_to_org(row)
        if not org["domain"] and ("live" in sources or "wayback" in sources):
            console.print(
                f"[yellow]'{org['name'] or '?'}' has no domain — "
                f"only the 990 source can run for it.[/yellow]"
            )
        crawler.seed_org(org)

    return crawler.run()


# ===========================================================================
# CLI
# ===========================================================================
SAMPLE = [
    {"name": "Carnegie Hall", "domain": "carnegiehall.org", "ein": "131923635"},
    {"name": "Whitney Museum of American Art", "domain": "whitney.org"},
]


def main():
    global YEARS_BACK
    ap = argparse.ArgumentParser(description="Collect nonprofit annual reports (990s + published PDFs) over ~20 years.")
    ap.add_argument("--orgs", help="Path to orgs JSON file")
    ap.add_argument("--out", default=OUTPUT_DIR, help="Output directory")
    ap.add_argument("--years", type=int, default=YEARS_BACK, help="Lookback window in years")
    ap.add_argument("--sources", default="990,wayback,live",
                    help="Comma list of sources to run: 990,wayback,live")
    ap.add_argument("--depth", type=int, default=MAX_DEPTH, help="Live-crawl link depth")
    ap.add_argument("--all-pdfs", action="store_true",
                    help="Keep every PDF, not just ones whose URL looks like a report")
    ap.add_argument("--download", action="store_true",
                    help="Download and save the PDFs instead of only collecting links")
    ap.add_argument("--ocr", action="store_true", help="OCR scanned PDFs (needs ocrmypdf+tesseract)")
    ap.add_argument("--sample", action="store_true", help="Write a sample orgs.json and exit")
    args = ap.parse_args()

    if args.sample:
        with open("orgs.json", "w") as f:
            json.dump(SAMPLE, f, indent=2)
        console.print("[green]Wrote sample orgs.json[/green]")
        return

    if not args.orgs:
        ap.error("--orgs is required (or use --sample to generate one)")

    YEARS_BACK = args.years
    if args.ocr and not OCR_AVAILABLE:
        console.print("[yellow]--ocr requested but ocrmypdf is not installed; continuing without OCR.[/yellow]")

    with open(args.orgs) as f:
        orgs = json.load(f)

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    oldest, this_year = window_years()
    console.print(f"[bold]Collecting {oldest}-{this_year} from sources: {', '.join(sources)}[/bold]\n")

    crawler = Crawler(args.out, sources=sources, all_pdfs=args.all_pdfs,
                      do_ocr=args.ocr, max_depth=args.depth,
                      links_only=not args.download)
    for org in orgs:
        crawler.seed_org(org)

    df = crawler.run()
    if df is not None:
        console.print(f"\n[green]Done. {len(df)} documents.[/green] "
                      f"Manifest: {os.path.join(args.out, 'manifest.csv')}")
        cols = [c for c in ("org", "source", "year", "pages", "saved_path", "url")
                if c in df.columns]
        console.print(df[cols].to_string(index=False))


if __name__ == "__main__":
    main()