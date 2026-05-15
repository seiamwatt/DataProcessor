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

def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)

load_dotenv(resource_path(".env"))

def load_csv(file_path):
    try:
        file = pd.load_csv(file_path)
        return file
    except Exception as e:
        print("load csv failed")
        return None



def connect_to_candid(ein, api_key):
    url = f"https://api.candid.org/essentials/v4/{ein}"

    headers = {
        "Subscription-Key": api_key
    }

    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        data = response.json()

        # Extract financial info
        financials = data.get("financials", {})
        summary = data.get("summary", {})

        result = {
            "name": summary.get("organization_name"),
            "ein": ein,
            "mission": summary.get("mission"),
            "total_revenue": financials.get("total_revenue"),
            "total_expenses": financials.get("total_expenses"),
            "total_assets": financials.get("total_assets"),
            "net_assets": financials.get("net_assets"),
        }
        return result
    else:
        return {"error": response.status_code, "message": response.text}

def batch_proceess(df_batch):
    return






