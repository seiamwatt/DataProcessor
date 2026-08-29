import json
import re
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
from rich.console import Console
import sys, os
from rapidfuzz import fuzz,process 
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# The document type itself, in the spellings PDF text extraction throws up.
# Matched as substrings against whitespace-normalised text, so these also cover
# titles like "2023 Annual Report" and "Annual Report FY2024".
KEY_WORDS = {
    "annual report",
    "annual reports",
    "annual-report",
    "annual_report",
    "annualreport",
    "annual impact report",
    "annual review",
    "annual-review",
    "ar",
    "impact report",
    "impact reports",
    "impact-report",
    "impact_report",
    "impactreport",
    "presidential report",
    "presidential-report",
    "presidential_report",
    "presidentialreport",
    "president's report",
    "president’s report",      # curly apostrophe — PDF extraction emits this
    "presidents report",
    "presidents-report",
    "presidents_report",
    "presidentsreport",
    "president report",
}

# Full phrases that name the document explicitly. Any one of these is enough.
STRONG_INDICATORS = {
    "this annual report",
    "in this annual report",
    "our annual report",
    "the annual report of",
    "annual report to the community",
    "annual report to our donors",
    "annual report to our supporters",
    "annual report and financial statements",
    "annual impact report",
    "annual report highlights",
    "this impact report",
    "our impact report",
    "the impact report of",
    "impact report highlights",
    "this presidential report",
    "our president's report",
    "our president’s report",
    "the president's report",
    "the president’s report",
    "report of the president",
    "report from the president",
}

def load_csv(file_path):
    try:
        file = pd.read_csv(file_path,encoding="utf-8")
        return file
    except Exception as e:
        print(f"error processing csv file: {e}")
        return None 

def need_ocr(pdf_reader) -> bool:
    text = ""

    for page in range(min(15,len(pdf_reader.pages))):
        text += pdf_reader.pages[page].extract_text() or ""

    return len(text.strip()) < 50

def IMG_to_pdf(file_path):
    output_path = file_path
    ocrmypdf.ocr(file_path,output_path,deskew=True)
    return output_path


# TODO: make func that filter by URL first if they match it\
def identify_by_url(pdf_url):
    lower_case_url = pdf_url.lower()
    for word in KEY_WORDS:
        if word in lower_case_url:
            return True
    return False

def extract_pdf_text(pdf_url,max_pages= 15):
    try:
        response = requests.get(pdf_url,timeout = 30)
        response.raise_for_status()

        pdf_file = BytesIO(response.content)
        pdf_reader =  PyPDF2.PdfReader(pdf_file)

        if need_ocr(pdf_reader):
            with tempfile.NamedTemporaryFile(suffix=".pdf",delete=False) as tmp:
                tmp.write(response.content)
                tmp_path = tmp.name

            IMG_to_pdf(tmp_path)
            pdf_reader = PyPDF2.PdfReader(tmp_path)

        text = ""
        num_pages = min(len(pdf_reader.pages),max_pages)

        for page in range(num_pages):
            text += pdf_reader.pages[page].extract_text() or ""

        return text[:6000]
    except Exception as e:
        print(f"error processing pdf: {e}")
        return None

def normalise(text):
    """Lowercase and collapse whitespace so phrases survive PDF line breaks."""
    return re.sub(r"\s+", " ", text.lower())

def identify_reports(pdf_url):
    """True when the PDF names itself an annual report.

    Nonprofit vocabulary alone is not enough — a 990, an appeal letter and a
    newsletter all use it. The document has to say what it is.
    """
    extracted_text = extract_pdf_text(pdf_url)

    if not extracted_text:
        return False

    text = normalise(extracted_text)

    for phrase in KEY_WORDS | STRONG_INDICATORS:
        if phrase in text:
            return True

    return False





        
        