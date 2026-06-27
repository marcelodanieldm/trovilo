"""Servidor HTTP local con headers que evitan Tracking Prevention de Edge."""
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5500

class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin-allow-popups")
        self.send_header("Cross-Origin-Embedder-Policy", "unsafe-none")
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass  # silencioso

print(f"http://localhost:{PORT}")
HTTPServer(("", PORT), Handler).serve_forever()
