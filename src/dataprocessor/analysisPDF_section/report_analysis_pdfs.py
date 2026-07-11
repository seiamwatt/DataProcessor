import uuid
import json
import argparse
import requests
import pandas as pd
from dotenv import load_dotenv
from dataprocessor.config import load_env
from tqdm import tqdm
import PyPDF2
from io import BytesIO
import time
import os
import ocrmypdf
import tempfile
from openai import OpenAI
from google import genai
from google.genai import types
import sys


def resource_path(relative_path):
    """Get path for bundled files (works for both dev and PyInstaller)"""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


load_env()
def extract_pdf_text(pdf_file, maxPages=None):
    if maxPages is None:
        maxPages = 4000

    try:
        pdf_reader = PyPDF2.PdfReader(pdf_file)

        text = ""
        num_pages = min(len(pdf_reader.pages), maxPages)

        # BUG FIX: was `for page in num_pages` — can't iterate over an int
        for page in range(num_pages):
            text += pdf_reader.pages[page].extract_text() or ""

        return text
    except Exception as e:
        print(f"PDF extraction error: {e}")
        return None


def format_api_output(input):
    cleaned = input.strip().strip("```json").strip("```").strip()
    return json.loads(cleaned)


# Y1 prompt call will also return org_id and year published
def create_prompt_Y1(pdf_text):
    return f"""You are an expert political and policy analyst. Analyze the following report and answer the questions precisely in the specified format. Do not add extra commentary beyond what is requested.



--- BEGIN REPORT TEXT ---
{pdf_text}
--- END REPORT TEXT ---

**Y1 – Main Issue (max 10 words):**
Identify the single most prominent issue of concern in this report. Reply in 10 words or fewer.

**ORG – Organization Abbreviation:**
Identify the organization that published this report and provide its common abbreviation (e.g., "CBO", "GAO", "WHO").

**YEAR – Year Published:**
Identify the year this report was published.

Respond ONLY with valid JSON:
{{
  "Y1": "<main issue in ≤10 words>",
  "ORG": "<organization abbreviation>",
  "YEAR": "<four-digit year>"
}}"""


def create_prompt_Y2(pdf_text, Y1_response):
    return f"""You are an expert political and policy analyst. Analyze the following report and answer the question precisely in the specified format. Do not add extra commentary beyond what is requested.



The issue is: {Y1_response}

--- BEGIN REPORT TEXT ---
{pdf_text}
--- END REPORT TEXT ---

**Y2 – Position Strength (integer 0–100):**

Rate the organization's stance on the above issue on a **continuous scale from 0 to 100**.

- 0 means the organization is as strongly IN FAVOR / supportive as possible.
- 100 means the organization is as strongly AGAINST / opposed as possible.
- 50 means genuinely neutral or no clear position.

**Important:** Use the FULL range of the scale with precision. Values like 12, 37, 63, 84 are expected and encouraged. Do NOT round to the nearest 25. Think of this as a thermometer — place your rating at the exact point that best reflects the strength and direction of the stance.

To calibrate your answer, use a two-step process:
1. **Direction:** First decide — is the organization supportive (0–45), neutral (46–54), or opposed (55–100)?
2. **Degree:** Then pinpoint exactly HOW supportive or opposed. For example:
   - Enthusiastic, active advocacy → 0–10
   - Clear support with some caveats → 15–30
   - Leaning supportive but weak signal → 35–45
   - Truly ambiguous or silent → 46–54
   - Leaning opposed but weak signal → 55–65
   - Clear opposition with reasoning → 70–85
   - Aggressive, active opposition → 86–100

Respond ONLY with valid JSON:
{{ "Y2": <integer 0-100> }}"""


def create_prompt_Y3(pdf_text, Y1_response, Y2_response):
    return f"""You are an expert political and policy analyst. Analyze the following report and answer the question precisely in the specified format. Do not add extra commentary beyond what is requested.

--- BEGIN REPORT TEXT ---
{pdf_text}
--- END REPORT TEXT ---

**Y3 – Supporting Evidence (1–2 direct quotes):**
Issue: {Y1_response}
Reponse: {Y2_response}
Extract one or two verbatim quotes from the report that best justify the main issue and position strength above. Include only exact text from the document.

Respond ONLY with valid JSON:
{{
  "Y3": ["<quote 1>", "<quote 2>"]
}}"""


def create_prompt_Y4(pdf_text):
    return f"""You are an expert political and policy analyst. Analyze the following report and answer the question precisely in the specified format. Do not add extra commentary beyond what is requested.


--- BEGIN REPORT TEXT ---
{pdf_text}
--- END REPORT TEXT ---

**Y4 – Political / Cultural Orientation (integer 1–5):**
Classify the publishing organization's overall political, social, and cultural leaning:
  1 = very liberal / left
  2 = moderately liberal / left
  3 = centrist / middle of the road
  4 = moderately conservative / right
  5 = very conservative / right
Return only an integer.

Respond ONLY with valid JSON:
{{
  "Y4": <integer 1-5>
}}"""


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


def connect_to_GPT(api_key, prompt, chat_model=None, max_tries=None):
    if chat_model is None:
        chat_model = "gpt-5.1"

    if max_tries is None:
        max_tries = 8

    for attempt in range(1, max_tries + 1):
        try:
            client = OpenAI(api_key=api_key)

            response = client.chat.completions.create(
                model=chat_model,
                messages=[{"role": "user", "content": prompt}],
                max_completion_tokens=3000,
                temperature=0,
            )

            content = response.choices[0].message.content
            return format_api_output(content)

        except Exception as e:
            print(f"GPT attempt {attempt} failed: {e}")

            if attempt < max_tries:
                time.sleep(2**attempt)
            else:
                print("All GPT tries exhausted")
                return None


def connect_to_Gemini(api_key, prompt, chat_model=None, max_tries=None):
    if chat_model is None:
        chat_model = "gemini-3-flash-preview"

    if max_tries is None:
        max_tries = 8

    for attempt in range(1, max_tries + 1):
        try:
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=chat_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(thinking_level="high", include_thoughts=False),
                    max_output_tokens=3000
                ),
            )

            content = response.text
            return format_api_output(content)

        except Exception as e:
            print(f"Gemini attempt {attempt} failed: {e}")
            if attempt < max_tries:
                time.sleep(2**attempt)
            else:
                print("All Gemini tries exhausted")
                return None


def pdf_processing(deepseek_key, gemini_key, gpt_key, pdf_file):
    try:
        start_time = time.time()
        pdf_txt = extract_pdf_text(pdf_file=pdf_file)
        elapsed = time.time() - start_time

        results = []
        result_row_DeepSeek = {}
        result_row_Gemini = {}
        result_row_GPT = {}

        if pdf_txt is None:
            print(f"  PDF extraction FAILED ({elapsed:.1f}s)")
            # BUG FIX: return empty DataFrame instead of continuing with None text
            return pd.DataFrame()
        else:
            print(f"  PDF extracted: {len(pdf_txt)} chars, {elapsed:.1f}s")

        prompt_Y1 = create_prompt_Y1(pdf_text=pdf_txt)
        Y1_all = connect_to_GPT(api_key=gpt_key, prompt=prompt_Y1)

        if Y1_all is None:
            print("  Y1 call failed — skipping this PDF")
            return pd.DataFrame()

        # BUG FIX: extract the Y1 string instead of passing the whole dict
        Y1_issue = Y1_all.get("Y1", "")
        Y1_org = Y1_all.get("ORG", "")
        Y1_year = Y1_all.get("YEAR", "")

        # --- DeepSeek Analysis (chained: Y2 depends on Y1, Y3 depends on Y1+Y2) ---
        print(f"  Calling DeepSeek Y2-Y4...", end=" ", flush=True)
        ds_start = time.time()
        prompt_Y2 = create_prompt_Y2(pdf_txt, Y1_issue)
        DeepSeek_analysis_Y2 = connect_to_DeepSeek(
            api_key=deepseek_key, prompt=prompt_Y2
        )
        prompt_Y3 = create_prompt_Y3(
            pdf_txt, Y1_issue, DeepSeek_analysis_Y2.get("Y2", "") if DeepSeek_analysis_Y2 else ""
        )
        DeepSeek_analysis_Y3 = connect_to_DeepSeek(
            api_key=deepseek_key, prompt=prompt_Y3
        )
        prompt_Y4 = create_prompt_Y4(pdf_txt)
        DeepSeek_analysis_Y4 = connect_to_DeepSeek(
            api_key=deepseek_key, prompt=prompt_Y4
        )
        ds_ok = all(
            [DeepSeek_analysis_Y2, DeepSeek_analysis_Y3, DeepSeek_analysis_Y4]
        )
        print(f"{'OK' if ds_ok else 'FAILED'} ({time.time() - ds_start:.1f}s)")

        # --- GPT Analysis (chained) ---
        print(f"  Calling GPT Y2-Y4...", end=" ", flush=True)
        gpt_start = time.time()
        prompt_Y2 = create_prompt_Y2(pdf_txt, Y1_issue)
        GPT_analysis_Y2 = connect_to_GPT(api_key=gpt_key, prompt=prompt_Y2)
        prompt_Y3 = create_prompt_Y3(
            pdf_txt, Y1_issue, GPT_analysis_Y2.get("Y2", "") if GPT_analysis_Y2 else ""
        )
        GPT_analysis_Y3 = connect_to_GPT(api_key=gpt_key, prompt=prompt_Y3)
        prompt_Y4 = create_prompt_Y4(pdf_txt)
        GPT_analysis_Y4 = connect_to_GPT(api_key=gpt_key, prompt=prompt_Y4)
        gpt_ok = all([GPT_analysis_Y2, GPT_analysis_Y3, GPT_analysis_Y4])
        print(f"{'OK' if gpt_ok else 'FAILED'} ({time.time() - gpt_start:.1f}s)")

        # --- Gemini Analysis (chained) ---
        print(f"  Calling Gemini Y2-Y4...", end=" ", flush=True)
        gem_start = time.time()
        prompt_Y2 = create_prompt_Y2(pdf_txt, Y1_issue)
        Gemini_analysis_Y2 = connect_to_Gemini(
            api_key=gemini_key, prompt=prompt_Y2
        )
        prompt_Y3 = create_prompt_Y3(
            pdf_txt, Y1_issue, Gemini_analysis_Y2.get("Y2", "") if Gemini_analysis_Y2 else ""
        )
        Gemini_analysis_Y3 = connect_to_Gemini(
            api_key=gemini_key, prompt=prompt_Y3
        )
        prompt_Y4 = create_prompt_Y4(pdf_txt)
        Gemini_analysis_Y4 = connect_to_Gemini(
            api_key=gemini_key, prompt=prompt_Y4
        )
        gem_ok = all([Gemini_analysis_Y2, Gemini_analysis_Y3, Gemini_analysis_Y4])
        print(f"{'OK' if gem_ok else 'FAILED'} ({time.time() - gem_start:.1f}s)")

        temp_uuid = str(uuid.uuid4())

        # --- Build result rows ---
        def build_row(llm_name, y2_result, y3_result, y4_result):
            row = {
                "ID": temp_uuid,
                "LLM": llm_name,
                "ORG": Y1_org,
                "YEAR": Y1_year,
                "Y1": Y1_issue,
                "PDF": os.path.basename(pdf_file),
            }
            if y2_result and y3_result and y4_result:
                row["Y2"] = y2_result.get("Y2", "")
                row["Y3"] = y3_result.get("Y3", "")
                row["Y4"] = y4_result.get("Y4", "")
            else:
                row["Y2"] = "API failed"
                row["Y3"] = "API failed"
                row["Y4"] = "API failed"
            return row

        results.append(
            build_row("DeepSeek", DeepSeek_analysis_Y2, DeepSeek_analysis_Y3, DeepSeek_analysis_Y4)
        )
        results.append(
            build_row("GPT", GPT_analysis_Y2, GPT_analysis_Y3, GPT_analysis_Y4)
        )
        results.append(
            build_row("Gemini", Gemini_analysis_Y2, Gemini_analysis_Y3, Gemini_analysis_Y4)
        )

        return pd.DataFrame(results)

    except Exception as e:
        print(f"pdf_processing error: {e}")
        # BUG FIX: return empty DataFrame instead of None so .to_csv() won't crash
        return pd.DataFrame()


def main():
    pass


if __name__ == "__main__":
    main()