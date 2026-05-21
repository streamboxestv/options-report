import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler


ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
HISTORY_PATH = os.path.join(ROOT_DIR, "report_history.json")
DEFAULT_REPOSITORY = "streamboxestv/options-report"
DEFAULT_BRANCH = "main"


def load_history():
    repository = os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPOSITORY)
    branch = os.environ.get("GITHUB_BRANCH", DEFAULT_BRANCH)
    remote_url = f"https://raw.githubusercontent.com/{repository}/{branch}/report_history.json"

    try:
        with urllib.request.urlopen(remote_url, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, list) else []
    except (urllib.error.URLError, json.JSONDecodeError):
        if not os.path.exists(HISTORY_PATH):
            return []
        with open(HISTORY_PATH, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, list) else []


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            snapshots = load_history()
        except Exception:
            snapshots = []

        body = json.dumps({"snapshots": snapshots}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
