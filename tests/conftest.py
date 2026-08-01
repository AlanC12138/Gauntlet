import sys
from pathlib import Path

# Allows `pytest` to run without `poetry install` first.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
