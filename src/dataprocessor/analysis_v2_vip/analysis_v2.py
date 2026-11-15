# LLM calls, one direct api request per report (no batch jobs)
from openai import OpenAI
# load openai submodules now, lazy loading from many threads at once deadlocks
import openai.resources
from google import genai
from google.genai import types
import json
import os
import tempfile
import threading
from io import BytesIO

import pikepdf
import requests
# pypdf (not PyPDF2) decrypts AES pdfs with the `cryptography` package
# and is more tolerant of damaged files
from pypdf import PdfReader
import logging
# pypdf warns loudly about recoverable damage (missing EOF etc), keep the console clean
logging.getLogger("pypdf").setLevel(logging.ERROR)
from dotenv import load_dotenv
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from dataprocessor.analysis_v2_section import prompts
# shared with the batch section: csv loading and the ocr check
from dataprocessor.analysis_v2_section.analysis_v2 import (
    load_csv,
    need_ocr,
    IMG_to_pdf,
)

load_dotenv()
GEMINI_KEY = os.getenv("Gemini_key")
GPT_KEY = os.getenv("GPT_key")

GEMINI_MODEL = "gemini-3.8-flash"
GPT_MODEL = "gpt-6-luna"
# GEMINI_MODEL = "gemini-4-argon"
# GPT_MODEL = "gpt-6.1-sol"

# long reports can take a while to answer
REQUEST_TIMEOUT = 600
API_MAX_TRIES = int(os.getenv("API_MAX_TRIES", 6))

# no rate limiter on downloads, pdfs are fetched directly
HTTP = requests.Session()
HTTP.headers["User-Agent"] = os.getenv("USER_AGENT","nonprofit-reports/1.2 (+research)")

# ocrmypdf is not safe to run from several threads at once
OCR_LOCK = threading.Lock()


# NOTE: text extraction

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


def pdf_text_from_bytes(content):
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
        with OCR_LOCK:
            IMG_to_pdf(tmp_path)
        return read_all_pages(PdfReader(tmp_path, strict=False))
    finally:
        os.remove(tmp_path)


def extract_pdf_text_url(pdf_url):
    try:
        response = HTTP.get(pdf_url, timeout=60)
        response.raise_for_status()
        return pdf_text_from_bytes(response.content)
    except Exception as e:
        print(f"error processing pdf {pdf_url}: {e}")
        return None


def extract_pdf_text_file(file_path):
    try:
        with open(file_path,"rb") as f:
            return pdf_text_from_bytes(f.read())
    except Exception as e:
        print(f"error processing pdf {file_path}: {e}")
        return None


def build_prompt(extracted_text):
    if not extracted_text or not extracted_text.strip():
        return None
    return prompts.construct_system_prompt(extracted_text)


# NOTE: retry rules

def gpt_should_retry(e):
    # out of credit is a 429 too, but waiting will not fix it
    if isinstance(e, openai.RateLimitError):
        return getattr(e, "code", None) != "insufficient_quota"
    return isinstance(e, (
        openai.APIConnectionError,
        openai.APITimeoutError,
        openai.InternalServerError,
    ))


def gemini_should_retry(e):
    if isinstance(e, genai.errors.ServerError):
        return True
    if isinstance(e, genai.errors.ClientError):
        return getattr(e, "code", None) == 429
    return False


# NOTE: GPT

def gpt_client(api_key):
    # clients are thread safe, create once per run and share across workers
    return OpenAI(api_key=api_key,timeout=REQUEST_TIMEOUT)


@retry(
    retry=retry_if_exception(gpt_should_retry),
    wait=wait_exponential(multiplier=2,min=5,max=120),
    stop=stop_after_attempt(API_MAX_TRIES),
    reraise=True,
)
def gpt_analyze(client,prompt):
    response = client.chat.completions.create(
        model=GPT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


# NOTE: Gemini

def gemini_client(api_key):
    return genai.Client(api_key=api_key)


@retry(
    retry=retry_if_exception(gemini_should_retry),
    wait=wait_exponential(multiplier=2,min=5,max=120),
    stop=stop_after_attempt(API_MAX_TRIES),
    reraise=True,
)
def gemini_analyze(client,prompt):
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    return json.loads(response.text)
