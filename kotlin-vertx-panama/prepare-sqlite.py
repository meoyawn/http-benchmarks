#!/usr/bin/env python3
"""Build the common optimized SQLite engine for this application's FFM library."""
from pathlib import Path
import runpy
import sys

project = Path(__file__).resolve().parent
sys.argv[1:1] = ["--project", str(project)]
runpy.run_path(str(project.parent / "db/prepare-sqlite.py"), run_name="__main__")
