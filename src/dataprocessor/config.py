import os

from dotenv import find_dotenv, load_dotenv


def load_env():
    """Load API keys from ~/.dataprocessor/.env (installed tool) and any
    project .env found from the current directory upward (development)."""
    home_env = os.path.join(os.path.expanduser("~"), ".dataprocessor", ".env")
    if os.path.exists(home_env):
        load_dotenv(home_env)
    load_dotenv(find_dotenv(usecwd=True))
