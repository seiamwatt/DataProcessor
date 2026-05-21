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
from rich.progress import Progress
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
    
def populate_data(preferred_NTEE_code, num_pages, ntee_catagory_id,start_state_index,end_state_index):

    url = "https://projects.propublica.org/nonprofits/api/v2"

    results = []
    filtered_results = []

    states =  [
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA",
    "HI","ID","IL","IN","IA","KS","KY","LA","ME","MD",
    "MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
    "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC",
    "SD","TN","TX","UT","VT","VA","WA","WV","WI","WY",
    "DC","AS","GU","MP","PR","VI","ZZ"]


    with Progress() as progress:
        task1 = progress.add_task("[red]Processing Data", total=num_pages * (end_state_index - start_state_index))

        for i in range(start_state_index,end_state_index):
            print(f"State Index: {i}")

            for page in range(num_pages):
                params = {
                    "ntee[id]": ntee_catagory_id,
                    "page": page,
                    "state[id]":states[i]
                }

                try:
                    response = requests.get(url=f"{url}/search.json", params=params)
                    data = response.json()
                    progress.console.print(f"Connected - page {page}")
                    progress.update(task1, advance=1)

                    orgs = data.get("organizations", [])

                    if not orgs:
                        break
                except Exception as e:
                    print("API call error")
                    time.sleep(10)
                    continue

                results.extend(orgs)
                time.sleep(5)

    console = Console()
    console.print(f"Found {len(results)} total orgs from search")

    with Progress() as progress:
        task1 = progress.add_task("[red]Filtering Data", total=len(results))
        obtained_count = 0
        appended_count = 0
        for org in results:
            ntee_code = org.get("ntee_code", " ")


            if ntee_code != preferred_NTEE_code:
                progress.update(task1,advance=1)
                continue


            ein = org["ein"]
            detail_url = f"{url}/organizations/{ein}.json"

            try:
                response = requests.get(detail_url)
                data = response.json()
            except Exception as e:
                print("API call error")
                time.sleep(10)
                continue

            obtained_count += 1
            progress.console.print(f"organisation data obtained | number obtained: {obtained_count}")

            org_object = data.get("organization", {})

            ntee_code = org_object.get("ntee_code", "")
            ruling_date = org_object.get("ruling_date", "")

            if not ruling_date:
                progress.update(task1, advance=1)
                continue

            try:
                ruling_year = int(str(ruling_date)[:4])
            except Exception as e:
                print("ruling year error")
                continue

            if ntee_code == preferred_NTEE_code and ruling_year <= 2000:
                filtered_results.append({
                    "name": org_object.get("name"),
                    "ein": ein,
                    "ntee_code": ntee_code,
                    "ruling_date": ruling_date,
                    "address": org_object.get("address"),
                    "city": org_object.get("city"),
                    "state": org_object.get("state"),
                    "revenue": org_object.get("revenue_amount"),
                    "asset_amount": org_object.get("asset_amount"),
                    "total_asset_end": org_object.get("totassetend")
                })

                appended_count += 1
                progress.console.print(f"[green]Data appended: {org_object.get('name')} | Appended: {appended_count}")

            time.sleep(1)
            progress.update(task1, advance=1)

    return filtered_results

def main():
    # test code
    # results = populate_data(preferred_NTEE_code="A01",num_pages=100,ntee_id=1)
    
    # print(f"\nTotal filtered results: {len(results)}")
    # for r in results:
    #     print(r)
    return


if __name__ == "__main__":
    main()