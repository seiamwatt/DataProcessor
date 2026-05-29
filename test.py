import urllib.request
import json
import time

categories = {
    1: "Arts, Culture & Humanities",
    2: "Education",
    3: "Environment & Animals",
    4: "Health",
    5: "Human Services",
    6: "International, Foreign Affairs",
    7: "Public, Societal Benefit",
    8: "Religion Related",
    9: "Mutual/Membership Benefit",
    10: "Unknown, Unclassified",
}

states = [
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA",
    "HI","ID","IL","IN","IA","KS","KY","LA","ME","MD",
    "MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
    "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC",
    "SD","TN","TX","UT","VT","VA","WA","WV","WI","WY",
    "DC","AS","GU","MP","PR","VI","ZZ"
]

base_url = "https://projects.propublica.org/nonprofits/api/v2/search.json"

# Pick which category to test (change this to test others)
TEST_CATEGORY = 1

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode())

print("=" * 70)
print(f"Testing category {TEST_CATEGORY}: {categories[TEST_CATEGORY]}")
print("=" * 70)

# --- NEW APPROACH: loop through states ---
print(f"\n[NEW APPROACH] Looping through {len(states)} states:")
print(f"{'State':<8} {'Results':<12} {'Pages':<8}")
print("-" * 30)

grand_total = 0
grand_pages = 0

for state in states:
    try:
        data = fetch(f"{base_url}?ntee%5Bid%5D={TEST_CATEGORY}&state%5Bid%5D={state}")
        total = data.get("total_results", 0)
        pages = data.get("num_pages", 0)
        grand_total += total
        grand_pages += pages

        if total > 0:
            capped = " *** CAPPED ***" if total == 10000 else ""
            print(f"  {state:<6} {total:<12} {pages:<8}{capped}")
    except Exception as e:
        print(f"  {state:<6} ERROR: {e}")

    time.sleep(1)


print("-" * 30)
print(f"\n[RESULTS COMPARISON]")

print(f"  New approach (by state):  {grand_total} results")
print(f"  Total pages across states: {grand_pages}")
print("\nDone!")