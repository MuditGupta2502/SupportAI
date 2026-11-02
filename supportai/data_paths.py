"""Resolve paths for writable application data and host-mounted storage."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=False)

PACKAGE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("SUPPORTAI_DATA_DIR", str(PACKAGE_DIR))).expanduser()
DATA_DIR.mkdir(parents=True, exist_ok=True)
