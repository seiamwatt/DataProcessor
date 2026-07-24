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
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

KEY_WORDS = []

