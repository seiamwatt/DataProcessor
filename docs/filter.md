# Annual Report PDF Classifier

A Python tool that automatically classifies PDF documents as annual reports using the DeepSeek API. It extracts text from PDFs, sends it to an LLM for analysis, and returns structured classification results including confidence level and detected report year.

---

## Overview

This script processes a CSV file containing PDF URLs, downloads and extracts text from each PDF, then uses the DeepSeek reasoning model to determine whether each document is an annual report. Results are enriched with classification metadata (confidence, reasoning, and detected year) and returned as a DataFrame.

---

## Dependencies

| Package | Purpose |
|---|---|
| `requests` | Downloading PDFs and calling the DeepSeek API |
| `pandas` | Reading CSVs and managing tabular results |
| `PyPDF2` | Extracting text from PDF files |
| `ocrmypdf` | OCR fallback for image-based or scanned PDFs |
| `python-dotenv` | Loading environment variables from `.env` files |
| `tqdm` | Progress bars (imported but not actively used in shown code) |
| `rich` | Console formatting for debug output |

---

## Core Functions

### `load_csv(file_path)`

Reads a CSV file into a Pandas DataFrame using UTF-8 encoding. Returns `None` on failure.

### `need_ocr(pdf_reader) → bool`

Checks whether a PDF requires OCR by extracting text from the first 15 pages. If the combined text is fewer than 50 characters, the PDF is considered image-based and OCR is needed.

### `IMG_to_pdf(file_path)`

Runs `ocrmypdf` on a PDF file in-place, applying deskew correction. Converts scanned/image-based PDFs into searchable text PDFs.

### `extract_pdf_text(pdf_url, max_pages=15)`

Downloads a PDF from a URL and extracts its text content. The workflow is:

1. Fetch the PDF via HTTP (30-second timeout).
2. Wait 30 seconds after download (rate-limiting pause).
3. Read the PDF with PyPDF2.
4. If OCR is needed, write to a temp file, run OCR, then re-read.
5. Extract text from up to `max_pages` pages.
6. Return the first 200,000 characters.

### `create_prompt(pdf_url, pdf_text=None)`

Builds the LLM prompt for classification. Two variants exist:

- **With text** — includes the extracted document text and asks the model to analyze content directly.
- **Without text** — relies solely on the PDF URL for clues (e.g., filenames like `2022-annual-report.pdf`).

The prompt instructs the model to return a JSON object with four fields: `is_annual_report`, `confidence`, `reason`, and `year`.

### `DeepSeek_Connect(api_key, prompt, model="deepseek-reasoner")`

Sends a prompt to the DeepSeek API and parses the JSON response. Handles markdown code fences that the model sometimes wraps around its output. Returns the parsed JSON as a Python dict, or `None` on failure.

**API configuration:**

- Endpoint: `https://api.deepseek.com/v1/chat/completions`
- Temperature: `0` (deterministic output)
- Max tokens: `2000`

### `batch_processing(df_batch, api_key, pdf_url_column, extract_text=True)`

Processes a batch of rows from a DataFrame. For each row:

1. Reads the PDF URL from the specified column.
2. Optionally extracts PDF text.
3. Builds a prompt and calls the DeepSeek API.
4. Appends classification fields (`is_annual_report`, `confidence`, `classification_reason`, `year`) to the original row.
5. Pauses 0.5 seconds between rows to avoid rate limiting.

Returns a new DataFrame with all original columns plus the classification columns.

---

## Classification Output Schema

Each processed row is enriched with these fields:

| Field | Type | Description |
|---|---|---|
| `is_annual_report` | `bool / None` | Whether the document is an annual report |
| `confidence` | `str` | `"high"`, `"medium"`, `"low"`, or `"failed"` |
| `classification_reason` | `str` | Brief explanation from the model |
| `year` | `int / None` | Four-digit year the report covers (e.g., `2023`) |

---

## Usage

```python
import pandas as pd
from dotenv import load_dotenv
import os

load_dotenv()
api_key = os.getenv("DEEPSEEK_API_KEY")

df = pd.read_csv("pdfs.csv")
results = batch_processing(df, api_key, pdf_url_column="pdf_url", extract_text=True)
results.to_csv("classified_results.csv", index=False)
```

The input CSV must contain a column with publicly accessible PDF URLs. Pass the column name as `pdf_url_column`.

---

## Notes

- **Rate limiting** — `extract_pdf_text` includes a hard-coded 30-second sleep after each download. Combined with the 0.5-second inter-row pause in `batch_processing`, throughput is roughly one document per 30–60 seconds.
- **OCR fallback** — scanned PDFs are automatically detected and processed with `ocrmypdf`. This requires Tesseract to be installed on the system.
- **Text truncation** — extracted text is capped at 200,000 characters to stay within LLM context limits.
- **`main()` is a no-op** — the entry point is currently empty; the script is designed to be imported and called programmatically.