import os
from openai import OpenAI
from dotenv import load_dotenv,find_dotenv
from google import genai
import sys
# check connections

def resource_path(relative_path):
    """Get path for bundled files (works for both dev and PyInstaller)"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)

load_dotenv(resource_path(".env"))


def DeepSeek_connect_test() -> bool:

    key = os.getenv("DeepSeek_key")

    client = OpenAI(api_key=key,base_url="https://api.deepseek.com")

    response = client.chat.completions.create(
    model="deepseek-chat",
    messages=[
        {"role": "system", "content": "check"},
        {"role": "user", "content": "check"},
    ],
    stream=False)
    if response:
        return True

    return False

def GTP_connect_test() -> bool:
    key = os.getenv("GPT_key")
    client = OpenAI(api_key=key)

    response = client.responses.create(
    model="gpt-4.1",
    input="test"
    )

    if response:
        return True

    return False

def Gemini_connect_test() -> bool:
    key = os.getenv("Gemini_key")
    client = genai.Client(api_key=key)
    response = client.models.generate_content(
    model="gemini-3-flash-preview",
    contents="test",)

    if response:
        return True
    
    return False


