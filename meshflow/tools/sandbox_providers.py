"""Cloud sandbox providers for MeshFlow's CodeInterpreter ecosystem.

Adds two optional cloud backends — E2B and Modal — plus a ``SandboxRouter``
that selects the right backend automatically based on the environment variable
``MESHFLOW_SANDBOX_BACKEND``.

All three classes share the same interface as :class:`~meshflow.tools.code_interpreter.CodeInterpreter`:

* ``run(code) -> CodeResult``  — synchronous
* ``arun(code) -> CodeResult`` — async (non-blocking in an event loop)

Quick-start
-----------
**Subprocess (default / zero deps)**::

    from meshflow.tools.sandbox_providers import SandboxRouter
    result = SandboxRouter().run("print(1 + 1)")
    print(result)   # 2

**E2B cloud sandbox** (requires ``pip install e2b``)::

    import os
    os.environ["MESHFLOW_SANDBOX_BACKEND"] = "e2b"
    os.environ["E2B_API_KEY"] = "your-key"

    from meshflow.tools.sandbox_providers import SandboxRouter
    result = SandboxRouter().run("import sys; print(sys.version)")

**Modal ephemeral sandbox** (requires ``pip install modal``)::

    import os
    os.environ["MESHFLOW_SANDBOX_BACKEND"] = "modal"

    from meshflow.tools.sandbox_providers import SandboxRouter
    result = SandboxRouter().run("print('hello from Modal')")

**Direct instantiation**::

    from meshflow.tools.sandbox_providers import E2BSandboxProvider, ModalSandboxProvider

    e2b = E2BSandboxProvider(timeout=60, api_key="sk-...")
    result = e2b.run("print('secure cloud exec')")

    modal_p = ModalSandboxProvider(timeout=120, allow_modules=["numpy", "pandas"])
    result = modal_p.run("import numpy as np; print(np.__version__)")

Backend selection via ``MESHFLOW_SANDBOX_BACKEND``
---------------------------------------------------
+------------------+------------------------------------------+
| Value            | Provider                                 |
+==================+==========================================+
| ``subprocess``   | :class:`~meshflow.tools.code_interpreter.CodeInterpreter` (default) |
| ``e2b``          | :class:`E2BSandboxProvider`              |
| ``modal``        | :class:`ModalSandboxProvider`            |
+------------------+------------------------------------------+

If the chosen backend's optional SDK is not installed, a clear
``ImportError`` is raised with the ``pip install`` command to fix it.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import TYPE_CHECKING

from meshflow.tools.code_interpreter import CodeInterpreter, CodeResult

if TYPE_CHECKING:
    pass

__all__ = [
    "E2BSandboxProvider",
    "ModalSandboxProvider",
    "SandboxRouter",
]

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

_SUBPROCESS = "subprocess"
_E2B = "e2b"
_MODAL = "modal"
_VALID_BACKENDS = (_SUBPROCESS, _E2B, _MODAL)


def _run_sync(coro: object) -> CodeResult:
    """Run an async coroutine synchronously regardless of event-loop state."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    import asyncio as _asyncio  # local to avoid shadowing at module level

    if loop is not None and loop.is_running():
        # We are inside an event loop — use a thread to avoid blocking it.
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_asyncio.run, coro)  # type: ignore[arg-type]
            return future.result()
    else:
        return _asyncio.run(coro)  # type: ignore[arg-type]


# ──────────────────────────────────────────────────────────────────────────────
# E2B sandbox provider
# ──────────────────────────────────────────────────────────────────────────────

class E2BSandboxProvider:
    """Run Python code inside an `E2B <https://e2b.dev>`_ cloud micro-VM.

    E2B provides isolated, short-lived sandboxes that are ideal for staging
    and CI workloads where subprocess isolation is not sufficient.

    Parameters
    ----------
    timeout:
        Default wall-clock timeout **in seconds** per :meth:`run` call.
        Passed directly to ``e2b.Sandbox`` as the process timeout.
    allow_modules:
        Optional allow-list of top-level importable modules.  When supplied,
        a ``builtins.__import__`` shim is prepended to the executed code — the
        same strategy used by :class:`~meshflow.tools.code_interpreter.CodeInterpreter`.
    api_key:
        E2B API key.  Falls back to the ``E2B_API_KEY`` environment variable
        when left empty (recommended for production).

    Raises
    ------
    ImportError
        Raised at *instantiation time* when ``e2b`` is not installed.

    Examples
    --------
    >>> provider = E2BSandboxProvider(timeout=30, api_key="sk-...")
    >>> result = provider.run("print(2 ** 10)")
    >>> print(result)
    1024
    """

    def __init__(
        self,
        timeout: int = 30,
        allow_modules: list[str] | None = None,
        api_key: str = "",
    ) -> None:
        try:
            import e2b as _e2b  # noqa: F401 — validate presence at construction
        except ImportError as exc:
            raise ImportError(
                "The 'e2b' package is required for E2BSandboxProvider.\n"
                "Install it with:  pip install e2b\n"
                "Then set your API key:  export E2B_API_KEY=sk-..."
            ) from exc

        self.timeout = timeout
        self.allow_modules = allow_modules
        self._api_key: str = api_key or os.environ.get("E2B_API_KEY", "")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_preamble(self) -> str:
        """Return an import-restriction preamble matching CodeInterpreter's."""
        if self.allow_modules is None:
            return ""
        import json
        allowed_json = json.dumps(self.allow_modules)
        return f"""
import builtins as _bi
_allowed = {allowed_json}
_orig_import = _bi.__import__
def _restricted_import(name, *args, **kwargs):
    top = name.split('.')[0]
    _safe = ('builtins', '__future__', '_thread', '_warnings', 'abc',
             'codecs', 'collections', 'contextlib', 'copy', 'dataclasses',
             'datetime', 'enum', 'functools', 'gc', 'importlib', 'io',
             'itertools', 'operator', 'os', 'pathlib', 're', 'sys',
             'textwrap', 'threading', 'time', 'traceback', 'types',
             'typing', 'warnings', 'weakref')
    if top not in _allowed and top not in _safe:
        raise ImportError(f"Module '{{name}}' is not in the allow-list")
    return _orig_import(name, *args, **kwargs)
_bi.__import__ = _restricted_import
"""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, code: str) -> CodeResult:
        """Execute *code* synchronously inside an E2B sandbox.

        A fresh ``e2b.Sandbox`` context is opened for every call, ensuring
        full isolation between invocations.

        Parameters
        ----------
        code:
            Arbitrary Python source code to execute.

        Returns
        -------
        CodeResult
            Populated with ``stdout``, ``stderr``, ``error``,
            ``execution_time_ms``, and ``timed_out``.
        """
        return _run_sync(self.arun(code))

    async def arun(self, code: str) -> CodeResult:
        """Async variant of :meth:`run` — awaitable from an async context.

        Parameters
        ----------
        code:
            Arbitrary Python source code to execute.

        Returns
        -------
        CodeResult
        """
        import e2b  # type: ignore[import-untyped]

        full_code = self._build_preamble() + "\n" + code
        start = time.monotonic()

        kwargs: dict[str, object] = {}
        if self._api_key:
            kwargs["api_key"] = self._api_key

        try:
            with e2b.Sandbox(**kwargs) as sandbox:
                execution = sandbox.run_code(full_code, timeout=self.timeout)
                elapsed = (time.monotonic() - start) * 1000.0

                stdout = "\n".join(
                    getattr(r, "text", str(r))
                    for r in (getattr(execution, "logs", None) or [])
                    if getattr(r, "source", None) != "stderr"
                ) or ""
                stderr = "\n".join(
                    getattr(r, "text", str(r))
                    for r in (getattr(execution, "logs", None) or [])
                    if getattr(r, "source", None) == "stderr"
                ) or ""
                error_val = getattr(execution, "error", None)
                error_str = str(error_val) if error_val else ""

                return CodeResult(
                    stdout=stdout,
                    stderr=stderr,
                    error=error_str,
                    execution_time_ms=elapsed,
                    timed_out=False,
                )
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000.0
            timed_out = "timeout" in str(exc).lower() or "timed out" in str(exc).lower()
            return CodeResult(
                error=str(exc),
                execution_time_ms=elapsed,
                timed_out=timed_out,
            )


# ──────────────────────────────────────────────────────────────────────────────
# Modal sandbox provider
# ──────────────────────────────────────────────────────────────────────────────

class ModalSandboxProvider:
    """Run Python code inside a `Modal <https://modal.com>`_ ephemeral sandbox.

    Modal sandboxes are launched on-demand as short-lived containers, making
    them suitable for production multi-tenant workloads where network egress
    control and GPU access are important.

    Parameters
    ----------
    timeout:
        Default wall-clock timeout **in seconds** per :meth:`run` call.
    allow_modules:
        Optional module allow-list (same semantics as :class:`E2BSandboxProvider`).
    image:
        Modal ``Image`` object used for the sandbox container.  Defaults to
        ``modal.Image.debian_slim()`` when *None*.

    Raises
    ------
    ImportError
        Raised at *instantiation time* when ``modal`` is not installed.

    Examples
    --------
    >>> provider = ModalSandboxProvider(timeout=60)
    >>> result = provider.run("import platform; print(platform.system())")
    >>> print(result)
    Linux
    """

    def __init__(
        self,
        timeout: int = 30,
        allow_modules: list[str] | None = None,
        image: object = None,
    ) -> None:
        try:
            import modal as _modal  # noqa: F401 — validate presence at construction
        except ImportError as exc:
            raise ImportError(
                "The 'modal' package is required for ModalSandboxProvider.\n"
                "Install it with:  pip install modal\n"
                "Then authenticate:  modal token new"
            ) from exc

        self.timeout = timeout
        self.allow_modules = allow_modules
        self._image = image  # may be None → resolved lazily

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_preamble(self) -> str:
        """Return an import-restriction preamble matching CodeInterpreter's."""
        if self.allow_modules is None:
            return ""
        import json
        allowed_json = json.dumps(self.allow_modules)
        return f"""
import builtins as _bi
_allowed = {allowed_json}
_orig_import = _bi.__import__
def _restricted_import(name, *args, **kwargs):
    top = name.split('.')[0]
    _safe = ('builtins', '__future__', '_thread', '_warnings', 'abc',
             'codecs', 'collections', 'contextlib', 'copy', 'dataclasses',
             'datetime', 'enum', 'functools', 'gc', 'importlib', 'io',
             'itertools', 'operator', 'os', 'pathlib', 're', 'sys',
             'textwrap', 'threading', 'time', 'traceback', 'types',
             'typing', 'warnings', 'weakref')
    if top not in _allowed and top not in _safe:
        raise ImportError(f"Module '{{name}}' is not in the allow-list")
    return _orig_import(name, *args, **kwargs)
_bi.__import__ = _restricted_import
"""

    def _get_image(self) -> object:
        import modal  # type: ignore[import-untyped]
        return self._image if self._image is not None else modal.Image.debian_slim()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, code: str) -> CodeResult:
        """Execute *code* synchronously inside a Modal ephemeral sandbox.

        Creates a new ``modal.Sandbox`` via ``modal.Sandbox.create()`` for
        every invocation, guaranteeing hermetic isolation.

        Parameters
        ----------
        code:
            Arbitrary Python source code to execute.

        Returns
        -------
        CodeResult
        """
        return _run_sync(self.arun(code))

    async def arun(self, code: str) -> CodeResult:
        """Async variant of :meth:`run` — awaitable from an async context.

        Parameters
        ----------
        code:
            Arbitrary Python source code to execute.

        Returns
        -------
        CodeResult
        """
        import modal  # type: ignore[import-untyped]

        full_code = self._build_preamble() + "\n" + code
        start = time.monotonic()

        try:
            app = modal.App.lookup("meshflow-sandbox", create_if_missing=True)
            image = self._get_image()

            # modal.Sandbox.create returns a Sandbox handle
            sandbox = modal.Sandbox.create(
                "python",
                "-c",
                full_code,
                app=app,
                image=image,
                timeout=self.timeout,
            )

            sandbox.wait()
            elapsed = (time.monotonic() - start) * 1000.0

            stdout_handle = sandbox.stdout
            stderr_handle = sandbox.stderr
            stdout = stdout_handle.read() if stdout_handle else ""
            stderr = stderr_handle.read() if stderr_handle else ""
            returncode = getattr(sandbox, "returncode", None)
            error_str = stderr.strip() if (returncode is not None and returncode != 0) else ""

            return CodeResult(
                stdout=stdout or "",
                stderr=stderr or "",
                error=error_str,
                execution_time_ms=elapsed,
                timed_out=False,
            )
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000.0
            timed_out = "timeout" in str(exc).lower() or "timed out" in str(exc).lower()
            return CodeResult(
                error=str(exc),
                execution_time_ms=elapsed,
                timed_out=timed_out,
            )


# ──────────────────────────────────────────────────────────────────────────────
# SandboxRouter — pick backend from env var
# ──────────────────────────────────────────────────────────────────────────────

class SandboxRouter:
    """Route code execution to the appropriate sandbox backend.

    The backend is selected via the ``MESHFLOW_SANDBOX_BACKEND`` environment
    variable.  This lets operators promote workloads from local development to
    cloud staging/production without any code changes.

    +------------------+----------------------------------------------------+
    | Env value        | Provider                                           |
    +==================+====================================================+
    | ``subprocess``   | :class:`~meshflow.tools.code_interpreter.CodeInterpreter` (default) |
    | ``e2b``          | :class:`E2BSandboxProvider`                        |
    | ``modal``        | :class:`ModalSandboxProvider`                      |
    +------------------+----------------------------------------------------+

    All keyword arguments passed to :class:`SandboxRouter` are forwarded to
    the chosen provider's constructor.  Only arguments that the provider
    accepts are forwarded — extras are silently ignored so that the router can
    be constructed with a superset of options without raising ``TypeError``.

    Parameters
    ----------
    backend:
        Override the backend explicitly instead of reading
        ``MESHFLOW_SANDBOX_BACKEND``.  Useful for tests.
    timeout:
        Default timeout in seconds, forwarded to the provider.
    allow_modules:
        Module allow-list forwarded to the provider.
    api_key:
        API key forwarded to :class:`E2BSandboxProvider` when ``backend="e2b"``.
    e2b_api_key:
        Alias for *api_key* — makes intent explicit when configuring E2B.
    modal_image:
        Modal ``Image`` object forwarded to :class:`ModalSandboxProvider`.
    subprocess_timeout_s:
        Timeout forwarded to :class:`~meshflow.tools.code_interpreter.CodeInterpreter`
        as ``timeout_s``.  Overrides *timeout* for the subprocess backend.

    Examples
    --------
    **Auto-select from env** (recommended for all environments)::

        from meshflow.tools.sandbox_providers import SandboxRouter

        router = SandboxRouter(timeout=30)
        result = router.run("print('hello')")
        print(result)

    **Force a specific backend for testing**::

        router = SandboxRouter(backend="subprocess")
        result = router.run("x = 40 + 2; print(x)")

    **Async usage inside an agent**::

        result = await router.arun("import json; print(json.dumps({'ok': True}))")
    """

    def __init__(
        self,
        *,
        backend: str | None = None,
        timeout: int = 30,
        allow_modules: list[str] | None = None,
        api_key: str = "",
        e2b_api_key: str = "",
        modal_image: object = None,
        subprocess_timeout_s: float | None = None,
    ) -> None:
        chosen = (backend or os.environ.get("MESHFLOW_SANDBOX_BACKEND", _SUBPROCESS)).strip().lower()

        if chosen not in _VALID_BACKENDS:
            raise ValueError(
                f"Unknown sandbox backend {chosen!r}. "
                f"Valid values: {', '.join(_VALID_BACKENDS)}"
            )

        self._backend_name = chosen

        if chosen == _E2B:
            effective_key = e2b_api_key or api_key
            self._provider: CodeInterpreter | E2BSandboxProvider | ModalSandboxProvider = (
                E2BSandboxProvider(
                    timeout=timeout,
                    allow_modules=allow_modules,
                    api_key=effective_key,
                )
            )
        elif chosen == _MODAL:
            self._provider = ModalSandboxProvider(
                timeout=timeout,
                allow_modules=allow_modules,
                image=modal_image,
            )
        else:
            # subprocess (default)
            effective_timeout = subprocess_timeout_s if subprocess_timeout_s is not None else float(timeout)
            self._provider = CodeInterpreter(
                timeout_s=effective_timeout,
                allowed_modules=allow_modules,
            )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def backend(self) -> str:
        """Name of the active backend: ``"subprocess"``, ``"e2b"``, or ``"modal"``."""
        return self._backend_name

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, code: str) -> CodeResult:
        """Execute *code* using the configured backend synchronously.

        Parameters
        ----------
        code:
            Arbitrary Python source code to execute.

        Returns
        -------
        CodeResult
        """
        provider = self._provider
        if isinstance(provider, CodeInterpreter):
            return provider.run(code)
        # E2BSandboxProvider or ModalSandboxProvider
        return provider.run(code)

    async def arun(self, code: str) -> CodeResult:
        """Execute *code* using the configured backend asynchronously.

        For :class:`~meshflow.tools.code_interpreter.CodeInterpreter` (subprocess),
        the blocking :meth:`~meshflow.tools.code_interpreter.CodeInterpreter.run`
        is offloaded to a thread pool executor so it does not block the event loop.

        Parameters
        ----------
        code:
            Arbitrary Python source code to execute.

        Returns
        -------
        CodeResult
        """
        provider = self._provider
        if isinstance(provider, CodeInterpreter):
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, provider.run, code)
        # E2BSandboxProvider or ModalSandboxProvider — both have arun
        return await provider.arun(code)

    def __repr__(self) -> str:
        return f"SandboxRouter(backend={self._backend_name!r}, provider={self._provider!r})"
