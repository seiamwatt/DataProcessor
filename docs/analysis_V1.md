# Multi-LLM Policy Report Analyzer

A Python pipeline that extracts text from PDF reports and sends them through three LLMs (DeepSeek, GPT, and Gemini) in parallel to produce structured political and policy analysis. Each document is evaluated on four dimensions (Y1–Y4) using a chained prompting strategy, and results from all three models are collected side-by-side for comparison.

---

## Overview

The script reads a CSV of PDF URLs, downloads and extracts text from each document, then runs a four-step analysis chain against DeepSeek, GPT, and Gemini. The prompts are **chained** — Y2 depends on the Y1 answer, and Y3 depends on both Y1 and Y2 — which forces each model to build on its own prior reasoning. A shared UUID links the three model rows back to the same source document.

---

## Dependencies

| Package | Purpose |
|---|---|
| `requests` | Downloading PDFs and calling the DeepSeek REST API |
| `pandas` | CSV I/O and result aggregation |
| `PyPDF2` | Text extraction from PDFs |
| `ocrmypdf` | OCR fallback for scanned/image-based PDFs (requires Tesseract) |
| `openai` | Official OpenAI SDK for GPT calls |
| `google-genai` | Google GenAI SDK for Gemini calls |
| `python-dotenv` | Loading API keys from `.env` files |
| `tqdm` | Progress bars (imported, not actively used in shown code) |

---

## Analysis Dimensions (Y1–Y4)

Each PDF is evaluated on four variables via separate prompts:

| Variable | Description | Format |
|---|---|---|
| **Y1** | Main issue of concern in the report | Free text, ≤ 10 words |
| **Y2** | Position strength on that issue (0 = strongly in favor, 100 = strongly opposed) | Integer 0–100 |
| **Y3** | Supporting evidence — verbatim quotes from the document | Array of 1–2 strings |
| **Y4** | Political/cultural orientation of the publishing organization (1 = very liberal, 5 = very conservative) | Integer 1–5 |

### Prompt Chaining

The prompts form a dependency chain:

```
Y1 (independent) ──► Y2 (receives Y1) ──► Y3 (receives Y1 + Y2)
Y4 (independent)
```

Y1 is determined once by GPT and shared across all three models so every model analyzes the same issue. Y2, Y3, and Y4 are then computed independently by each model.

---

## Core Functions

### PDF Parsing

#### `load_csv(file_path)`
Reads a CSV into a Pandas DataFrame (UTF-8). Returns `None` on failure.

#### `need_ocr(pdf_reader) → bool`
Samples the first 15 pages; returns `True` if fewer than 50 characters of text are found, indicating a scanned or image-based PDF.

#### `IMG_to_pdf(file_path)`
Runs `ocrmypdf` in-place with deskew correction, converting an image-based PDF into a searchable one.

#### `extract_pdf_text(pdf_url, max_pages=None)`
Downloads a PDF and extracts its full text. Key behavior:

- Default page limit is 2,000 (effectively unlimited for most documents).
- If OCR is needed, a temp file is created, OCR'd, re-read, then cleaned up in a `finally` block.
- Returns `None` on any failure.

### Prompt Construction

#### `create_prompt_Y1(pdf_url, pdf_text)`
Asks the model to identify the single most prominent issue of concern in ≤ 10 words.

#### `create_prompt_Y2(pdf_url, pdf_text, Y1_response)`
Asks the model to rate position strength on a 0–100 scale relative to the Y1 issue. Includes detailed calibration guidance (direction first, then degree).

#### `create_prompt_Y3(pdf_url, pdf_text, Y1_response, Y2_response)`
Asks for 1–2 verbatim quotes that justify the Y1 issue and Y2 rating.

#### `create_prompt_Y4(pdf_url, pdf_text)`
Asks for a 1–5 political/cultural orientation classification. Independent of Y1–Y3.

### API Connectors

All three connectors share the same pattern: retry up to `max_tries` times (default 8) with exponential backoff, parse the JSON response, and return a Python dict or `None`.

#### `format_api_output(input)`
Strips markdown code fences and parses the resulting string as JSON.

#### `connect_to_DeepSeek(api_key, prompt, chat_model=None, max_tries=None)`
Calls the DeepSeek REST API directly via `requests`. Default model: `deepseek-chat`. Temperature: `0`.

> **Note:** The current error handling returns `None` on the first failure rather than retrying. The retry loop and backoff logic are commented out.

#### `connect_to_GPT(api_key, prompt, chat_model=None, max_tries=None)`
Uses the OpenAI Python SDK. Default model: `gpt-5.1`. Temperature: `0`. Retries with exponential backoff (`2^attempt` seconds).

#### `connect_to_Gemini(api_key, prompt, chat_model=None, max_tries=None)`
Uses the Google GenAI SDK with thinking mode set to `"high"`. Default model: `gemini-3.1-pro-preview`. Retries with exponential backoff.

### Batch Processing

#### `batch_processing(df_batch, pdf_url_column, deepseek_key, gemini_key, gpt_key, batch_num=0, total_rows=0, rows_done=0)`

Processes a DataFrame of PDF URLs. For each row:

1. Downloads and extracts PDF text.
2. Calls GPT once for Y1 — this answer is shared across all three models.
3. Runs Y2, Y3, Y4 independently on each of the three models (DeepSeek, GPT, Gemini).
4. Generates a single UUID and assigns it to all three result rows so they can be linked.
5. Appends three rows per document (one per model) to the results list.

Returns a DataFrame with the original columns plus `ID`, `LLM`, `Y1`, `Y2`, `Y3`, and `Y4`.

---

## Output Schema

Each input row produces **three output rows** (one per model). Added columns:

| Column | Type | Description |
|---|---|---|
| `ID` | `UUID` | Shared identifier linking the three model results for the same document |
| `LLM` | `str` | `"DeepSeek"`, `"GPT"`, or `"Gemini"` |
| `Y1` | `str` | Main issue (≤ 10 words) |
| `Y2` | `int` | Position strength (0–100) |
| `Y3` | `list[str]` | 1–2 verbatim supporting quotes |
| `Y4` | `int` | Political orientation (1–5) |

If any model's API calls fail, all four Y-fields for that model are set to `"API failed"`.

---

## Usage

```python
import pandas as pd
from dotenv import load_dotenv
import os

load_dotenv()

df = pd.read_csv("reports.csv")
results = batch_processing(
    df_batch=df,
    pdf_url_column="pdf_url",
    deepseek_key=os.getenv("DEEPSEEK_API_KEY"),
    gemini_key=os.getenv("GEMINI_API_KEY"),
    gpt_key=os.getenv("OPENAI_API_KEY"),
    total_rows=len(df),
)
results.to_csv("analysis_results.csv", index=False)
```

---

## Notes

- **Shared Y1** — GPT determines the main issue once, and that answer is fed into all three models for Y2/Y3. This ensures cross-model comparability on the same issue.
- **DeepSeek retry bug** — The retry/backoff logic in `connect_to_DeepSeek` is commented out; the function currently returns `None` after the first failure instead of retrying.
- **Temp file cleanup** — `extract_pdf_text` uses a `finally` block to delete OCR temp files, unlike the first version of the script.
- **No text truncation** — Unlike the earlier classifier script, extracted text is not capped, so very large documents may exceed LLM context limits.
- **Rate of API calls** — Each document triggers up to 10 API calls (1 shared Y1 + 3 × 3 model-specific calls), so processing is I/O-bound. No inter-row sleep is added beyond the backoff on failures.