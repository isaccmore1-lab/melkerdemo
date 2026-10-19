"""Cloud/container entry point for the shareable ERP demo."""
import os
from pathlib import Path
import shutil

APP_DIR = Path(__file__).resolve().parent
# Hosted demo is intentionally ephemeral and isolated from production data.
os.environ.setdefault("ERP_V2_DATA_DIR", "/tmp/erp_v2_demo")
root = Path(os.environ["ERP_V2_DATA_DIR"])
root.mkdir(parents=True, exist_ok=True)
seed = APP_DIR / "data" / "erp_v2.db"
live = root / "erp_v2.db"
# Reset on every container process start unless explicitly disabled.
if os.environ.get("DEMO_RESET_ON_START", "1") == "1" or not live.exists():
    shutil.copy2(seed, live)

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, app_dir=str(APP_DIR), proxy_headers=True)
