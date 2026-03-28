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
import sys, os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

console = Console()

def load_csv(file_path):
    try:
        file = pd.read_csv(file_path,encoding="utf-8")
        return file
    except Exception as e:
        print(f"error processing csv file: {e}")
        return None
    

def need_ocr(pdf_reader) -> bool:
    text = ""

    for page in range(min(15, len(pdf_reader.pages))):
        text += pdf_reader.pages[page].extract_text() or ""

    return len(text.strip()) < 50

def IMG_to_pdf(file_path):

    output_path = file_path
    ocrmypdf.ocr(file_path, output_path, deskew=True)

    return output_path

def extract_pdf_text(pdf_url, max_pages=15):

    try:

        response = requests.get(pdf_url, timeout=30)
        response.raise_for_status()
        time.sleep(30)

        pdf_file = BytesIO(response.content)
        pdf_reader = PyPDF2.PdfReader(pdf_file)

        if need_ocr(pdf_reader):

            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(response.content)
                tmp_path = tmp.name

            IMG_to_pdf(tmp_path)
            pdf_reader = PyPDF2.PdfReader(tmp_path)

        text = ""
        num_pages = min(len(pdf_reader.pages), max_pages)

        for page in range(num_pages):
            text += pdf_reader.pages[page].extract_text() or ""

        return text[:200000]

    except Exception as e:
        print(f"error processing pdf: {e}")
        return None

def create_prompt(pdf_url, pdf_text=None):

    prompt = f"""Analyze this annual report and extract the following metadata.

**YEAR** – The fiscal/calendar year the report covers.
- Use the report title, fiscal year end date, or financial statement dates.
- For split fiscal years (e.g. "July 2022 – June 2023"), use the ending year.

**ORG** – The common abbreviation of the publishing organization (e.g. "CBO", "GAO", "WHO").

**Y1** – The single main topic or issue of the report in 10 words or fewer.

{f"Source URL: {pdf_url}" if pdf_url else ""}
{f"Document text:\\n{pdf_text}" if pdf_text else ""}

Respond ONLY with valid JSON, no markdown, no explanation:
{{
  "Y1": "<main issue in ≤10 words>",
  "ORG": "<organization abbreviation>",
  "YEAR": "<four-digit year>"
}}"""

    return prompt



def format_api_output(input):
    cleaned = input.strip().strip("```json").strip("```").strip()
    return json.loads(cleaned)


def connect_to_DeepSeek(api_key, prompt, chat_model=None, max_tries=None):
    if chat_model is None:
        chat_model = "deepseek-chat"

    if max_tries is None:
        max_tries = 8

    for attempt in range(1, max_tries + 1):
        try:
            api_url = "https://api.deepseek.com/v1/chat/completions"

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }

            payload = {
                "model": chat_model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 3000,
                "temperature": 0,
            }

            response = requests.post(
                api_url, headers=headers, json=payload, timeout=60
            )
            response.raise_for_status()

            result = response.json()
            content = result["choices"][0]["message"]["content"]

            return format_api_output(content)

        except Exception as e:
            print(f"DeepSeek attempt {attempt} failed: {e}")
            # BUG FIX: was returning None on first failure instead of retrying
            if attempt < max_tries:
                time.sleep(2**attempt)
            else:
                print("All DeepSeek tries exhausted")
                return None
            

def batch_processing():
    pass


