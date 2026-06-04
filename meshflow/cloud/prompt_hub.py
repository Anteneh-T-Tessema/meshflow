"""PromptHub — pull and push versioned prompts from meshflow.dev.

Usage::

    from meshflow.cloud import PromptHub

    # Fetch the active version of a prompt by slug
    system_prompt = PromptHub.get("hipaa-intake-processor")

    # Pin a specific version
    system_prompt = PromptHub.get("hipaa-intake-processor", version=3)

    # Push a new version (increments automatically)
    PromptHub.push("hipaa-intake-processor", content="You are …", notes="tightened PII rules")

    # List all prompt slugs registered for the org
    slugs = PromptHub.list()

Environment variables
---------------------

MESHFLOW_API_KEY       — required (same key used by MeshFlowCloud)
MESHFLOW_CLOUD_URL     — optional; defaults to https://meshflow.dev
"""

from __future__ import annotations

import os
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

_DEFAULT_BASE = "https://meshflow.dev"
_TTL_S        = 60   # cache TTL in seconds
_TIMEOUT_S    = 8


@dataclass
class _CacheEntry:
    content:    str
    version:    int
    model:      str
    temperature: float
    expires_at: float


class PromptHub:
    """Class-level interface for fetching/pushing prompts via the MeshFlow Cloud API.

    All methods are accessible on the class directly — no instance needed.
    """

    _cache: dict[str, _CacheEntry] = {}

    @classmethod
    def _cfg(cls) -> tuple[str, str, bool]:
        """Return (api_key, base_url, enabled)."""
        key  = os.environ.get("MESHFLOW_API_KEY", "")
        url  = os.environ.get("MESHFLOW_CLOUD_URL", _DEFAULT_BASE).rstrip("/")
        ok   = os.environ.get("MESHFLOW_CLOUD_ENABLED", "1") != "0" and bool(key)
        return key, url, ok

    @classmethod
    def _get_request(cls, path: str) -> dict[str, Any] | None:
        key, url, enabled = cls._cfg()
        if not enabled:
            return None
        req = urllib.request.Request(
            f"{url}{path}",
            headers={"x-meshflow-key": key, "Accept": "application/json"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, json.JSONDecodeError):
            return None

    @classmethod
    def _post_request(cls, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        key, url, enabled = cls._cfg()
        if not enabled:
            return None
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(
            f"{url}{path}",
            data=data,
            headers={
                "Content-Type": "application/json",
                "x-meshflow-key": key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, json.JSONDecodeError):
            return None

    @classmethod
    def get(
        cls,
        slug: str,
        version: int | None = None,
        *,
        default: str = "",
        ttl: int = _TTL_S,
    ) -> str:
        """Return the content of a prompt by slug.

        Parameters
        ----------
        slug:
            The prompt slug as registered in the Prompt Hub (e.g. ``"hipaa-intake-processor"``).
        version:
            Pin to a specific version number.  Defaults to the currently active version.
        default:
            Fallback string returned when the API is unreachable or the prompt
            is not found.  Useful for local/offline development.
        ttl:
            Cache TTL in seconds.  Set to 0 to bypass cache.
        """
        cache_key = f"{slug}:{version or 'active'}"
        now       = time.monotonic()

        if ttl > 0 and cache_key in cls._cache:
            entry = cls._cache[cache_key]
            if entry.expires_at > now:
                return entry.content
            del cls._cache[cache_key]

        path = f"/api/ingest/prompts?slug={slug}"
        if version is not None:
            path += f"&version={version}"

        data = cls._get_request(path)
        if not data or "content" not in data:
            return default

        content     = data["content"]
        cls._cache[cache_key] = _CacheEntry(
            content=content,
            version=data.get("version", 0),
            model=data.get("model", ""),
            temperature=data.get("temperature", 0.5),
            expires_at=now + ttl,
        )
        return content

    @classmethod
    def push(
        cls,
        slug: str,
        content: str,
        *,
        name: str | None = None,
        description: str | None = None,
        model: str = "",
        temperature: float = 0.5,
        notes: str = "",
    ) -> bool:
        """Push a new prompt version to the Hub.

        Creates the prompt if it doesn't exist yet; otherwise increments the
        version and marks it active.

        Returns ``True`` on success, ``False`` on error.
        """
        payload: dict[str, Any] = {"slug": slug, "content": content}
        if name:
            payload["name"] = name
        if description:
            payload["description"] = description
        if model:
            payload["model"] = model
        if temperature != 0.5:
            payload["temperature"] = temperature
        if notes:
            payload["notes"] = notes

        # Invalidate before the request — the user intends to replace the content
        for k in list(cls._cache):
            if k.startswith(f"{slug}:"):
                del cls._cache[k]
        result = cls._post_request("/api/ingest/prompts", payload)
        return result is not None and ("version" in result or result.get("ok") is True)

    @classmethod
    def list(cls) -> list[str]:
        """Return all prompt slugs registered for this org."""
        data = cls._get_request("/api/ingest/prompts?list=1")
        if isinstance(data, list):
            return [item.get("slug", "") for item in data if item.get("slug")]
        return []

    @classmethod
    def clear_cache(cls) -> None:
        """Flush the in-process prompt cache."""
        cls._cache.clear()
