from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import logging
import os
import re
import socket
import sys
import tempfile
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from io import BytesIO
from typing import Iterator, Optional

import pandas as pd
import requests
from dotenv import load_dotenv
from dataprocessor.config import load_env
from requests.adapters import HTTPAdapter
from rich.console import Console
from tqdm import tqdm
from urllib.parse import urljoin, urlparse, urldefrag, unquote
from urllib.robotparser import RobotFileParser




