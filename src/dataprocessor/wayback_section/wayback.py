#!/usr/bin/env python3
"""
wayback.py
==========

Collect nonprofit "annual reports" from the Internet Archive -- by default the
whole of it, 1996 to now -- and drop everything into one manifest.

This is the Wayback half of what used to live inside spider.py. The split runs
along the axis that actually matters operationally: the live crawler talks to
hundreds of independent nonprofit hosts and can go as wide as it likes, while
everything in here talks to exactly ONE host -- web.archive.org -- so the whole
run shares a single request budget. Mixing the two in one process made that
budget impossible to state, let alone hold to: per-host politeness handed the
archive the same allowance every small nonprofit got, and parallelism across
orgs quietly multiplied it.

So the rate limit here is global and explicit: 50 requests per minute, paced
at one request every 1.2s and hard-capped over any rolling 60-second window
(see RateLimiter -- the window is what stops a burst at a window boundary from
landing 100 requests in two seconds). A 429 or 503 from the archive pauses
EVERY worker, not just the one that got it.

The aim is to sit AT 50/min whenever there is work, never above. Two things
are needed for that, and only one of them is the limiter:

  * the limiter banks a few unclaimed slots (WaybackConfig.burst), so a worker
    that was busy when its slot came up can still make the request a moment
    later rather than losing it forever. Rigid pacing measures a couple of
    requests per minute short for exactly this reason.
  * there have to be enough requests IN FLIGHT to spend the budget at all.
    That is Little's law, and it is usually the binding constraint: a 20s CDX
    query with 4 workers yields 12 requests/min no matter how generous the
    limit is. WaybackConfig.effective_workers sizes the pool from the budget
    instead of guessing, which is why the default is ~17 threads and not 4.
    They are blocked on sockets, not CPU, and the limiter still caps the rate.

The end-of-run line reports the rate actually achieved, so a starved run is
visible rather than silent.

Per org, three passes over the CDX index:

  1. documents -- application/pdf by default, or every format in
     DOCUMENT_MIMETYPES with --all-formats
  2. xml       -- text/xml and application/xml whose URL says "report" or
                  "annual". Its own pass because the archive's XML is mostly
                  sitemaps and feeds, so it has to be narrowed by URL in a way
                  the other document types don't need
  3. pages     -- text/html whose URL contains "report", which is where the
                  early-2000s reports live (lssmn.org/2002_annual_report.htm
                  is an annual report that only ever existed as HTML)

Whatever comes back is recorded in manifest_wayback.csv, next to (and never
merged with) the live crawler's manifest_live.csv. A report that exists in both
is deliberately recorded twice -- the live row carries a URL that may since
have died, and the snapshot is the copy that is guaranteed to still resolve.
Collapse the two on your own terms downstream.

By default this runs in LINKS-ONLY mode: it records each snapshot's URL rather
than downloading it, which costs 2 requests per org instead of 2 + one per
report. Pass --download (CLI) or links_only=False (populate_data) to fetch and
save the files themselves.

The generic report-URL heuristics, storage and content sniffing are shared with
the live crawler and imported from spider.py; what lives here is everything
that knows the archive exists.

Input: the same orgs CSV the spider takes (columns: name, domain, ein).

Run:
  python wayback.py --orgs orgs.csv --out ./reports   # 1996-now
  python wayback.py --orgs orgs.csv --download        # save the files too
  python wayback.py --orgs orgs.csv --rpm 30          # be gentler still
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import math
import os
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from email.utils import parsedate_to_datetime
from typing import Optional

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from rich.console import Console
from tqdm import tqdm

from dataprocessor.config import load_env

# Shared crawl machinery. None of it knows the archive exists -- it is the
# "is this a report?" / "what format is this?" / "where does it go on disk?"
# layer that both collectors need. Keeping one copy means a tweak to the
# report heuristics changes live and archived results together, which is the
# only way the two manifests stay comparable.
from dataprocessor.Spider_section.spider import (
    ContentParser,
    ContentSeen,
    ContentStorage,
    CrawlProgress,
    Downloader,
    URLSeen,
    document_format,
    is_page_report_url,
    is_report_url,
    load_csv,
    sniff_format,
)
from dataprocessor.Spider_section.spider import _row_to_org as row_to_org

load_env()
console = Console()
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
log = logging.getLogger("wayback")

CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"

# The Internet Archive's first captures. Asking CDX for anything earlier is
# meaningless, so this is the floor the lookback window clamps to.
ARCHIVE_EPOCH = 1996
ARCHIVE_HOST = "web.archive.org"

# The manifest's `source` column, and the on-disk folder every saved file lands
# in. Matches what spider.py used to write for archived rows, so manifests from
# before the split still line up with new ones.
SOURCE = "wayback"
MANIFEST_NAME = "manifest_wayback.csv"

# Server-side urlkey pre-filter for the HTML and XML passes (urlkey is CDX's
# lowercased, canonicalized URL, so no case handling is needed). This is not an
# optimization: a domain's PDFs are few enough to filter client-side, but its
# *pages* are the site's entire history, and that payload runs into the
# downloader's size cap and loses the org's HTML seeds entirely.
#
# These stems are deliberately LOOSER than the client-side keyword lists, for
# two reasons. The urlkey keeps its separators, so "yearinreview" as spelled in
# REPORT_KEYWORDS would not match "year-in-review" in the index -- matching the
# stem "review" does. And precision is not this filter's job: everything that
# comes back is still put through is_report_url / is_page_report_url. The only
# requirement is that it never rejects a row the client-side test would keep.
#
# Costs recall in one known way: a report named org+year with none of these
# stems in its URL is not found in the archive. The live crawl still finds it
# if the site still serves it.
_URLKEY_STEMS = (
    "report", "annual", "review", "yearbook", "almanac", "stewardship",
    "glance", "yearend", "yearin",
    "annrep", "annrpt", "anrep", "anrpt", "anlrep", "anlrpt", "arept", "arpt",
)
_URLKEY_REGEX = ".*(" + "|".join(_URLKEY_STEMS) + ").*"

HTML_URLKEY_REGEX = _URLKEY_REGEX

# XML is swept in a pass of its own -- see _passes.
XML_MIMETYPE_FILTER = "(text/xml|application/xml)"
XML_URLKEY_REGEX = _URLKEY_REGEX

# CDX mimetypes for the document pass, listed explicitly rather than as a
# vnd.* wildcard, which also drags in application/vnd.ms-fontobject (.eot
# fonts). Mirrors DOCUMENT_EXTENSIONS in spider.py.
DOCUMENT_MIMETYPES = (
    "application/pdf",
    "application/msword",
    "application/rtf", "text/rtf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.presentation",
    "application/epub+zip",
)
# XML is deliberately absent HERE but still collected -- it gets its own
# urlkey-narrowed pass (see XML_MIMETYPE_FILTER), because joining this
# unnarrowed alternation would pull in every sitemap snapshot on the domain.
#
# text/plain and text/csv are the ones genuinely left out: like XML they are
# among the highest-volume types in the archive, but unlike XML there is no
# era in which an annual report was normally a .txt, so the pass they would
# need isn't worth a request per org. The live crawl still collects them.

# What each archived mimetype means, for rows whose URL has no extension.
FORMAT_FOR_MIMETYPE = {
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/rtf": "rtf", "text/rtf": "rtf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.oasis.opendocument.presentation": "odp",
    "application/epub+zip": "epub",
    "text/plain": "txt", "text/csv": "csv",
    "text/xml": "xml", "application/xml": "xml",
}


def _env_flag(name: str, default: bool) -> bool:
    """Read a boolean .env override; unset keeps the default."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


# ===========================================================================
# Config
# ===========================================================================
@dataclass(frozen=True)
class WaybackConfig:
    user_agent: str = "nonprofit-reports/1.2 (+research; contact you@example.com)"

    # 0 means the whole archive, 1996 to now -- see window_years. It is the
    # default because the window filters on CAPTURE date, not publication
    # date, so a narrow one silently drops exactly what this tool exists to
    # find: a 1999 report last captured in 2004 is invisible to a 20-year
    # window, and only survives one if the org happened to re-upload it later.
    years_back: int = 0

    # THE number that matters. Every request this module makes -- CDX listings
    # and snapshot fetches alike -- goes to web.archive.org, so this is the
    # whole run's budget, not a per-host one. 50/min is comfortably under what
    # the archive starts refusing at, and leaves headroom for the redirects a
    # snapshot fetch can trigger (see ArchiveDownloader).
    requests_per_minute: int = 50

    # Slots of credit the limiter may bank while nothing is asking for one.
    # 0 is rigid 1.2s pacing: perfectly smooth, but a slot nobody claimed is
    # gone forever, so a run that stalls on one slow response never catches
    # back up and drifts to 47-48/min. A small allowance lets that many
    # requests go back-to-back to refill the pipe, which is what pins the rate
    # at the ceiling instead of just below it. The rolling-window cap still
    # applies on top, so a burst can never put us over the promised rate.
    burst: int = 5

    # A CDX listing for a 20-year domain is a slow query on the archive's side;
    # 30s (the live crawler's timeout) times out on the bigger ones.
    request_timeout: int = 60
    max_bytes: int = 50 * 1024 * 1024   # per-response download cap (50 MB)

    # How many times one URL is re-attempted after a throttle/transient error
    # before it is given up on. Each attempt costs a slot in the budget.
    max_retries: int = 4
    # Fallback pause when the archive throttles us without a Retry-After.
    cooldown_seconds: float = 60.0
    # Ceiling on an honoured Retry-After, so one absurd header can't park the
    # whole run for an hour.
    max_cooldown_seconds: float = 300.0

    output_dir: str = "./reports"
    all_pdfs: bool = False
    # Collect reports published as web pages too (see is_page_report_url), not
    # just documents. False = documents only.
    html_reports: bool = True
    # Widen the document pass past PDF to every format in DOCUMENT_MIMETYPES.
    # Off by default because it is measurably expensive: CDX matches the
    # mimetype alternation against every record in the domain's index, rather
    # than taking the fast path an equality filter gets. Measured on lssmn.org,
    # 86.9s / 1073 rows for the alternation vs 23.7s / 1008 rows for plain
    # equality on application/pdf -- 3.7x the time for 6.4% more documents.
    all_formats: bool = False
    do_ocr: bool = False
    links_only: bool = True

    # Orgs worked on at once. 0 means "derive it from the budget" -- see
    # effective_workers, which is where the reasoning lives. This never raises
    # the request rate; the limiter is global and every worker queues behind
    # it. Too FEW workers is the thing that silently costs throughput.
    max_workers: int = 0

    # Assumed mean round-trip for an archive request, used to size the worker
    # count. CDX listings dominate in links-only mode and run tens of seconds;
    # snapshot fetches are far quicker. Errs high on purpose: a surplus worker
    # just blocks on the limiter, costing a socket and nothing else, while a
    # missing one costs requests we were entitled to make.
    assumed_latency: float = 20.0

    @property
    def request_delay(self) -> float:
        """Even spacing implied by the per-minute budget, in seconds.

        Also what Downloader falls back to if it is ever built without a
        limiter, so that path can't silently run unthrottled.
        """
        return 60.0 / max(1, self.requests_per_minute)

    @property
    def effective_workers(self) -> int:
        """How many requests must be in flight to actually spend the budget.

        Little's law: a budget of R requests/second whose mean round-trip is L
        seconds is only reachable with R*L requests outstanding. At 50/min
        against a 20s CDX query that is ~17 -- which is why a 4-worker run
        tops out near 12/min however generous the limiter is, and why "make it
        faster" is nearly always a concurrency question rather than a rate
        one. These threads are blocked on a socket, not burning CPU.
        """
        if self.max_workers > 0:
            return self.max_workers
        need = self.requests_per_minute / 60.0 * self.assumed_latency
        return max(2, min(32, math.ceil(need)))

    @classmethod
    def from_env(cls) -> "WaybackConfig":
        """Environment (.env) overrides. USER_AGENT and YEARS_BACK are shared
        with the live crawler; the rest are WAYBACK_-prefixed so tuning the
        archive's budget can't move the live crawl's settings."""
        return cls(
            user_agent=os.getenv("USER_AGENT", cls.user_agent),
            years_back=int(os.getenv("YEARS_BACK", cls.years_back)),
            requests_per_minute=int(os.getenv("WAYBACK_RPM",
                                              cls.requests_per_minute)),
            burst=int(os.getenv("WAYBACK_BURST", cls.burst)),
            request_timeout=int(os.getenv("WAYBACK_TIMEOUT", cls.request_timeout)),
            max_workers=int(os.getenv("WAYBACK_WORKERS", cls.max_workers)),
            max_bytes=int(os.getenv("MAX_BYTES", cls.max_bytes)),
            output_dir=os.getenv("OUTPUT_DIR", cls.output_dir),
            html_reports=_env_flag("HTML_REPORTS", cls.html_reports),
            all_formats=_env_flag("ARCHIVE_ALL_FORMATS", cls.all_formats),
        )

    def window_years(self) -> tuple[int, int]:
        """(oldest_year, this_year) for the configured lookback.

        Anchored to ARCHIVE_EPOCH rather than counted back from today, so the
        default window doesn't quietly slide forward a year every January.
        """
        this_year = dt.date.today().year
        if self.years_back <= 0:
            return ARCHIVE_EPOCH, this_year
        return max(ARCHIVE_EPOCH, this_year - self.years_back), this_year


# ===========================================================================
# Rate limiter -- the whole point of this module living apart from spider.py
# ===========================================================================
class RateLimiter:
    """One global budget for web.archive.org, shared by every worker.

    The goal is to sit AT the promised rate whenever there is work, and never
    above it. Those are two different problems, so there are two rules:

      * a rolling window -- at most `rpm` requests in any 60 seconds. This is
        the hard invariant and the only thing that guarantees we keep the
        promise; retries, redirects and a lucky run of fast responses all pass
        through it.
      * paced release with a burst allowance -- the sustained rate is one
        request per 60/rpm seconds, but up to `burst` unclaimed slots may be
        banked and spent back-to-back.

    The burst allowance is what makes the difference between 47/min and 50.
    Under rigid pacing every slot nobody was ready to claim is lost for good:
    a worker that spent 3 seconds parsing a huge CDX payload misses two slots
    and there is no way to make them up, so the measured rate sits a little
    under the ceiling forever. Banking a few slots lets the run absorb that
    jitter and settle back onto the line. The window cap sits above it, so the
    catch-up can never turn into an overrun.

    Threading: the lock is held only while inspecting and stamping the
    bookkeeping, never across the sleep. A worker that has taken its slot goes
    off to spend seconds on an HTTP request, and the next worker must be able
    to claim the following slot meanwhile -- that overlap is the only reason
    workers help at all. Sleepers re-check under the lock when they wake, so
    the cap holds even when several are released at once.
    """

    def __init__(self, requests_per_minute: int = 50, window: float = 60.0,
                 burst: int = 5) -> None:
        self.rpm = max(1, int(requests_per_minute))
        self.window = window
        self.min_gap = window / self.rpm
        self.burst = max(0, int(burst))
        self._lock = threading.Lock()
        self._hits: deque[float] = deque()   # request times inside the window
        # Next time the paced schedule wants a request, in the GCRA sense:
        # each accepted request pushes it out by one gap, and a run of idle
        # time lets `now` fall behind it by at most burst*min_gap.
        self._next: float = 0.0
        self._cooldown_until: float = 0.0
        self.total = 0                       # requests made, for reporting
        self.throttled = 0                   # times the archive pushed back

    def wait(self, host: str = ARCHIVE_HOST) -> None:
        """Block until this thread may make one request, then record it.

        Signature matches spider's HostThrottle so a Downloader takes either;
        `host` is ignored because there is only ever one host here.
        """
        while True:
            with self._lock:
                now = time.monotonic()
                # Drop hits that have aged out of the window.
                while self._hits and now - self._hits[0] >= self.window:
                    self._hits.popleft()

                sleep_for = 0.0
                if now < self._cooldown_until:          # archive pushed back
                    sleep_for = self._cooldown_until - now
                else:
                    # Hard cap. Wait for the oldest hit to fall out.
                    if len(self._hits) >= self.rpm:
                        sleep_for = self.window - (now - self._hits[0])
                    # Pacing, minus whatever credit has been banked.
                    earliest = self._next - self.burst * self.min_gap
                    sleep_for = max(sleep_for, earliest - now)

                if sleep_for <= 0:
                    self._hits.append(now)
                    # max(now, ...) stops credit accruing without bound while
                    # nothing is asking -- an hour idle still only banks
                    # `burst` slots, not an hour's worth.
                    self._next = max(now, self._next) + self.min_gap
                    self.total += 1
                    return
            # Sleep OUTSIDE the lock, then loop and re-check: another thread
            # may have taken the slot we were waiting for.
            time.sleep(min(sleep_for, self.window))

    def cooldown(self, seconds: float) -> None:
        """Pause every worker for `seconds` -- the archive asked us to stop.

        Global on purpose: a 429 is about the run's aggregate rate, so backing
        off only the thread that happened to receive it just hands the same
        429 to the next worker a moment later.
        """
        if seconds <= 0:
            return
        with self._lock:
            self.throttled += 1
            self._cooldown_until = max(self._cooldown_until,
                                       time.monotonic() + seconds)

    def stats(self) -> dict:
        """Point-in-time copy for whatever is rendering progress."""
        with self._lock:
            now = time.monotonic()
            recent = sum(1 for t in self._hits if now - t < self.window)
            paused = max(0.0, self._cooldown_until - now)
            return {"total": self.total, "rpm_limit": self.rpm,
                    "in_window": recent, "throttled": self.throttled,
                    "paused_for": paused}


# ===========================================================================
# Downloader -- Downloader + archive-aware throttling
# ===========================================================================
class ArchiveDownloader(Downloader):
    """Fetches from web.archive.org, treating throttling as a run-wide signal.

    The base Downloader hands 429s to urllib3's Retry, which re-sends inside
    this thread, invisibly, while every other worker carries on at full rate --
    exactly backwards for a single shared host. Retries are handled here
    instead so a push-back slows the whole run down (see RateLimiter.cooldown).
    """

    # 403 is not here on purpose: the archive returns it for snapshots it has
    # excluded, and no amount of waiting will change that.
    RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

    def __init__(self, cfg: WaybackConfig, limiter: RateLimiter) -> None:
        super().__init__(cfg, throttle=limiter)   # type: ignore[arg-type]
        self.cfg = cfg
        self.limiter = limiter
        # Drop the session-level retry the base class installs, so the only
        # retry logic in play is the one below that tells the limiter.
        adapter = HTTPAdapter(max_retries=0)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def get(self, url: str, params=None) -> Optional[requests.Response]:
        """One rate-limited GET, with archive-aware retries.

        NOTE: each attempt claims its own slot in the budget, because each
        attempt really is a request. Redirects (a snapshot URL bouncing to the
        nearest capture) do not, which is part of why the default rpm leaves
        headroom under the archive's actual ceiling rather than sitting on it.
        """
        for attempt in range(self.cfg.max_retries):
            self.limiter.wait(ARCHIVE_HOST)
            try:
                r = self.session.get(url, params=params,
                                     timeout=self.cfg.request_timeout,
                                     allow_redirects=True, stream=True)
            except requests.RequestException as e:
                # Connection-level failure: back off locally and try again.
                # Not a cooldown -- nothing says the archive is overloaded.
                log.warning("request failed %s: %s", url, e)
                time.sleep(min(2.0 * (attempt + 1), 10.0))
                continue

            status = r.status_code
            if status in self.RETRY_STATUSES:
                pause = self._retry_after(r)
                if pause is None:
                    pause = self.cfg.cooldown_seconds * (attempt + 1)
                pause = min(pause, self.cfg.max_cooldown_seconds)
                r.close()
                console.print(f"[yellow]Archive returned {status}; pausing all "
                              f"workers for {pause:.0f}s[/yellow]")
                self.limiter.cooldown(pause)
                continue
            if status >= 400:
                log.warning("archive returned %s for %s", status, url)
                r.close()
                return None

            body = self._read_capped(r, url)
            if body is None:
                return None
            r._content = body         # backs .content/.text/.json()
            return r

        log.warning("giving up on %s after %d attempts", url, self.cfg.max_retries)
        return None

    def _read_capped(self, r: requests.Response, url: str) -> Optional[bytes]:
        """Stream the body with a size cap, so one enormous or mislabeled
        capture can't blow up memory."""
        limit = self.cfg.max_bytes
        # Trust Content-Length when present to bail early...
        clen = r.headers.get("Content-Length", "")
        if clen.isdigit() and int(clen) > limit:
            log.warning("skipping %s: Content-Length %s > %d-byte cap",
                        url, clen, limit)
            r.close()
            return None
        # ...but enforce the cap while reading regardless (CDX listings carry
        # no length at all, and the archive's replay headers often lie).
        chunks: list[bytes] = []
        size = 0
        try:
            for chunk in r.iter_content(chunk_size=64 * 1024):
                chunks.append(chunk)
                size += len(chunk)
                if size > limit:
                    log.warning("skipping %s: body exceeds %d-byte cap", url, limit)
                    r.close()
                    return None
        except requests.RequestException as e:
            log.warning("read failed %s: %s", url, e)
            r.close()
            return None
        return b"".join(chunks)

    @staticmethod
    def _retry_after(resp: requests.Response) -> Optional[float]:
        """Seconds from a Retry-After header (delta or HTTP-date), or None."""
        raw = (resp.headers.get("Retry-After") or "").strip()
        if not raw:
            return None
        if raw.isdigit():
            return float(raw)
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        return max(0.0, (when - dt.datetime.now(dt.timezone.utc)).total_seconds())


# ===========================================================================
# CDX -- what the archive holds for a domain
# ===========================================================================
@dataclass
class Snapshot:
    url: str          # the replay URL we would actually fetch
    original: str     # the URL as it was on the live site, when captured
    year: int
    fmt: str          # "pdf" | "html" | ... -- what we expect to find


def cdx_rows(downloader: ArchiveDownloader, domain: str, mimetype: str,
             cfg: WaybackConfig, url_regex: Optional[str] = None,
             want_mimetype: bool = False):
    """One CDX listing for a domain, narrowed to a mimetype (or a regex of
    several). `want_mimetype` also returns each row's type, which the document
    pass needs to tell a .doc from a .xls."""
    oldest, this_year = cfg.window_years()
    params = [
        ("url", f"{domain}*"),
        ("filter", f"mimetype:{mimetype}"),
        ("filter", "statuscode:200"),
        ("from", f"{oldest}0101"),
        ("to", f"{this_year}1231"),
        ("output", "json"),
        ("collapse", "urlkey"),
        # Only the fields we use -- CDX payloads for big domains are huge.
        ("fl", "timestamp,mimetype,original" if want_mimetype
               else "timestamp,original"),
    ]
    if url_regex:
        params.append(("filter", f"urlkey:{url_regex}"))
    return downloader.get_json(CDX_ENDPOINT, params=params)


def replay_url(timestamp: str, original: str) -> str:
    """The raw archived file, with no Wayback chrome wrapped around it.

    The `id_` modifier is what makes a snapshot usable as data: without it the
    archive injects its own banner and rewrites links, so an archived HTML
    report comes back as a page *about* the report.
    """
    return f"https://web.archive.org/web/{timestamp}id_/{original}"


@dataclass(frozen=True)
class CdxPass:
    """One CDX listing: which mimetypes to ask for, and how to read the rows."""
    mimetype: str                      # CDX filter -- one type or an alternation
    kind: str                          # "doc" (is_report_url) | "html" (page test)
    default_fmt: str                   # format when the URL carries no extension
    url_regex: Optional[str] = None    # server-side urlkey narrowing
    want_mimetype: bool = False        # ask CDX for each row's type as well


def _passes(cfg: WaybackConfig) -> list[CdxPass]:
    """The CDX listings to run for one domain.

    Each pass is a request against the rate budget, so they are kept few and
    each one is narrowed as hard as the format allows.
    """
    if cfg.all_formats:
        # One pass for every office format via a mimetype alternation, rather
        # than a query each -- see WaybackConfig.all_formats for what it costs.
        doc_filter = "(" + "|".join(m.replace("+", "\\+")
                                    for m in DOCUMENT_MIMETYPES) + ")"
        passes = [CdxPass(doc_filter, "doc", "bin", want_mimetype=True)]
    else:
        # Equality on a single mimetype takes CDX's fast path.
        passes = [CdxPass("application/pdf", "doc", "pdf")]

    # XML gets a pass of its own rather than joining the alternation above,
    # because it is the one document type whose archived volume is dominated by
    # things that are never reports: every sitemap.xml, rss.xml and feed.xml
    # snapshot across 20 years. Unnarrowed it would swamp the document pass and
    # run into the downloader's size cap. Narrowed by urlkey it costs one small
    # listing and still finds annual_report_2003.xml -- Word 2003's "Save As
    # XML", which is a real format for reports of that era.
    passes.append(CdxPass(XML_MIMETYPE_FILTER, "doc", "xml",
                          url_regex=XML_URLKEY_REGEX))

    if cfg.html_reports:
        passes.append(CdxPass("text/html", "html", "html",
                              url_regex=HTML_URLKEY_REGEX))
    return passes


def find_snapshots(org: dict, downloader: ArchiveDownloader,
                   cfg: WaybackConfig) -> list[Snapshot]:
    """Every archived report for one org's domain, inside the lookback window.

    Returns them in the order CDX listed them, deduped on the replay URL.
    """
    domain = org["domain"]
    seen = URLSeen()
    found: list[Snapshot] = []
    for p in _passes(cfg):
        rows = cdx_rows(downloader, domain, p.mimetype, cfg, p.url_regex,
                        want_mimetype=p.want_mimetype)
        if rows is None:
            console.print(f"[yellow]Wayback: no {p.kind} response for "
                          f"{domain}[/yellow]")
            continue
        if not rows or len(rows) < 2:
            continue
        header, *records = rows
        idx = {name: i for i, name in enumerate(header)}
        for rec in records:
            ts = rec[idx["timestamp"]]
            original = rec[idx["original"]]
            if p.kind == "doc":
                keep = cfg.all_pdfs or is_report_url(original, org.get("name"))
                # Prefer the URL's extension, which is more specific than the
                # mimetype (text/xml covers both a sitemap and a Word 2003
                # doc); fall back to what the archive recorded, or to the
                # pass's own type when every row in it is one format.
                fmt = document_format(original) or (
                    FORMAT_FOR_MIMETYPE.get(rec[idx["mimetype"]], p.default_fmt)
                    if p.want_mimetype else p.default_fmt)
            else:
                # all_pdfs never widens the page test -- a site's whole
                # 20-year history of HTML is not a pile of annual reports.
                keep = is_page_report_url(original, org.get("name"))
                fmt = p.default_fmt
            if not keep:
                continue
            url = replay_url(ts, original)
            if seen.add(url):
                found.append(Snapshot(url=url, original=original,
                                      year=int(ts[:4]), fmt=fmt))
    return found


# ===========================================================================
# Collector -- snapshots to manifest rows
# ===========================================================================
class ArchiveCollector:
    """Collects one org at a time. Cheap to build: everything expensive (the
    limiter, the manifest) is passed in and shared across workers."""

    def __init__(self, cfg: WaybackConfig, storage: ContentStorage,
                 limiter: RateLimiter) -> None:
        self.cfg = cfg
        self.storage = storage
        self.downloader = ArchiveDownloader(cfg, limiter)
        self.parser = ContentParser()

    def collect(self, org: dict, pbar=None) -> int:
        """Find and record every archived report for one org. Returns the
        number of documents recorded."""
        row = org.get("row")
        label = "" if row is None else f"[dim]row {row}[/dim] "
        console.print(f"[dim]wayback[/dim] {label}[bold cyan]{org['name']}[/bold cyan] "
                      f"({org.get('domain') or 'no domain'})")
        if not org.get("domain"):
            console.print(f"[yellow]'{org['name'] or '?'}' has no domain -- skipping.[/yellow]")
            return 0

        snapshots = find_snapshots(org, self.downloader, self.cfg)
        if not snapshots:
            console.print(f"  Wayback: nothing archived for {org['domain']}")
            return 0

        breakdown: dict[str, int] = {}
        for snap in snapshots:
            breakdown[snap.fmt] = breakdown.get(snap.fmt, 0) + 1
        detail = ", ".join(f"{n} {f}" for f, n in
                           sorted(breakdown.items(), key=lambda kv: -kv[1]))
        console.print(f"  Wayback: found {len(snapshots)} archived "
                      f"document(s) [dim]({detail})[/dim]")

        # Per-org, matching the live crawler: this dedupes the same file
        # captured at several URLs on one domain, which is the case that
        # actually happens. Keeping it per-org also keeps it single-threaded,
        # so it needs no lock.
        content_seen = ContentSeen()
        recorded = 0
        for snap in snapshots:
            if self._record(org, snap, content_seen):
                recorded += 1
            if pbar is not None:
                pbar.update(1)
        return recorded

    def _record(self, org: dict, snap: Snapshot, content_seen: ContentSeen) -> bool:
        # Links-only: the CDX row already told us this is a report of a known
        # format at a URL the archive served with a 200. There is nothing a
        # fetch would add, so skip it -- that is the difference between two
        # requests per org and two plus one per report.
        if self.cfg.links_only:
            self.storage.record_link(org=org["name"],
                                     domain=org.get("domain") or "",
                                     source=SOURCE, year=snap.year,
                                     url=snap.url, fmt=snap.fmt)
            return True

        resp = self.downloader.get(snap.url)
        if resp is None:
            return False
        body = resp.content
        # Content decides the format, not the URL: CMS download handlers
        # (/index.cfm?fuseaction=download&id=9) carry no extension at all, and
        # the servers of that era routinely mislabeled a .doc as text/html.
        # Nothing recognizable means we fell back to a page -- often the
        # archive's own "not captured" notice, which is worth keeping as the
        # html it is rather than saving under a .pdf that won't open.
        fmt = sniff_format(body, snap.url) or "html"
        if not content_seen.is_new(body):
            return False   # byte-identical to something already saved
        n_pages, text_len, ocr_used = self.parser.document_info(
            body, fmt, do_ocr=self.cfg.do_ocr)
        self.storage.save_document(body, org=org["name"],
                                   domain=org.get("domain") or "",
                                   source=SOURCE,
                                   year=snap.year, url=snap.url, fmt=fmt,
                                   n_pages=n_pages, text_len=text_len,
                                   ocr_used=ocr_used)
        return True


# ===========================================================================
# Runner
# ===========================================================================
def run_orgs(cfg: WaybackConfig, orgs: list[dict],
             progress: Optional[CrawlProgress] = None,
             limiter: Optional[RateLimiter] = None) -> Optional[pd.DataFrame]:
    """Work every org, sharing one manifest and one request budget.

    Layout: orgs are handed to a thread pool, and the only objects they share
    are the ContentStorage (which appends under a lock) and the RateLimiter.
    There is no per-org state worth isolating because there is no BFS here --
    an org is a CDX listing and a flat list of URLs.

    The pool is sized to saturate the budget rather than to "go parallel" --
    see WaybackConfig.effective_workers. Fewer workers than that, and the run
    is limited by how long the archive takes to answer instead of by the rate
    we chose; the limiter simply never binds.
    """
    limiter = limiter or RateLimiter(cfg.requests_per_minute, burst=cfg.burst)
    storage = ContentStorage(cfg.output_dir, MANIFEST_NAME)
    started = time.monotonic()
    pbar = tqdm(desc="wayback", unit="doc")
    interrupted = threading.Event()
    # Always track, even with no UI attached: the interrupt handler needs the
    # done/not-done boundary to tell the user where to resume.
    progress = progress or CrawlProgress()
    progress.begin(orgs, 1)

    def work(org: dict) -> None:
        if interrupted.is_set():
            return
        collector = ArchiveCollector(cfg, storage, limiter)
        try:
            with progress.track(org, SOURCE):
                collector.collect(org, pbar)
        except KeyboardInterrupt:
            interrupted.set()
        except Exception as e:           # one bad org must not kill the run
            log.warning("org %r failed: %s", org.get("name"), e)
            console.print(f"[yellow]'{org['name'] or '?'}' failed: {e}[/yellow]")

    # NOTE: the executor is NOT used as a context manager. `with` runs
    # shutdown(wait=True) during exception unwinding, which joins every worker
    # BEFORE the except clause can set `interrupted` -- so Ctrl-C gets
    # swallowed and the whole org list runs to completion. Setting the flag
    # first is what makes the interrupt take effect.
    ex = ThreadPoolExecutor(max_workers=cfg.effective_workers,
                            thread_name_prefix="wayback")
    try:
        list(ex.map(work, orgs))
    except KeyboardInterrupt:
        interrupted.set()
        console.print("\n[yellow]Interrupted -- finishing the orgs already in "
                      "flight, then writing a partial manifest.[/yellow]")
    finally:
        ex.shutdown(wait=True, cancel_futures=True)
        pbar.close()

    df = storage.write_manifest()

    if interrupted.is_set():
        resume = progress.resume_row()
        if resume is not None:
            console.print(f"[bold]Resume with start row {resume}[/bold] "
                          f"[dim](rows at or above it may be incomplete)[/dim]")

    # Report the rate we ACHIEVED, not the one we configured. If this sits
    # well under the ceiling the run was starved of concurrency, not throttled
    # -- more workers (or a lower assumed_latency) is the fix, not a higher
    # rpm, which would change nothing.
    stats = limiter.stats()
    elapsed = max(1e-6, time.monotonic() - started)
    achieved = stats["total"] / elapsed * 60.0
    note = f", {stats['throttled']} throttled" if stats["throttled"] else ""
    console.print(f"[dim]{stats['total']} archive requests in "
                  f"{elapsed:.0f}s -- {achieved:.1f}/min of the "
                  f"{cfg.requests_per_minute}/min budget "
                  f"({cfg.effective_workers} workers{note})[/dim]")
    path = os.path.join(cfg.output_dir, MANIFEST_NAME)
    if os.path.exists(path):
        console.print(f"  [bold]wayback[/bold]: {path}")
    else:
        console.print("  [yellow]wayback: nothing collected[/yellow]")
    return df


def populate_data(orgs_df: Optional[pd.DataFrame], out_dir: str,
                  years: int = 0, start_row: int = 0,
                  end_row: Optional[int] = None, *,
                  all_pdfs: bool = False, do_ocr: bool = False,
                  links_only: bool = True, html_reports: bool = True,
                  all_formats: bool = False,
                  requests_per_minute: Optional[int] = None,
                  burst: Optional[int] = None,
                  max_workers: Optional[int] = None,
                  progress: Optional[CrawlProgress] = None,
                  limiter: Optional[RateLimiter] = None) -> Optional[pd.DataFrame]:
    """Sweep the archive for a slice of an orgs DataFrame; return the manifest.

    Mirrors spider.populate_data so the two are interchangeable from a UI's
    point of view, minus the live-only knobs (depth, sources) and plus the
    rate budget. Pass a `limiter` to watch the request rate while it runs.
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

    cfg = replace(WaybackConfig.from_env(), years_back=years, output_dir=out_dir,
                  all_pdfs=all_pdfs, do_ocr=do_ocr, links_only=links_only,
                  html_reports=html_reports, all_formats=all_formats)
    if requests_per_minute is not None:
        cfg = replace(cfg, requests_per_minute=max(1, int(requests_per_minute)))
    if burst is not None:
        cfg = replace(cfg, burst=max(0, int(burst)))
    # 0 (or None) keeps the budget-derived default -- see effective_workers.
    if max_workers is not None:
        cfg = replace(cfg, max_workers=max(0, int(max_workers)))

    orgs, skipped = [], []
    for offset, (_, row) in enumerate(rows.iterrows()):
        # Absolute CSV position, so a reported row matches the start/end the
        # user typed rather than an offset into the slice.
        org = row_to_org(row, start + offset)
        (orgs if org["domain"] else skipped).append(org)
    for org in skipped:
        console.print(f"[yellow]row {org['row']}: '{org['name'] or '?'}' "
                      f"has no domain -- skipped.[/yellow]")
    if not orgs:
        console.print("[yellow]No orgs with a domain -- nothing to do.[/yellow]")
        return None

    oldest, this_year = cfg.window_years()
    console.print(f"[bold]Sweeping the archive for {oldest}-{this_year}, rows "
                  f"{start}:{end} ({len(orgs)} orgs, "
                  f"{cfg.effective_workers} at a time, "
                  f"{cfg.requests_per_minute} requests/min)[/bold]")

    return run_orgs(cfg, orgs, progress, limiter)


# ===========================================================================
# CLI
# ===========================================================================
def main() -> None:
    env_cfg = WaybackConfig.from_env()
    ap = argparse.ArgumentParser(
        description="Collect nonprofit annual reports from the Internet Archive.")
    ap.add_argument("--orgs", required=True,
                    help="Path to the orgs CSV (columns: name, domain, ein)")
    ap.add_argument("--out", default=env_cfg.output_dir, help="Output directory")
    ap.add_argument("--years", type=int, default=env_cfg.years_back,
                    help=f"Lookback window in years, counted back from now and "
                         f"clamped at {ARCHIVE_EPOCH}. 0 (the default) sweeps "
                         f"the whole archive. Note this filters on CAPTURE "
                         f"date, not publication date")
    ap.add_argument("--rpm", type=int, default=env_cfg.requests_per_minute,
                    help=f"Requests per minute to web.archive.org "
                         f"(default: {env_cfg.requests_per_minute}). This is "
                         f"the whole run's budget, not a per-worker one")
    ap.add_argument("--workers", type=int, default=env_cfg.max_workers,
                    help=f"Orgs worked on at once; 0 sizes it from the budget "
                         f"(currently {env_cfg.effective_workers}). This never "
                         f"raises the rate -- it is what stops the run "
                         f"finishing BELOW it")
    ap.add_argument("--burst", type=int, default=env_cfg.burst,
                    help=f"Unclaimed request slots that may be banked and "
                         f"spent back-to-back (default: {env_cfg.burst}). 0 is "
                         f"rigid pacing, which measures a little under the "
                         f"budget; the rolling cap applies either way")
    ap.add_argument("--start-row", type=int, default=0)
    ap.add_argument("--end-row", type=int, default=None)
    ap.add_argument("--all-pdfs", action="store_true",
                    help="Keep every document, not just ones whose URL looks "
                         "like a report")
    ap.add_argument("--all-formats", action="store_true",
                    help="Sweep for Word/decks/spreadsheets too, not just PDF. "
                         "Roughly 3.7x slower per org for ~6%% more documents")
    ap.add_argument("--no-html-reports", action="store_true",
                    help="Documents only -- skip reports published as web "
                         "pages (e.g. lssmn.org/2002_annual_report.htm)")
    ap.add_argument("--download", action="store_true",
                    help="Download and save the files instead of only "
                         "recording their snapshot URLs")
    ap.add_argument("--ocr", action="store_true",
                    help="OCR scanned PDFs (needs ocrmypdf+tesseract)")
    args = ap.parse_args()

    try:
        orgs_df = load_csv(args.orgs)
    except Exception as e:
        console.print(f"[red]Unable to read {args.orgs}: {e}[/red]")
        sys.exit(1)

    df = populate_data(orgs_df, args.out, years=args.years,
                       start_row=args.start_row, end_row=args.end_row,
                       all_pdfs=args.all_pdfs, do_ocr=args.ocr,
                       links_only=not args.download,
                       html_reports=not args.no_html_reports,
                       all_formats=args.all_formats,
                       requests_per_minute=args.rpm, burst=args.burst,
                       max_workers=args.workers)
    if df is not None:
        console.print(f"\n[green]Done. {len(df)} documents.[/green]")
        cols = [c for c in ("org", "domain", "source", "format", "year",
                            "pages", "saved_path", "url")
                if c in df.columns]
        console.print(df[cols].to_string(index=False))


if __name__ == "__main__":
    main()
