# Doctor — Pre-Deploy Health Check

`Doctor` validates your environment before deployment and reports actionable issues.

```bash
meshflow doctor
```

```
  MeshFlow Doctor — Environment Report
  ──────────────────────────────────────────────────────────
  ✅ Python 3.11+                  Python 3.12.3
  ✅ ANTHROPIC_API_KEY             set (sk-ant-...)
  ✅ meshflow package              v1.0.0
  ✅ SQLite backend                meshflow_runs.db writable
  ⚠  OTEL endpoint                 not configured (optional)
  ❌ Redis connection              MESHFLOW_REDIS_URL not set
  ✅ Disk space                    14.2 GB available
  ✅ Network                       outbound HTTPS reachable
  ──────────────────────────────────────────────────────────
  2 warnings  1 error
```

## Python API

```python
from meshflow import Doctor, DoctorReport, CheckResult, CheckStatus

doctor = Doctor()
report: DoctorReport = doctor.run()

print(report.passed)   # bool — all required checks passed
print(report.summary)  # one-line summary string

for check in report.checks:
    c: CheckResult = check
    print(c.name, c.status, c.message)
    # c.status: CheckStatus.OK | WARNING | ERROR
```

## Custom checks

```python
from meshflow import Doctor, CheckResult, CheckStatus

def check_my_database(config) -> CheckResult:
    try:
        # ... test connection
        return CheckResult(name="postgres", status=CheckStatus.OK, message="connected")
    except Exception as e:
        return CheckResult(name="postgres", status=CheckStatus.ERROR, message=str(e))

doctor = Doctor(extra_checks=[check_my_database])
report = doctor.run()
```

## CI usage

```bash
# Block deployment if doctor fails
meshflow doctor || exit 1
```
