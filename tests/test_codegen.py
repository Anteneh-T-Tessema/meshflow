"""Unit tests for the multi-language SDK codegen engine."""

from __future__ import annotations

import tempfile
import os
from meshflow.core.codegen import SDKCodeGenerator


def test_codegen_dotnet_and_java():
    yaml_content = """
name: demo-codegen-flow
nodes:
  - id: researcher
    kind: native
    agent:
      role: researcher
      model: claude-sonnet-4-6
  - id: checker
    kind: native
    agent:
      role: critic
      model: claude-haiku-3-5
  - id: webhook
    kind: http
    url: https://api.example.com/webhook
edges:
  - from: researcher
    to: checker
  - from: checker
    to: webhook
"""

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        path = f.name

    try:
        gen = SDKCodeGenerator(path)
        
        # Test Dotnet
        dotnet_code = gen.generate_dotnet()
        assert "class DemoCodegenFlowWorkflow" in dotnet_code
        assert 'public string Name => "demo-codegen-flow";' in dotnet_code
        assert 'Nodes.Add(new AgentNode("researcher", NodeKind.Native) { Role = "researcher", Model = "claude-sonnet-4-6" });' in dotnet_code
        assert 'Nodes.Add(new AgentNode("webhook", NodeKind.Http) { Url = "https://api.example.com/webhook" });' in dotnet_code
        assert 'Edges.Add(new TransitionEdge("researcher", "checker"));' in dotnet_code
        assert 'Edges.Add(new TransitionEdge("checker", "webhook"));' in dotnet_code

        # Test Java
        java_code = gen.generate_java()
        assert "public class DemoCodegenFlowWorkflow" in java_code
        assert 'private final String name = "demo-codegen-flow";' in java_code
        assert 'nodes.add(new AgentNode("researcher", NodeKind.NATIVE).setRole("researcher").setModel("claude-sonnet-4-6"));' in java_code
        assert 'nodes.add(new AgentNode("webhook", NodeKind.HTTP).setUrl("https://api.example.com/webhook"));' in java_code
        assert 'edges.add(new TransitionEdge("researcher", "checker"));' in java_code
        assert 'edges.add(new TransitionEdge("checker", "webhook"));' in java_code

        # Test Go
        go_code = gen.generate_go()
        assert "package sdk" in go_code
        assert "type DemoCodegenFlowWorkflow struct {" in go_code
        assert "Name  string" in go_code
        assert 'Name: "demo-codegen-flow",' in go_code
        assert '{Id: "researcher", Kind: NodeKindNative, Role: "researcher", Model: "claude-sonnet-4-6"}' in go_code
        assert '{Id: "webhook", Kind: NodeKindHttp, Url: "https://api.example.com/webhook"}' in go_code
        assert '{From: "researcher", To: "checker"}' in go_code
        assert '{From: "checker", To: "webhook"}' in go_code

    finally:
        os.unlink(path)
