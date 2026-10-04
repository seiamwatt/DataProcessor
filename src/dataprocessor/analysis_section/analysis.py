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
from analysis_section import prompts

# LLM calls 
from openai import OpenAI
from google import genai
from google.genai import types

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = ""
GEMINI_MODEL = ""
GPT_MODEL = ""

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
    ocrmypdf.ocr(file_path, output_path, deskew=True)

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

            IMG_to_pdf(tmp_path)
            pdf_reader = PyPDF2.PdfReader(tmp_path)

        text = ""
        num_pages = pdf_reader.pages

        for page in range(num_pages):
            text += pdf_reader.pages[page].extract_text() or ""
        # edit amount of text extracted here
        return text[:2000]

    except Exception as e:
        print(f"error processing pdf: {e}")
        return None

def connect_deepseek(prompt,api_key):
    client = OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com"
    )
    response = client.chat.completions.create(
        model = DEEPSEEK_MODEL,
        messages = prompt,
        stream=False,
        reasoning_effort="high",
        response_format={"type": "json_object"}
    )

    return json.loads(response.choices[0].message.content)

def connect_gemini(prompt,api_key):
    client = genai.Client(api_key=api_key)
    response = client.interactions.create(
        model = GEMINI_MODEL,
        input = prompt,
        response_mime_type="application/json",
    )

    return json.loads(response.outputs[-1].text)

def connect_gpt(prompt,api_key):
    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=GPT_MODEL,
        messages=prompt,
        response_format={"type":"json_object"}
    )
    return json.loads(response.choices[0].message.content)


def process_report(extracted_text,api_key):
    system_prompt = prompts.construct_system_prompt()
    user_prompt = prompts.construct_user_prompt(extracted_text)

    # deepseek and gpt take a message list, gemini interactions takes a single string
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    gemini_prompt = f"{system_prompt}\n\n{user_prompt}"

    deepseek_response = connect_deepseek(messages,api_key)
    gemini_response = connect_gemini(gemini_prompt,api_key)
    gpt_response = connect_gpt(messages,api_key)

    return {
        "deepseek_response": deepseek_response,
        "gemini_response": gemini_response,
        "gpt_response": gpt_response
    }



