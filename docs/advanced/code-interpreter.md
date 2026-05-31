# Code Interpreter

`CodeInterpreter` executes Python code in a resource-isolated subprocess — memory-capped, network-blocked, with an optional Docker backend.

## Basic usage

```python
from meshflow import CodeInterpreter, CodeResult

interpreter = CodeInterpreter(
    timeout_s=10.0,
    max_memory_mb=256,    # RLIMIT_AS on Linux/macOS
    block_network=True,   # strip HTTP_PROXY / HTTPS_PROXY from env
    allowed_modules=["math", "json", "re", "datetime"],  # allowlist imports
)

result: CodeResult = interpreter.run("""
import math
print(math.sqrt(144))
""")

print(result.stdout)   # "12.0"
print(result.stderr)   # ""
print(result.success)  # True
print(result.timed_out)  # False
```

## CodeResult fields

| Field | Type | Description |
|-------|------|-------------|
| `stdout` | `str` | Captured standard output |
| `stderr` | `str` | Captured standard error |
| `return_value` | `str` | Last expression value (if any) |
| `error` | `str` | Error message if execution failed |
| `execution_time_ms` | `float` | Wall-clock time |
| `timed_out` | `bool` | True if timeout was hit |
| `success` | `bool` | `not error and not timed_out` |

## Docker backend

```python
interpreter = CodeInterpreter(
    docker=True,
    docker_image="python:3.12-slim",
    timeout_s=30.0,
    max_memory_mb=512,
    block_network=True,
)
result = interpreter.run("import subprocess; subprocess.run(['ls', '/'])")
```

Docker provides full OS-level isolation — recommended for untrusted code.

## Built-in python_repl tool

The `python_repl` tool registered in the global tool registry automatically routes through `CodeInterpreter`:

```python
from meshflow import Agent

agent = Agent(
    name="coder",
    role="executor",
    tools=["python_repl"],  # memory-limited, network-blocked by default
)
result = await agent.run("Compute the Fibonacci sequence up to 100")
```

## Async usage

```python
import asyncio
from meshflow import CodeInterpreter

interpreter = CodeInterpreter(timeout_s=5.0, max_memory_mb=256)

async def run_code(code: str) -> str:
    result = await asyncio.to_thread(interpreter.run, code)
    return str(result)
```
