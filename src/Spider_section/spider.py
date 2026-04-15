# Spider for extracting annual reports
import requests
import pandas as pd
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re
import time

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

# keywords that suggest an annual report link
REPORT_KEYWORDS = re.compile(
    r"annual[\s\-_]?report|year[\s\-_]?in[\s\-_]?review|financials|990|form[\s\-_]?990",
    re.IGNORECASE,
)


def load_csv(file_path):
    try:
        return pd.read_csv(file_path, encoding="utf-8")
    except Exception as e:
        print(f"Error processing CSV file: {e}")
        return None


def fetch_page(url, timeout=15):
    """GET a page and return a BeautifulSoup object, or None on failure."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"  Failed to fetch {url}: {e}")
        return None


def find_report_links(soup, base_url):
    """
    Scan all <a> tags for links that look like annual reports.
    Matches on:
      1. href pointing to a PDF whose filename matches keywords
      2. anchor text matching keywords
    Returns a deduplicated list of absolute URLs.
    """
    found = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(strip=True)
        abs_url = urljoin(base_url, href)

        # check the link text
        if REPORT_KEYWORDS.search(text):
            found.add(abs_url)
            continue

        # check the href itself (catches .pdf filenames)
        if REPORT_KEYWORDS.search(href):
            found.add(abs_url)
            continue

        # catch any PDF link with "annual" or "report" in the path
        if href.lower().endswith(".pdf") and re.search(r"annual|report", href, re.IGNORECASE):
            found.add(abs_url)

    return sorted(found)


def scrape_site(url):
    """
    Try the main page first, then fall back to common
    sub-paths where orgs typically put annual reports.
    """
    fallback_paths = [
        "/annual-report",
        "/annualreport",
        "/about/annual-report",
        "/about/financials",
        "/financials",
        "/transparency",
        "/publications",
    ]

    all_links = set()

    # 1 — scan the landing page
    soup = fetch_page(url)
    if soup:
        all_links.update(find_report_links(soup, url))

    # 2 — try common sub-paths
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    for path in fallback_paths:
        sub_url = base + path
        soup = fetch_page(sub_url)
        if soup:
            all_links.update(find_report_links(soup, sub_url))

    return sorted(all_links)


def batch_processing(csv_path, url_column="url", delay=2):
    """
    Read a CSV of org websites, scrape each one for annual
    report links, and return a DataFrame with one row per PDF found.
    """
    df = load_csv(csv_path)
    if df is None:
        return pd.DataFrame()

    rows = []
    for idx, row in df.iterrows():
        url = row[url_column]
        print(f"[{idx + 1}/{len(df)}] Scraping {url} ...")

        pdf_urls = scrape_site(url)
        print(f"  Found {len(pdf_urls)} link(s)")

        if pdf_urls:
            for pdf_url in pdf_urls:
                new_row = row.to_dict()
                new_row["pdf_url"] = pdf_url
                rows.append(new_row)
        else:
            new_row = row.to_dict()
            new_row["pdf_url"] = None
            rows.append(new_row)

        time.sleep(delay)  # be polite

    return pd.DataFrame(rows)
