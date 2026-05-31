"""Unit tests for the template sharing and marketplace functionality."""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.request
import urllib.error
from http.server import HTTPServer
import pytest

from meshflow.registry.templates import AgentTemplate, TemplateRegistry
from meshflow.cli.studio import StudioHTTPRequestHandler


def get_free_port() -> int:
    """Get a free port on localhost."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def studio_server():
    """Start the studio HTTP server on a background thread for testing."""
    port = get_free_port()
    server = HTTPServer(("127.0.0.1", port), StudioHTTPRequestHandler)
    
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    
    # Wait for server to boot
    time.sleep(0.1)
    
    yield f"http://127.0.0.1:{port}"
    
    server.shutdown()
    server.server_close()
    thread.join(timeout=1.0)


def test_api_templates_and_shared(studio_server):
    # Ensure GET /api/templates works
    req = urllib.request.Request(f"{studio_server}/api/templates")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert isinstance(data, list)

    # Ensure GET /api/shared-templates works (includes seeding if empty)
    req = urllib.request.Request(f"{studio_server}/api/shared-templates")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert isinstance(data, list)
        assert len(data) >= 3  # Ensure seeded templates are present
        assert any(t["name"] == "financial-report-analyst" for t in data)

    # Test POST /api/templates to create a new template
    new_template = {
        "name": "tester-agent",
        "role": "executor",
        "model": "claude-haiku-3-5",
        "system_prompt": "You are a test runner.",
        "description": "Used in automated verification.",
        "tags": ["test", "verification"],
        "author": "pytest",
        "version": "0.9.9",
        "metadata": {}
    }
    
    post_data = json.dumps(new_template).encode("utf-8")
    req = urllib.request.Request(
        f"{studio_server}/api/templates",
        data=post_data,
        headers={"Content-Type": "application/json"}
    )
    
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 201
        res = json.loads(resp.read().decode("utf-8"))
        assert res["status"] == "success"
        assert res["name"] == "tester-agent"

    # Verify the local registry now has the template
    req = urllib.request.Request(f"{studio_server}/api/templates")
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        assert any(t["name"] == "tester-agent" for t in data)


def test_cli_share_command(tmp_path, monkeypatch):
    """Test CLI sharing command copying template to community marketplace."""
    import argparse
    from meshflow.cli.main import _cmd_templates

    # Setup directories
    local_dir = tmp_path / "local"
    shared_dir = tmp_path / "shared"
    local_dir.mkdir()
    shared_dir.mkdir()

    # Mock directories
    monkeypatch.setenv("HOME", str(tmp_path))
    
    # Pre-seed a template in local registry
    tmpl = AgentTemplate(
        name="local-hero",
        role="executor",
        description="A local hero agent template.",
        tags=["hero"],
        version="1.0.0"
    )
    
    # We can override the registry_dir inside TemplateRegistry or mock Path.home()
    # The CLI relies on Path.home() / ".meshflow" / "templates" for local
    # and ~/.meshflow/shared_templates for shared.
    home_meshflow = tmp_path / ".meshflow"
    (home_meshflow / "templates").mkdir(parents=True, exist_ok=True)
    (home_meshflow / "shared_templates").mkdir(parents=True, exist_ok=True)

    # Publish template locally first
    local_reg = TemplateRegistry(registry_dir=str(home_meshflow / "templates"))
    local_reg.publish(tmpl)

    # Mock the home folder return or verify the command via _cmd_templates
    # Let's override Path.home() / env HOME which monkeypatch.setenv("HOME", ...) handles.
    
    # Verify the file is in local but not shared
    assert (home_meshflow / "templates" / "local-hero.yaml").exists()
    assert not (home_meshflow / "shared_templates" / "local-hero.yaml").exists()

    # Construct arguments for share command
    args = argparse.Namespace(
        templates_cmd="share",
        name="local-hero"
    )

    # We need to ensure TemplateRegistry uses the mocked home.
    # In templates.py, Path.home() returns the mocked HOME.
    # Run the share command
    _cmd_templates(args)

    # Verify it was copied to the shared marketplace
    assert (home_meshflow / "shared_templates" / "local-hero.yaml").exists()
    shared_reg = TemplateRegistry(registry_dir=str(home_meshflow / "shared_templates"))
    shared_tmpl = shared_reg.pull("local-hero")
    assert shared_tmpl.name == "local-hero"
    assert shared_tmpl.description == tmpl.description


def test_curated_templates_endpoints(studio_server):
    # Ensure GET /templates page is served
    req = urllib.request.Request(f"{studio_server}/templates")
    with urllib.request.urlopen(req) as resp:
      assert resp.status == 200
      html = resp.read().decode("utf-8")
      assert "<title>MeshFlow Studio — Curated Agent Template Gallery</title>" in html

    # Ensure GET /api/curated-templates returns the 20 pre-built templates
    req = urllib.request.Request(f"{studio_server}/api/curated-templates")
    with urllib.request.urlopen(req) as resp:
      assert resp.status == 200
      data = json.loads(resp.read().decode("utf-8"))
      assert isinstance(data, list)
      assert len(data) == 20
      assert any(t["name"] == "hipaa-compliance-analyst" for t in data)

