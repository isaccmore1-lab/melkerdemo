from __future__ import annotations

"""DEMO-only storage.

This shareable build is deliberately isolated from the real ERP data directory.
It NEVER scans for or migrates production ERP databases.
"""

from pathlib import Path
import os
import shutil
import sqlite3
from typing import Iterable

APP_DIR = Path(__file__).resolve().parent
BUNDLED_DB = APP_DIR / "data" / "erp_v2.db"


def data_root() -> Path:
    override = os.environ.get("ERP_V2_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "ERP_v2_DEMO"

    # Linux/container fallback for the shareable demo.
    return APP_DIR / "demo_data"


DATA_ROOT = data_root()
DB_PATH = DATA_ROOT / "erp_v2.db"
BACKUP_DIR = DATA_ROOT / "backups"


def bootstrap_database() -> Path:
    """Create an isolated demo DB from the bundled synthetic seed.

    Important: this demo build must never import or overwrite the user's real ERP v2
    database.  It only copies the bundled demo seed when the demo DB is missing.
    """
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.exists():
        if BUNDLED_DB.exists():
            shutil.copy2(BUNDLED_DB, DB_PATH)
        else:
            sqlite3.connect(DB_PATH).close()
    return DB_PATH


bootstrap_database()
