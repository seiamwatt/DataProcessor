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
(`ein` is optional -- if omitted, the script searches ProPublica by `name`.)

Run:
  python nonprofit_reports.py --orgs orgs.json --out ./reports --years 20
  python nonprofit_reports.py --orgs orgs.json --download   # save PDFs too
  python nonprofit_reports.py --sample          # writes a sample orgs.json
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import logging
import os
import re
import socket
import sys
import tempfile
import time
from collections import deque
from dataclasses import dataclass, field, replace
from io import BytesIO
from typing import Iterator, Optional

import pandas as pd
import requests
from dotenv import load_dotenv
from dataprocessor.config import load_env
from requests.adapters import HTTPAdapter
from rich.console import Console
from tqdm import tqdm
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.robotparser import RobotFileParser

# pypdf is the maintained successor to PyPDF2; fall back for old environments.
try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    from PyPDF2 import PdfReader  # type: ignore

# OCR is optional and pulls in heavy system binaries (tesseract, ghostscript).
try:
    import ocrmypdf
    OCR_AVAILABLE = True
except Exception:
    ocrmypdf = None
    OCR_AVAILABLE = False

try:
    from urllib3.util.retry import Retry
except Exception:  # very old urllib3
    Retry = None


def resource_path(relative_path: str) -> str:
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)  # type: ignore[attr-defined]
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


load_env()
console = Console()
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
log = logging.getLogger("nonprofit_reports")

PROPUBLICA_SEARCH = "https://projects.propublica.org/nonprofits/api/v2/search.json"
PROPUBLICA_ORG = "https://projects.propublica.org/nonprofits/api/v2/organizations/{ein}.json"
WAYBACK_CDX = "https://web.archive.org/cdx/search/cdx"

# Filename tokens that mark a PDF as an "annual report" (used to avoid grabbing
# every PDF on a site). Disable with --all-pdfs.
REPORT_KEYWORDS = ("annual", "report", "annualreport", "annual-report",
                   "year-in-review", "yearinreview", "impact", "ar20", "ar-20")

# Static-asset extensions the live crawl should never follow. Following these
# just burns the per-site page budget on files that can't contain report PDFs
# (the crawler only collects PDFs, so doc/xls links lose us nothing either).
SKIP_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
                   ".css", ".js", ".json", ".xml", ".rss", ".zip", ".gz",
                   ".tar", ".rar", ".7z", ".mp3", ".mp4", ".mov", ".avi",
                   ".wmv", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                   ".woff", ".woff2", ".ttf", ".eot")


# ===========================================================================
# Config -- one immutable object instead of mutable module globals
# ===========================================================================
@dataclass(frozen=True)
class Config:
    user_agent: str = "nonprofit-reports/1.2 (+research; contact you@example.com)"
    years_back: int = 20
    request_delay: float = 1.0        # seconds between hits to the same host
    request_timeout: int = 30
    max_depth: int = 3                # live-crawl link depth
    max_pages_per_site: int = 400
    max_bytes: int = 50 * 1024 * 1024  # per-response download cap (50 MB)
    output_dir: str = "./reports"
    all_pdfs: bool = False
    do_ocr: bool = False
    links_only: bool = True
    sources: tuple[str, ...] = ("990", "wayback", "live")

    @classmethod
    def from_env(cls) -> "Config":
        """Environment (.env) overrides for the defaults above."""
        return cls(
            user_agent=os.getenv("USER_AGENT", cls.user_agent),
            years_back=int(os.getenv("YEARS_BACK", cls.years_back)),
            request_delay=float(os.getenv("REQUEST_DELAY", cls.request_delay)),
            request_timeout=int(os.getenv("REQUEST_TIMEOUT", cls.request_timeout)),
            max_depth=int(os.getenv("MAX_DEPTH", cls.max_depth)),
            max_pages_per_site=int(os.getenv("MAX_PAGES_PER_SITE", cls.max_pages_per_site)),
            max_bytes=int(os.getenv("MAX_BYTES", cls.max_bytes)),
            output_dir=os.getenv("OUTPUT_DIR", cls.output_dir),
        )

    def window_years(self) -> tuple[int, int]:
        """(oldest_year, this_year) for the configured lookback."""
        this_year = dt.date.today().year
        return this_year - self.years_back, this_year


# ===========================================================================
# small utilities
# ===========================================================================
def slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", (text or "").strip().lower())
    return re.sub(r"[\s_-]+", "-", text)[:80] or "org"


def registered_domain(host: str) -> str:
    """Naive eTLD+1 (good enough for *.org / *.com nonprofits).

    For domains under country-code suffixes (e.g. .org.uk) consider swapping
    this for `tldextract`.
    """
    host = (host or "").lower().split(":")[0]
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def load_csv(file_path: str) -> pd.DataFrame:
    """Read the org-list CSV (columns: name, domain, ein) into a DataFrame.

    Lets exceptions propagate so the caller (the UI) can show the error and
    re-prompt. Column names are lower-cased/stripped so downstream lookups are
    casing-proof. `ein` is read as a string so leading zeros survive.
    """
    df = pd.read_csv(file_path, dtype={"ein": "string", "EIN": "string"})
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df


# ===========================================================================
# Work items + URL frontier -- BFS queue
# ===========================================================================
@dataclass
class WorkItem:
    url: str
    source: str                 # "990" | "wayback" | "live"
    org: str
    depth: int = 0
    expand: bool = False        # follow links found on this page?
    year: Optional[int] = None


class URLSeen:
    """Dedup URLs we've already enqueued. Normalization keeps the path's case
    (paths ARE case-sensitive on most servers) and only lowercases the
    scheme/host, which are not."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    @staticmethod
    def _norm(url: str) -> str:
        url, _ = urldefrag(url)
        p = urlparse(url)
        return f"{p.scheme.lower()}://{p.netloc.lower()}{p.path.rstrip('/')}" + \
               (f"?{p.query}" if p.query else "")

    def add(self, url: str) -> bool:
        """Add url; return True if it was new."""
        key = self._norm(url)
        if key in self._seen:
            return False
        self._seen.add(key)
        return True


class URLFrontier:
    """FIFO queue -> breadth-first traversal. Dedup happens at ENQUEUE time so
    duplicate discoveries never bloat the queue."""

    def __init__(self, seen: URLSeen) -> None:
        self._q: deque[WorkItem] = deque()
        self._seen = seen

    def add(self, item: WorkItem) -> bool:
        if not self._seen.add(item.url):
            return False
        self._q.append(item)
        return True

    def next(self) -> WorkItem:
        return self._q.popleft()

    def __len__(self) -> int:
        return len(self._q)


# ===========================================================================
# Downloader -- polite, retrying fetch for HTML, JSON, and binary (PDF)
# ===========================================================================
class Downloader:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._last_hit: dict[str, float] = {}   # host -> last request time
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": cfg.user_agent})
        if Retry is not None:
            kwargs = dict(total=3, backoff_factor=0.5,
                          status_forcelist=(429, 500, 502, 503, 504))
            try:
                retry = Retry(allowed_methods=("GET", "HEAD"), **kwargs)
            except TypeError:  # urllib3 < 1.26 used a different kwarg name
                retry = Retry(method_whitelist=("GET", "HEAD"), **kwargs)
            adapter = HTTPAdapter(max_retries=retry)
            self.session.mount("http://", adapter)
            self.session.mount("https://", adapter)

    def _throttle(self, host: str) -> None:
        wait = self.cfg.request_delay - (time.time() - self._last_hit.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        self._last_hit[host] = time.time()

    def get(self, url: str, params=None) -> Optional[requests.Response]:
        """Fetch a URL, streaming the body with a size cap so one mislabeled
        or enormous file can't blow up memory. The body is materialized onto
        the Response, so callers keep using .content/.text/.json() as usual.
        """
        self._throttle(urlparse(url).netloc)
        try:
            r = self.session.get(url, params=params,
                                 timeout=self.cfg.request_timeout,
                                 allow_redirects=True, stream=True)
            r.raise_for_status()

            limit = self.cfg.max_bytes
            # Trust Content-Length when present to bail early...
            clen = r.headers.get("Content-Length", "")
            if clen.isdigit() and int(clen) > limit:
                log.warning("skipping %s: Content-Length %s > %d-byte cap",
                            url, clen, limit)
                r.close()
                return None
            # ...but enforce the cap while reading regardless (servers lie
            # about, or omit, Content-Length).
            chunks: list[bytes] = []
            size = 0
            for chunk in r.iter_content(chunk_size=64 * 1024):
                chunks.append(chunk)
                size += len(chunk)
                if size > limit:
                    log.warning("skipping %s: body exceeds %d-byte cap", url, limit)
                    r.close()
                    return None
            r._content = b"".join(chunks)   # backs .content/.text/.json()
            return r
        except requests.RequestException as e:
            log.warning("download failed %s: %s", url, e)
            return None

    def get_json(self, url: str, params=None):
        """Fetch and decode JSON, returning None on any failure."""
        r = self.get(url, params=params)
        if r is None:
            return None
        try:
            return r.json()
        except ValueError as e:
            log.warning("bad JSON from %s: %s", url, e)
            return None


# ===========================================================================
# DNS resolver -- cached, so we resolve each host once and skip dead ones
# ===========================================================================
class DNSResolver:
    def __init__(self) -> None:
        self._cache: dict[str, Optional[str]] = {}

    def resolve(self, host: str) -> Optional[str]:
        host = host.split(":")[0]
        if host not in self._cache:
            try:
                self._cache[host] = socket.gethostbyname(host)
            except socket.gaierror:
                self._cache[host] = None
                log.warning("DNS resolution failed: %s", host)
        return self._cache[host]


# ===========================================================================
# Robots -- fetched through OUR session (UA + timeout + throttle), cached
# ===========================================================================
class Robots:
    """RobotFileParser.read() uses urllib with no timeout and the default
    Python UA; fetching robots.txt through our Downloader fixes both."""

    def __init__(self, downloader: Downloader) -> None:
        self.downloader = downloader
        self._cache: dict[str, Optional[RobotFileParser]] = {}

    def allowed(self, url: str) -> bool:
        p = urlparse(url)
        host = p.netloc
        if host not in self._cache:
            rp: Optional[RobotFileParser] = RobotFileParser()
            resp = self.downloader.get(f"{p.scheme}://{host}/robots.txt")
            if resp is None:
                rp = None                      # unreachable -> assume allowed
            else:
                try:
                    rp.parse(resp.text.splitlines())
                except Exception:
                    rp = None
            self._cache[host] = rp
        rp = self._cache[host]
        return True if rp is None else rp.can_fetch(self.downloader.cfg.user_agent, url)


# ===========================================================================
# content parser -- HTML -> soup;  PDF bytes -> page count / text (+ OCR)
# ===========================================================================
class ContentParser:
    def parse_html(self, html_bytes: bytes, base_url: str):
        # Imported lazily so a missing bs4 only matters if you actually crawl.
        from bs4 import BeautifulSoup
        return BeautifulSoup(html_bytes, "html.parser")

    @staticmethod
    def _text_len(reader: PdfReader) -> int:
        return len("".join((p.extract_text() or "") for p in reader.pages).strip())

    def pdf_info(self, pdf_bytes: bytes, do_ocr: bool = False) -> tuple[int, int, bool]:
        """Return (n_pages, text_len, ocr_used). Used to flag scanned/image PDFs."""
        n_pages, text_len, ocr_used = 0, 0, False
        try:
            reader = PdfReader(BytesIO(pdf_bytes))
            n_pages = len(reader.pages)
            text_len = self._text_len(reader)
        except Exception as e:
            log.warning("PDF parse error: %s", e)

        if do_ocr and text_len < 50 and OCR_AVAILABLE:
            fin_path = fout_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fin:
                    fin.write(pdf_bytes)
                    fin_path = fin.name
                fd, fout_path = tempfile.mkstemp(suffix=".pdf")
                os.close(fd)
                ocrmypdf.ocr(fin_path, fout_path, skip_text=True, progress_bar=False)
                text_len = self._text_len(PdfReader(fout_path))
                ocr_used = True
            except Exception as e:
                log.warning("OCR failed: %s", e)
            finally:
                for p in (fin_path, fout_path):     # don't leak temp files
                    if p and os.path.exists(p):
                        try:
                            os.unlink(p)
                        except OSError:
                            pass
        return n_pages, text_len, ocr_used


# ===========================================================================
# Content seen -- dedup downloaded *content* by hash (same PDF from 2 sources)
# ===========================================================================
class ContentSeen:
    def __init__(self) -> None:
        self._hashes: set[str] = set()

    @staticmethod
    def digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def is_new(self, data: bytes) -> bool:
        h = self.digest(data)
        if h in self._hashes:
            return False
        self._hashes.add(h)
        return True


# ===========================================================================
# Content storage -- dated folders per org/source + a pandas manifest
# ===========================================================================
class ContentStorage:
    # Union of every field either row shape can produce, so the streamed CSV
    # has a stable header whether we're in links-only or download mode.
    FIELDNAMES = ["org", "source", "year", "url", "saved_path", "bytes",
                  "pages", "text_len", "scanned_or_ocr", "sha256", "collected_at"]

    def __init__(self, out_dir: str) -> None:
        self.out_dir = out_dir
        self.rows: list[dict] = []
        os.makedirs(out_dir, exist_ok=True)
        self.manifest_csv = os.path.join(out_dir, "manifest.csv")
        self._header_written = False
        self._load_existing()

    def _load_existing(self) -> None:
        """Preload a prior manifest so a re-run resumes it (append lands in the
        same file) instead of starting from an empty file that clobbers it."""
        if not os.path.exists(self.manifest_csv) or os.path.getsize(self.manifest_csv) == 0:
            return
        try:
            prev = pd.read_csv(self.manifest_csv, dtype={"ein": "string"})
        except Exception as e:
            # An unparseable manifest must not be clobbered by this run's
            # rewrite at the end -- set it aside so the data survives on disk.
            backup = self.manifest_csv + ".unreadable"
            i = 1
            while os.path.exists(backup):
                backup = f"{self.manifest_csv}.unreadable.{i}"
                i += 1
            try:
                os.replace(self.manifest_csv, backup)
                console.print(f"[yellow]Existing manifest could not be parsed "
                              f"({e}); moved it to {backup} and starting a "
                              f"fresh manifest.[/yellow]")
            except OSError as move_err:
                log.warning("could not back up unreadable manifest %s: %s",
                            self.manifest_csv, move_err)
            return
        self.rows.extend(prev.to_dict("records"))
        self._header_written = True   # file already has a header row
        # Older versions wrote the final manifest with only the columns the
        # run produced (5 in links-only mode). Appending FIELDNAMES-shaped
        # rows under such a header misaligns the file, so normalize it to the
        # stable header before this run appends anything.
        if list(prev.columns) != self.FIELDNAMES:
            try:
                prev.reindex(columns=self.FIELDNAMES).to_csv(
                    self.manifest_csv, index=False)
            except Exception as e:
                log.warning("could not normalize manifest header %s: %s",
                            self.manifest_csv, e)

    def _append_row(self, row: dict) -> None:
        """Persist one row to manifest.csv right now, flushed to disk, so a
        crash / disconnect mid-crawl keeps everything collected so far."""
        self.rows.append(row)
        try:
            write_header = not self._header_written and (
                not os.path.exists(self.manifest_csv)
                or os.path.getsize(self.manifest_csv) == 0)
            with open(self.manifest_csv, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=self.FIELDNAMES, extrasaction="ignore")
                if write_header:
                    w.writeheader()
                w.writerow(row)
                f.flush()
                os.fsync(f.fileno())
            self._header_written = True
        except Exception as e:
            log.warning("could not append to manifest %s: %s", self.manifest_csv, e)

    def record_link(self, *, org: str, source: str, year, url: str) -> None:
        """Links-only mode: record the PDF's URL in the manifest without
        downloading the file. The row is streamed to disk immediately."""
        self._append_row({
            "org": org, "source": source, "year": year if year else "undated",
            "url": url, "collected_at": dt.datetime.now().isoformat(timespec="seconds"),
        })

    def save_pdf(self, data: bytes, *, org: str, source: str, year, url: str,
                 n_pages: int, text_len: int, ocr_used: bool) -> str:
        year = year if year else "undated"
        folder = os.path.join(self.out_dir, slugify(org), source, str(year))
        os.makedirs(folder, exist_ok=True)

        base = os.path.basename(urlparse(url).path) or "document.pdf"
        if not base.lower().endswith(".pdf"):
            base += ".pdf"
        stem = slugify(os.path.splitext(base)[0])
        path = os.path.join(folder, f"{year}_{stem}.pdf")

        # Avoid clobbering same-name different-content files.
        i = 1
        while os.path.exists(path):
            path = os.path.join(folder, f"{year}_{stem}_{i}.pdf")
            i += 1

        with open(path, "wb") as f:
            f.write(data)

        self._append_row({
            "org": org, "source": source, "year": year, "url": url,
            "saved_path": path, "bytes": len(data), "pages": n_pages,
            "text_len": text_len, "scanned_or_ocr": ocr_used,
            "sha256": ContentSeen.digest(data),
            "collected_at": dt.datetime.now().isoformat(timespec="seconds"),
        })
        return path

    def write_manifest(self) -> Optional[pd.DataFrame]:
        """Final pass: rewrite the streamed manifest.csv as a clean, deduped,
        sorted file (and the JSON view). self.rows already holds any prior
        manifest plus everything appended this run, so we don't re-read disk."""
        if not self.rows:
            console.print("[yellow]No documents collected -- manifest is empty.[/yellow]")
            return None

        # Always emit the full stable header: _append_row streams rows in
        # FIELDNAMES order, so a narrower header here would misalign the next
        # run's appends and make the file unreadable (losing everything).
        df = pd.DataFrame(self.rows).reindex(columns=self.FIELDNAMES)

        # Rows re-collected across resumes can repeat; dedup on (org, source,
        # url), keeping the latest so refreshed metadata wins.
        df = df.drop_duplicates(subset=["org", "source", "url"], keep="last")

        # year mixes ints and "undated"; sort on a string view to avoid a
        # TypeError from pandas comparing int to str.
        df = df.sort_values(["org", "source", "year"],
                            key=lambda s: s.astype(str)).reset_index(drop=True)
        df.to_csv(self.manifest_csv, index=False)
        df.to_json(os.path.join(self.out_dir, "manifest.json"),
                   orient="records", indent=2)
        return df


# ===========================================================================
# URL extractor -- pull candidate links out of an HTML page
# ===========================================================================
class URLExtractor:
    def extract(self, soup, base_url: str) -> set[str]:
        urls: set[str] = set()
        for a in soup.find_all("a", href=True):
            try:
                absolute = urljoin(base_url, a["href"])
            except ValueError:                    # malformed href, e.g. stray brackets
                continue
            absolute, _ = urldefrag(absolute)     # drop #fragments
            if absolute.startswith(("http://", "https://")):
                urls.add(absolute)
        return urls


# ===========================================================================
# URL filter -- decide what is worth enqueuing / keeping
# ===========================================================================
class URLFilter:
    def __init__(self, seed_domain: str, all_pdfs: bool = False) -> None:
        self.seed_domain = registered_domain(seed_domain)
        self.all_pdfs = all_pdfs

    def same_site(self, url: str) -> bool:
        return registered_domain(urlparse(url).netloc) == self.seed_domain

    @staticmethod
    def is_pdf_url(url: str) -> bool:
        return urlparse(url).path.lower().endswith(".pdf")

    def looks_like_report(self, url: str) -> bool:
        if self.all_pdfs:
            return True
        low = url.lower()
        return any(k in low for k in REPORT_KEYWORDS)

    def keep_pdf(self, url: str) -> bool:
        """A PDF is worth collecting if it's on-site and looks like a report."""
        return self.same_site(url) and self.looks_like_report(url)

    def follow_html(self, url: str) -> bool:
        """Follow same-site links, but not PDFs or static assets -- assets
        can't lead to reports and would eat the per-site page budget."""
        if not self.same_site(url) or self.is_pdf_url(url):
            return False
        return not urlparse(url).path.lower().endswith(SKIP_EXTENSIONS)


# ===========================================================================
# Seeders -- ProPublica + Wayback push URLs into the frontier
# ===========================================================================
def seed_propublica(org: dict, frontier: URLFrontier, downloader: Downloader,
                    cfg: Config) -> None:
    """Find the org's EIN (if not given) and enqueue every Form 990 PDF in window."""
    oldest, _ = cfg.window_years()
    ein = org.get("ein")

    if not ein:
        q = org.get("name") or org.get("domain", "")
        data = downloader.get_json(PROPUBLICA_SEARCH, params={"q": q})
        results = (data or {}).get("organizations", [])
        if not results:
            console.print(f"[yellow]ProPublica: no match for '{q}'[/yellow]")
            return
        ein = str(results[0]["ein"])
        console.print(f"  ProPublica matched '{q}' -> EIN {ein} "
                      f"({results[0].get('name', '?')}) "
                      f"[dim](verify if unsure)[/dim]")

    ein = re.sub(r"\D", "", str(ein))
    data = downloader.get_json(PROPUBLICA_ORG.format(ein=ein))
    if not data:
        return
    filings = (data.get("filings_with_data") or []) + \
              (data.get("filings_without_data") or [])

    queued = 0
    for f in filings:
        pdf = f.get("pdf_url")
        if not pdf:
            continue
        yr = f.get("tax_prd_yr") or f.get("tax_prd")
        try:
            yr_int: Optional[int] = int(str(yr)[:4])
        except (TypeError, ValueError):
            yr_int = None
        if yr_int and yr_int < oldest:
            continue
        if frontier.add(WorkItem(pdf, source="990", org=org["name"], year=yr_int)):
            queued += 1
    console.print(f"  ProPublica: queued {queued} Form 990 PDF(s)")


def seed_wayback(org: dict, frontier: URLFrontier, downloader: Downloader,
                 cfg: Config) -> None:
    """Enqueue every archived PDF on the domain within the lookback window."""
    oldest, this_year = cfg.window_years()
    domain = org["domain"]
    params = [
        ("url", f"{domain}*"),
        ("filter", "mimetype:application/pdf"),
        ("filter", "statuscode:200"),
        ("from", f"{oldest}0101"),
        ("to", f"{this_year}1231"),
        ("output", "json"),
        ("collapse", "urlkey"),
        # Only the two fields we use -- CDX payloads for big domains are huge.
        ("fl", "timestamp,original"),
    ]
    rows = downloader.get_json(WAYBACK_CDX, params=params)
    if rows is None:
        console.print(f"[yellow]Wayback: bad response for {domain}[/yellow]")
        return
    if not rows or len(rows) < 2:
        console.print(f"  Wayback: nothing archived for {domain}")
        return

    header, *records = rows
    idx = {name: i for i, name in enumerate(header)}
    queued = 0
    for rec in records:
        ts = rec[idx["timestamp"]]
        original = rec[idx["original"]]
        if not cfg.all_pdfs and not any(k in original.lower() for k in REPORT_KEYWORDS):
            continue
        # `id_` returns the raw archived file with no Wayback wrapper.
        archived = f"https://web.archive.org/web/{ts}id_/{original}"
        if frontier.add(WorkItem(archived, source="wayback", org=org["name"],
                                 year=int(ts[:4]))):
            queued += 1
    console.print(f"  Wayback: queued {queued} archived PDF(s)")


# ===========================================================================
# Crawler -> BFS -- the orchestrator that ties every component together
# ===========================================================================
class Crawler:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.url_seen = URLSeen()
        self.frontier = URLFrontier(self.url_seen)
        self.downloader = Downloader(cfg)
        self.dns = DNSResolver()
        self.parser = ContentParser()
        self.robots = Robots(self.downloader)
        self.content_seen = ContentSeen()
        self.storage = ContentStorage(cfg.output_dir)
        self.extractor = URLExtractor()
        self._filters: dict[str, URLFilter] = {}   # org -> live-crawl filter

    # ---- per-org seeding ---------------------------------------------------
    def seed_org(self, org: dict) -> None:
        console.print(f"[bold cyan]{org['name']}[/bold cyan] ({org.get('domain') or 'no domain'})")
        if "990" in self.cfg.sources:
            seed_propublica(org, self.frontier, self.downloader, self.cfg)
        if not org.get("domain"):
            return  # wayback + live both need a domain; 990 can run via EIN/name
        if "wayback" in self.cfg.sources:
            seed_wayback(org, self.frontier, self.downloader, self.cfg)
        if "live" in self.cfg.sources:
            self._filters[org["name"]] = URLFilter(org["domain"], self.cfg.all_pdfs)
            self.frontier.add(WorkItem(f"https://{org['domain']}/", source="live",
                                       org=org["name"], depth=0, expand=True))

    # ---- the BFS loop --------------------------------------------------------
    def run(self) -> Optional[pd.DataFrame]:
        pages_seen: dict[str, int] = {}   # org -> live pages fetched (breadth cap)
        pbar = tqdm(desc="crawling", unit="url")

        try:
            while len(self.frontier):
                item = self.frontier.next()
                pbar.update(1)
                self._process(item, pages_seen)
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted -- writing partial manifest.[/yellow]")
        finally:
            pbar.close()

        return self.storage.write_manifest()

    def _process(self, item: WorkItem, pages_seen: dict[str, int]) -> None:
        url = item.url

        # Links-only: known PDF URLs (990/wayback seeds, or .pdf links found on
        # the live crawl) go straight to the manifest -- no fetch needed.
        if self.cfg.links_only and (item.source in ("990", "wayback")
                                    or URLFilter.is_pdf_url(url)):
            self.storage.record_link(org=item.org, source=item.source,
                                     year=item.year, url=url)
            return

        # live-crawl breadth cap + robots (only enforced for the live crawl).
        # Robots runs BEFORE the counter, so disallowed URLs don't eat the
        # page budget for pages we never fetch.
        if item.source == "live":
            if pages_seen.get(item.org, 0) >= self.cfg.max_pages_per_site:
                return
            if not self.robots.allowed(url):
                return
            pages_seen[item.org] = pages_seen.get(item.org, 0) + 1

        # DNS gate (skip hosts that don't resolve)
        if self.dns.resolve(urlparse(url).netloc) is None:
            return

        resp = self.downloader.get(url)
        if resp is None:
            return

        # If we were redirected, mark the final URL as seen too, so the same
        # page isn't fetched again when discovered under its canonical URL.
        if resp.url != url:
            self.url_seen.add(resp.url)

        ctype = resp.headers.get("Content-Type", "").lower()
        body = resp.content
        is_pdf = "application/pdf" in ctype or body[:5] == b"%PDF-"

        if is_pdf:
            if self.cfg.links_only:
                # A live URL with no .pdf extension that turned out to be a PDF.
                self.storage.record_link(org=item.org, source=item.source,
                                         year=item.year, url=item.url)
            else:
                self._handle_pdf(item, body)
            return

        # HTML: only the live crawl expands further
        if item.expand and item.depth < self.cfg.max_depth and "html" in ctype:
            self._expand_html(item, resp)

    # ---- handlers --------------------------------------------------------------
    def _handle_pdf(self, item: WorkItem, body: bytes) -> None:
        if not self.content_seen.is_new(body):
            return   # identical file already saved (e.g. live + wayback dupe)
        n_pages, text_len, ocr_used = self.parser.pdf_info(body, do_ocr=self.cfg.do_ocr)
        self.storage.save_pdf(body, org=item.org, source=item.source,
                              year=item.year, url=item.url,
                              n_pages=n_pages, text_len=text_len, ocr_used=ocr_used)

    def _expand_html(self, item: WorkItem, resp: requests.Response) -> None:
        soup = self.parser.parse_html(resp.content, resp.url)
        # One filter per org (built at seed time); fall back to the current
        # host if a redirect landed us somewhere we didn't seed.
        flt = self._filters.get(item.org) or URLFilter(urlparse(resp.url).netloc,
                                                       self.cfg.all_pdfs)
        for link in self.extractor.extract(soup, resp.url):
            if flt.is_pdf_url(link):
                if flt.keep_pdf(link):
                    # Year is unknown for live finds -- leave undated instead of
                    # stamping today's year on a possibly-old report.
                    self.frontier.add(WorkItem(link, source="live", org=item.org,
                                               depth=item.depth + 1))
            elif flt.follow_html(link):
                self.frontier.add(WorkItem(link, source="live", org=item.org,
                                           depth=item.depth + 1, expand=True))


# ===========================================================================
# Programmatic entry point -- used by the questionary UI (spider_UI.py)
# ===========================================================================
def _row_to_org(row: pd.Series) -> dict:
    """Turn a DataFrame row (name, domain, ein) into the dict the crawler wants.

    pandas fills missing cells with NaN; convert those (and blanks) to None so
    seed_propublica's `org.get("ein")` falls through to search-by-name, and
    strip any scheme/trailing slash off the domain.
    """
    def val(key: str) -> Optional[str]:
        if key not in row.index:
            return None
        v = row[key]
        if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NA:
            return None
        s = str(v).strip()
        return s or None

    domain = re.sub(r"^https?://", "", val("domain") or "", flags=re.I).strip("/").lower()
    # Fall back to the domain when no name column is present, so each org still
    # gets its own folder/manifest label instead of collapsing into "org".
    return {"name": val("name") or domain or "", "domain": domain, "ein": val("ein")}


def populate_data(orgs_df: Optional[pd.DataFrame], out_dir: str, sources,
                  years: int = 20, depth: int = 3,
                  start_row: int = 0, end_row: Optional[int] = None, *,
                  all_pdfs: bool = False, do_ocr: bool = False,
                  links_only: bool = True) -> Optional[pd.DataFrame]:
    """Run the crawler over a slice of an orgs DataFrame; return the manifest df.

    Same signature as before, but configuration now flows through a Config
    object instead of mutating module globals -- so concurrent/repeated calls
    can't stomp on each other's settings.
    """
    if orgs_df is None or len(orgs_df) == 0:
        console.print("[yellow]No orgs to process.[/yellow]")
        return None

    n = len(orgs_df)
    start = max(0, int(start_row))
    end = n if end_row is None else min(int(end_row), n)
    rows = orgs_df.iloc[start:end]
    if rows.empty:
        console.print(f"[yellow]Row range {start}:{end} is empty -- nothing to do.[/yellow]")
        return None

    cfg = replace(Config.from_env(), years_back=years, max_depth=depth,
                  output_dir=out_dir, sources=tuple(sources),
                  all_pdfs=all_pdfs, do_ocr=do_ocr, links_only=links_only)

    oldest, this_year = cfg.window_years()
    console.print(f"[bold]Collecting {oldest}-{this_year} for rows {start}:{end} "
                  f"from sources: {', '.join(cfg.sources)}[/bold]")

    crawler = Crawler(cfg)
    for _, row in rows.iterrows():
        org = _row_to_org(row)
        if not org["domain"] and ("live" in cfg.sources or "wayback" in cfg.sources):
            console.print(f"[yellow]'{org['name'] or '?'}' has no domain -- "
                          f"only the 990 source can run for it.[/yellow]")
        crawler.seed_org(org)

    return crawler.run()


# ===========================================================================
# CLI
# ===========================================================================
SAMPLE = [
    {"name": "Carnegie Hall", "domain": "carnegiehall.org", "ein": "131923635"},
    {"name": "Whitney Museum of American Art", "domain": "whitney.org"},
]


def main() -> None:
    env_cfg = Config.from_env()
    ap = argparse.ArgumentParser(
        description="Collect nonprofit annual reports (990s + published PDFs) over ~20 years.")
    ap.add_argument("--orgs", help="Path to orgs JSON file")
    ap.add_argument("--out", default=env_cfg.output_dir, help="Output directory")
    ap.add_argument("--years", type=int, default=env_cfg.years_back,
                    help="Lookback window in years")
    ap.add_argument("--sources", default="990,wayback,live",
                    help="Comma list of sources to run: 990,wayback,live")
    ap.add_argument("--depth", type=int, default=env_cfg.max_depth,
                    help="Live-crawl link depth")
    ap.add_argument("--all-pdfs", action="store_true",
                    help="Keep every PDF, not just ones whose URL looks like a report")
    ap.add_argument("--download", action="store_true",
                    help="Download and save the PDFs instead of only collecting links")
    ap.add_argument("--ocr", action="store_true",
                    help="OCR scanned PDFs (needs ocrmypdf+tesseract)")
    ap.add_argument("--sample", action="store_true",
                    help="Write a sample orgs.json and exit")
    args = ap.parse_args()

    if args.sample:
        with open("orgs.json", "w") as f:
            json.dump(SAMPLE, f, indent=2)
        console.print("[green]Wrote sample orgs.json[/green]")
        return

    if not args.orgs:
        ap.error("--orgs is required (or use --sample to generate one)")

    if args.ocr and not OCR_AVAILABLE:
        console.print("[yellow]--ocr requested but ocrmypdf is not installed; "
                      "continuing without OCR.[/yellow]")

    with open(args.orgs) as f:
        orgs = json.load(f)

    sources = tuple(s.strip() for s in args.sources.split(",") if s.strip())
    unknown = set(sources) - {"990", "wayback", "live"}
    if unknown:
        ap.error(f"unknown source(s): {', '.join(sorted(unknown))}")

    cfg = replace(env_cfg, years_back=args.years, max_depth=args.depth,
                  output_dir=args.out, sources=sources, all_pdfs=args.all_pdfs,
                  do_ocr=args.ocr, links_only=not args.download)

    oldest, this_year = cfg.window_years()
    console.print(f"[bold]Collecting {oldest}-{this_year} "
                  f"from sources: {', '.join(sources)}[/bold]\n")

    crawler = Crawler(cfg)
    for org in orgs:
        org.setdefault("name", org.get("domain", ""))
        crawler.seed_org(org)

    df = crawler.run()
    if df is not None:
        console.print(f"\n[green]Done. {len(df)} documents.[/green] "
                      f"Manifest: {os.path.join(cfg.output_dir, 'manifest.csv')}")
        cols = [c for c in ("org", "source", "year", "pages", "saved_path", "url")
                if c in df.columns]
        console.print(df[cols].to_string(index=False))


if __name__ == "__main__":
    main()