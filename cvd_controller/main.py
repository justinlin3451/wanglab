# main.py
"""
CVD Controller – application entry point.
Run: python main.py
"""
import sys
import os
from pathlib import Path

# Ensure working directory is the project root
os.chdir(Path(__file__).parent)

# Create required directories
for d in ["data", "logs", "recipes", "config"]:
    Path(d).mkdir(exist_ok=True)

from gui.main_window import run_app

if __name__ == "__main__":
    run_app()
