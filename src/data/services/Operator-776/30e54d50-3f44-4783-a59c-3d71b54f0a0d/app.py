import sys, io
from http.server import HTTPServer, BaseHTTPRequestHandler

_buf = io.StringIO()
_old_stdout = sys.stdout
sys.stdout = _buf
try:
    print("Hello, World!")
except Exception as _e:
    print(f"Error: {_e}")
sys.stdout = _old_stdout
_output = _buf.getvalue()

class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        html = (
            "<!DOCTYPE html><html><head><title>SandPy App</title>"
            "<style>body{font-family:monospace;background:#0a0a0a;color:#e0e0e0;padding:2rem;}"
            "pre{background:#111;padding:1rem;border-radius:8px;border:1px solid #333;white-space:pre-wrap;}</style>"
            "</head><body><h2>SandPy Deployment Output</h2><pre>"
            + _output.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
            + "</pre></body></html>"
        )
        self.wfile.write(html.encode())
    def log_message(self, *args):
        pass

print(f"Serving on 0.0.0.0:8000")
HTTPServer(("0.0.0.0", 8000), _Handler).serve_forever()
