# LLM calls, one direct api request per report (no batch jobs)
from openai import OpenAI
# load openai submodules now, lazy loading from many threads at once deadlocks
import openai.resources
from google import genai
from google.genai import types
import json
import os
import re
import threading

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from dataprocessor.analysis_v2_section import prompts
# shared with the batch section: csv loading and pdf text extraction
from dataprocessor.analysis_v2_section.analysis_v2 import (
    load_csv,
    pdf_text_from_bytes as shared_pdf_text_from_bytes,
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
# a report is either a pdf or the webpage itself; extract_* return (text, kind), kind "pdf" or "html"

HTML_EXTENSIONS = (".html", ".htm")
# page chrome and non-text parts; wm-* is the wayback machine toolbar
HTML_DROP_TAGS = ["script", "style", "noscript", "template", "svg", "canvas", "iframe",
                  "form", "button", "nav", "header", "footer", "aside"]
WAYBACK_ID = re.compile(r"^wm-", re.I)
# fewer characters than this is a js-only shell or an error page, not a report
MIN_HTML_CHARS = 200


def pdf_text_from_bytes(content):
    # shared reader (handles AES, missing EOF, non-pdf responses); ocr serialised by our lock
    return shared_pdf_text_from_bytes(content, ocr_lock=OCR_LOCK)


def is_pdf(content):
    return b"%PDF" in content[:1024]


def is_html(content, content_type=""):
    if "html" in (content_type or "").lower():
        return True
    head = content[:2048].lower()
    return b"<!doctype html" in head or b"<html" in head or b"<body" in head


def html_text_from_bytes(content):
    # bs4 reads the charset from the page's meta tag, falls back to a best guess
    soup = BeautifulSoup(content, "lxml")
    for tag in soup(HTML_DROP_TAGS):
        tag.decompose()
    for tag in soup.find_all(id=WAYBACK_ID):
        tag.decompose()

    root = soup.find("main") or soup.find("article") or soup.body or soup
    lines = (line.strip() for line in root.get_text("\n").splitlines())
    text = "\n".join(line for line in lines if line)

    title = soup.title.get_text(strip=True) if soup.title else ""
    if title and not text.startswith(title):
        text = f"{title}\n\n{text}"

    if len(text) < MIN_HTML_CHARS:
        raise ValueError(f"webpage has almost no text ({len(text)} chars), likely js-only or an error page")
    # the prompt asks for pdf page locators, a webpage has none
    return f"[Source is a webpage, not a PDF: it has no page numbers, cite by section and paragraph.]\n\n{text}"


def text_from_bytes(content, content_type=""):
    # pdf first: some servers label pdfs text/html
    if is_pdf(content):
        return pdf_text_from_bytes(content), "pdf"
    if is_html(content, content_type):
        return html_text_from_bytes(content), "html"
    # neither, the pdf reader raises a clear "not a pdf" error
    return pdf_text_from_bytes(content), "pdf"


def extract_text_url(url):
    try:
        # csv cells often carry trailing spaces/newlines, which requests encodes into the url (404)
        response = HTTP.get(str(url).strip(), timeout=60)
        response.raise_for_status()
        return text_from_bytes(response.content, response.headers.get("Content-Type", ""))
    except Exception as e:
        print(f"error processing report {url}: {e}")
        return None, None


def extract_text_file(file_path):
    try:
        with open(file_path,"rb") as f:
            content = f.read()
        content_type = "text/html" if file_path.lower().endswith(HTML_EXTENSIONS) else ""
        return text_from_bytes(content, content_type)
    except Exception as e:
        print(f"error processing report {file_path}: {e}")
        return None, None


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
