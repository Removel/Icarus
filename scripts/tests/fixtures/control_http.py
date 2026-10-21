"""Real subprocess fixture: delayed HTTP readiness, env evidence and foreground hold."""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import time

parser = argparse.ArgumentParser()
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--delay", type=float, default=0)
parser.add_argument("--gateway-url")
args = parser.parse_args()
time.sleep(args.delay)
print("fixture started", flush=True)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({"status": "ready", "value": os.getenv("FIXTURE_VALUE"),
                                   "dependency": os.getenv("FIXTURE_DEPENDENCY"),
                                   "gateway_url": args.gateway_url}).encode())

    def log_message(self, *args):
        pass

HTTPServer((args.host, args.port), Handler).serve_forever()
