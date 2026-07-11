import uuid
import json
import requests
import pandas as pd
from dotenv import load_dotenv
from dataprocessor.config import load_env
import PyPDF2
from io import BytesIO
import time
import os
import glob
import ocrmypdf
import tempfile
import sys
import re
from openai import OpenAI
from google import genai
from google.genai import types
from rich.console import Console

console = Console()


def resource_path(relative_path):
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


load_env()
# PDF parsing ----------------------------------------------------------------

def need_ocr(pdf_reader) -> bool:
    test_text = ""
    for page in range(min(15, len(pdf_reader.pages))):
        test_text += pdf_reader.pages[page].extract_text() or " "

    if len(test_text) < 50:
        return True
    return False


def IMG_to_pdf(file_path):
    output_path = file_path
    ocrmypdf.ocr(file_path, output_file=output_path, deskew=True)
    return output_path


def extract_pdf_text(pdf_path, max_pages=None):
    """Read a local PDF file. OCR fallback if text is too sparse."""
    if max_pages is None:
        max_pages = 2000

    tmp_path = None
    try:
        pdf_reader = PyPDF2.PdfReader(pdf_path)

        if need_ocr(pdf_reader):
            # copy to temp file so ocrmypdf can overwrite it
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                with open(pdf_path, "rb") as src:
                    tmp.write(src.read())
                tmp_path = tmp.name

            IMG_to_pdf(tmp_path)
            pdf_reader = PyPDF2.PdfReader(tmp_path)

        text = ""
        num_pages = min(len(pdf_reader.pages), max_pages)

        for page in range(num_pages):
            text += pdf_reader.pages[page].extract_text() or ""

        return text

    except Exception as e:
        print(f"  PDF extraction error: {e}")
        return None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


# JSON helpers ---------------------------------------------------------------

def _fix_json_strings(s):
    """Escape raw newlines/tabs inside JSON string values."""
    result = []
    in_string = False
    i = 0
    while i < len(s):
        ch = s[i]
        if not in_string:
            result.append(ch)
            if ch == '"':
                in_string = True
        else:
            if ch == '\\':
                result.append(ch)
                if i + 1 < len(s):
                    i += 1
                    result.append(s[i])
            elif ch == '"':
                result.append(ch)
                in_string = False
            elif ch == '\n':
                result.append('\\n')
            elif ch == '\r':
                result.append('\\r')
            elif ch == '\t':
                result.append('\\t')
            else:
                result.append(ch)
        i += 1
    return ''.join(result)


def format_api_output(input):
    if not input or not input.strip():
        return None

    cleaned = input.strip()
    cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
    cleaned = re.sub(r'\s*```$', '', cleaned)
    cleaned = cleaned.strip()

    candidates = [cleaned]
    no_trailing = re.sub(r',\s*([}\]])', r'\1', cleaned)
    candidates.append(no_trailing)
    candidates.append(_fix_json_strings(cleaned))
    candidates.append(_fix_json_strings(no_trailing))

    match = re.search(r'\{[\s\S]*\}', cleaned)
    if match:
        extracted = match.group()
        candidates.append(extracted)
        candidates.append(_fix_json_strings(re.sub(r',\s*([}\]])', r'\1', extracted)))

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue

    return None


# LLM connectors ------------------------------------------------------------

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
                "max_tokens": 5000,
                "temperature": 0,
            }
            response = requests.post(api_url, headers=headers, json=payload, timeout=60)
            response.raise_for_status()
            result = response.json()
            content = result["choices"][0]["message"]["content"]
            return format_api_output(content)

        except Exception as e:
            print(f"    DeepSeek attempt {attempt} failed: {e}")
            if attempt < max_tries:
                time.sleep(2 ** attempt)
            else:
                print("    All DeepSeek tries exhausted")
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
            print(f"    GPT attempt {attempt} failed: {e}")
            if attempt < max_tries:
                time.sleep(2 ** attempt)
            else:
                print("    All GPT tries exhausted")
                return None


def connect_to_Gemini(api_key, prompt, chat_model=None, max_tries=None):
    if chat_model is None:
        chat_model = "gemini-3.1-pro-preview"
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
                    max_output_tokens=5000,
                ),
            )
            content = response.text
            return format_api_output(content)

        except Exception as e:
            print(f"    Gemini attempt {attempt} failed: {e}")
            if attempt < max_tries:
                time.sleep(2 ** attempt)
            else:
                print("    All Gemini tries exhausted")
                return None


# Prompt ---------------------------------------------------------------------

def create_prompt(pdf_filename, pdf_text):
    input_prompt = f"""You are a rigorous content analyst coding an organization's annual report. Analyze the following document and return ONLY a valid JSON object with no other text, no markdown fences, and no preamble.

CRITICAL RULE FOR MISSING DATA:
- For NUMERIC fields (scores, category codes): use 999 if the document does not contain enough information to determine a valid answer.
- For TEXT fields (names, explanations, justifications): use "N/A" if the document does not contain enough information to determine a valid answer.

PDF File: {pdf_filename}

PDF TEXT:
{pdf_text}

INSTRUCTIONS:
For each variable below, read the full document carefully and assign scores based on the definitions provided. If the document does not provide enough information to confidently code a variable, assign 999 for numeric fields or "N/A" for text fields.

SECTION 1: IDENTITY CODING

ID_Primary (1-12, or 999 if unable to determine): Which group identity is MOST CENTRAL to this organization's work and narrative?
1=Economic Class (working class, poor, low-income, workers, unions)
2=Wealthy/Business (entrepreneurs, business owners, job creators, investors)
3=Religious/Faith (Christians, religious communities, people of faith)
4=Racial/Ethnic Minorities (Black, Latino, Asian, Indigenous, people of color)
5=National/Patriots (Americans, citizens, the nation, national interest)
6=Ideological Conservative (conservatives, traditional values, right)
7=Ideological Progressive (progressives, liberals, left, social justice advocates)
8=Women/Gender (women, girls, gender equality)
9=LGBTQ+ (LGBTQ+ people, queer communities, sexual minorities)
10=Universal/Humanity (all people, humanity, global citizens, everyone)
11=Policy/Technocratic (policymakers, researchers, evidence-based community)
12=Multiple/No Dominant (no single clear identity OR multiple equally important)

ID_Intensity (0-100, or 999 if unable to determine): How central and pervasive is this identity throughout the report?
0=Absent: Identity is not mentioned or relevant to the organization's work
25=Mentioned: Identity appears occasionally but is not central to narrative
50=Important: Identity is clearly present and structures some discussion
75=Very Central: Identity is repeatedly emphasized and shapes most content
100=Dominant: Identity is constant, pervasive, and defines entire organizational narrative

ID_OutGroup (YES, NO, or N/A if unable to determine): Does this report identify a clear out-group — people or institutions that are opposed, criticized, or framed as adversaries?

ID_OutType (13-24, or 999 if unable to determine): If an out-group exists, which identity best describes them?
13=Economic Elites (wealthy, corporations, Wall Street, billionaires)
14=Poor/Welfare Recipients (people on welfare, non-workers, dependents)
15=Religious Conservatives (religious right, Christian fundamentalists)
16=Secular/Non-Religious (secularists, atheists, anti-religion)
17=Racial Majority/White (white people, white supremacy, systemic racism)
18=Immigrants/Foreigners (illegal immigrants, non-citizens, outsiders)
19=Liberals/Progressives/Left (Democrats, socialists, woke ideology, the left)
20=Conservatives/Right (Republicans, MAGA, far-right, fascists)
21=LGBTQ+ Community (trans activists, LGBTQ+ agenda)
22=Government/Bureaucrats (big government, bureaucracy, administrative state)
23=Multiple Groups (several distinct out-groups)
24=N/A (no out-group)

ID_OutIntensity (0-100, or 999 if unable to determine): How strongly does this report oppose, criticize, or vilify the out-group(s)?
0=No Opposition: No out-group OR out-group mentioned neutrally
25=Mild Critique: Disagrees professionally without harsh language
50=Clear Opposition: Identifies adversaries, uses critical language
75=Strong Opposition: Harsh criticism, attributes bad motives, "they threaten us"
100=Vilification: Extreme language, existential threat, enemy language, dehumanization

ID_UsVsThem (0-100, or 999 if unable to determine): Overall us-vs-them polarization.
0=No Polarization: Universal or neutral; no us-vs-them framing
25=Low Polarization: Some identity but inclusive; acknowledges common ground
50=Moderate: Clear in-group identity with some contrast to others
75=High Polarization: Strong us-vs-them; our group vs. their group central to narrative
100=Maximum Polarization: Intense tribal framing; zero-sum conflict; we win or they win

SECTION 2: ISSUE POSITIONS (each has Position 0-100 and Intensity 0-100, or 999 if unable to determine)

ISS1 Economic Policy:
Position: 0=Strong Left (maximum government intervention, high taxes on wealthy, extensive regulation, strong redistribution, pro-labor/union) to 50=Centrist/Mixed (pragmatic balance, case-by-case, or not addressed) to 100=Strong Right (free markets, minimal regulation, low taxes, oppose redistribution, individual economic responsibility).
Intensity: 0=Not Addressed (no mention of economic policy) to 50=Moderate Advocacy (clear position but professional tone, acknowledges complexity) to 100=Intense Advocacy (central focus, urgent language, no acknowledgment of tradeoffs).

ISS2 Social/Cultural Values:
Position: 0=Strong Progressive (abortion rights, LGBTQ+ equality including trans rights, strict church-state separation, diverse family structures) to 50=Centrist/Mixed (mixed positions, moderate, or not addressed) to 100=Strong Traditional (pro-life, traditional marriage, religious freedom/accommodation, traditional gender roles and family).
Intensity: 0=Not Addressed to 50=Moderate Advocacy to 100=Intense Advocacy.

ISS3 Racial Justice & Equality:
Position: 0=Systemic Racism Frame (structural/systemic racism is central problem; support for racial equity policies, affirmative action, reparations, explicit racial justice focus) to 50=Mixed/Moderate (acknowledges racial issues but mixed approach, or not addressed) to 100=Colorblind/Merit Frame (opposes race-based policies, emphasizes individual merit, "all lives matter," opposes DEI, critical race theory).
Intensity: 0=Not Addressed to 50=Moderate Advocacy to 100=Intense Advocacy.

ISS4 Immigration:
Position: 0=Pro-Immigration (path to citizenship, immigrant rights, asylum protection, oppose deportation/ICE, immigration as positive) to 50=Mixed/Moderate (balanced approach, or not addressed) to 100=Restrictionist (border security, enforcement, oppose amnesty, limit immigration, immigration as problem).
Intensity: 0=Not Addressed to 50=Moderate Advocacy to 100=Intense Advocacy.

ISS5 Environmental Protection & Climate:
Position: 0=Strong Environmental (climate change is urgent crisis, aggressive regulation, rapid transition from fossil fuels, environmental justice focus) to 50=Mixed/Moderate (balanced approach, or not addressed) to 100=Pro-Development (market-based solutions only, oppose climate regulations, skeptical of climate urgency, prioritize economic growth over environment).
Intensity: 0=Not Addressed to 50=Moderate Advocacy to 100=Intense Advocacy.

ISS6 Government Size & Role:
Position: 0=Expansive Government (government should expand to solve problems, more programs/spending, government as solution) to 50=Mixed/Moderate (focus on efficiency over size, or not addressed) to 100=Limited Government (reduce government size/scope, private sector solutions, government is problem not solution).
Intensity: 0=Not Addressed to 50=Moderate Advocacy to 100=Intense Advocacy.

ISS7 Globalism vs. Nationalism:
Position: 0=Globalist/Internationalist (strong international cooperation, multilateralism, global institutions, international law, human rights abroad) to 50=Mixed/Moderate (case-by-case, or not addressed) to 100=Nationalist/America First (national sovereignty priority, skeptical of international institutions, America first, limited foreign engagement).
Intensity: 0=Not Addressed to 50=Moderate Advocacy to 100=Intense Advocacy.

ISS8 Individual vs. Systemic Solutions:
Position: 0=Systemic Focus (problems from structural barriers, institutional failures; solutions require systemic change, policy reform, regulation) to 50=Mixed (combines individual and structural factors) to 100=Individual Focus (problems from individual choices, behavior, culture; solutions through individual responsibility, character, values).
Intensity: 0=Not Addressed to 50=Moderate Advocacy to 100=Intense Advocacy.

SECTION 3: OVERALL
OVERALL_Intensity (0-100, or 999 if unable to determine): How ideologically intense is this annual report's language, framing, and tone?
0=Neutral/Technocratic: Academic, objective language; avoids normative claims; presents findings as tentative
25=Professional Advocacy: Clear values but measured tone; acknowledges complexity
50=Committed Advocacy: Strong normative positions; passionate but professional
75=Intense/Urgent: Moral urgency; combative language; high stakes framing
100=Maximum Intensity: Apocalyptic, existential threat language; extreme us-vs-them; vilification of opponents

SECTION 4: METADATA
META_Type (1-4, or 999 if unable to determine):
1=Think Tank (research and policy analysis)
2=Advocacy Group (campaigns, lobbying, mobilization)
3=Foundation (primarily grantmaking)
4=Hybrid (combines multiple types)

REMEMBER:
- Use 999 for NUMERIC fields (scores, category codes) where the document does not provide sufficient information.
- Use "N/A" for TEXT fields (names, explanations, justifications) where the document does not provide sufficient information.

Return ONLY this JSON structure with no additional text:
{{
  "Org_Name": "<organization name or N/A>",
  "Year": "<year of annual report or N/A>",
  "ID_Primary": <1-12 or 999>,
  "ID_PrimName": "<text name or N/A>",
  "ID_Intensity": <0-100 or 999>,
  "ID_IntensityE": "<2-3 sentence justification with quotes/examples, or N/A>",
  "ID_OutGroup": "<YES, NO, or N/A>",
  "ID_OutGroupE": "<1-2 sentences describing out-group, or N/A>",
  "ID_OutType": <13-24 or 999>,
  "ID_OutName": "<text name or N/A>",
  "ID_OutIntensity": <0-100 or 999>,
  "ID_OutIntensityE": "<2-3 sentences with examples of oppositional language, or N/A>",
  "ID_UsVsThem": <0-100 or 999>,
  "ID_UsVsThemE": "<2-3 sentences describing polarization dynamic, or N/A>",
  "ISS1_Econ_Pos": <0-100 or 999>,
  "ISS1_Econ_Int": <0-100 or 999>,
  "ISS1_Econ_E": "<2-3 sentences or N/A>",
  "ISS2_Social_Pos": <0-100 or 999>,
  "ISS2_Social_Int": <0-100 or 999>,
  "ISS2_Social_E": "<2-3 sentences or N/A>",
  "ISS3_Race_Pos": <0-100 or 999>,
  "ISS3_Race_Int": <0-100 or 999>,
  "ISS3_Race_E": "<2-3 sentences or N/A>",
  "ISS4_Immig_Pos": <0-100 or 999>,
  "ISS4_Immig_Int": <0-100 or 999>,
  "ISS4_Immig_E": "<2-3 sentences or N/A>",
  "ISS5_Enviro_Pos": <0-100 or 999>,
  "ISS5_Enviro_Int": <0-100 or 999>,
  "ISS5_Enviro_E": "<2-3 sentences or N/A>",
  "ISS6_Govt_Pos": <0-100 or 999>,
  "ISS6_Govt_Int": <0-100 or 999>,
  "ISS6_Govt_E": "<2-3 sentences or N/A>",
  "ISS7_Global_Pos": <0-100 or 999>,
  "ISS7_Global_Int": <0-100 or 999>,
  "ISS7_Global_E": "<2-3 sentences or N/A>",
  "ISS8_IndSys_Pos": <0-100 or 999>,
  "ISS8_IndSys_Int": <0-100 or 999>,
  "ISS8_IndSys_E": "<2-3 sentences or N/A>",
  "OVERALL_Intensity": <0-100 or 999>,
  "OVERALL_IntensityE": "<2-3 sentences with specific examples, or N/A>",
  "META_Type": <1-4 or 999>,
  "META_TypeName": "<Think Tank, Advocacy Group, Foundation, Hybrid, or N/A>"
}}"""
    return input_prompt


# Result field list ----------------------------------------------------------

JSON_FIELDS = [
    "Org_Name", "Year",
    "ID_Primary", "ID_PrimName", "ID_Intensity", "ID_IntensityE",
    "ID_OutGroup", "ID_OutGroupE", "ID_OutType", "ID_OutName",
    "ID_OutIntensity", "ID_OutIntensityE", "ID_UsVsThem", "ID_UsVsThemE",
    "ISS1_Econ_Pos", "ISS1_Econ_Int", "ISS1_Econ_E",
    "ISS2_Social_Pos", "ISS2_Social_Int", "ISS2_Social_E",
    "ISS3_Race_Pos", "ISS3_Race_Int", "ISS3_Race_E",
    "ISS4_Immig_Pos", "ISS4_Immig_Int", "ISS4_Immig_E",
    "ISS5_Enviro_Pos", "ISS5_Enviro_Int", "ISS5_Enviro_E",
    "ISS6_Govt_Pos", "ISS6_Govt_Int", "ISS6_Govt_E",
    "ISS7_Global_Pos", "ISS7_Global_Int", "ISS7_Global_E",
    "ISS8_IndSys_Pos", "ISS8_IndSys_Int", "ISS8_IndSys_E",
    "OVERALL_Intensity", "OVERALL_IntensityE",
    "META_Type", "META_TypeName",
]

# Fields that expect numeric values (use 999 as default)
NUMERIC_FIELDS = {
    "ID_Primary", "ID_Intensity", "ID_OutType", "ID_OutIntensity", "ID_UsVsThem",
    "ISS1_Econ_Pos", "ISS1_Econ_Int",
    "ISS2_Social_Pos", "ISS2_Social_Int",
    "ISS3_Race_Pos", "ISS3_Race_Int",
    "ISS4_Immig_Pos", "ISS4_Immig_Int",
    "ISS5_Enviro_Pos", "ISS5_Enviro_Int",
    "ISS6_Govt_Pos", "ISS6_Govt_Int",
    "ISS7_Global_Pos", "ISS7_Global_Int",
    "ISS8_IndSys_Pos", "ISS8_IndSys_Int",
    "OVERALL_Intensity",
    "META_Type",
}

# All other fields are text (use "N/A" as default)


def _default_for_field(field_name):
    """Return the appropriate missing-data sentinel for a given field."""
    if field_name in NUMERIC_FIELDS:
        return 999
    return "N/A"


# Single-PDF processing ------------------------------------------------------

def pdf_processing(pdf_path, deepseek_key, gemini_key, gpt_key):
    """Process one local PDF file. Returns a dict (one row) with ds_/gm_/gpt_ prefixed fields."""

    result_row = {"PDF": os.path.basename(pdf_path)}

    start_time = time.time()
    pdf_txt = extract_pdf_text(pdf_path)
    elapsed = time.time() - start_time

    if pdf_txt is None:
        print(f"  PDF extraction FAILED ({elapsed:.1f}s)")
        return result_row
    else:
        print(f"  PDF extracted: {len(pdf_txt)} chars, {elapsed:.1f}s")

    pdf_filename = os.path.basename(pdf_path)
    prompt = create_prompt(pdf_filename=pdf_filename, pdf_text=pdf_txt)

    print("  Calling DeepSeek...", end=" ", flush=True)
    deepseek_output = connect_to_DeepSeek(api_key=deepseek_key, prompt=prompt)
    print("OK" if deepseek_output else "FAILED")

    print("  Calling Gemini...", end=" ", flush=True)
    gemini_output = connect_to_Gemini(api_key=gemini_key, prompt=prompt)
    print("OK" if gemini_output else "FAILED")

    print("  Calling GPT...", end=" ", flush=True)
    gpt_output = connect_to_GPT(api_key=gpt_key, prompt=prompt)
    print("OK" if gpt_output else "FAILED")

    llm_outputs = {
        "ds": deepseek_output,
        "gm": gemini_output,
        "gpt": gpt_output,
    }

    for prefix, output in llm_outputs.items():
        if output:
            for field in JSON_FIELDS:
                result_row[f"{prefix}_{field}"] = output.get(field, _default_for_field(field))
        else:
            for field in JSON_FIELDS:
                result_row[f"{prefix}_{field}"] = "Parsing Error"

    return result_row


# Directory processing -------------------------------------------------------

def process_directory(pdf_dir, output_csv, deepseek_key, gemini_key, gpt_key):
    """Iterate through all PDFs in a directory, process each, save results to CSV."""

    pdf_files = sorted(glob.glob(os.path.join(pdf_dir, "**", "*.pdf"), recursive=True))

    if not pdf_files:
        print(f"No PDF files found in {pdf_dir}")
        return pd.DataFrame()

    print(f"Found {len(pdf_files)} PDF(s) in {pdf_dir}\n")

    results = []

    for i, pdf_path in enumerate(pdf_files, 1):
        print(f"[{i}/{len(pdf_files)}] {os.path.basename(pdf_path)}")

        row = pdf_processing(
            pdf_path=pdf_path,
            deepseek_key=deepseek_key,
            gemini_key=gemini_key,
            gpt_key=gpt_key,
        )
        results.append(row)

        # incremental save
        df = pd.DataFrame(results)
        df.to_csv(output_csv, index=False)
        print(f"  -> Saved {len(df)} rows to {output_csv}\n")

    final = pd.DataFrame(results)
    print(f"Done. {len(final)} rows across {len(pdf_files)} PDFs.")
    return final