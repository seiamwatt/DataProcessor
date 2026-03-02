import uuid
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
from openai import OpenAI
from google import genai
from google.genai import types

def load_csv(file_path):
    
    try:
        file = pd.read_csv(file_path,encoding="utf-8")
        return file
    except Exception as e:
        print(f"Error: {e}")
        return None
    
def need_ocr(pdf_reader) -> bool:
    test_text = ""

    for page in range(min(15,len(pdf_reader.pages))):
        test_text += pdf_reader.pages[page].extract_text() or " "
        

    if len(test_text) < 50:
        return True
    
    return False

def IMG_to_pdf(file_path):
    output_path = file_path
    ocrmypdf.ocr(file_path,output_file=output_path,deskew=True)
    
    return output_path


def extract_pdf_text(pdf_url,max_pages=None):
    if max_pages is None:
        max_pages = 2000

    tmp_path = None
    try:
        response = requests.get(pdf_url,timeout=30)
        response.raise_for_status()

        # use bytesIO so data does not need to be saved in disk
        pdf_buffer = BytesIO(response.content)
        pdf_reader = PyPDF2.PdfReader(pdf_buffer)

        if need_ocr(pdf_reader=pdf_reader):
            with tempfile.NamedTemporaryFile(suffix=".pdf",delete=False) as tmp:
                tmp.write(response.content)
                tmp_path = tmp.name
            
            IMG_to_pdf(tmp_path)
            # Re-read the OCR'd PDF
            pdf_reader = PyPDF2.PdfReader(tmp_path)

        text = ""
        num_pages = min(len(pdf_reader.pages),max_pages)

        for page in range(num_pages):
            text += pdf_reader.pages[page].extract_text() or ""

        return text
    
    except Exception as e:
        print(f"error: {e}")
        return None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    
def format_api_output(input):
    cleaned = input.strip().strip("```json").strip("```").strip()
    return json.loads(cleaned)
    
def create_prompt_Y1(pdf_url, pdf_text):
    return f"""You are an expert political and policy analyst. Analyze the following report and answer the question precisely in the specified format. Do not add extra commentary beyond what is requested.

SOURCE URL: {pdf_url}

--- BEGIN REPORT TEXT ---
{pdf_text}
--- END REPORT TEXT ---

**Y1 – Main Issue (max 10 words):**
Identify the single most prominent issue of concern in this report. Reply in 10 words or fewer.

Respond ONLY with valid JSON:
{{
  "Y1": "<main issue in ≤10 words>"
}}"""

def create_prompt_Y2(pdf_url, pdf_text,Y1_response):
    return f"""You are an expert political and policy analyst. Analyze the following report and answer the question precisely in the specified format. Do not add extra commentary beyond what is requested.

SOURCE URL: {pdf_url}

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


def create_prompt_Y3(pdf_url, pdf_text,Y1_response,Y2_response):
    return f"""You are an expert political and policy analyst. Analyze the following report and answer the question precisely in the specified format. Do not add extra commentary beyond what is requested.

SOURCE URL: {pdf_url}

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


def create_prompt_Y4(pdf_url, pdf_text):
    return f"""You are an expert political and policy analyst. Analyze the following report and answer the question precisely in the specified format. Do not add extra commentary beyond what is requested.

SOURCE URL: {pdf_url}

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


def connect_to_DeepSeek(api_key,prompt,chat_model=None,max_tries=None):

    if chat_model is None:
        chat_model='deepseek-chat'

    if max_tries is None:
        max_tries = 8

    for attempt in range(1,max_tries + 1):

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

            response = requests.post(api_url,headers=headers,json=payload,timeout=60)
            response.raise_for_status()

            result = response.json()
            content = result["choices"][0]["message"]["content"]

            return format_api_output(content)
     
        except Exception as e:
            if(attempt < max_tries):
                time.sleep(2 ** attempt)
            else:
                print("all tries completed")
                return None

def connect_to_GPT(api_key,prompt,chat_model=None,max_tries=None):

    if chat_model is None:
        chat_model = "gpt-4.1"

    
    if max_tries is None:
        max_tries = 8

    for attempt in range(1,max_tries + 1):
        try:
            client = OpenAI(api_key=api_key)

            response = client.chat.completions.create(
                model=chat_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=3000,
                temperature=0,
            )

            content = response.choices[0].message.content
            return format_api_output(content)


        except Exception as e:
            print(f"Attempt:{attempt} failed")
            print(f"Error: {e}")

            if(attempt < max_tries):
                time.sleep(2 ** attempt)
            else:
                print("all tries completed")
                return None
            
def connect_to_Gemini(api_key,prompt,chat_model=None,max_tries=None):

    if chat_model is None:
        chat_model = "gemini-3-flash-preview"
        
    if max_tries is None:
        max_tries = 8

    for attempt in range(1,max_tries + 1):
        try:
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model = chat_model,
                contents = prompt,
                config = types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(thinking_level="high",include_thoughts=False),
                    max_output_tokens=3000
                ) 
            )  

            content = response.text
            return format_api_output(content)

        except Exception as e:
            print(f"Attempt:{attempt} failed")
            print(f"Error: {e}")
            if(attempt < max_tries):
                time.sleep(2 ** attempt)
            else:
                print("all tries completed")
                return None

def batch_processing(df_batch, pdf_url_column, deepseek_key, gemini_key, gpt_key, batch_num=0, total_rows=0, rows_done=0):

    results = []
    batch_size = len(df_batch)

    for rows_in_batch, (idx, row) in enumerate(df_batch.iterrows(), 1):
        current_total = rows_done + rows_in_batch
        print(f"\n--- Row {current_total}/{total_rows} (batch {batch_num}, row {rows_in_batch}/{batch_size}) ---")

        result_row_DeepSeek = row.to_dict()
        result_row_Gemini = row.to_dict()
        result_row_GPT = row.to_dict()

        pdf_url = row[pdf_url_column]

        print(f"  Downloading PDF: {pdf_url[:100]}...")
        start_time = time.time()
        pdf_txt = extract_pdf_text(pdf_url=pdf_url)
        elapsed = time.time() - start_time

        if pdf_txt is None:
            print(f"  PDF extraction FAILED ({elapsed:.1f}s)")
        else:
            print(f"  PDF extracted: {len(pdf_txt)} chars, {elapsed:.1f}s")

        # DeepSeek Analysis (chained: Y2 depends on Y1, Y3 depends on Y1+Y2)
        print(f"  Calling DeepSeek Y1-Y4...", end=" ", flush=True)
        ds_start = time.time()
        prompt_Y1 = create_prompt_Y1(pdf_url, pdf_txt)
        DeepSeek_analysis_Y1 = connect_to_DeepSeek(api_key=deepseek_key, prompt=prompt_Y1)
        prompt_Y2 = create_prompt_Y2(pdf_url, pdf_txt, DeepSeek_analysis_Y1)
        DeepSeek_analysis_Y2 = connect_to_DeepSeek(api_key=deepseek_key, prompt=prompt_Y2)
        prompt_Y3 = create_prompt_Y3(pdf_url, pdf_txt, DeepSeek_analysis_Y1, DeepSeek_analysis_Y2)
        DeepSeek_analysis_Y3 = connect_to_DeepSeek(api_key=deepseek_key, prompt=prompt_Y3)
        prompt_Y4 = create_prompt_Y4(pdf_url, pdf_txt)
        DeepSeek_analysis_Y4 = connect_to_DeepSeek(api_key=deepseek_key, prompt=prompt_Y4)
        ds_ok = all([DeepSeek_analysis_Y1, DeepSeek_analysis_Y2, DeepSeek_analysis_Y3, DeepSeek_analysis_Y4])
        print(f"{'OK' if ds_ok else 'FAILED'} ({time.time() - ds_start:.1f}s)")

        # GPT Analysis (chained)
        print(f"  Calling GPT Y1-Y4...", end=" ", flush=True)
        gpt_start = time.time()
        prompt_Y1 = create_prompt_Y1(pdf_url, pdf_txt)
        GPT_analysis_Y1 = connect_to_GPT(api_key=gpt_key, prompt=prompt_Y1)
        prompt_Y2 = create_prompt_Y2(pdf_url, pdf_txt, GPT_analysis_Y1)
        GPT_analysis_Y2 = connect_to_GPT(api_key=gpt_key, prompt=prompt_Y2)
        prompt_Y3 = create_prompt_Y3(pdf_url, pdf_txt, GPT_analysis_Y1, GPT_analysis_Y2)
        GPT_analysis_Y3 = connect_to_GPT(api_key=gpt_key, prompt=prompt_Y3)
        prompt_Y4 = create_prompt_Y4(pdf_url, pdf_txt)
        GPT_analysis_Y4 = connect_to_GPT(api_key=gpt_key, prompt=prompt_Y4)
        gpt_ok = all([GPT_analysis_Y1, GPT_analysis_Y2, GPT_analysis_Y3, GPT_analysis_Y4])
        print(f"{'OK' if gpt_ok else 'FAILED'} ({time.time() - gpt_start:.1f}s)")

        # Gemini Analysis (chained)
        print(f"  Calling Gemini Y1-Y4...", end=" ", flush=True)
        gem_start = time.time()
        prompt_Y1 = create_prompt_Y1(pdf_url, pdf_txt)
        Gemini_analysis_Y1 = connect_to_Gemini(api_key=gemini_key, prompt=prompt_Y1)
        prompt_Y2 = create_prompt_Y2(pdf_url, pdf_txt, Gemini_analysis_Y1)
        Gemini_analysis_Y2 = connect_to_Gemini(api_key=gemini_key, prompt=prompt_Y2)
        prompt_Y3 = create_prompt_Y3(pdf_url, pdf_txt, Gemini_analysis_Y1, Gemini_analysis_Y2)
        Gemini_analysis_Y3 = connect_to_Gemini(api_key=gemini_key, prompt=prompt_Y3)
        prompt_Y4 = create_prompt_Y4(pdf_url, pdf_txt)
        Gemini_analysis_Y4 = connect_to_Gemini(api_key=gemini_key, prompt=prompt_Y4)
        gem_ok = all([Gemini_analysis_Y1, Gemini_analysis_Y2, Gemini_analysis_Y3, Gemini_analysis_Y4])
        print(f"{'OK' if gem_ok else 'FAILED'} ({time.time() - gem_start:.1f}s)")

        # UUID generate
        temp_uuid = uuid.uuid4()
        if DeepSeek_analysis_Y1 and DeepSeek_analysis_Y2 and DeepSeek_analysis_Y3 and DeepSeek_analysis_Y4:
            result_row_DeepSeek["ID"] = temp_uuid
            result_row_DeepSeek["LLM"] = "DeepSeek"
            result_row_DeepSeek["Y1"] = DeepSeek_analysis_Y1.get("Y1", " ")
            result_row_DeepSeek["Y2"] = DeepSeek_analysis_Y2.get("Y2", " ")
            result_row_DeepSeek["Y3"] = DeepSeek_analysis_Y3.get("Y3", " ")
            result_row_DeepSeek["Y4"] = DeepSeek_analysis_Y4.get("Y4", " ")
        else:
            result_row_DeepSeek["ID"] = temp_uuid
            result_row_DeepSeek["LLM"] = "DeepSeek"
            result_row_DeepSeek["Y1"] = "API failed"
            result_row_DeepSeek["Y2"] = "API failed"
            result_row_DeepSeek["Y3"] = "API failed"
            result_row_DeepSeek["Y4"] = "API failed"

        if GPT_analysis_Y1 and GPT_analysis_Y2 and GPT_analysis_Y3 and GPT_analysis_Y4:
            result_row_GPT["ID"] = temp_uuid
            result_row_GPT["LLM"] = "GPT"
            result_row_GPT["Y1"] = GPT_analysis_Y1.get("Y1", " ")
            result_row_GPT["Y2"] = GPT_analysis_Y2.get("Y2", " ")
            result_row_GPT["Y3"] = GPT_analysis_Y3.get("Y3", " ")
            result_row_GPT["Y4"] = GPT_analysis_Y4.get("Y4", " ")
        else:
            result_row_GPT["ID"] = temp_uuid
            result_row_GPT["LLM"] = "GPT"
            result_row_GPT["Y1"] = "API failed"
            result_row_GPT["Y2"] = "API failed"
            result_row_GPT["Y3"] = "API failed"
            result_row_GPT["Y4"] = "API failed"

        if Gemini_analysis_Y1 and Gemini_analysis_Y2 and Gemini_analysis_Y3 and Gemini_analysis_Y4:
            result_row_Gemini["ID"] = temp_uuid
            result_row_Gemini["LLM"] = "Gemini"
            result_row_Gemini["Y1"] = Gemini_analysis_Y1.get("Y1", " ")
            result_row_Gemini["Y2"] = Gemini_analysis_Y2.get("Y2", " ")
            result_row_Gemini["Y3"] = Gemini_analysis_Y3.get("Y3", " ")
            result_row_Gemini["Y4"] = Gemini_analysis_Y4.get("Y4", " ")
        else:
            result_row_Gemini["ID"] = temp_uuid
            result_row_Gemini["LLM"] = "Gemini"
            result_row_Gemini["Y1"] = "API failed"
            result_row_Gemini["Y2"] = "API failed"
            result_row_Gemini["Y3"] = "API failed"
            result_row_Gemini["Y4"] = "API failed"

        results.append(result_row_DeepSeek)
        results.append(result_row_GPT)
        results.append(result_row_Gemini)

        print(f"  Row {current_total}/{total_rows} complete. DeepSeek={('OK' if ds_ok else 'FAIL')} | GPT={('OK' if gpt_ok else 'FAIL')} | Gemini={('OK' if gem_ok else 'FAIL')}")

    return pd.DataFrame(results)

def main():
    pass

if __name__ == "__main__":
    main()

# example command line: caffeinate -i python3 data_analysis.py --input PART1_COMBINED.csv --output PART2_COMBINED.CSV --pdf_url_column pdf_url --batch_size 5 --start_row 0