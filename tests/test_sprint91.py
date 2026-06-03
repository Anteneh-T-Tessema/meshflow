"""Sprint 91 — RedisMemoryBackend, FileMemoryBackend."""
from __future__ import annotations

import json
import os
import tempfile

import pytest

os.environ.setdefault("MESHFLOW_MOCK", "1")


# ═══════════════════════════════════════════════════════════════════════════════
# FileMemoryBackend — no external deps, always testable
# ═══════════════════════════════════════════════════════════════════════════════

class TestFileMemoryBackend:
    def _backend(self, tmp_path):
        from meshflow import FileMemoryBackend
        return FileMemoryBackend(str(tmp_path / "memory"))

    def test_save_and_load(self, tmp_path):
        b = self._backend(tmp_path)
        b.save("session-1", {"key": "value", "count": 42})
        result = b.load("session-1")
        assert result == {"key": "value", "count": 42}

    def test_load_missing_returns_none(self, tmp_path):
        b = self._backend(tmp_path)
        assert b.load("nonexistent") is None

    def test_delete_removes_session(self, tmp_path):
        b = self._backend(tmp_path)
        b.save("s1", {"x": 1})
        b.delete("s1")
        assert b.load("s1") is None

    def test_delete_nonexistent_no_crash(self, tmp_path):
        b = self._backend(tmp_path)
        b.delete("ghost")  # should not raise

    def test_list_sessions_empty(self, tmp_path):
        b = self._backend(tmp_path)
        assert b.list_sessions() == []

    def test_list_sessions(self, tmp_path):
        b = self._backend(tmp_path)
        b.save("alice", {"a": 1})
        b.save("bob",   {"b": 2})
        sessions = set(b.list_sessions())
        assert sessions == {"alice", "bob"}

    def test_list_sessions_after_delete(self, tmp_path):
        b = self._backend(tmp_path)
        b.save("a", {})
        b.save("b", {})
        b.delete("a")
        assert b.list_sessions() == ["b"]

    def test_save_overwrites(self, tmp_path):
        b = self._backend(tmp_path)
        b.save("s", {"v": 1})
        b.save("s", {"v": 99})
        assert b.load("s") == {"v": 99}

    def test_save_is_atomic(self, tmp_path):
        """save() writes .tmp then renames — no partial files on success."""
        b = self._backend(tmp_path)
        b.save("s", {"data": "x" * 10_000})
        # .tmp file should not exist after successful save
        import os as _os
        tmp_files = [f for f in _os.listdir(b.directory) if f.endswith(".tmp")]
        assert tmp_files == []

    def test_complex_snapshot(self, tmp_path):
        b = self._backend(tmp_path)
        snapshot = {
            "agent_id": "analyst",
            "step_count": 5,
            "working": [{"content": "Q3 revenue up 12%", "score": 0.9}],
            "episodic": [],
            "semantic_entities": {"revenue": "up 12%"},
        }
        b.save("sess", snapshot)
        loaded = b.load("sess")
        assert loaded["agent_id"] == "analyst"
        assert loaded["step_count"] == 5
        assert loaded["working"][0]["content"] == "Q3 revenue up 12%"

    def test_session_id_sanitisation(self, tmp_path):
        """Slash and dot characters in session IDs are sanitised to prevent path traversal."""
        b = self._backend(tmp_path)
        b.save("../../../etc/passwd", {"evil": True})
        # File must be inside the directory
        import os as _os
        files = _os.listdir(b.directory)
        assert all(not f.startswith("..") for f in files)
        # Can still load the session using the original ID
        result = b.load("../../../etc/passwd")
        assert result == {"evil": True}

    def test_directory_created_automatically(self, tmp_path):
        nested = str(tmp_path / "a" / "b" / "c")
        from meshflow import FileMemoryBackend
        b = FileMemoryBackend(nested)
        b.save("s", {"ok": True})
        assert b.load("s") == {"ok": True}

    def test_json_file_is_human_readable(self, tmp_path):
        b = self._backend(tmp_path)
        b.save("readable", {"hello": "world"})
        import os as _os
        files = _os.listdir(b.directory)
        json_file = next(f for f in files if f.endswith(".json"))
        content = open(_os.path.join(b.directory, json_file)).read()
        # Must be indented JSON
        data = json.loads(content)
        assert data["hello"] == "world"
        assert "\n" in content  # indented

    def test_exported_from_meshflow(self):
        from meshflow import FileMemoryBackend
        assert FileMemoryBackend is not None

    def test_implements_memory_backend(self, tmp_path):
        from meshflow import FileMemoryBackend, MemoryBackend
        b = self._backend(tmp_path)
        assert isinstance(b, MemoryBackend)


# ═══════════════════════════════════════════════════════════════════════════════
# RedisMemoryBackend — tested with a mock when redis is not available
# ═══════════════════════════════════════════════════════════════════════════════

class TestRedisMemoryBackendInterface:
    """Tests that do not require a live Redis server."""

    def test_exported_from_meshflow(self):
        from meshflow import RedisMemoryBackend
        assert RedisMemoryBackend is not None

    def test_implements_memory_backend(self):
        from meshflow import RedisMemoryBackend, MemoryBackend
        b = RedisMemoryBackend("redis://localhost:6379/0")
        assert isinstance(b, MemoryBackend)

    def test_key_prefix_default(self):
        from meshflow import RedisMemoryBackend
        b = RedisMemoryBackend("redis://localhost:6379/0")
        assert b._key("my-session") == "meshflow:memory:my-session"

    def test_key_custom_prefix(self):
        from meshflow import RedisMemoryBackend
        b = RedisMemoryBackend("redis://localhost:6379/0", prefix="app:mem:")
        assert b._key("sess") == "app:mem:sess"

    def test_ttl_attribute(self):
        from meshflow import RedisMemoryBackend
        b = RedisMemoryBackend("redis://localhost:6379/0", ttl=3600)
        assert b.ttl == 3600

    def test_ttl_none_by_default(self):
        from meshflow import RedisMemoryBackend
        b = RedisMemoryBackend("redis://localhost:6379/0")
        assert b.ttl is None

    def test_missing_redis_raises_import_error(self):
        """When redis package is absent, save() raises ImportError with install hint."""
        import sys
        from meshflow import RedisMemoryBackend

        # Temporarily hide redis from the import system
        original = sys.modules.pop("redis", None)
        try:
            b = RedisMemoryBackend("redis://localhost:6379/0")
            b._client = None  # force re-import attempt
            with pytest.raises(ImportError, match="pip install redis"):
                b.save("s", {"x": 1})
        finally:
            if original is not None:
                sys.modules["redis"] = original


class TestRedisMemoryBackendWithMock:
    """Tests using a minimal in-memory Redis mock — no live server needed."""

    def _backend_with_mock(self):
        from meshflow import RedisMemoryBackend

        class MockRedis:
            def __init__(self):
                self._store: dict[str, str] = {}
                self._ttls: dict[str, int] = {}

            def set(self, key, value):
                self._store[key] = value

            def setex(self, key, ttl, value):
                self._store[key] = value
                self._ttls[key] = ttl

            def get(self, key):
                return self._store.get(key)

            def delete(self, key):
                self._store.pop(key, None)
                self._ttls.pop(key, None)

            def keys(self, pattern):
                # naive glob: pattern ends with *
                prefix = pattern.rstrip("*")
                return [k for k in self._store if k.startswith(prefix)]

            def expire(self, key, ttl):
                if key in self._store:
                    self._ttls[key] = ttl
                    return True
                return False

        b = RedisMemoryBackend("redis://localhost:6379/0", prefix="test:")
        b._client = MockRedis()
        return b

    def test_save_and_load(self):
        b = self._backend_with_mock()
        b.save("s1", {"agent": "analyst", "steps": 3})
        result = b.load("s1")
        assert result == {"agent": "analyst", "steps": 3}

    def test_load_missing_returns_none(self):
        b = self._backend_with_mock()
        assert b.load("does-not-exist") is None

    def test_delete(self):
        b = self._backend_with_mock()
        b.save("s", {"x": 1})
        b.delete("s")
        assert b.load("s") is None

    def test_list_sessions(self):
        b = self._backend_with_mock()
        b.save("alice", {})
        b.save("bob", {})
        sessions = set(b.list_sessions())
        assert sessions == {"alice", "bob"}

    def test_save_uses_setex_when_ttl_set(self):
        from meshflow import RedisMemoryBackend

        class MockRedis:
            def __init__(self):
                self._store = {}
                self.setex_calls = []

            def set(self, key, value):
                self._store[key] = value

            def setex(self, key, ttl, value):
                self._store[key] = value
                self.setex_calls.append((key, ttl))

            def get(self, key):
                return self._store.get(key)

            def delete(self, key):
                self._store.pop(key, None)

            def keys(self, pattern):
                return list(self._store.keys())

            def expire(self, key, ttl):
                return key in self._store

        b = RedisMemoryBackend("redis://localhost:6379/0", ttl=1800, prefix="t:")
        b._client = MockRedis()
        b.save("s", {"data": "x"})
        assert len(b._client.setex_calls) == 1
        key, ttl = b._client.setex_calls[0]
        assert key == "t:s"
        assert ttl == 1800

    def test_save_uses_set_when_no_ttl(self):
        b = self._backend_with_mock()
        b.save("s", {"data": "x"})
        assert "test:s" in b._client._store
        assert "test:s" not in b._client._ttls

    def test_refresh_ttl_false_when_no_ttl(self):
        b = self._backend_with_mock()
        b.save("s", {})
        assert b.refresh_ttl("s") is False

    def test_refresh_ttl_true_when_ttl_set(self):
        from meshflow import RedisMemoryBackend

        class MockRedis:
            def __init__(self):
                self._store = {}
                self._ttls = {}

            def set(self, k, v):
                self._store[k] = v

            def setex(self, k, ttl, v):
                self._store[k] = v
                self._ttls[k] = ttl

            def get(self, k):
                return self._store.get(k)

            def delete(self, k):
                self._store.pop(k, None)

            def keys(self, pattern):
                return list(self._store.keys())

            def expire(self, k, ttl):
                if k in self._store:
                    self._ttls[k] = ttl
                    return True
                return False

        b = RedisMemoryBackend("redis://localhost:6379/0", ttl=3600, prefix="t:")
        b._client = MockRedis()
        b.save("s", {})
        assert b.refresh_ttl("s") is True

    def test_complex_snapshot_round_trip(self):
        b = self._backend_with_mock()
        snapshot = {
            "agent_id": "writer",
            "step_count": 10,
            "working": [{"content": "Revenue up 12%", "score": 0.9}],
            "episodic": [{"summary": "analysed Q3"}],
        }
        b.save("writer-session", snapshot)
        loaded = b.load("writer-session")
        assert loaded["agent_id"] == "writer"
        assert loaded["step_count"] == 10
        assert loaded["working"][0]["score"] == 0.9


# ═══════════════════════════════════════════════════════════════════════════════
# Backend compatibility: all backends pass the same interface contract
# ═══════════════════════════════════════════════════════════════════════════════

class TestBackendContract:
    """Parametrised contract tests — every backend must honour the same interface."""

    def _backends(self, tmp_path):
        from meshflow import FileMemoryBackend, InMemoryBackend, SQLiteMemoryBackend
        return [
            ("InMemory",  InMemoryBackend()),
            ("SQLite",    SQLiteMemoryBackend(":memory:")),
            ("File",      FileMemoryBackend(str(tmp_path / "mem"))),
        ]

    @pytest.mark.parametrize("name,backend", [
        ("InMemory",  None),
        ("SQLite",    None),
        ("File",      None),
    ])
    def test_save_load_delete(self, tmp_path, name, backend):
        from meshflow import FileMemoryBackend, InMemoryBackend, SQLiteMemoryBackend
        backends = {
            "InMemory": InMemoryBackend(),
            "SQLite":   SQLiteMemoryBackend(":memory:"),
            "File":     FileMemoryBackend(str(tmp_path / "mem")),
        }
        b = backends[name]
        b.save("sess", {"val": 42})
        assert b.load("sess") == {"val": 42}
        b.delete("sess")
        assert b.load("sess") is None

    @pytest.mark.parametrize("name,backend", [
        ("InMemory",  None),
        ("SQLite",    None),
        ("File",      None),
    ])
    def test_list_sessions(self, tmp_path, name, backend):
        from meshflow import FileMemoryBackend, InMemoryBackend, SQLiteMemoryBackend
        backends = {
            "InMemory": InMemoryBackend(),
            "SQLite":   SQLiteMemoryBackend(":memory:"),
            "File":     FileMemoryBackend(str(tmp_path / "mem2")),
        }
        b = backends[name]
        b.save("a", {})
        b.save("b", {})
        sessions = set(b.list_sessions())
        assert "a" in sessions
        assert "b" in sessions

    @pytest.mark.parametrize("name,backend", [
        ("InMemory",  None),
        ("SQLite",    None),
        ("File",      None),
    ])
    def test_overwrite(self, tmp_path, name, backend):
        from meshflow import FileMemoryBackend, InMemoryBackend, SQLiteMemoryBackend
        backends = {
            "InMemory": InMemoryBackend(),
            "SQLite":   SQLiteMemoryBackend(":memory:"),
            "File":     FileMemoryBackend(str(tmp_path / "mem3")),
        }
        b = backends[name]
        b.save("s", {"v": 1})
        b.save("s", {"v": 2})
        assert b.load("s") == {"v": 2}
