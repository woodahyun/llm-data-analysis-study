"""Prepare Chapter 09 model inputs from the common raw data."""
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
result = subprocess.run(
    [sys.executable, "scripts/preprocess_data.py"],
    cwd=ROOT,
    check=True,
    text=True,
)
print("Chapter 09 input preparation completed")
