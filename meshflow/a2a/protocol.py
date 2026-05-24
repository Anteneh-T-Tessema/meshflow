"""A2A transport types — wire-compatible with the Google A2A spec draft."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentCard:
    """Describes an agent reachable over A2A HTTP."""

    name: str
    description: str = ""
    url: str = ""
    capabilities: list[str] = field(default_factory=list)
    version: str = "1.0"
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "url": self.url,
            "capabilities": self.capabilities,
            "version": self.version,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentCard":
        return cls(
            name=d.get("name", ""),
            description=d.get("description", ""),
            url=d.get("url", ""),
            capabilities=d.get("capabilities", []),
            version=d.get("version", "1.0"),
            input_schema=d.get("input_schema", {}),
            output_schema=d.get("output_schema", {}),
        )


@dataclass
class A2AMessage:
    """A task message sent to a remote agent."""

    content: str
    sender: str = "user"
    context: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "sender": self.sender,
            "context": self.context,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "A2AMessage":
        return cls(
            content=d.get("content", ""),
            sender=d.get("sender", "user"),
            context=d.get("context", {}),
            metadata=d.get("metadata", {}),
        )


@dataclass
class A2AResponse:
    """Response returned by a remote A2A agent."""

    content: str
    agent_name: str = ""
    tokens: int = 0
    cost_usd: float = 0.0
    blocked: bool = False
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return not self.blocked and not self.error

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "agent_name": self.agent_name,
            "tokens": self.tokens,
            "cost_usd": self.cost_usd,
            "blocked": self.blocked,
            "error": self.error,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "A2AResponse":
        return cls(
            content=d.get("content", ""),
            agent_name=d.get("agent_name", ""),
            tokens=d.get("tokens", 0),
            cost_usd=d.get("cost_usd", 0.0),
            blocked=d.get("blocked", False),
            error=d.get("error", ""),
            metadata=d.get("metadata", {}),
        )
