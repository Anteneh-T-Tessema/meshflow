"""Local HTTP server for MeshFlow Studio visual designer."""

from __future__ import annotations

import http.server
import os
import socketserver
import webbrowser


class StudioHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Custom request handler that serves index.html for the root path."""

    def translate_path(self, path: str) -> str:
        # Resolve to index.html under meshflow/studio/templates/
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        template_dir = os.path.join(base_dir, "studio", "templates")
        
        if path == "/" or path == "/index.html":
            return os.path.join(template_dir, "index.html")
            
        return super().translate_path(path)


def start_studio_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Start the local web server and open the browser."""
    server_address = (host, port)
    
    # Allow port reuse to avoid 'Address already in use' errors
    socketserver.TCPServer.allow_reuse_address = True
    
    with socketserver.TCPServer(server_address, StudioHTTPRequestHandler) as httpd:
        url = f"http://{host}:{port}/"
        print(f"  [studio] Starting MeshFlow Studio UI at {url}")
        print("  [studio] Press Ctrl-C to stop.")
        
        try:
            webbrowser.open(url)
        except Exception:
            pass
            
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  [studio] Server stopped.")
