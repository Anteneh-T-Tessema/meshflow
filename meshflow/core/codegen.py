"""Multi-language SDK code generation engine.

Translates MeshFlow workflow YAML topologies into native C# (.NET) and Java
wrapper definitions to ease cross-language integration.
"""

from __future__ import annotations

import yaml


class SDKCodeGenerator:
    """Generates C# and Java wrapper structures for a MeshFlow workflow."""

    def __init__(self, yaml_path: str) -> None:
        self.yaml_path = yaml_path
        with open(yaml_path) as f:
            self.data = yaml.safe_load(f) or {}

    def generate_dotnet(self) -> str:
        """Generate C# wrapper classes for the workflow."""
        name = self.data.get("name", "UnnamedWorkflow")
        class_name = "".join(part.capitalize() for part in name.replace("-", "_").split("_"))

        nodes = self.data.get("nodes", [])
        edges = self.data.get("edges", [])

        # Start class definition
        code = [
            "using System;",
            "using System.Collections.Generic;",
            "",
            "namespace MeshFlow.SDK",
            "{",
            f"    public class {class_name}Workflow",
            "    {",
            '        public string Name => "' + name + '";',
            "        public List<AgentNode> Nodes { get; } = new List<AgentNode>();",
            "        public List<TransitionEdge> Edges { get; } = new List<TransitionEdge>();",
            "",
            f"        public {class_name}Workflow()",
            "        {",
        ]

        # Add Nodes
        for n in nodes:
            node_id = n.get("id", "unnamed")
            kind = n.get("kind", "native")

            if kind == "native":
                agent = n.get("agent", {})
                role = agent.get("role", "executor")
                model = agent.get("model", "claude-sonnet-4-6")
                code.append(
                    f'            Nodes.Add(new AgentNode("{node_id}", NodeKind.Native) {{ '
                    f'Role = "{role}", Model = "{model}" }});'
                )
            elif kind == "http":
                url = n.get("url", "")
                code.append(
                    f'            Nodes.Add(new AgentNode("{node_id}", NodeKind.Http) {{ '
                    f'Url = "{url}" }});'
                )
            elif kind == "subgraph":
                wf_ref = n.get("workflow", "")
                code.append(
                    f'            Nodes.Add(new AgentNode("{node_id}", NodeKind.Subgraph) {{ '
                    f'WorkflowRef = "{wf_ref}" }});'
                )

        code.append("")

        # Add Edges
        for e in edges:
            frm = e.get("from", "")
            to = e.get("to", "")
            code.append(f'            Edges.Add(new TransitionEdge("{frm}", "{to}"));')

        # Add supporting classes and close
        code.extend([
            "        }",
            "    }",
            "",
            "    public enum NodeKind { Native, Http, Subgraph }",
            "",
            "    public class AgentNode",
            "    {",
            "        public string Id { get; }",
            "        public NodeKind Kind { get; }",
            "        public string Role { get; set; }",
            "        public string Model { get; set; }",
            "        public string Url { get; set; }",
            "        public string WorkflowRef { get; set; }",
            "",
            "        public AgentNode(string id, NodeKind kind)",
            "        {",
            "            Id = id;",
            "            Kind = kind;",
            "        }",
            "    }",
            "",
            "    public class TransitionEdge",
            "    {",
            "        public string From { get; }",
            "        public string To { get; }",
            "",
            "        public TransitionEdge(string from, string to)",
            "        {",
            "            From = from;",
            "            To = to;",
            "        }",
            "    }",
            "}",
        ])

        return "\n".join(code)

    def generate_java(self) -> str:
        """Generate Java wrapper classes for the workflow."""
        name = self.data.get("name", "UnnamedWorkflow")
        class_name = "".join(part.capitalize() for part in name.replace("-", "_").split("_"))

        nodes = self.data.get("nodes", [])
        edges = self.data.get("edges", [])

        code = [
            "package meshflow.sdk;",
            "",
            "import java.util.ArrayList;",
            "import java.util.List;",
            "",
            f"public class {class_name}Workflow {{",
            '    private final String name = "' + name + '";',
            "    private final List<AgentNode> nodes = new ArrayList<>();",
            "    private final List<TransitionEdge> edges = new ArrayList<>();",
            "",
            f"    public {class_name}Workflow() {{",
        ]

        # Add Nodes
        for n in nodes:
            node_id = n.get("id", "unnamed")
            kind = n.get("kind", "native")

            if kind == "native":
                agent = n.get("agent", {})
                role = agent.get("role", "executor")
                model = agent.get("model", "claude-sonnet-4-6")
                code.append(
                    f'        nodes.add(new AgentNode("{node_id}", NodeKind.NATIVE)'
                    f'.setRole("{role}").setModel("{model}"));'
                )
            elif kind == "http":
                url = n.get("url", "")
                code.append(
                    f'        nodes.add(new AgentNode("{node_id}", NodeKind.HTTP)'
                    f'.setUrl("{url}"));'
                )
            elif kind == "subgraph":
                wf_ref = n.get("workflow", "")
                code.append(
                    f'        nodes.add(new AgentNode("{node_id}", NodeKind.SUBGRAPH)'
                    f'.setWorkflowRef("{wf_ref}"));'
                )

        code.append("")

        # Add Edges
        for e in edges:
            frm = e.get("from", "")
            to = e.get("to", "")
            code.append(f'        edges.add(new TransitionEdge("{frm}", "{to}"));')

        # Add supporting classes and close
        code.extend([
            "    }",
            "",
            "    public String getName() { return name; }",
            "    public List<AgentNode> getNodes() { return nodes; }",
            "    public List<TransitionEdge> getEdges() { return edges; }",
            "",
            "    public enum NodeKind { NATIVE, HTTP, SUBGRAPH }",
            "",
            "    public static class AgentNode {",
            "        private final String id;",
            "        private final NodeKind kind;",
            "        private String role;",
            "        private String model;",
            "        private String url;",
            "        private String workflowRef;",
            "",
            "        public AgentNode(String id, NodeKind kind) {",
            "            this.id = id;",
            "            this.kind = kind;",
            "        }",
            "",
            "        public String getId() { return id; }",
            "        public NodeKind getKind() { return kind; }",
            "        public String getRole() { return role; }",
            "        public AgentNode setRole(String role) { this.role = role; return this; }",
            "        public String getModel() { return model; }",
            "        public AgentNode setModel(String model) { this.model = model; return this; }",
            "        public String getUrl() { return url; }",
            "        public AgentNode setUrl(String url) { this.url = url; return this; }",
            "        public String getWorkflowRef() { return workflowRef; }",
            "        public AgentNode setWorkflowRef(String ref) { this.workflowRef = ref; return this; }",
            "    }",
            "",
            "    public static class TransitionEdge {",
            "        private final String from;",
            "        private final String to;",
            "",
            "        public TransitionEdge(String from, String to) {",
            "            this.from = from;",
            "            this.to = to;",
            "        }",
            "",
            "        public String getFrom() { return from; }",
            "        public String getTo() { return to; }",
            "    }",
            "}",
        ])

        return "\n".join(code)

    def generate_go(self) -> str:
        """Generate Go wrapper structs for the workflow."""
        name = self.data.get("name", "UnnamedWorkflow")
        class_name = "".join(part.capitalize() for part in name.replace("-", "_").split("_"))

        nodes = self.data.get("nodes", [])
        edges = self.data.get("edges", [])

        code = [
            "package sdk",
            "",
            "type NodeKind string",
            "",
            "const (",
            '\tNodeKindNative   NodeKind = "NATIVE"',
            '\tNodeKindHttp     NodeKind = "HTTP"',
            '\tNodeKindSubgraph NodeKind = "SUBGRAPH"',
            ")",
            "",
            "type AgentNode struct {",
            '\tId          string   `json:"id"`',
            '\tKind        NodeKind `json:"kind"`',
            '\tRole        string   `json:"role,omitempty"`',
            '\tModel       string   `json:"model,omitempty"`',
            '\tUrl         string   `json:"url,omitempty"`',
            '\tWorkflowRef string   `json:"workflow_ref,omitempty"`',
            "}",
            "",
            "type TransitionEdge struct {",
            '\tFrom string `json:"from"`',
            '\tTo   string `json:"to"`',
            "}",
            "",
            f"type {class_name}Workflow struct {{",
            "\tName  string",
            "\tNodes []AgentNode",
            "\tEdges []TransitionEdge",
            "}",
            "",
            f"func New{class_name}Workflow() *{class_name}Workflow {{",
            f"\treturn &{class_name}Workflow{{",
            f'\t\tName: "{name}",',
            "\t\tNodes: []AgentNode{",
        ]

        for n in nodes:
            node_id = n.get("id", "unnamed")
            kind = n.get("kind", "native")

            if kind == "native":
                agent = n.get("agent", {})
                role = agent.get("role", "executor")
                model = agent.get("model", "claude-sonnet-4-6")
                code.append(
                    f'\t\t\t{{Id: "{node_id}", Kind: NodeKindNative, Role: "{role}", Model: "{model}"}},'
                )
            elif kind == "http":
                url = n.get("url", "")
                code.append(
                    f'\t\t\t{{Id: "{node_id}", Kind: NodeKindHttp, Url: "{url}"}},'
                )
            elif kind == "subgraph":
                wf_ref = n.get("workflow", "")
                code.append(
                    f'\t\t\t{{Id: "{node_id}", Kind: NodeKindSubgraph, WorkflowRef: "{wf_ref}"}},'
                )

        code.extend([
            "\t\t},",
            "\t\tEdges: []TransitionEdge{",
        ])

        for e in edges:
            frm = e.get("from", "")
            to = e.get("to", "")
            code.append(f'\t\t\t{{From: "{frm}", To: "{to}"}},')

        code.extend([
            "\t\t},",
            "\t}",
            "}",
        ])

        return "\n".join(code)

