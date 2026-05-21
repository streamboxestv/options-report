import json
import os
from http.server import BaseHTTPRequestHandler


ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
LATEST_REPORT_PATH = os.path.join(ROOT_DIR, "latest_report.json")


def json_response(request: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    request.send_response(status)
    request.send_header("Content-Type", "application/json; charset=utf-8")
    request.send_header("Cache-Control", "no-store")
    request.end_headers()
    request.wfile.write(body)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not os.path.exists(LATEST_REPORT_PATH):
            json_response(self, 404, {"error": "latest_report.json is not available yet."})
            return

        try:
            with open(LATEST_REPORT_PATH, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            json_response(self, 500, {"error": f"Could not read latest_report.json: {exc}"})
            return

        json_response(self, 200, payload)
