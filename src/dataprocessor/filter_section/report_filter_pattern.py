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
from rich.console import Console
import sys, os
from rapidfuzz import fuzz,process 
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

KEY_WORDS = {
    # tax status / IRS filings (very high precision — near-exclusive to nonprofits)
    "501c3", "990", "990-ez", "990-pf", "ein",
    "tax-exempt", "tax-deductible", "nonprofit", "non-profit","annual-report"
    "not-for-profit", "charitable", "charity",
    # financial-statement terminology (nonprofit-specific)
    "endowment", "unrestricted", "restricted",
    # fundraising / philanthropy
    "philanthropy", "philanthropic", "donors", "donations", "donor",
    "fundraising", "fundraiser", "grantmaking", "grantees", "grants",
    "bequest", "bequests", "stewardship", "beneficiaries",
    # governance / mission
    "trustees", "volunteers", "volunteerism", "underserved",
    # registries
    "guidestar",
}

KEY_SENTENCES = {
    # legal / tax-status boilerplate (very high precision)
    "is a 501(c)(3)",
    "501(c)(3) nonprofit organization",
    "your donation is tax-deductible",
    "all donations are tax-deductible",
    "no goods or services were provided",
    # leadership letters (standard front-matter of nonprofit annual reports)
    "letter from the executive director",
    "message from the executive director",
    "on behalf of the board of directors",
    # audited financial statement headings
    "statement of financial position",
    "statement of activities",
    "statement of functional expenses",
    "notes to the financial statements",
    "independent auditor's report",
    "net assets without donor restrictions",
    "net assets with donor restrictions",
    # mission / donor-gratitude framing
    "our mission is to",
    "thanks to the generosity of our donors",
    "with your continued support",
    "the communities we serve",
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

def find_key_sentence(pdf_url):
    extracted_text = extract_pdf_text(pdf_url)

    if not extracted_text:
        return False

    text = extracted_text.lower()
    count = 0

    for sentence in KEY_SENTENCES:
        if sentence in text:
            return True

    return False

def identify_reports(pdf_url,target:int = 10):
    extracted_text = extract_pdf_text(pdf_url)

    if not extracted_text:
        return False

    text = extracted_text.lower()
    count = 0

    for word in text.split():
        word = word.strip(".,;:()[]\"'")
        if word in KEY_WORDS:
            count += 1

        if count >= target:
            return True
    
    return False





        
        