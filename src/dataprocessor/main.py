from rich.console import Console

from dataprocessor.filter_section import report_filter
import time
import questionary
import subprocess
import os

# CUSTOM MODULES HERE
from dataprocessor.main_section import main_page
from dataprocessor.main_section import settings_page
from dataprocessor.filter_section import filter_page
from dataprocessor.filter_section import report_filter
from dataprocessor.analysis_section import analysis_page
from dataprocessor.analysis_section import report_analysis
from dataprocessor.analysisPDF_section import analysisPDF_page
from dataprocessor.analysisPDF_section import report_analysis_pdfs
from dataprocessor.analysis_v2_section import analysis_v2_page
from dataprocessor.analysis_v2_section import analysis_v2
from dataprocessor.analysisPDF_v2_section import analysisPDF_v2
from dataprocessor.analysisPDF_v2_section import analysisPDF_v2_page
from dataprocessor.propublica_section import propublica_UI
from dataprocessor.propublica_cloud_section import propublica_cloud_UI
from dataprocessor.Spider_section import spider_UI
# SETUP ---------------------------------------------------------------------------

console = Console(color_system="truecolor")
script_dir = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------------
# MAIN UI -------------------------------------------------------------------------

# UI pages
def choice_menu():
    choice = questionary.select("Select terminal",choices = ["Part 1:Filter data","Part 2:LLM analysis","ALL"]).ask()
    return choice

def main():
    main_page.show()
    choice = questionary.select("Select terminal",choices=["Filter Data","LLM Analysis - PDF URL","LLM Analysis - Raw PDF","LLM Analysis V2","LLM Analysis V2 - Raw PDF","Propublica API","Propublica API [CLOUD]","Spider","Settings"]).ask()

    if choice == "Filter Data":
        os.system("clear")
        filter_page.show()
    elif choice == "LLM Analysis - PDF URL":
        analysis_page.show()
    elif choice == "LLM Analysis - Raw PDF":
        analysisPDF_page.show()
    elif choice == "LLM Analysis V2":
        analysis_v2_page.show()
    elif choice == "LLM Analysis V2 - Raw PDF":
        analysisPDF_v2_page.show()
    elif choice == "Propublica API":
        propublica_UI.show()
    elif choice == "Propublica API [CLOUD]":
        propublica_cloud_UI.show()
    elif choice == "Spider":
        spider_UI.show()

    elif choice == "Settings":
        settings_page.show()

if __name__ == "__main__":
    main()

# ---------------------------------------------------------------------------------
# run locally:            uv run dataprocessor
# install as a CLI tool:  uv tool install git+ssh://git@github.com/<you>/DataProcessor
# upgrade installed tool: uv tool upgrade dataprocessor
# API keys go in ~/.dataprocessor/.env (or a .env in the working directory)