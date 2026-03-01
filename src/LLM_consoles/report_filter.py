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
    """Create prompt for DeepSeek to identify annual reports"""

    if pdf_text:
        prompt = f"""Analyze the following document and determine if it is an ANNUAL REPORT.

An annual report typically:
- Provides a comprehensive overview of an organization's activities over the past year
- Includes financial statements, operations summary, achievements, and future outlook
- Is NOT just a Form 990 (tax filing), financial statement only, or other specific financial documents

PDF URL: {pdf_url}

Document text (first few pages):
{pdf_text}

Respond with ONLY a JSON object in this exact format:
{{"is_annual_report": true/false, "confidence": "high/medium/low", "reason": "brief explanation"}}"""
    else:
        prompt = f"""Based on the PDF URL, determine if this document is likely an ANNUAL REPORT.

An annual report typically:
- Provides a comprehensive overview of an organization's activities over the past year
- Includes financial statements, operations summary, achievements, and future outlook
- Is NOT just a Form 990 (tax filing), financial statement only, or other specific financial documents

PDF URL: {pdf_url}

Respond with ONLY a JSON object in this exact format:
{{"is_annual_report": true/false, "confidence": "high/medium/low", "reason": "brief explanation"}}"""

    return prompt


def DeepSeek_Connect(api_key, prompt, model="deepseek-chat"):

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
            result_row["confidence"] = classification.get("confidence", "unknown")
            result_row["classification_reason"] = classification.get("reason", "")

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
    print("filter")

    load_dotenv()
    parser = argparse.ArgumentParser(description="Identify annual reports from PDF URLs using DeepSeek")
    parser.add_argument("--input", type=str, required=True, help="input CSV file path")
    parser.add_argument("--output", type=str, required=True, help="output CSV file path")
    parser.add_argument("--batch_size", type=int,default=10,help="number of rows to process in each batch",)
    parser.add_argument("--start_row", type=int, default=0, help="start row (0-indexed)")
    parser.add_argument("--end_row", type=int, default=None, help="end row (exclusive)")
    parser.add_argument("--no_extract",action="store_true",help="skip PDF text extraction, use URL only",)
    parser.add_argument("--annual_reports_only",action="store_true",help="only save rows classified as annual reports",)
    parser.add_argument("--pdf_url_column",type=str,help="column name of the pdf url")

    args = parser.parse_args()

    api_key = os.getenv("DeepSeek_key")
    if not api_key:
        print("Error: DeepSeek_key not found in environment variables")
        return

    print(f"Loading CSV from {args.input}...")
    df = load_csv(args.input)
    if df is None:
        return

    start_row = args.start_row
    end_row = args.end_row if args.end_row is not None else len(df)

    print(f"Processing rows {start_row} to {end_row}")
    df_subset = df.iloc[start_row:end_row]

    # Process in batches
    all_results = []
    total_batches = (len(df_subset) + args.batch_size - 1) // args.batch_size

    pdf_url_col_name = None
    if args.pdf_url_column is None:
        pdf_url_col_name = "pdf_url"
    else:
        pdf_url_col_name = args.pdf_url_column

    for i in range(0, len(df_subset), args.batch_size):
        batch_num = i // args.batch_size + 1
        print(f"\n{'='*60}")
        print(f"Processing batch {batch_num}/{total_batches}")
        print(f"{'='*60}")

        batch = df_subset.iloc[i : i + args.batch_size]
        batch_results = batch_processing(df_batch=batch,api_key= api_key,pdf_url_column=pdf_url_col_name,extract_text=not args.no_extract)
        all_results.append(batch_results)

    # Combine all results
    if all_results:
        final_df = pd.concat(all_results, ignore_index=True)

        # Filter to only annual reports if requested
        if args.annual_reports_only:
            print(f"\nFiltering to only annual reports...")
            final_df = final_df[final_df["is_annual_report"] == True]
            print(
                f"Found {len(final_df)} annual reports out of {len(df_subset)} processed"
            )

        # Save results
        print(f"\nSaving results to {args.output}...")
        final_df.to_csv(args.output, index=False)
        print(f"Saved {len(final_df)} rows to {args.output}")

        # Print summary
        if not args.annual_reports_only:
            num_annual_reports = len(final_df[final_df["is_annual_report"] == True])
            num_not_annual_reports = len(final_df[final_df["is_annual_report"] == False])
            num_failed = len(final_df[final_df["is_annual_report"].isna()])

            print(f"\nSummary:")
            print(f"  Annual reports: {num_annual_reports}")
            print(f"  Not annual reports: {num_not_annual_reports}")
            print(f"  Failed classifications: {num_failed}")
    else:
        print("No results to save")


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
