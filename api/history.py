import json
import os
from http.server import BaseHTTPRequestHandler


ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
HISTORY_PATH = os.path.join(ROOT_DIR, "report_history.json")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        snapshots = []
        if os.path.exists(HISTORY_PATH):
            try:
                with open(HISTORY_PATH, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                if isinstance(payload, list):
                    snapshots = payload
            except Exception:
                snapshots = []

        body = json.dumps({"snapshots": snapshots}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
