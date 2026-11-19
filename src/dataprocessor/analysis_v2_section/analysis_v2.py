# LLM calls 
from openai import OpenAI
# load openai submodules now, lazy loading from many threads at once deadlocks
import openai.resources
from google import genai
from google.genai import types
import json
import argparse
import requests
import pandas as pd
from dotenv import load_dotenv
from tqdm import tqdm
import logging
import pikepdf
# pypdf (not PyPDF2) decrypts AES pdfs with the `cryptography` package
# and is more tolerant of damaged files
from pypdf import PdfReader
from io import BytesIO
import time
import os
import ocrmypdf
import tempfile
from rich.console import Console
import sys
from concurrent.futures import ThreadPoolExecutor
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
console = Console()
from dataprocessor.analysis_v2_section import prompts
from tenacity import (
    retry,
    retry_if_exception_type,
    retry_if_result,
    stop_after_delay,
    wait_exponential,
)
import threading
from collections import deque
# keys
load_dotenv()
GEMINI_KEY = os.getenv("Gemini_key")
GPT_KEY = os.getenv("GPT_key")

# pypdf warns loudly about recoverable damage (missing EOF etc), keep the console clean
logging.getLogger("pypdf").setLevel(logging.ERROR)

GEMINI_MODEL = "gemini-3.8-flash"
GPT_MODEL = "gpt-6-luna"
# GEMINI_MODEL = "gemini-4-argon"
# GPT_MODEL = "gpt-6.1-sol"

def load_csv(file_path):
    try:
        # read everything as text so original columns are written back untouched (e.g. ein leading zeros)
        file = pd.read_csv(file_path,encoding="utf-8",dtype=str,keep_default_na=False)
        return file
    except Exception as e:
        print(e)
        return None

def need_ocr(pdf_reader) -> bool:
    text = ""
    pages_len = min(15,len(pdf_reader.pages))

    for page in range(pages_len):
        text += pdf_reader.pages[page].extract_text() or ""

    return len(text.strip()) < 50


def IMG_to_pdf(file_path):
    output_path = file_path
    ocrmypdf.ocr(file_path,output_path,deskew=True,skip_text=True)
    return output_path


def read_all_pages(pdf_reader):
    text = ""
    for page in range(len(pdf_reader.pages)):
        text += pdf_reader.pages[page].extract_text() or ""
    return text


def repair_pdf(content):
    # qpdf (via pikepdf) rebuilds broken xref tables / missing EOF markers and
    # drops encryption that only uses an empty user password
    with pikepdf.open(BytesIO(content)) as pdf:
        out = BytesIO()
        pdf.save(out)
        return out.getvalue()


def open_pdf(content):
    """Return (reader, pdf_bytes). pdf_bytes may be a repaired copy of content."""
    if not content:
        raise ValueError("empty response, no pdf data")

    # some servers return an html error page (or leading junk) instead of a pdf
    start = content.find(b"%PDF", 0, 1024)
    if start == -1:
        raise ValueError(f"not a pdf (starts with {content[:40]!r})")
    content = content[start:]

    try:
        reader = PdfReader(BytesIO(content), strict=False)
        if reader.is_encrypted:
            reader.decrypt("")
        # touch the pages so broken files fail here, not halfway through extraction
        len(reader.pages)
        return reader, content
    except Exception as first_error:
        try:
            content = repair_pdf(content)
        except Exception:
            raise first_error
        reader = PdfReader(BytesIO(content), strict=False)
        if reader.is_encrypted:
            reader.decrypt("")
        return reader, content


def pdf_text_from_bytes(content, ocr_lock=None):
    pdf_reader, content = open_pdf(content)

    if not need_ocr(pdf_reader):
        return read_all_pages(pdf_reader)

    # ocrmypdf refuses encrypted input, hand it a decrypted copy
    if pdf_reader.is_encrypted:
        content = repair_pdf(content)

    # ocr works on a temp copy, so local input files are never modified
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        if ocr_lock is not None:
            with ocr_lock:
                IMG_to_pdf(tmp_path)
        else:
            IMG_to_pdf(tmp_path)
        return read_all_pages(PdfReader(tmp_path, strict=False))
    finally:
        os.remove(tmp_path)

# NOTE: rate limiting for web.archive.org downloads

class RateLimiter:
    """At most max_request requests in any window_size seconds, shared by all threads.
    acquire() blocks until a slot is free; cooldown() pauses everyone (e.g. after a 429)."""

    def __init__(self,window_size,max_request):
        self.window_size = window_size
        self.max_request = max_request
        self.requests = deque()
        self.cooldown_until = 0
        self.lock = threading.Lock()

    def acquire(self):
        while True:
            with self.lock:
                time_now = time.monotonic()

                while self.requests and self.requests[0] <= time_now - self.window_size:
                    self.requests.popleft()

                if time_now < self.cooldown_until:
                    wait = self.cooldown_until - time_now
                elif len(self.requests) < self.max_request:
                    self.requests.append(time_now)
                    return
                else:
                    wait = self.requests[0] + self.window_size - time_now

            # sleep outside the lock so other threads can still check in
            time.sleep(wait)

    def cooldown(self,seconds):
        with self.lock:
            self.cooldown_until = max(self.cooldown_until,time.monotonic() + seconds)


# CDX API allows 60/min, stay under it
ARCHIVE_RPM = int(os.getenv("WAYBACK_RPM", 50))
ARCHIVE_LIMITER = RateLimiter(window_size=60,max_request=ARCHIVE_RPM)
ARCHIVE_MAX_TRIES = 5
ARCHIVE_COOLDOWN = 60          # pause when throttled without a Retry-After
ARCHIVE_MAX_COOLDOWN = 600     # cap on an honoured Retry-After
THROTTLE_STATUSES = {429, 503}

# the archive blocks the default python-requests user agent more aggressively
HTTP = requests.Session()
HTTP.headers["User-Agent"] = os.getenv("USER_AGENT","nonprofit-reports/1.2 (+research)")


def is_wayback(source):
    return str(source or "").strip().lower() == "wayback"


def retry_after_seconds(response):
    raw = (response.headers.get("Retry-After") or "").strip()
    if raw.isdigit():
        return min(int(raw),ARCHIVE_MAX_COOLDOWN)
    return ARCHIVE_COOLDOWN


def fetch_pdf(pdf_url,source=None):
    # csv cells often carry trailing spaces/newlines, which requests encodes into the url (404)
    pdf_url = str(pdf_url).strip()
    # only rows whose source is "wayback" go through the shared limiter
    if not is_wayback(source):
        response = HTTP.get(pdf_url, timeout=30)
        response.raise_for_status()
        return response

    for attempt in range(1, ARCHIVE_MAX_TRIES + 1):
        ARCHIVE_LIMITER.acquire()
        try:
            response = HTTP.get(pdf_url, timeout=30)
        except requests.ConnectionError:
            # a refused connection is how the archive blocks after repeated 429s
            if attempt == ARCHIVE_MAX_TRIES:
                raise
            ARCHIVE_LIMITER.cooldown(ARCHIVE_COOLDOWN * attempt)
            continue

        if response.status_code in THROTTLE_STATUSES and attempt < ARCHIVE_MAX_TRIES:
            pause = retry_after_seconds(response)
            print(f"archive throttled ({response.status_code}), pausing {pause}s")
            ARCHIVE_LIMITER.cooldown(pause)
            continue

        response.raise_for_status()
        return response


def extract_pdf_text(pdf_url,source=None):

    try:
        response = fetch_pdf(pdf_url,source)
        # edit amount of text extracted here
        return pdf_text_from_bytes(response.content)
    except Exception as e:
        print(f"error processing pdf {pdf_url}: {e}")
        return None


def create_reports_batch(url_links,sources=None):
    reports = []
    sources = sources or [None] * len(url_links)
    for url, source in zip(url_links, sources):
        extracted_text = extract_pdf_text(url,source)
        if not extracted_text:
            print(f"skipping {url}: no text extracted")
            reports.append([extracted_text,None])
            continue
        prompt = [{"role": "user", "content": prompts.construct_system_prompt(extracted_text)}]
        reports.append([extracted_text,prompt])
    return reports


def processing_status_gemini(job):
    invalid_status = ["JOB_STATE_SUCCEEDED","JOB_STATE_FAILED","JOB_STATE_CANCELLED","JOB_STATE_EXPIRED"]
    if job.state.name not in invalid_status:
        return True
    return False

def processing_status_gpt(batch):
    invalid_status = ["completed", "failed", "expired", "cancelled"]
    if batch.status not in invalid_status:
        return True 
    return False

@retry(
    retry=retry_if_result(processing_status_gemini) | retry_if_exception_type(genai.errors.ServerError),
    wait=wait_exponential(multiplier=2,min=10,max=300),
    stop=stop_after_delay(24 * 60 * 60)
)

def wait_gemini(job_name,api_key):
    client = genai.Client(api_key=api_key)
    return client.batches.get(name=job_name)

@retry(
    retry=retry_if_result(processing_status_gpt) | retry_if_exception_type((
        openai.APIConnectionError,
        openai.RateLimitError,
        openai.InternalServerError,
    )),
    wait=wait_exponential(multiplier=2,min=10,max=300),
    stop=stop_after_delay(24 * 60 * 60)
)
def wait_gpt(batch_id,api_key):
    client = OpenAI(api_key=api_key)
    return client.batches.retrieve(batch_id)


#  NOTE: Gemini
def gemini_batch_submit(reports,api_key):
    client = genai.Client(api_key=api_key)

    with tempfile.NamedTemporaryFile(mode = "w",suffix=".jsonl",delete=False) as tmp:
        for i in range(len(reports)):
            if reports[i][1] is None:
                continue
            prompt = reports[i][1][0]["content"]
            request = {
                "key": str(i),
                "request": {
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generation_config": {"response_mime_type": "application/json"}
                }
            }
            tmp.write(json.dumps(request) + "\n")
        tmp_path = tmp.name

    try:
        uploaded = client.files.upload(
            file=tmp_path,
            config=types.UploadFileConfig(display_name="report-batch",mime_type="jsonl")
        )
        job = client.batches.create(
            model=GEMINI_MODEL,
            src=uploaded.name,
            config={"display_name": "report-batch"}
        )

    finally:
        os.remove(tmp_path)

    return job.name

def gemini_batch(job_name,api_key,num_reports):
    client = genai.Client(api_key = api_key)
    job = client.batches.get(name =job_name)
    result_rows = [None] * num_reports

    # None means still running, terminal failures raise so callers stop polling
    if job.state.name in ("JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED"):
        raise RuntimeError(f"gemini batch {job_name} ended with {job.state.name}")
    if job.state.name != "JOB_STATE_SUCCEEDED":
        return None

    lines = client.files.download(file=job.dest.file_name).decode("utf-8").splitlines()

    for line in lines:
        row = json.loads(line)
        i = int(row["key"])
        try:
            text = row["response"]["candidates"][0]["content"]["parts"][0]["text"]
            result_rows[i] = json.loads(text)
        except Exception as e:
            print(f"error processing report {i}: {e}")

    return result_rows

# NOTE: GPT

def gpt_batch_submit(reports,api_key):
    client = OpenAI(api_key=api_key)

    with tempfile.NamedTemporaryFile(mode="w",suffix=".jsonl",delete=False) as tmp:
        for i in range(len(reports)):
            if reports[i][1] is None:
                continue
            request = {
                "custom_id": str(i),
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": GPT_MODEL,
                    "messages": reports[i][1],
                    "response_format": {"type": "json_object"}
                }
            }
            tmp.write(json.dumps(request) + "\n")

        tmp_path = tmp.name

    try:
        with open(tmp_path,"rb") as f:
            batch_file = client.files.create(file=f,purpose="batch")

        batch = client.batches.create(
            input_file_id = batch_file.id,
            endpoint = "/v1/chat/completions",
            completion_window="24h"
        )
            
    finally:
        os.remove(tmp_path)

    return batch.id

def gpt_batch(api_key,batch_id,num_reports):
    client = OpenAI(api_key=api_key)
    batch = client.batches.retrieve(batch_id)
    result_rows = [None] * num_reports
    
    # None means still running, terminal failures raise so callers stop polling
    if batch.status in ("failed", "expired", "cancelling", "cancelled"):
        raise RuntimeError(f"gpt batch {batch_id} ended with {batch.status}")
    if batch.status != "completed":
        return None
    
    lines = client.files.content(batch.output_file_id).text.splitlines()
    for line in lines:
        row = json.loads(line)
        i = int(row["custom_id"])
        try:
            content = row["response"]["body"]["choices"][0]["message"]["content"]
            result_rows[i] = json.loads(content)
        except Exception as e:
            print(f"error processing report {i}: {e}")
    return result_rows


