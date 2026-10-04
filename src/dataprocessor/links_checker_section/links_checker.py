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
import httpx

def load_csv(file_path):
    try:
        file = pd.read_csv(file_path,encoding="utf-8")
        return file
    except Exception as e:
        print(e)
        return None

def check_dead_live(url) -> bool:
    try:
        response = httpx.head(url,follow_redirects=True,timeout=5)
        if response.status_code == 404:
            return True

        if response.status_code in (403,405,429):
            response = httpx.get(url,follow_redirects=True,timeout=5)

        return response.status_code >= 400

    except httpx.HTTPError:
        return True

def check_dead_wayback(url) -> bool:
    try:
        response = httpx.head(url,follow_redirects=True,timeout= 60)
        if response.status_code == 404:
            return True

        if response.status_code in (403,405,429):
            time.sleep(60)
            response = httpx.get(url,follow_redirects=True,timeout=10)

        return response.status_code >= 400

    except httpx.HTTPError:
        return True

def check(url,link_source) -> bool:
    check_status = False


    if link_source == "wayback":
        check_status = check_dead_wayback(url)
        return check_status

    if link_source == "live":
        check_status = check_dead_live(url)
        return check_status

    return check_status 



    

  

