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
        # edit amount of text extracted here
        return text[:2000]

    except Exception as e:
        print(f"error processing pdf: {e}")
        return None


def create_prompt(pdf_url, pdf_text=None):
    """Create prompt for DeepSeek to identify annual reports and their year"""

    if pdf_text:
        prompt = f"""Analyze the following document and determine if it is an ANNUAL REPORT.

An annual report typically:
- Provides a comprehensive overview of an organization's activities over the past year
- Includes financial statements, operations summary, achievements, and future outlook
- Is NOT just a Form 990 (tax filing), financial statement only, or other specific financial documents

Also identify the YEAR the annual report covers. This is the fiscal or calendar year the report is about (e.g. "2023 Annual Report" covers year 2023). Look for clues such as:
- The title (e.g. "Annual Report 2022")
- The fiscal year period mentioned (e.g. "For the year ended December 31, 2023")
- Date ranges in the financial statements
- If the report covers a fiscal year like "July 2022 - June 2023", use the ending year (2023)

PDF URL: {pdf_url}

Document text (first few pages):
{pdf_text}

Respond with ONLY a JSON object in this exact format: 1 for True and 0 for False
{{"is_annual_report": 1/0, "confidence": "high/medium/low", "reason": "brief explanation", "year": 2023}}

For the "year" field:
- Use a 4-digit integer (e.g. 2023) if you can determine the year
- Use null if you cannot determine the year or if it is not an annual report"""
    else:
        prompt = f"""Based on the PDF URL, determine if this document is likely an ANNUAL REPORT.

An annual report typically:
- Provides a comprehensive overview of an organization's activities over the past year
- Includes financial statements, operations summary, achievements, and future outlook
- Is NOT just a Form 990 (tax filing), financial statement only, or other specific financial documents

Also identify the YEAR the annual report covers based on any clues in the URL (e.g. "2022-annual-report.pdf" suggests year 2022).

PDF URL: {pdf_url}

Respond with ONLY a JSON object in this exact format: 1 for True and 0 for False
{{"is_annual_report": 1/0, "confidence": "high/medium/low", "reason": "brief explanation", "year": 2023}}

For the "year" field:
- Use a 4-digit integer (e.g. 2023) if you can determine the year of the report
- Use null if you cannot determine the year or if it is not an annual report"""

    return prompt


def DeepSeek_Connect(api_key, prompt, model="deepseek-v4-pro"):

    try:
        api_url = "https://api.deepseek.com/v1/chat/completions"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 2000,
            "temperature": 0,
        }

        response = requests.post(api_url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()

        result = response.json()
        content = result["choices"][0]["message"]["content"]

        # Strip markdown fences if present
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]

        return json.loads(content.strip())

    except Exception as e:
        print(f"error connecting to deepseek: {e}")
        return None

def identify_reports(pdf_url,api_key):
    try:
        extracted_text = extract_pdf_text(pdf_url=pdf_url)

        prompt = create_prompt(pdf_url,extracted_text)
        response = DeepSeek_Connect(api_key,prompt)
        if response is None:
            return None

        if response.get("is_annual_report",0) == 1:
            return 1
        return 0
    except Exception as e:
        print(e)
        return None


def batch_processing(df_batch, api_key,pdf_url_column, extract_text=True):
    """Process a batch of rows"""
    results = []

    for idx, row in df_batch.iterrows():
        # pdf_url = row["pdf_url"]
        pdf_url = row[pdf_url_column]

        # Extract PDF text if enabled
        pdf_text = None
        if extract_text:
            pdf_text = extract_pdf_text(pdf_url)
            if pdf_text:
                print(f"  Extracted {len(pdf_text)} characters from PDF")

        # Create prompt and call API
        prompt = create_prompt(pdf_url, pdf_text)
        classification = DeepSeek_Connect(api_key, prompt)

        if classification:
            print(f"  Result: {classification}")

            
            result_row = row.copy()
            result_row["is_annual_report"] = classification.get("is_annual_report", False)
            # console.print(f"[bold red]Classification {result_row['is_annual_report']}[/bold red]")
            result_row["confidence"] = classification.get("confidence", "unknown")
            result_row["classification_reason"] = classification.get("reason", "")

            detected_year = classification.get("year",None)
            if detected_year is not None:
                result_row["year"] = detected_year

            results.append(result_row)
        else:
            print(f"  Failed to classify")
            result_row = row.copy()
            result_row["is_annual_report"] = None
            result_row["confidence"] = "failed"
            result_row["classification_reason"] = "API call failed"
            results.append(result_row)

        # Small delay to avoid rate limiting
        time.sleep(0.5)

    return pd.DataFrame(results)

def main():
    pass

if __name__ == "__main__":
    main()

# command line to run

# python3 identify_reports.py --input all_orgs.csv --output results.csv

# Custom batch size and row range
# python3 identify_reports.py --input all_orgs.csv --output results.csv --batch_size 20 --start_row 0 --end_row 100

# Skip PDF text extraction (classify by URL only)
# python3 identify_reports.py --input all_orgs.csv --output results.csv --no_extract

# Only save rows classified as annual reports
# python3 identify_reports.py --input all_orgs.csv --output results.csv --annual_reports_only
