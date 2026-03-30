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
from rich.console import Console
# SETUP -------------------------------------------------------------------------------------------------------
console = Console()

# PDF parsing ---------------------------------------------------------------------------------------------------------------
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
# ----------------------------------------------------------------------------------------------------------------------------------------------

# API CALLS ------------------------------------------------------------------------------------------------------------------------------------
    
def format_api_output(input):
    cleaned = input.strip().strip("```json").strip("```").strip()
    return json.loads(cleaned)

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
            time.sleep(1)
            print("all tries completed")
            return None
            
            # if(attempt < max_tries):
            #     time.sleep(2 ** attempt)
            # else:
            #     print("all tries completed")
            #     return None

def connect_to_GPT(api_key,prompt,chat_model=None,max_tries=None):

    if chat_model is None:
        chat_model = "gpt-5.1"

    
    if max_tries is None:
        max_tries = 8

    for attempt in range(1,max_tries + 1):
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
# ----------------------------------------------------------------------------------------------------------------------------
# Prompt --------------------------------------------------------------------------------------------------------------------------
def create_prompt(pdf_url, pdf_text):
    input_prompt = f"""You are a rigorous content analyst coding an organization's annual report. Analyze the following document and return ONLY a valid JSON object with no other text, no markdown fences, and no preamble.

CRITICAL RULE: If the document does not contain enough information to determine a valid answer for ANY field, use 999 as the value for that field. This applies to ALL numeric fields AND all text fields. 999 means "insufficient information to code."

PDF URL: {pdf_url}

PDF TEXT:
{pdf_text}

INSTRUCTIONS:
For each variable below, read the full document carefully and assign scores based on the definitions provided. If the document does not provide enough information to confidently code a variable, assign 999.

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

ID_OutGroup (YES, NO, or 999 if unable to determine): Does this report identify a clear out-group — people or institutions that are opposed, criticized, or framed as adversaries?

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

REMEMBER: Use 999 for ANY field where the document does not provide sufficient information to code a valid answer. This includes numeric scores, category codes, and text explanations (use "999" as a string for text fields).

Return ONLY this JSON structure with no additional text:
{{
  "Org_Name": "<organization name or 999>",
  "Year": "<year of annual report or 999>",
  "ID_Primary": <1-12 or 999>,
  "ID_PrimName": "<text name or 999>",
  "ID_Intensity": <0-100 or 999>,
  "ID_IntensityE": "<2-3 sentence justification with quotes/examples, or 999>",
  "ID_OutGroup": "<YES, NO, or 999>",
  "ID_OutGroupE": "<1-2 sentences describing out-group, 'No clear out-group identified', or 999>",
  "ID_OutType": <13-24 or 999>,
  "ID_OutName": "<text name, N/A, or 999>",
  "ID_OutIntensity": <0-100 or 999>,
  "ID_OutIntensityE": "<2-3 sentences with examples of oppositional language, or 999>",
  "ID_UsVsThem": <0-100 or 999>,
  "ID_UsVsThemE": "<2-3 sentences describing polarization dynamic, or 999>",
  "ISS1_Econ_Pos": <0-100 or 999>,
  "ISS1_Econ_Int": <0-100 or 999>,
  "ISS1_Econ_E": "<2-3 sentences or 999>",
  "ISS2_Social_Pos": <0-100 or 999>,
  "ISS2_Social_Int": <0-100 or 999>,
  "ISS2_Social_E": "<2-3 sentences or 999>",
  "ISS3_Race_Pos": <0-100 or 999>,
  "ISS3_Race_Int": <0-100 or 999>,
  "ISS3_Race_E": "<2-3 sentences or 999>",
  "ISS4_Immig_Pos": <0-100 or 999>,
  "ISS4_Immig_Int": <0-100 or 999>,
  "ISS4_Immig_E": "<2-3 sentences or 999>",
  "ISS5_Enviro_Pos": <0-100 or 999>,
  "ISS5_Enviro_Int": <0-100 or 999>,
  "ISS5_Enviro_E": "<2-3 sentences or 999>",
  "ISS6_Govt_Pos": <0-100 or 999>,
  "ISS6_Govt_Int": <0-100 or 999>,
  "ISS6_Govt_E": "<2-3 sentences or 999>",
  "ISS7_Global_Pos": <0-100 or 999>,
  "ISS7_Global_Int": <0-100 or 999>,
  "ISS7_Global_E": "<2-3 sentences or 999>",
  "ISS8_IndSys_Pos": <0-100 or 999>,
  "ISS8_IndSys_Int": <0-100 or 999>,
  "ISS8_IndSys_E": "<2-3 sentences or 999>",
  "OVERALL_Intensity": <0-100 or 999>,
  "OVERALL_IntensityE": "<2-3 sentences with specific examples, or 999>",
  "META_Type": <1-4 or 999>,
  "META_TypeName": "<Think Tank, Advocacy Group, Foundation, Hybrid, or 999>"
}}"""
    return input_prompt


# Batch Processing ------------------------------------------------------------------------------------------------------------------------

def batch_processing(df_batch,pdf_url_column,deepseek_key,gemini_key,gpt_key,batch_num=0,total_rows=0,rows_done=0):
    results = []
    batch_size = len(df_batch)

    for rows_in_batch,(idx,row) in enumerate(df_batch.iterrows(),1):
        current_total = rows_done + rows_in_batch
        print(f"\n--- Row {current_total}/{total_rows} (batch {batch_num}, row {rows_in_batch}/{batch_size}) ---")

        # convert df row to dict format
        result_row = row.to_dict()
        
        pdf_url = row[pdf_url_column]

        if not isinstance(pdf_url,str) or not pdf_url:
            continue

        print(f"  Downloading PDF: {pdf_url[:100]}...")
        start_time = time.time()
        pdf_txt = extract_pdf_text(pdf_url=pdf_url)
        elapsed = time.time() - start_time

        if pdf_txt is None:
            print(f"  PDF extraction FAILED ({elapsed:.1f}s)")
        else:
            print(f"  PDF extracted: {len(pdf_txt)} chars, {elapsed:.1f}s")

        
        prompt = create_prompt(pdf_url=pdf_url,pdf_text=pdf_txt)
        print(f"  Calling LLMs...", end=" ", flush=True)
        start_time = time.time()

        deepseek_output = connect_to_DeepSeek(api_key=deepseek_key,prompt=prompt)
        gemini_output = connect_to_Gemini(api_key=gemini_key,prompt=prompt)
        gpt_output = connect_to_GPT(api_key=gpt_key,prompt=prompt)

        LLM_status = all([deepseek_output,gemini_output,gpt_output])

        if LLM_status:
            console.print("[bold red]All LLM responded")
        else:
            console.print("[bold red]LLM response fail")

        
        JSON_FIELDS = [
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

        llm_outputs = {
            "ds": deepseek_output,
            "gm": gemini_output,
            "gpt": gpt_output,
        }

        for prefix, output in llm_outputs.items():
            for field in JSON_FIELDS:
                result_row[f"{prefix}_{field}"] = output.get(field, "")
            else:
                 result_row[f"{prefix}_{field}"] = "Parsing Error"
        
        results.append(result_row)

    return pd.DataFrame(results)

def main():
    pass

if __name__ == "__main__":
    main()

            






   
