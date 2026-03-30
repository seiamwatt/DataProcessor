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

