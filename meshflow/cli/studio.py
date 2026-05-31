"""Local HTTP server for MeshFlow Studio visual designer."""

from __future__ import annotations

import http.server
import os
import socketserver
import tempfile
import uuid
import webbrowser
from typing import Any


async def _run_workflow_yaml(yaml_str: str, task: str) -> dict[str, Any]:
    """Execute a workflow defined as a YAML string and return a result summary.

    Used by the studio Run button (/api/run POST endpoint).  Writes the YAML to
    a temp file, loads it as a WorkflowDefinition, and executes it with a
    minimal StepRuntime using the workflow's own policy.
    """
    from meshflow.core.workflow import WorkflowDefinition
    from meshflow.core.runtime import StepRuntime
    from meshflow.core.schemas import Policy

    run_id = f"studio-{uuid.uuid4().hex[:8]}"
    tmpfile = tempfile.NamedTemporaryFile(
        suffix=".yaml", mode="w", delete=False, encoding="utf-8"
    )
    try:
        tmpfile.write(yaml_str)
        tmpfile.close()

        wf = WorkflowDefinition.from_yaml(tmpfile.name)
        runtime = StepRuntime(policy=wf.policy or Policy(), run_id=run_id)
        result = await wf.run(task=task, runtime=runtime)

        return {
            "ok": True,
            "run_id": run_id,
            "output": result.output[:1000] if hasattr(result, "output") else str(result)[:1000],
            "cost_usd": result.total_cost_usd if hasattr(result, "total_cost_usd") else 0.0,
            "tokens": result.total_tokens if hasattr(result, "total_tokens") else 0,
        }
    except Exception as exc:
        return {
            "ok": False,
            "run_id": run_id,
            "error": str(exc),
            "output": "",
            "cost_usd": 0.0,
            "tokens": 0,
        }
    finally:
        try:
            os.unlink(tmpfile.name)
        except OSError:
            pass


class StudioHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Custom request handler that serves index.html and handles REST API routes."""

    def translate_path(self, path: str) -> str:
        # Resolve to index.html under meshflow/studio/templates/
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        template_dir = os.path.join(base_dir, "studio", "templates")
        
        if path == "/" or path == "/index.html":
            return os.path.join(template_dir, "index.html")
        if path in ("/templates", "/templates.html"):
            return os.path.join(template_dir, "templates.html")
        if path in ("/trace", "/trace.html"):
            return os.path.join(template_dir, "trace.html")
        if path in ("/graph", "/graph.html"):
            return os.path.join(template_dir, "graph.html")
        if path in ("/rag", "/rag.html"):
            return os.path.join(template_dir, "rag_builder.html")
            
        return super().translate_path(path)

    def do_GET(self) -> None:
        import json
        from meshflow.registry.templates import AgentTemplate, TemplateRegistry

        if self.path == "/api/curated-templates":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            from meshflow.registry.curated_templates import CURATED_TEMPLATES
            templates = [t.to_dict() for t in CURATED_TEMPLATES]
            self.wfile.write(json.dumps(templates).encode("utf-8"))
            return

        elif self.path == "/api/templates":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            reg = TemplateRegistry()
            templates = [t.to_dict() for t in reg.list()]
            self.wfile.write(json.dumps(templates).encode("utf-8"))
            return

        elif self.path == "/api/shared-templates":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            shared_dir = os.path.expanduser("~/.meshflow/shared_templates")
            reg = TemplateRegistry(registry_dir=shared_dir)
            templates = [t.to_dict() for t in reg.list()]
            
            # Seed community templates if empty to showcase a beautiful ecosystem
            if not templates:
                seeds = [
                    AgentTemplate(
                        name="financial-report-analyst",
                        role="researcher",
                        model="claude-sonnet-4-6",
                        system_prompt="You are a senior financial analyst. Audit quarterly filings.",
                        description="Extracts key metrics, runs liquidity ratio checks, and designs markdown financial reports.",
                        tags=["finance", "report", "sec"],
                        author="meshflow_team",
                        version="1.0.0"
                    ),
                    AgentTemplate(
                        name="security-code-critic",
                        role="critic",
                        model="claude-haiku-3-5",
                        system_prompt="You are an expert security auditor. Find vulnerabilities.",
                        description="Performs strict security inspection of source code, checking for SQLi, XSS, and command injection.",
                        tags=["security", "audit", "critic"],
                        author="sec_ops",
                        version="1.2.0"
                    ),
                    AgentTemplate(
                        name="smart-email-escalator",
                        role="executor",
                        model="claude-sonnet-4-6",
                        system_prompt="Auto-categorise and route incoming customer support messages.",
                        description="Processes customer feedback, performs sentiment analysis, and routes to webhook if urgent.",
                        tags=["customer-support", "routing"],
                        author="community_dev",
                        version="2.1.0"
                    )
                ]
                for s in seeds:
                    reg.publish(s)
                templates = [t.to_dict() for t in reg.list()]
                
            self.wfile.write(json.dumps(templates).encode("utf-8"))
            return

        super().do_GET()

    def do_POST(self) -> None:
        import json
        from meshflow.registry.templates import AgentTemplate, TemplateRegistry

        if self.path == "/api/run":
            import asyncio
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            try:
                body = json.loads(post_data.decode("utf-8"))
                yaml_str = body.get("yaml", "")
                task = body.get("task", "")
                if not yaml_str or not task:
                    raise ValueError("Both 'yaml' and 'task' are required")

                result = asyncio.run(_run_workflow_yaml(yaml_str, task))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(result).encode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        if self.path in ("/api/templates", "/api/shared-templates"):
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode("utf-8"))
                tmpl = AgentTemplate.from_dict(data)

                if self.path == "/api/shared-templates":
                    shared_dir = os.path.expanduser("~/.meshflow/shared_templates")
                    reg = TemplateRegistry(registry_dir=shared_dir)
                else:
                    reg = TemplateRegistry()

                reg.publish(tmpl)
                self.send_response(201)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "name": tmpl.name}).encode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        self.send_error(404)


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
