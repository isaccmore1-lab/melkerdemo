from pathlib import Path
import importlib.util
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from storage import DB_PATH

APP_DIR = Path(__file__).resolve().parent
VERSION = "0.8.8-demo"
PREFERRED_PORT = 8765

REQUIRED = ["fastapi", "uvicorn", "pydantic"]
missing = [m for m in REQUIRED if importlib.util.find_spec(m) is None]

if missing:
    print("Installing ERP v2 dependencies...")
    subprocess.check_call([
        sys.executable, "-m", "pip", "install",
        "fastapi", "uvicorn", "pydantic"
    ])

import uvicorn


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def choose_port() -> int:
    for port in range(PREFERRED_PORT, PREFERRED_PORT + 20):
        if port_is_free(port):
            return port
    raise RuntimeError("No free local ERP port found between 8765 and 8784.")


def open_browser(port: int):
    time.sleep(1.3)
    # Query parameter prevents a stale cached start page from being reused.
    webbrowser.open(f"http://127.0.0.1:{port}/?build={VERSION.replace('.', '_')}")


if __name__ == "__main__":
    port = choose_port()
    print("")
    print("==============================================")
    print(f" ERP v2 — SHAREABLE DEMO v{VERSION}")
    print(f" http://127.0.0.1:{port}")
    print("==============================================")
    if port != PREFERRED_PORT:
        print("")
        print(f" NOTE: Port {PREFERRED_PORT} is already in use.")
        print(" An older ERP prototype is probably still running.")
        print(f" This v{VERSION} build has been started on port {port} instead,")
        print(" so the browser opens the correct version rather than the old one.")
    print("")
    print(f" Data: {DB_PATH}")
    print("")
    threading.Thread(target=open_browser, args=(port,), daemon=True).start()
    uvicorn.run("app:app", host="127.0.0.1", port=port, reload=False, app_dir=str(APP_DIR))
