import threading
import os
import sys

sys.dont_write_bytecode = True

import webview

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
os.chdir(BASE)

from app import app

ICON = os.path.join(BASE, "static", "app-icon.png")

def start_server():
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)

if __name__ == "__main__":
    server = threading.Thread(target=start_server, daemon=True)
    server.start()

    window = webview.create_window(
        "PL Dashboard",
        "http://127.0.0.1:5000",
        width=1100,
        height=750,
        min_size=(400, 500),
    )
    webview.start(icon=ICON)
