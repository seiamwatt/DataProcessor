"""
clean_csv.py
------------
Cleans a coded-data CSV so that:
  - TEXT columns: "999" and "Parsing Error" are replaced with "N/A"
  - INTEGER columns: "Parsing Error" is replaced with 999; existing 999 stays as-is
  - year column: "Parsing Error" is replaced with 999

Usage:
    python3 clean_csv.py <input_file.csv>

Install pandas first (one-time):
    pip3 install pandas --break-system-packages

The cleaned file is saved in the same folder as the input with "_cleaned"
appended (e.g. my_data.csv → my_data_cleaned.csv).
"""

import pandas as pd
import sys
import os


# ── resolve input / output paths ──────────────────────────────────────────
if len(sys.argv) < 2:
    print("Usage:  python3 clean_csv.py <input_file.csv>")
    print("Example: python3 clean_csv.py data_pdf_url_output.csv")
    sys.exit(1)

INPUT_PATH = sys.argv[1]

if not os.path.isfile(INPUT_PATH):
    print(f"Error: file not found → {INPUT_PATH}")
    sys.exit(1)

# Build output path: same folder as input, filename + "_cleaned"
folder = os.path.dirname(INPUT_PATH) or "."
filename = os.path.basename(INPUT_PATH)
base, ext = os.path.splitext(filename)
OUTPUT_PATH = os.path.join(folder, f"{base}_cleaned{ext}")


# ── read ───────────────────────────────────────────────────────────────────
df = pd.read_csv(INPUT_PATH, dtype=str)  # read everything as string first
print(f"Loaded  : {INPUT_PATH}")
print(f"Shape   : {df.shape[0]} rows × {df.shape[1]} columns")


# ── identify text columns among the coded data ────────────────────────────
# These suffixes mark columns whose content is descriptive text / labels,
# where "999" means "not applicable" and should become "N/A".
TEXT_SUFFIXES = (
    "_E",              # issue explanation columns (e.g. ds_ISS1_Econ_E)
    "_IntensityE",     # identity / overall intensity explanation
    "_OutGroupE",      # out-group explanation
    "_OutIntensityE",  # out-group intensity explanation
    "_UsVsThemE",      # us-vs-them explanation
    "_PrimName",       # identity primary name
    "_OutName",        # out-group name
    "_TypeName",       # meta type name
    "_OutGroup",       # YES / NO indicator (text)
    "_Org_Name",       # organization name (raw_pdfs format)
)

# Prefixes that mark coded-data columns (as opposed to metadata)
CODED_PREFIXES = ("ds_", "gm_", "gpt_")

text_cols = []
int_cols = []

for col in df.columns:
    if not any(col.startswith(p) for p in CODED_PREFIXES):
        continue  # skip metadata columns (org_id, pdf_url, year, etc.)
    if any(col.endswith(s) for s in TEXT_SUFFIXES):
        text_cols.append(col)
    else:
        int_cols.append(col)

print(f"\nText columns (999 → N/A):    {len(text_cols)}")
print(f"Integer columns (999 stays): {len(int_cols)}")


# ── clean: "Parsing Error" → 999 in int cols, → N/A in text cols ─────────
parse_fixes = 0
for col in int_cols:
    mask = df[col].astype(str).str.strip() == "Parsing Error"
    count = mask.sum()
    if count:
        df.loc[mask, col] = "999"
        parse_fixes += count

# Also fix "Parsing Error" in the year column (not coded, but should be int)
if "year" in df.columns:
    mask = df["year"].astype(str).str.strip() == "Parsing Error"
    count = mask.sum()
    if count:
        df.loc[mask, "year"] = "999"
        parse_fixes += count

print(f"\nParsing Error → 999 in integer columns: {parse_fixes}")

for col in text_cols:
    mask = df[col].astype(str).str.strip() == "Parsing Error"
    count = mask.sum()
    if count:
        df.loc[mask, col] = "N/A"

# ── clean: 999 → N/A in text columns ─────────────────────────────────────
replacements = 0
for col in text_cols:
    mask = df[col].astype(str).str.strip() == "999"
    count = mask.sum()
    if count:
        df.loc[mask, col] = "N/A"
        replacements += count

print(f"999 → N/A in text columns:             {replacements}")

# Quick sanity check: show a few integer columns that still have 999
sample_int = [c for c in int_cols if (df[c] == "999").any()][:5]
if sample_int:
    print(f"Verified: integer columns still contain 999 (e.g. {sample_int})")


# ── save ───────────────────────────────────────────────────────────────────
df.to_csv(OUTPUT_PATH, index=False)
print(f"\nCleaned file saved to: {OUTPUT_PATH}")