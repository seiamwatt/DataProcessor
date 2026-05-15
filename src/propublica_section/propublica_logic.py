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
import tempfile
from rich.console import Console
import sys, os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)

load_dotenv(resource_path(".env"))

def load_csv(file_path):
    try:
        file = pd.read_csv(file_path)
        return file
    
    except Exception as e:
        print("load csv failed")
        return None
    

def populate_data(preferred_NTEE_code, num_pages,ntee_id):

    url = "https://projects.propublica.org/nonprofits/api/v2"

    results = []
    filtered_results = []

    # Step 1: Search by broad NTEE category, page by page
    for page in range(num_pages):
        params = {
            "ntee[id]": ntee_id,
            "page": page
        }

        response = requests.get(url=f"{url}/search.json", params=params)
        data = response.json()
        print(f"Connected - page {page}")
        
        orgs = data.get("organizations", [])
        
        if not orgs:
            break

        results.extend(orgs)
        time.sleep(1)

    print(f"Found {len(results)} total orgs from search")

    # Step 2: Get details for each org and filter
    for org in results:
        ein = org["ein"]
        detail_url = f"{url}/organizations/{ein}.json"
        response = requests.get(detail_url)
        data = response.json()

        print("org data obtained")

        org_data = data.get("organization", {})
        ntee_code = org_data.get("ntee_code", "")
        ruling_date = org_data.get("ruling_date", "")

        if not ruling_date:
            continue

        ruling_year = int(str(ruling_date)[:4])

        if ntee_code == preferred_NTEE_code and ruling_year <= 2000:
            filtered_results.append({
                "name": org_data.get("name"),
                "ein": ein,
                "ntee_code": ntee_code,
                "ruling_date": ruling_date,
                "city": org_data.get("city"),
                "state": org_data.get("state"),
            })

            print(f"Data appended: {org_data.get('name')}")

        time.sleep(1)

    return filtered_results


def main():
    # test code
    results = populate_data(preferred_NTEE_code="A01",num_pages=10,ntee_id=1)
    
    print(f"\nTotal filtered results: {len(results)}")
    for r in results:
        print(r)


if __name__ == "__main__":
    main()