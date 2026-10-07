import time
import threading
from collections import deque

import httpx
import pandas as pd

def load_csv(file_path):
    try:
        file = pd.read_csv(file_path,encoding="utf-8")
        return file
    except Exception as e:
        print(e)
        return None

# True = dead link, False = live link
def check_dead_live(url) -> bool:
    try:
        response = httpx.head(url,follow_redirects=True,timeout=5)
        if response.status_code == 404:
            return True

        # some servers reject HEAD, retry with GET
        if response.status_code in (403,405,429):
            response = httpx.get(url,follow_redirects=True,timeout=5)

        return response.status_code >= 400

    except httpx.HTTPError:
        return True

COOLDOWN = 60
REFUSED_RETRIES = 3

def check_dead_wayback(url,limiter):
    for attempt in range(REFUSED_RETRIES):
        # another worker may have hit a block, wait it out first
        limiter.wait_cooldown()
        try:
            response = httpx.head(url,follow_redirects=True,timeout=60)
            if response.status_code == 404:
                return True

            # archive pushed back, cool down every worker before retrying with GET
            if response.status_code in (403,405,429,503):
                limiter.cooldown(COOLDOWN)
                limiter.wait_cooldown()
                response = httpx.get(url,follow_redirects=True,timeout=10)

            if response.status_code in (429,503):
                return None

            return response.status_code >= 400

        # archive blocked our ip, cool down every worker then try this link again
        except httpx.ConnectError:
            limiter.cooldown(COOLDOWN)
            if attempt == REFUSED_RETRIES - 1:
                return None

        except httpx.HTTPError:
            return None

def check_dead(url,link_source,limiter) -> bool:
    if link_source == "wayback":
        return check_dead_wayback(url,limiter)

    if link_source == "live":
        return check_dead_live(url)

    return False

# sliding window
class rate_limiter:
    def __init__(self,window_size,max_request):
        self.window_size = window_size
        self.max_request = max_request
        self.requests = deque()
        self.cooldown_until = 0
        self.lock = threading.Lock()

    def allow_request(self):
        with self.lock:
            time_now = time.time()
            if time_now < self.cooldown_until:
                return False
            
            while self.requests and self.requests[0] <= time_now - self.window_size:
                self.requests.popleft()

            if len(self.requests) < self.max_request:
                self.requests.append(time_now)
                return True

            return False

    def cooldown(self,seconds):
        with self.lock:
            self.cooldown_until = max(self.cooldown_until, time.time() + seconds)

    def wait_cooldown(self):
        while True:
            with self.lock:
                wait = self.cooldown_until - time.time()
            if wait <= 0:
                return
            time.sleep(wait)
