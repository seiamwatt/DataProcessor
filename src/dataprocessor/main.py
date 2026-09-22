from rich.console import Console

from dataprocessor.filter_section import report_filter_llm
import time
import questionary
import subprocess
import os

# CUSTOM MODULES HERE
from dataprocessor.main_section import main_page
from dataprocessor.main_section import settings_page
from dataprocessor.filter_section import filter_page
from dataprocessor.filter_section import report_filter_llm
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
from dataprocessor.propublica_domain_finder_section import domain_finder_UI
from dataprocessor.Spider_section import spider_UI
from dataprocessor.wayback_section import wayback_UI
# SETUP ---------------------------------------------------------------------------

console = Console(color_system="truecolor")
script_dir = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------------
# MAIN UI -------------------------------------------------------------------------
ANALYSIS_SECTION = "Analysis Section"
PROPUBLICA_SECTION = "Propublica Section"
SPIDER_SECTION = "Spider Section"
WAYBACK_SECTION = "Wayback Section"


FILTER_PAGE = "Filter data"
LLM_ANALYSIS_URL = "LLM Analysis - url"
LLM_ANALYSIS_PDF = "LLM Analysis - pdf"
LLM_ANALYSIS_URL_V2 = "LLM Analysis - url"
LLM_ANALYSIS_PDF_V2 = "LLM Analysis - pdf"
PROPUBLICA_ORG_FINDER = "Probulica API ORG Finder"
PROPUBLICA_DOMAIN_FINDER = "Propublica domain finder"
SPIDER_V1 = "Spider v1"
SPIDER_V2 = "Spider v2"
SETTINGS = "Settings"



def main():
    

    main_page.show()

    choice = questionary.select("Select terminal", choices = [FILTER_PAGE,ANALYSIS_SECTION,PROPUBLICA_SECTION,SPIDER_SECTION,WAYBACK_SECTION,SETTINGS]).ask()

    if choice == FILTER_PAGE:
        filter_page.show()
    elif choice == ANALYSIS_SECTION:
        choice = questionary.select("Select version", choices = [LLM_ANALYSIS_URL_V2,LLM_ANALYSIS_PDF_V2]).ask()
        if choice == LLM_ANALYSIS_URL_V2:
            analysis_v2_page.show()
        elif choice == LLM_ANALYSIS_PDF_V2:
            analysisPDF_v2_page.show()
    elif choice == PROPUBLICA_SECTION:
        choice = questionary.select("Select tool", choices= [PROPUBLICA_ORG_FINDER,PROPUBLICA_DOMAIN_FINDER]).ask()
        if choice == PROPUBLICA_ORG_FINDER:
            propublica_UI.show()
        elif choice == PROPUBLICA_DOMAIN_FINDER:
            domain_finder_UI.show()
    elif choice == SPIDER_SECTION:
        choice = questionary.select("Select version", choices = [SPIDER_V1,SPIDER_V2]).ask()
        if choice == SPIDER_V1:
            spider_UI.show()
        elif choice == SPIDER_V2:
            pass
    elif choice == WAYBACK_SECTION:
        wayback_UI.show()
    elif choice == SETTINGS:
        settings_page.show()



if __name__ == "__main__":
    main()

# ---------------------------------------------------------------------------------
# run locally:            uv run dataprocessor
# install as a CLI tool:  uv tool install git+https://github.com/seiamwatt/DataProcessor
# upgrade installed tool: uv tool upgrade dataprocessor
# API keys go in ~/.dataprocessor/.env (or a .env in the working directory)

