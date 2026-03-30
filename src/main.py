from rich.console import Console

from filter_section import report_filter
import time 
import questionary
import subprocess
import os

# CUSTOM MODULES HERE 
from main_section import main_page
from main_section import settings_page
from filter_section import filter_page
from filter_section import report_filter
from analysis_section import analysis_page
from analysis_section import report_analysis
from analysisPDF_section import analysisPDF_page
from analysisPDF_section import report_analysis_pdfs
from analysis_v2_section import analysis_v2_page
from analysis_v2_section import analysis_v2
# SETUP ---------------------------------------------------------------------------

console = Console(color_system="truecolor")
script_dir = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------------
# MAIN UI -------------------------------------------------------------------------

def choice_menu():
    choice = questionary.select("Select terminal",choices = ["Part 1:Filter data","Part 2:LLM analysis","ALL"]).ask()
    return choice

def main():
    main_page.show()
    choice = questionary.select("Select terminal",choices=["Filter Data","LLM Analysis - PDF URL","LLM Analysis - Raw PDF","LLM Analysis V2","Settings"]).ask()

    if choice == "Filter Data":
        os.system("clear")
        filter_page.show()
    elif choice == "LLM Analysis - PDF URL":
        analysis_page.show()
    elif choice == "LLM Analysis - Raw PDF":
        analysisPDF_page.show()
    elif choice == "LLM Analysis V2":
        analysis_v2_page.show()
    elif choice == "Settings":
        settings_page.show()
   
if __name__ == "__main__":
    main()
    
# ---------------------------------------------------------------------------------


# pyinstaller --onefile --add-data ".env:." src/main.py