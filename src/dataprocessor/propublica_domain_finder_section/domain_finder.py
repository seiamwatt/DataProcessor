import json
import argparse
import requests
import pandas as pd
from dotenv import load_dotenv
from dataprocessor.config import load_env
from tqdm import tqdm
import PyPDF2
from io import BytesIO
import time
import os
import re
import tempfile
import ocrmypdf
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse, parse_qs
from rich.console import Console
import sys, os
from rich.progress import Progress
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

PRO_PUBLICA_URL = "https://projects.propublica.org/nonprofits/api/v2"

# propublica serves its filing downloads behind a bot check (403 to a script),
# the irs hosts the same scans unprotected - modern filenames only
IRS_PDF_URL = "https://apps.irs.gov/pub/epostcard/cor"

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

# form 990 page 1 line J
WEBSITE_LABEL = re.compile(r"web\s*site\s*[:;.]?", re.IGNORECASE)

# strict wants a clean break after the tld, loose picks up ocr glue like
# "WWW.CARNEGIEHALL.ORGH(a)" where the next form label runs into the domain
TLD = r"(?:org|com|net|edu|info|us|io|co|ca|uk)"
DOMAIN_STRICT = re.compile(r"(?:www\.)?[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)*\." + TLD + r"\b", re.IGNORECASE)
DOMAIN_LOOSE = re.compile(r"(?:www\.)?[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)*\." + TLD, re.IGNORECASE)

# away from line J the page is full of form numbers and addresses, only trust
# something that spells out www
WWW_STRICT = re.compile(r"www\.[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)*\." + TLD + r"\b", re.IGNORECASE)
WWW_LOOSE = re.compile(r"www\.[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)*\." + TLD, re.IGNORECASE)
 
# form boilerplate and preparer software, never the org itself
BLOCKED_DOMAINS = {"irs.gov", "propublica.org", "adobe.com", "intuit.com", "form990.org"}

NO_DOMAIN_VALUES = {"n/a", "na", "none", "nla", "nia", "not applicable", "same"}


def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)

load_env()
def load_csv(file_path):
    try:
        file = pd.read_csv(file_path)
        return file
    
    except Exception as e:
        print("load csv failed")
        return None


def need_ocr(pdf_reader, max_pages):
    text = ""

    for page in range(min(max_pages, len(pdf_reader.pages))):
        text += pdf_reader.pages[page].extract_text() or ""

    return len(text.strip()) < 50


# ocr the first pages only, a full 990 runs to 90+ pages and the website
# line is always on page 1
def IMG_to_pdf(pdf_reader, max_pages):

    writer = PyPDF2.PdfWriter()

    for page in range(min(max_pages, len(pdf_reader.pages))):
        writer.add_page(pdf_reader.pages[page])

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        writer.write(tmp)
        tmp_path = tmp.name

    output_path = tmp_path.replace(".pdf", "_ocr.pdf")

    # jobs=1 because the UI already runs a batch of these in parallel, letting
    # each one grab every core just thrashes
    try:
        ocrmypdf.ocr(tmp_path, output_path, deskew=True, force_ocr=True, progress_bar=False, jobs=1)
    finally:
        os.remove(tmp_path)

    return output_path


def extract_text(file, max_pages=2):
    ocr_path = None

    try:
        pdf_reader = PyPDF2.PdfReader(file)
        extracted_text = ""

        if need_ocr(pdf_reader, max_pages):
            ocr_path = IMG_to_pdf(pdf_reader, max_pages)
            pdf_reader = PyPDF2.PdfReader(ocr_path)

        for page in range(min(max_pages, len(pdf_reader.pages))):
            extracted_text += pdf_reader.pages[page].extract_text() or ""

        return extracted_text
    except Exception as e:
        print(f"extract text failed: {e}")
        return None
    finally:
        if ocr_path and os.path.exists(ocr_path):
            os.remove(ocr_path)


def get_form990(ein):
    try:
        url = f"{PRO_PUBLICA_URL}/organizations/{ein}.json"
        response = requests.get(url, timeout=30, headers=HEADERS)
        response.raise_for_status()

        response = response.json()

    except Exception as e:
        print(e)
        return None

    filings = []
    filings.extend(response.get("filings_without_data", []) or [])
    filings.extend(response.get("filings_with_data", []) or [])

    pdf_urls = []

    # newest filing first, the most recent website on file is the one we want
    for filing in sorted(filings, key=lambda f: f.get("tax_prd_yr") or 0, reverse=True):
        pdf_url = filing.get("pdf_url")

        if pdf_url:
            pdf_urls.append(pdf_url)

    return pdf_urls


def irs_mirror_url(pdf_url):
    try:
        path = parse_qs(urlparse(pdf_url).query).get("path", [""])[0]
        file_name = os.path.basename(path)

        # legacy propublica scans (13-1923626_990_201006.pdf) are not mirrored
        if not file_name or "-" in file_name:
            return None

        return f"{IRS_PDF_URL}/{file_name}"
    except Exception as e:
        print(f"mirror url failed: {e}")
        return None


def download_form990(pdf_url):

    urls = [irs_mirror_url(pdf_url), pdf_url]

    for url in urls:
        if not url:
            continue

        try:
            response = requests.get(url, timeout=60, headers=HEADERS)
            response.raise_for_status()

            if not response.content.startswith(b"%PDF"):
                print("not a pdf, trying next source")
                continue

            return BytesIO(response.content)
        except Exception as e:
            print(f"pdf download failed: {e}")
            continue

    return None


def clean_domain(domain):

    domain = domain.strip().strip(".").lower()
    domain = re.sub(r"^www\.", "", domain)

    if domain in BLOCKED_DOMAINS:
        return None

    # ocr turns form numbers and dollar amounts into things like "13.com"
    name = domain.split(".")[0]

    if len(name) < 2 or name.isdigit():
        return None

    return domain


def extract_domain(text):

    if not text:
        return None

    # ocr splits domains on the dots ("www. irs.gov"), close them up first
    text = re.sub(r"\s*\.\s*", ".", text)

    label = WEBSITE_LABEL.search(text)

    if label:
        # the value sits between line J and the H(a) block that follows it
        window = text[label.end():label.end() + 120]

        if window.strip().lower()[:15].strip(" :.") in NO_DOMAIN_VALUES:
            return None

        for pattern in (DOMAIN_STRICT, DOMAIN_LOOSE):
            match = pattern.search(window)

            if match:
                domain = clean_domain(match.group(0))

                if domain:
                    return domain

    # no usable line J (a 990-PF has no website field at all), fall back to a
    # spelled out www address anywhere on the page
    for pattern in (WWW_STRICT, WWW_LOOSE):
        for match in pattern.finditer(text):
            domain = clean_domain(match.group(0))

            if domain:
                return domain

    return None


def find_domain(ein, max_filings=3):
    try:
        pdf_urls = get_form990(ein)

        if not pdf_urls:
            print(f"no filings found for {ein}")
            return None

        for pdf_url in pdf_urls[:max_filings]:
            pdf_file = download_form990(pdf_url)

            if pdf_file is None:
                continue

            text = extract_text(pdf_file)
            domain = extract_domain(text)

            if domain:
                return domain

            print(f"no domain on filing {pdf_url}")

        return None
    except Exception as e:
        print(e)
        return None


def main():
    # test code
    # print(find_domain(131923626))
    return

if __name__ == "__main__":
    main()
