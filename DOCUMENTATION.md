# DataProcessor — Project Documentation

A terminal application for collecting U.S. nonprofit data and running large-language-model (LLM) analysis over the PDF annual reports those organizations publish. It is an interactive CLI built with [Rich](https://github.com/Textualize/rich) (styled output) and [questionary](https://github.com/tmbo/questionary) (prompts), packaged as a standalone executable with PyInstaller.

---

## 1. What it does

The app supports an end-to-end pipeline:

1. **Collect** nonprofit organizations from the **ProPublica Nonprofit Explorer API** (filtered by NTEE category, state, and ruling year) into a CSV.
2. **Filter** a CSV of PDF URLs — download each PDF, extract its text (OCR if needed), and ask an LLM whether it is an *annual report* (and what year it covers).
3. **Analyze** the annual reports — send each PDF's text to **three LLMs in parallel** (DeepSeek, GPT, Gemini) and extract a structured, ~40-field political/cultural content coding of the organization.

Results are written to CSV progressively (one batch at a time, appended) and, for the filter step, uploaded to AWS S3.

---

## 2. Tech stack & dependencies

| Concern | Library |
|---|---|
| Terminal UI / styling | `rich` |
| Interactive prompts | `questionary` |
| Data handling | `pandas` |
| HTTP | `requests` |
| PDF text extraction | `PyPDF2` |
| OCR (image-based PDFs) | `ocrmypdf` |
| LLM clients | `openai` (DeepSeek + GPT), `google-genai` (Gemini) |
| Cloud storage | `boto3` (AWS S3) |
| Config | `python-dotenv` |
| Timezones | `pytz` |
| Packaging | `pyinstaller` |

> ⚠️ There is **no `requirements.txt`** in the repo. The list above is derived from imports. Consider adding one.

---

## 3. Project structure

```
DataProcessor/
├── main.spec / V1.1.0.spec      # PyInstaller build specs (bundle .env as data)
├── .env                          # secrets + version (gitignored)
├── output.csv                    # sample data
└── src/
    ├── main.py                   # ENTRY POINT — top-level menu / router
    │
    ├── main_section/             # app shell
    │   ├── main_page.py          #   welcome screen, process overviews, LLM status
    │   ├── main_util.py          #   LLM connectivity test functions
    │   └── settings_page.py      #   shows API keys + live online/offline status
    │
    ├── propublica_section/       # data collection
    │   ├── propublica_UI.py      #   prompts, session loop, CSV append
    │   └── propublica_logic.py   #   ProPublica API search + detail + filtering
    │
    ├── filter_section/           # annual-report classification
    │   ├── filter_page.py        #   prompts, batch loop, S3 upload
    │   └── report_filter.py      #   PDF extract + DeepSeek classify
    │
    ├── analysis_v2_section/      # CURRENT analysis engine (3-model coding)
    │   ├── analysis_v2_page.py   #   prompts, batch loop, CSV append
    │   └── analysis_v2.py        #   PDF extract + DeepSeek/GPT/Gemini + JSON parse
    │
    ├── analysisPDF_v2_section/   # variant: analysis from raw local PDF files
    ├── analysis_section/         # LEGACY v1 analysis (still wired into menu)
    ├── analysisPDF_section/      # LEGACY v1 raw-PDF analysis
    │
    ├── Spider_section/           # UNFINISHED web crawler (stubs only)
    │   ├── spider.py
    │   └── spider_UI.py
    │
    └── util/
        └── clean_csv.py          # standalone CSV cleaning helper
```

### Section pattern
Most features follow a **UI / logic split**:
- `*_page.py` / `*_UI.py` — Rich/questionary presentation, input gathering, the batch loop, and CSV/S3 output.
- `report_*.py` / `*_logic.py` / `analysis_v2.py` — the actual work: HTTP calls, PDF parsing, LLM calls, data shaping.

---

## 4. How it runs

### Entry point — `src/main.py`
1. Imports every section module.
2. `main_page.show()` prints the welcome banner, LLM token-cost table, process overviews, and live LLM status.
3. A `questionary.select` menu routes to one section's `show()`:

| Menu choice | Module | Purpose |
|---|---|---|
| Filter Data | `filter_page` | Classify PDFs as annual reports (DeepSeek) |
| LLM Analysis - PDF URL | `analysis_page` | **v1** analysis from PDF URLs |
| LLM Analysis - Raw PDF | `analysisPDF_page` | **v1** analysis from local PDFs |
| LLM Analysis V2 | `analysis_v2_page` | **v2** 3-model analysis from PDF URLs |
| LLM Analysis V2 - Raw PDF | `analysisPDF_v2_page` | **v2** 3-model analysis from local PDFs |
| Propublica API | `propublica_UI` | Collect orgs from ProPublica |
| Settings | `settings_page` | View keys + connection status |

Each section runs its own **interactive session loop** (`while status:`), asking to repeat or exit when a run finishes.

---

## 5. Feature details

### 5.1 ProPublica collection (`propublica_section`)
`populate_data(num_pages, ntee_catagory_id, start_state_index, end_state_index)`:
- Iterates over a fixed list of **57 U.S. states/territories** (sliced by start/end index) and paginates each one via `GET /nonprofits/api/v2/search.json` filtered by `ntee[id]` + `state[id]`.
- For each org, calls the detail endpoint `/organizations/{ein}.json` — but only if the NTEE code's first letter is in a target set (`R,X,C,D,B,P,A,N`) or the code ends in `01`.
- Keeps orgs with **ruling year ≤ 2000**, extracting name, EIN, NTEE code, ruling date, address, revenue, assets.
- Built-in `time.sleep` calls (5s between search pages, 1s between detail calls) to respect rate limits.
- Results appended to the chosen CSV (creates `output.csv` if a directory is given).

### 5.2 Filter / annual-report classification (`filter_section`)
Per row of the input CSV (`pdf_url` column by default):
1. `extract_pdf_text()` — download the PDF, read up to 15 pages with PyPDF2. If <50 chars of text are found, it's treated as image-based and run through **`ocrmypdf`** first. Returns first **2000 chars**. (Note: there is a hard-coded `time.sleep(30)` after each download.)
2. `create_prompt()` — builds a prompt asking DeepSeek to decide *is this an annual report?* and *what year does it cover?*
3. `DeepSeek_Connect()` — calls `deepseek-reasoner`, strips markdown fences, parses JSON.
4. Adds `is_annual_report`, `confidence`, `classification_reason`, `year` columns.

The page loop processes in **batches**, appends each batch to the output CSV, then `upload_to_s3()` pushes both input and output files to the `dataprocessor-input-bucket` / `dataprocessor-output-bucket` buckets (region `us-east-2`), timestamped in America/Chicago time.

### 5.3 Analysis V2 (`analysis_v2_section`) — the main analytical engine
Per row:
1. `extract_pdf_text()` — same download/OCR approach but extracts the **full document** (uses a temp file for OCR and cleans it up in `finally`).
2. `create_prompt()` — a large structured-coding prompt instructing the model to act as a "rigorous content analyst" and return **only JSON** with ~40 fields across four sections:
   - **Section 1 — Identity coding:** primary in-group identity (1–12), intensity, out-group presence/type (13–24), us-vs-them polarization.
   - **Section 2 — Issue positions:** 8 issues (economy, social/cultural, race, immigration, environment, government size, globalism, individual-vs-systemic), each with a Position 0–100 and Intensity 0–100.
   - **Section 3 — Overall ideological intensity (0–100).**
   - **Section 4 — Metadata:** organization type (think tank / advocacy / foundation / hybrid).
   - Missing data convention: **`999` for numeric fields, `"N/A"` for text fields.**
3. The same prompt is sent to **all three models** — `connect_to_DeepSeek` (`deepseek-chat`), `connect_to_Gemini` (`gemini-3.1-pro-preview`, high thinking), `connect_to_GPT` (`gpt-5.1`) — each with retry/backoff.
4. `format_api_output()` robustly parses the JSON (strips fences, removes trailing commas, escapes raw newlines, regex-extracts the `{...}` block, tries multiple candidates).
5. Each model's fields are flattened onto the result row with a prefix: **`ds_`, `gm_`, `gpt_`** (e.g. `ds_ID_Primary`, `gpt_OVERALL_Intensity`). On parse failure, fields are set to `"Parsing Error"`.

Output is appended to CSV per batch.

### 5.4 Settings (`main_section/settings_page.py`)
Displays the configured API keys and runs live connectivity tests (`main_util.DeepSeek_connect_test` / `GTP_connect_test` / `Gemini_connect_test`) showing each model **Online/Offline**.

---

## 6. Configuration

Configuration is loaded from a **`.env`** file (gitignored, bundled into the executable via the `.spec` `datas=[('.env', '.')]` rule). `resource_path()` resolves it for both dev and PyInstaller (`sys._MEIPASS`) runs.

Required variables:

```dotenv
DeepSeek_key=...    # DeepSeek API key
GPT_key=...         # OpenAI API key
Gemini_key=...      # Google Gemini API key
version=...         # version string shown in the UI
```

AWS credentials for the S3 upload step are expected via the standard boto3 mechanisms (env vars / `~/.aws/credentials` / IAM role) — they are **not** read from `.env`.

---

## 7. External services

- **ProPublica Nonprofit Explorer API** — `https://projects.propublica.org/nonprofits/api/v2` (no key required).
- **DeepSeek** — `https://api.deepseek.com` (`deepseek-chat`, `deepseek-reasoner`).
- **OpenAI / GPT** — via `openai` SDK (`gpt-5.1`, `gpt-4.1` for the connection test).
- **Google Gemini** — via `google-genai` (`gemini-3.1-pro-preview`).
- **AWS S3** — buckets `dataprocessor-input-bucket`, `dataprocessor-output-bucket` (region `us-east-2`).

---

## 8. Running & building

### Run from source
```bash
cd src
python3 main.py
```
(Run from `src/` so the `from <section> import ...` package-style imports resolve.)

### Build a standalone executable
```bash
pyinstaller --onefile --add-data ".env:." src/main.py
# or use the committed spec:
pyinstaller main.spec
```
The spec bundles `.env` as data and builds a single console executable named `main`.

---

## 9. Data flow summary

```
ProPublica API ──> orgs CSV ──> [Filter: download PDF + OCR + DeepSeek] ──> annual-reports CSV
                                                                                │
                                                                                ▼
                                          [Analysis V2: download PDF + 3 LLMs] ──> coded CSV
                                          (ds_*, gm_*, gpt_* columns per row)
```

---

## 10. Notes, caveats & cleanup opportunities

- **v1 vs v2 duplication.** `analysis_section` / `analysisPDF_section` (v1) and `analysis_v2_section` / `analysisPDF_v2_section` (v2) coexist, and both v1 menu entries are still wired into `main.py`. v2 (3-model coding) is the current engine; v1 appears to be superseded.
- **Spider section is unfinished.** `Spider_section/spider.py` and `spider_UI.py` are scaffolding/stubs (commented-out crawler stages, empty functions) and not reachable from the menu.
- **Hard-coded delays.** The filter step sleeps **30s after every PDF download** (`report_filter.extract_pdf_text`), which dominates runtime — verify this is intentional.
- **No dependency manifest.** Add a `requirements.txt`/`pyproject.toml` to make the environment reproducible.
- **Secrets.** `.env` is correctly gitignored; AWS creds rely on ambient configuration.
- **Imports are heavy and duplicated** across UI modules (the full Rich import block is copy-pasted into most `*_page.py` files) — a shared UI helper module would reduce drift.
