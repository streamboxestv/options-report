import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler


ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
LATEST_REPORT_PATH = os.path.join(ROOT_DIR, "latest_report.json")
DEFAULT_REPOSITORY = "streamboxestv/options-report"
DEFAULT_BRANCH = "main"


def json_response(request: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    request.send_response(status)
    request.send_header("Content-Type", "application/json; charset=utf-8")
    request.send_header("Cache-Control", "no-store")
    request.end_headers()
    request.wfile.write(body)


def load_latest_report() -> dict:
    repository = os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPOSITORY)
    branch = os.environ.get("GITHUB_BRANCH", DEFAULT_BRANCH)
    remote_url = f"https://raw.githubusercontent.com/{repository}/{branch}/latest_report.json"

    try:
        with urllib.request.urlopen(remote_url, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError):
        if not os.path.exists(LATEST_REPORT_PATH):
            raise FileNotFoundError("latest_report.json is not available yet.")
        with open(LATEST_REPORT_PATH, "r", encoding="utf-8") as handle:
            return json.load(handle)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            payload = load_latest_report()
        except FileNotFoundError as exc:
            json_response(self, 404, {"error": str(exc)})
            return
        except Exception as exc:
            json_response(self, 500, {"error": f"Could not read latest_report.json: {exc}"})
            return

        json_response(self, 200, payload)
