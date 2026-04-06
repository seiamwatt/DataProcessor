# Multi-LLM Ideological Content Analyzer

A Python pipeline that extracts text from organizational annual reports and sends each document through three LLMs (DeepSeek, GPT, Gemini) to produce a comprehensive ideological and policy content analysis. Unlike the earlier chained-prompt version, this script uses a single large prompt per model and returns all results in one row per document with prefixed columns for each model.

---

## Overview

The script processes a CSV of PDF URLs. For each document it downloads and extracts the text, builds a detailed coding prompt, sends that same prompt to all three models, then flattens each model's JSON response into prefixed columns (`ds_`, `gm_`, `gpt_`) on a single output row. This makes cross-model comparison straightforward at the row level without needing a shared UUID.

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
| `rich` | Console formatting |

---

## Coding Framework

The prompt asks each model to act as a content analyst and return a single JSON object covering four sections and 40+ fields.

### Section 1 — Identity Coding

| Field | Type | Description |
|---|---|---|
| `ID_Primary` | 1–12 / 999 | Which group identity is most central to the organization |
| `ID_PrimName` | text | Human-readable label for the primary identity |
| `ID_Intensity` | 0–100 / 999 | How pervasive that identity is throughout the report |
| `ID_IntensityE` | text | Justification with quotes/examples |
| `ID_OutGroup` | YES / NO / N/A | Whether the report identifies a clear adversary |
| `ID_OutGroupE` | text | Description of the out-group |
| `ID_OutType` | 13–24 / 999 | Category code for the out-group identity |
| `ID_OutName` | text | Human-readable label for the out-group |
| `ID_OutIntensity` | 0–100 / 999 | Strength of opposition toward the out-group |
| `ID_OutIntensityE` | text | Examples of oppositional language |
| `ID_UsVsThem` | 0–100 / 999 | Overall us-vs-them polarization score |
| `ID_UsVsThemE` | text | Description of the polarization dynamic |

Identity categories span economic class, racial/ethnic minorities, religious/faith communities, ideological camps, gender, LGBTQ+, universal/humanity, and policy/technocratic identities. Out-group categories mirror these with additions like government/bureaucrats and immigrants/foreigners.

### Section 2 — Issue Positions (ISS1–ISS8)

Eight policy domains, each scored on two axes:

| Axis | Scale | Meaning |
|---|---|---|
| Position (`_Pos`) | 0–100 | 0 = strong left/progressive, 50 = centrist/not addressed, 100 = strong right/conservative |
| Intensity (`_Int`) | 0–100 | 0 = not addressed, 50 = moderate advocacy, 100 = intense advocacy |

Each issue also has a free-text explanation field (`_E`).

| Code | Domain | Left Pole (0) | Right Pole (100) |
|---|---|---|---|
| ISS1 | Economic Policy | Government intervention, redistribution, pro-labor | Free markets, low taxes, minimal regulation |
| ISS2 | Social/Cultural Values | Abortion rights, LGBTQ+ equality, church-state separation | Pro-life, traditional marriage, religious freedom |
| ISS3 | Racial Justice | Systemic racism frame, equity policies | Colorblind/merit frame, oppose DEI |
| ISS4 | Immigration | Pro-immigration, path to citizenship | Restrictionist, border security |
| ISS5 | Environment & Climate | Climate urgency, aggressive regulation | Market solutions, skeptical of climate action |
| ISS6 | Government Size | Expansive government | Limited government |
| ISS7 | Globalism vs. Nationalism | Internationalist, multilateral cooperation | America First, national sovereignty |
| ISS8 | Individual vs. Systemic | Systemic/structural focus | Individual responsibility focus |

### Section 3 — Overall Intensity

| Field | Type | Description |
|---|---|---|
| `OVERALL_Intensity` | 0–100 / 999 | How ideologically intense the report's language and framing are |
| `OVERALL_IntensityE` | text | Specific examples supporting the rating |

### Section 4 — Metadata

| Field | Type | Description |
|---|---|---|
| `META_Type` | 1–4 / 999 | Organization type: Think Tank (1), Advocacy Group (2), Foundation (3), Hybrid (4) |
| `META_TypeName` | text | Human-readable type label |

### Missing Data Convention

Numeric fields use `999` and text fields use `"N/A"` when the document doesn't provide enough information. If a model's API call fails entirely, all fields are set to `"Parsing Error"`.

---

## Core Functions

### PDF Parsing

`load_csv`, `need_ocr`, `IMG_to_pdf`, and `extract_pdf_text` are identical to the v2 script. Key behaviors: OCR fallback via `ocrmypdf`, temp file cleanup in a `finally` block, and a default page limit of 2,000.

### JSON Repair

#### `_fix_json_strings(s)`
A character-level parser that escapes raw newlines, carriage returns, and tabs inside JSON string values. This handles the common case where an LLM inserts literal line breaks inside a quoted string.

#### `format_api_output(input)`
A multi-strategy JSON parser that tries increasingly aggressive repairs:

1. Strip markdown code fences.
2. Try parsing as-is.
3. Remove trailing commas before `}` or `]`.
4. Apply `_fix_json_strings`.
5. Extract the first `{...}` block via regex and retry.

Returns a parsed dict or `None` if all strategies fail.

### API Connectors

Same three connectors as v2 (`connect_to_DeepSeek`, `connect_to_GPT`, `connect_to_Gemini`) with the same retry patterns. DeepSeek's `max_tokens` is increased to 5,000 and Gemini's `max_output_tokens` is also 5,000 to accommodate the larger response schema.

> **Note:** The DeepSeek retry bug from v2 persists — the function returns `None` on the first exception rather than retrying.

### Prompt Construction

#### `create_prompt(pdf_url, pdf_text)`
A single comprehensive prompt replacing the four chained prompts (Y1–Y4) from v2. The model receives the full coding rubric and must return one JSON object with all fields. This eliminates inter-prompt dependencies and reduces API calls from 10+ per document to 3 (one per model).

### Field Definitions

`JSON_FIELDS` lists all expected keys in the response JSON. `NUMERIC_FIELDS` is the subset that should default to `999` on missing data; all others default to `"N/A"`. The helper `_default_for_field(field_name)` returns the appropriate sentinel.

### Batch Processing

#### `batch_processing(df_batch, pdf_url_column, deepseek_key, gemini_key, gpt_key, ...)`

For each row:

1. Download and extract PDF text.
2. Build one prompt and send it to all three models.
3. For each model, prefix every JSON field with `ds_`, `gm_`, or `gpt_` and merge into the original row.
4. Append a single result row.

Returns a DataFrame with the original columns plus ~120 new columns (40 fields × 3 models).

---

## Output Schema

Each input row produces **one output row** with the original columns plus prefixed analysis columns:

```
[original columns] | ds_Org_Name | ds_Year | ds_ID_Primary | ... | gm_Org_Name | ... | gpt_Org_Name | ...
```

Prefix mapping: `ds_` = DeepSeek, `gm_` = Gemini, `gpt_` = GPT.

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
results.to_csv("ideology_analysis.csv", index=False)
```

---

## Key Differences from v2

| Aspect | v2 (Chained Prompts) | v3 (Single Prompt) |
|---|---|---|
| Prompts per document | 10+ (Y1→Y2→Y3→Y4 × 3 models) | 3 (one per model) |
| Output rows per document | 3 (one per model) | 1 (all models side-by-side) |
| Analysis scope | 4 variables (Y1–Y4) | 40+ variables across 4 sections |
| Prompt chaining | Y2 depends on Y1; Y3 depends on Y1+Y2 | None — single independent prompt |
| JSON repair | Basic fence stripping | Multi-strategy with regex extraction and newline escaping |
| Shared Y1 | GPT determines Y1 for all models | Each model independently codes all fields |

---

## Notes

- **DeepSeek retry bug** — Same as v2: returns `None` on first failure instead of retrying. The backoff logic is commented out.
- **Large response size** — The expected JSON has 40+ fields including free-text justifications. The increased `max_tokens` (5,000) accommodates this, but very long explanations may still be truncated.
- **No text truncation** — Extracted PDF text is uncapped, so documents exceeding a model's context window will cause failures.
- **One row per document** — Unlike v2's three-row-per-document layout, this version merges all model outputs into a single wide row, simplifying downstream analysis but producing a very wide DataFrame (~120+ added columns).