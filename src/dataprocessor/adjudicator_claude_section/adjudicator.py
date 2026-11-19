import anthropic
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
from dataprocessor.system_prompt import prompt as prompts
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
console = Console()
load_dotenv()
CLAUDE_KEY = os.getenv("Claude_key")
CLAUDE_MODEL = "claude-opus-5-5"
MAX_TOKENS = 20000
QUANTITATIVE_COLS = [
    "ID1_Intensity",
    "ISS1_Econ_Pos", "ISS1_Econ_Int",
    "ISS2_Social_Pos", "ISS2_Social_Int",
    "ISS3_Race_Pos", "ISS3_Race_Int",
    "ISS4_Immig_Pos", "ISS4_Immig_Int",
    "ISS5_Enviro_Pos", "ISS5_Enviro_Int",
    "ISS6_Govt_Pos", "ISS6_Govt_Int",
    "ISS7_Global_Pos", "ISS7_Global_Int",
    "ISS8_IndSys_Pos", "ISS8_IndSys_Int",
    "CON_Wellbeing_M", "CON_Wellbeing_SP",
    "ID_OutIntensity", "ID_UsVsThem",
    "NARR_Focus", "OVERALL_Intensity",
]
THRESHOLD = 20
MISSING_CODE = 999
MAX_RETRIES = 5

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


# if batch processing ignore function for now
def create_reports_batch(url_links):
    reports = []
    for url in url_links:
        # row did not need adjudication, deepseek_batch skips None prompts
        if url is None:
            reports.append([None,None])
            continue
        extracted_text = extract_pdf_text(url)
        if not extracted_text:
            print(f"skipping {url}: no text extracted")
            reports.append([extracted_text,None])
            continue
        prompt = prompts.construct_system_prompt(extracted_text)
        reports.append([extracted_text,prompt])
    return reports

def parse_json(text):
    # tolerate markdown fences or stray text around the object
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in response")
    return json.loads(text[start:end + 1])

def connect_claude_helper(prompt,client):
    response = client.messages.create(
        max_tokens = MAX_TOKENS,
        messages = [
            {
                "content": prompt,
                "role": "user",
            }
        ],
        model = CLAUDE_MODEL
    )
    if response.stop_reason == "max_tokens":
        raise ValueError(f"response truncated at {MAX_TOKENS} tokens")
    text = "".join(block.text for block in response.content if block.type == "text")
    return parse_json(text)

def claude_batch(reports,api_key):
    MAX_WORKERS = 50
    futures = []
    result_rows = []
    # one shared client, SDK retries rate limits and overloads with backoff
    client = anthropic.Anthropic(api_key=api_key, max_retries=MAX_RETRIES)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for i in range(len(reports)):
            if reports[i][1] is None:
                futures.append(None)
                continue

            future = executor.submit(connect_claude_helper,reports[i][1],client)
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

def need_adjudicate(df,col_name="url"):
    url_links = []
    for i,row in df.iterrows():
        url = None
        for col in QUANTITATIVE_COLS:
            gemini_col = pd.to_numeric(row[f"gm_{col}"], errors="coerce")
            gpt_col = pd.to_numeric(row[f"gpt_{col}"], errors="coerce")

            # a model failed on this field, nothing to compare
            if pd.isna(gemini_col) or pd.isna(gpt_col):
                continue

            # 999 is a missing code, not a score, so a gap against it is not a disagreement
            if gemini_col == MISSING_CODE or gpt_col == MISSING_CODE:
                continue

            if abs(gemini_col - gpt_col) >= THRESHOLD:
                url = row[col_name]
                break
        url_links.append(url)

    return create_reports_batch(url_links)

