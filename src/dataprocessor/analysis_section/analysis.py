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
import ocrmypdf
import tempfile
from rich.console import Console
import sys
from concurrent.futures import ThreadPoolExecutor
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
console = Console()
from dataprocessor.analysis_section import prompts
from tenacity import (
    retry,
    retry_if_exception_type,
    retry_if_result,
    stop_after_delay,
    wait_exponential,
)

# LLM calls 
from openai import OpenAI
# load openai submodules now, lazy loading from many threads at once deadlocks
import openai.resources
from google import genai
from google.genai import types

# keys
load_dotenv()
DEEPSEEK_KEY = os.getenv("DeepSeek_key")
GEMINI_KEY = os.getenv("Gemini_key")
GPT_KEY = os.getenv("GPT_key")

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-pro"
GEMINI_MODEL = "gemini-3.8-flash"
GPT_MODEL = "gpt-6-luna"

# NOTE: HELPERS FUNCTIONS

def load_csv(file_path):
    try:
        file = pd.read_csv(file_path, encoding="utf-8")
        return file
    except Exception as e:
        print(f"error processing csv file: {e}")
        return None


# check if pdf text can be extracted
def need_ocr(pdf_reader) -> bool:
    text = ""
    for page in range(min(15, len(pdf_reader.pages))):
        text += pdf_reader.pages[page].extract_text() or ""
    return len(text.strip()) < 50


# convert pdf to a format where text can be extracted
def IMG_to_pdf(file_path):

    output_path = file_path
    ocrmypdf.ocr(file_path, output_path, deskew=True,skip_text=True)

    return output_path


def extract_pdf_text(pdf_url):

    try:
        response = requests.get(pdf_url, timeout=30)
        response.raise_for_status()

        pdf_file = BytesIO(response.content)
        pdf_reader = PyPDF2.PdfReader(pdf_file)

        if need_ocr(pdf_reader):

            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(response.content)
                tmp_path = tmp.name

            try:
                IMG_to_pdf(tmp_path)
                pdf_reader = PyPDF2.PdfReader(tmp_path)
                text = ""
                for page in range(len(pdf_reader.pages)):
                    text += pdf_reader.pages[page].extract_text() or ""
            finally:
                os.remove(tmp_path)
        else:
            text = ""
            for page in range(len(pdf_reader.pages)):
                text += pdf_reader.pages[page].extract_text() or ""

        # edit amount of text extracted here
        return text
    except Exception as e:
        print(f"error processing pdf: {e}")
        return None

# reports -> 2 dimensional [[extracted_text,prompt]]
# prompt is None when no text could be extracted, those reports are skipped

def create_reports_batch(url_links):
    reports = []
    for url in url_links:
        extracted_text = extract_pdf_text(url)
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


# keeps polling through dropped connections and server errors, other errors stop right away
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


# NOTE: DEEPSEEK
def connect_deepseek_helper(prompt,api_key):
    client = OpenAI(
        api_key=api_key,
        base_url=DEEPSEEK_BASE_URL
    )
    response = client.chat.completions.create(
        model = DEEPSEEK_MODEL,
        messages = prompt,
        stream=False,
        reasoning_effort="high",
        response_format={"type": "json_object"}
    )

    return json.loads(response.choices[0].message.content)


def deepseek_batch(reports,api_key):
    MAX_WORKERS = 50
    futures = []
    result_rows = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for i in range(len(reports)):
            if reports[i][1] is None:
                futures.append(None)
                continue
            future = executor.submit(connect_deepseek_helper,reports[i][1],api_key)
            futures.append(future)

    for i in range(len(reports)):
        if futures[i] is None:
            result_rows.append(None)
            continue
        try:
            row = futures[i].result()
        except Exception as e:
            print(f"error processing report {i}: {e}")
            row = None
        result_rows.append(row)

    return result_rows


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