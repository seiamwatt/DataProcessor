from rich.console import Console

from filter_section import report_filter
import time 
import questionary
from main_section import main_page
from main_section import settings_page
from filter_section import filter_page
from filter_section import report_filter
from analysis_section import analysis_page
from analysis_section import report_analysis
import subprocess
import os

console = Console()
script_dir = os.path.dirname(os.path.abspath(__file__))

def choice_menu():
    choice = questionary.select("Select terminal",choices = ["Part 1:Filter data","Part 2:LLM analysis","ALL"]).ask()
    return choice

def main():
    main_page.show()
    choice = questionary.select("Select terminal",choices = ["Part 1:Filter data","Part 2:LLM analysis","Settings"]).ask()

    if choice == "Part 1:Filter data":
        os.system("clear")
        filter_page.show()
    elif choice == "Part 2:LLM analysis":
        analysis_page.show()
    elif choice == "Settings":
        settings_page.show()
   
if __name__ == "__main__":
    main()