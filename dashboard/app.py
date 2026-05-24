"""MeshFlow Dashboard — Streamlit UI for /metrics and /traces endpoints.

Launch::

    pip install streamlit
    streamlit run dashboard/app.py -- --server http://localhost:8000 --api-key my-key

Or with environment variables::

    MESHFLOW_SERVER=http://localhost:8000
    MESHFLOW_API_KEY=my-key
    streamlit run dashboard/app.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

import streamlit as st

# ── Config ────────────────────────────────────────────────────────────────────

def _cfg(key: str, default: str = "") -> str:
    """Read from CLI args (--key value) then env then default."""
    for i, arg in enumerate(sys.argv):
        if arg == f"--{key}" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return os.environ.get(f"MESHFLOW_{key.upper().replace('-', '_')}", default)


SERVER   = _cfg("server",  "http://localhost:8000")
API_KEY  = _cfg("api-key", "")
REFRESH  = int(_cfg("refresh", "30"))  # auto-refresh seconds


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if API_KEY:
        h["Authorization"] = f"Bearer {API_KEY}"
    return h


# ── HTTP helpers ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=REFRESH)
def fetch_health() -> dict[str, Any]:
    import urllib.request
    try:
        with urllib.request.urlopen(f"{SERVER}/health", timeout=5) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"ok": False, "error": str(e)}


@st.cache_data(ttl=REFRESH)
def fetch_metrics() -> str:
    import urllib.request
    req = urllib.request.Request(f"{SERVER}/metrics", headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.read().decode()
    except Exception as e:
        return f"# error: {e}"


@st.cache_data(ttl=REFRESH)
def fetch_runs() -> list[str]:
    import urllib.request
    req = urllib.request.Request(f"{SERVER}/traces", headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read()).get("runs", [])
    except Exception:
        return []


@st.cache_data(ttl=REFRESH)
def fetch_trace(run_id: str) -> dict[str, Any] | None:
    import urllib.request
    req = urllib.request.Request(f"{SERVER}/traces/{run_id}", headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())
    except Exception:
        return None


@st.cache_data(ttl=REFRESH)
def fetch_pending_hitl() -> list[dict[str, Any]]:
    import urllib.request
    req = urllib.request.Request(f"{SERVER}/hitl/pending", headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read()).get("paused_runs", [])
    except Exception:
        return []


@st.cache_data(ttl=REFRESH)
def fetch_compliance_report(framework: str, run_id: str = "") -> dict[str, Any]:
    import urllib.request
    url = f"{SERVER}/compliance/report?framework={framework}"
    if run_id:
        url += f"&run_id={run_id}"
    req = urllib.request.Request(url, headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}


@st.cache_data(ttl=REFRESH)
def fetch_webhooks() -> dict[str, Any]:
    import urllib.request
    req = urllib.request.Request(f"{SERVER}/webhooks", headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"webhooks": [], "stats": {}, "error": str(e)}


def post_webhook(url_target: str, events: list[str], secret: str) -> dict[str, Any]:
    import urllib.request
    body = json.dumps({"url": url_target, "events": events, "secret": secret}).encode()
    req = urllib.request.Request(
        f"{SERVER}/webhooks",
        data=body,
        headers={**_headers(), "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}


def delete_webhook(webhook_id: str) -> bool:
    import urllib.request
    req = urllib.request.Request(
        f"{SERVER}/webhooks/{webhook_id}",
        headers=_headers(),
        method="DELETE",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception:
        return False


@st.cache_data(ttl=REFRESH)
def fetch_eval_results(suite: str = "") -> list[dict[str, Any]]:
    import urllib.request
    url = f"{SERVER}/eval-results"
    if suite:
        url += f"?suite={suite}"
    req = urllib.request.Request(url, headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read()).get("eval_results", [])
    except Exception:
        return []


@st.cache_data(ttl=REFRESH)
def fetch_plugins(group: str = "") -> list[dict[str, Any]]:
    import urllib.request
    url = f"{SERVER}/plugins"
    if group:
        url += f"?group={group}"
    req = urllib.request.Request(url, headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read()).get("plugins", [])
    except Exception:
        return []


def post_hitl(run_id: str, action: str, reviewer: str, notes: str) -> bool:
    import urllib.request
    body = json.dumps({"reviewer_id": reviewer, "notes": notes}).encode()
    req = urllib.request.Request(
        f"{SERVER}/hitl/{run_id}/{action}",
        data=body,
        headers={**_headers(), "Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception:
        return False


@st.cache_data(ttl=REFRESH)
def fetch_whoami() -> dict[str, Any]:
    import urllib.request
    req = urllib.request.Request(f"{SERVER}/keys/whoami", headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())
    except Exception:
        return {}


@st.cache_data(ttl=REFRESH)
def fetch_api_keys(tenant: str = "") -> dict[str, Any]:
    import urllib.request
    url = f"{SERVER}/keys"
    if tenant:
        url += f"?tenant={tenant}"
    req = urllib.request.Request(url, headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"keys": [], "count": 0, "error": str(e)}


def create_api_key(name: str, role: str, tenant_id: str) -> dict[str, Any]:
    import urllib.request
    body = json.dumps({"name": name, "role": role, "tenant_id": tenant_id}).encode()
    req = urllib.request.Request(
        f"{SERVER}/keys",
        data=body,
        headers={**_headers(), "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}


def revoke_api_key(key_id: str) -> dict[str, Any]:
    import urllib.request
    req = urllib.request.Request(
        f"{SERVER}/keys/{key_id}",
        headers=_headers(),
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}


# ── Metrics parser ────────────────────────────────────────────────────────────

def parse_prometheus(text: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.rsplit(" ", 1)
        if len(parts) == 2:
            try:
                values[parts[0]] = float(parts[1])
            except ValueError:
                pass
    return values


# ── Layout ────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="MeshFlow Dashboard",
    page_icon="⬡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Sidebar
with st.sidebar:
    st.title("⬡ MeshFlow")
    st.caption(f"Server: `{SERVER}`")
    health = fetch_health()
    if health.get("ok"):
        st.success(f"Online  v{health.get('version', '?')}  uptime {health.get('uptime_s', 0):.0f}s")
    else:
        st.error(f"Offline — {health.get('error', 'unreachable')}")

    page = st.radio(
        "Navigate",
        ["Overview", "Runs", "HITL Queue", "Metrics", "Submit Task", "Live", "Pool",
         "Evals", "Plugins", "Graph", "Audit", "SLA", "OTEL",
         "Compliance", "Alerts", "API Keys"],
        label_visibility="collapsed",
    )
    st.divider()
    # Tenant / identity indicator
    whoami = fetch_whoami()
    if whoami:
        name = whoami.get("name", "")
        role = whoami.get("role", "")
        tenant = whoami.get("tenant_id", "") or "global"
        st.caption(f"**{name}** `{role}`")
        st.caption(f"Tenant: `{tenant}`")
        st.divider()
    if st.button("Refresh now"):
        st.cache_data.clear()
        st.rerun()
    st.caption(f"Auto-refresh every {REFRESH}s")


# ── Overview ──────────────────────────────────────────────────────────────────

if page == "Overview":
    st.header("Overview")

    metrics_text = fetch_metrics()
    mv = parse_prometheus(metrics_text)

    runs = fetch_runs()
    pending = fetch_pending_hitl()

    eval_results = fetch_eval_results()
    plugins = fetch_plugins()

    col1, col2, col3, col4, col5, col6 = st.columns(6)
    col1.metric("Total Runs", len(runs))
    col2.metric(
        "Total Cost",
        f"${mv.get('meshflow_total_cost_usd', 0.0):.4f}",
    )
    col3.metric(
        "Blocked Steps",
        int(mv.get("meshflow_blocked_steps_total", 0)),
    )
    col4.metric("Pending HITL", len(pending))
    if eval_results:
        last_pass = eval_results[-1].get("pass_rate", 0.0)
        col5.metric("Last Eval Pass Rate", f"{last_pass:.1%}")
    else:
        col5.metric("Last Eval Pass Rate", "—")
    col6.metric("Installed Plugins", len(plugins))

    st.divider()

    col_l, col_r = st.columns(2)

    with col_l:
        st.subheader("Recent runs")
        if runs:
            st.dataframe(
                [{"run_id": r} for r in reversed(runs[-20:])],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No runs yet.")

    with col_r:
        st.subheader("Key metrics")
        kv_pairs = [
            ("Runs completed", int(mv.get("meshflow_runs_total{status=\"completed\"}", 0))),
            ("Runs failed",    int(mv.get("meshflow_runs_total{status=\"failed\"}", 0))),
            ("Runs aborted",   int(mv.get("meshflow_runs_total{status=\"aborted\"}", 0))),
            ("Total tokens",   int(mv.get("meshflow_total_tokens", 0))),
            ("Total carbon g", f"{mv.get('meshflow_total_carbon_g', 0.0):.4f}"),
            ("Collusion alerts", int(mv.get("meshflow_collusion_alerts_total", 0))),
        ]
        for label, val in kv_pairs:
            st.metric(label, val)


# ── Runs ──────────────────────────────────────────────────────────────────────

elif page == "Runs":
    st.header("Run Traces")

    runs = fetch_runs()
    if not runs:
        st.info("No runs in ledger.")
        st.stop()

    selected = st.selectbox("Select run", list(reversed(runs)))
    if not selected:
        st.stop()

    trace = fetch_trace(selected)
    if trace is None:
        st.error(f"Could not fetch trace for {selected}")
        st.stop()

    summary = trace.get("summary", {})
    ts = summary.get("timestamps", {})

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Steps", summary.get("steps", 0))
    col2.metric("Cost", f"${summary.get('total_cost_usd', 0.0):.4f}")
    col3.metric("Tokens", summary.get("total_tokens", 0))
    col4.metric("Blocked", summary.get("blocked_steps", 0))

    st.caption(f"Start: {ts.get('start', '—')}  →  End: {ts.get('end', '—')}")
    st.caption(f"Nodes: {', '.join(summary.get('nodes', []))}")

    verdicts = summary.get("verdicts", [])
    if verdicts:
        verdict_counts: dict[str, int] = {}
        for v in verdicts:
            verdict_counts[v] = verdict_counts.get(v, 0) + 1
        st.bar_chart(verdict_counts)

    st.divider()
    st.subheader("Step records")

    steps = trace.get("steps", [])
    if steps:
        rows = []
        for s in steps:
            rows.append({
                "step_id":    s.get("step_id", "")[:12],
                "node":       s.get("node_id", ""),
                "verdict":    s.get("verdict", ""),
                "blocked":    "🔴" if s.get("blocked") else "✅",
                "uncertainty":f"{s.get('uncertainty', 0.0):.2f}",
                "cost_usd":   f"${s.get('cost_usd', 0.0):.4f}",
                "tokens":     s.get("tokens_used", 0),
                "carbon_g":   f"{s.get('carbon_gco2', 0.0):.5f}",
                "timestamp":  s.get("timestamp", "")[:19],
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("No step records.")

    with st.expander("Chain hashes"):
        for s in steps:
            st.code(
                f"{s.get('step_id', '')[:8]}  prev={s.get('prev_hash', '')[:16]}  "
                f"hash={s.get('entry_hash', '')[:16]}",
                language=None,
            )

    with st.expander("Raw JSON"):
        st.json(trace)


# ── HITL Queue ────────────────────────────────────────────────────────────────

elif page == "HITL Queue":
    st.header("Human-in-the-Loop Queue")

    pending = fetch_pending_hitl()
    if not pending:
        st.success("No runs pending human review.")
        st.stop()

    reviewer = st.text_input("Reviewer ID", placeholder="alice")

    for item in pending:
        run_id = item.get("run_id", "")
        paused_at = item.get("paused_at", "")
        with st.expander(f"Run `{run_id[:20]}…`  paused at {paused_at[:19]}"):
            trace = fetch_trace(run_id)
            if trace:
                last_step = trace.get("steps", [{}])[-1]
                st.markdown(f"**Last output:** {last_step.get('output_content', '')[:500]}")

            notes = st.text_area("Review notes", key=f"notes_{run_id}")
            col_a, col_r = st.columns(2)
            if col_a.button("✅ Approve", key=f"approve_{run_id}"):
                if post_hitl(run_id, "approve", reviewer, notes):
                    st.success("Approved — run will resume.")
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.error("Failed to approve.")
            if col_r.button("❌ Reject", key=f"reject_{run_id}"):
                if post_hitl(run_id, "reject", reviewer, notes):
                    st.warning("Rejected.")
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.error("Failed to reject.")


# ── Metrics ───────────────────────────────────────────────────────────────────

elif page == "Metrics":
    st.header("Prometheus Metrics")

    metrics_text = fetch_metrics()
    mv = parse_prometheus(metrics_text)

    st.subheader("Gauges and counters")
    if mv:
        import pandas as pd
        df = pd.DataFrame(
            [{"metric": k, "value": v} for k, v in sorted(mv.items())],
        )
        st.dataframe(df, use_container_width=True, hide_index=True)

        cost_val = mv.get("meshflow_total_cost_usd", 0.0)
        tokens_val = mv.get("meshflow_total_tokens", 0)
        carbon_val = mv.get("meshflow_total_carbon_g", 0.0)
        st.divider()
        c1, c2, c3 = st.columns(3)
        c1.metric("Total cost USD", f"${cost_val:.4f}")
        c2.metric("Total tokens", f"{int(tokens_val):,}")
        c3.metric("Total carbon g", f"{carbon_val:.4f}")
    else:
        st.info("No metric values parsed.")

    with st.expander("Raw Prometheus text"):
        st.code(metrics_text, language=None)


# ── Submit Task ───────────────────────────────────────────────────────────────

elif page == "Submit Task":
    st.header("Submit a Task")
    st.caption("Sends a POST /run to the server and shows the result.")

    with st.form("submit_form"):
        task = st.text_area("Task", placeholder="Summarise the quarterly report…", height=120)
        col_mode, col_budget, col_tokens = st.columns(3)
        mode   = col_mode.selectbox("Policy mode", ["dev", "standard", "regulated", "legal-critical", "hipaa"])
        budget = col_budget.number_input("Budget USD", min_value=0.0, value=0.5, step=0.1)
        tokens = col_tokens.number_input("Max tokens", min_value=0, value=4096, step=512)
        submitted = st.form_submit_button("Run")

    if submitted and task.strip():
        import urllib.request
        body = json.dumps({
            "task": task,
            "policy": {"mode": mode, "budget_usd": budget, "budget_tokens": tokens},
        }).encode()
        req = urllib.request.Request(
            f"{SERVER}/run",
            data=body,
            headers={**_headers(), "Content-Type": "application/json"},
            method="POST",
        )
        with st.spinner("Running…"):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    result = json.loads(r.read())
                status = result.get("status", "?")
                if status == "completed":
                    st.success(f"Completed in {result.get('duration_s', 0):.1f}s")
                elif status == "failed":
                    st.error(f"Failed: {result.get('error', '?')}")
                else:
                    st.warning(f"Status: {status}")

                col1, col2, col3 = st.columns(3)
                col1.metric("Cost", f"${result.get('total_cost_usd', 0.0):.4f}")
                col2.metric("Tokens", result.get("total_tokens", 0))
                col3.metric("Ledger entries", result.get("ledger_entries", 0))

                st.markdown("**Run ID:** `" + result.get("run_id", "") + "`")

                with st.expander("Full result"):
                    st.json(result)

                st.cache_data.clear()
            except Exception as e:
                st.error(f"Request failed: {e}")
    elif submitted:
        st.warning("Enter a task first.")

# ── Live SSE stream ───────────────────────────────────────────────────────────

elif page == "Live":
    st.header("Live Event Stream")
    st.caption(
        "Streams workflow lifecycle events from the server via SSE. "
        "Events are replayed from server start then stream live."
    )

    col_filter, col_max = st.columns([3, 1])
    run_id_filter = col_filter.text_input("Filter by run_id (optional)", placeholder="leave blank for all runs")
    max_events = col_max.number_input("Max events shown", min_value=10, max_value=500, value=50, step=10)

    placeholder = st.empty()

    if st.button("Start streaming"):
        import urllib.request

        url = f"{SERVER}/events"
        if run_id_filter.strip():
            url += f"?run_id={run_id_filter.strip()}"

        req = urllib.request.Request(url, headers=_headers())

        events: list[dict[str, Any]] = []
        status_area = st.empty()

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                status_area.info("Connected — waiting for events…")
                for raw_line in resp:
                    line: str = raw_line.decode("utf-8").strip()
                    if not line.startswith("data:"):
                        continue
                    try:
                        payload = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    if "ok" in payload:
                        continue  # skip the connected handshake event

                    events.insert(0, payload)
                    if len(events) > max_events:
                        events = events[:max_events]

                    rows = [
                        {
                            "kind": e.get("kind", ""),
                            "run_id": str(e.get("run_id", ""))[:20],
                            "node_id": e.get("node_id", ""),
                            "time": str(round(e.get("timestamp", 0), 1)),
                        }
                        for e in events
                    ]
                    placeholder.dataframe(rows, use_container_width=True, hide_index=True)

        except Exception as exc:
            st.error(f"Stream error: {exc}")
    else:
        st.info("Click **Start streaming** to connect to the SSE feed.")


# ── Agent Pool ────────────────────────────────────────────────────────────────

elif page == "Pool":
    st.header("Agent Pool Status")
    st.caption("Live view of registered AgentPool instances via GET /pool/status.")

    import urllib.request as _ureq

    def _fetch_pool_status() -> list[dict[str, Any]]:
        req = urllib.request.Request(f"{SERVER}/pool/status", headers=_headers())
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read()).get("pools", [])
        except Exception as e:
            return []

    pools = _fetch_pool_status()

    if st.button("Refresh"):
        pools = _fetch_pool_status()

    if not pools:
        st.info("No AgentPool instances registered with the server yet.")
        st.caption(
            "Register a pool in your code: `from meshflow.agents.pool import register_pool; "
            "register_pool(pool)`"
        )
    else:
        for pool in pools:
            with st.expander(f"Pool: **{pool.get('pool_name', '?')}**", expanded=True):
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Active workers", pool.get("active_workers", 0))
                c2.metric("Queued", pool.get("queued", 0))
                c3.metric("Completed", pool.get("total_completed", 0))
                c4.metric("Failed", pool.get("total_failed", 0))

                c5, c6, c7, c8 = st.columns(4)
                c5.metric("Agents", pool.get("agent_count", 0))
                c6.metric("Concurrency", pool.get("concurrency", 0))
                c7.metric("Cost", f"${pool.get('total_cost_usd', 0.0):.4f}")
                c8.metric("Tokens", f"{pool.get('total_tokens', 0):,}")

                st.caption(f"Uptime: {pool.get('uptime_s', 0):.1f}s  |  Submitted: {pool.get('total_submitted', 0)}")


# ── Evals ─────────────────────────────────────────────────────────────────────

elif page == "Evals":
    st.header("Eval Results")
    st.caption("Stored eval baselines from the ledger (saved with `--save-to-ledger`).")

    suite_filter = st.text_input("Filter by suite name", placeholder="leave blank for all suites")

    if st.button("Refresh", key="evals_refresh"):
        st.cache_data.clear()

    results = fetch_eval_results(suite=suite_filter.strip())

    if not results:
        st.info("No eval results stored in the ledger yet.")
        st.caption("Run `meshflow eval my_suite.yaml --save-to-ledger` to store results.")
        st.stop()

    rows = []
    for r in results:
        rows.append({
            "suite":     r.get("suite_name", "?"),
            "pass_rate": f"{r.get('pass_rate', 0.0):.1%}",
            "score":     f"{r.get('weighted_score', r.get('score', 0.0)):.3f}",
            "scenarios": r.get("total_scenarios", len(r.get("scenarios", []))),
            "timestamp": r.get("timestamp", "")[:19],
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Compare two results")
    st.caption("Select two stored results to compute a diff report.")

    indices = list(range(len(results)))
    labels = [
        f"{r.get('suite_name', '?')}  {r.get('timestamp', '')[:19]}  ({r.get('pass_rate', 0.0):.1%})"
        for r in results
    ]

    if len(results) >= 2:
        col_a, col_b = st.columns(2)
        idx_a = col_a.selectbox("Baseline (older)", indices, format_func=lambda i: labels[i], index=0)
        idx_b = col_b.selectbox("Newer result",     indices, format_func=lambda i: labels[i], index=len(results) - 1)

        if st.button("Compute diff"):
            import os
            import tempfile
            from meshflow.eval import EvalBaseline

            ra, rb = results[idx_a], results[idx_b]
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fa:
                json.dump(ra, fa)
                path_a = fa.name
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fb:
                json.dump(rb, fb)
                path_b = fb.name

            try:
                old = EvalBaseline.load(path_a)
                new = EvalBaseline.load(path_b)
                diff = old.diff(new)
                report = diff.report(verbose=True)
                if diff.has_regressions:
                    st.error("Regressions detected")
                else:
                    st.success("No regressions")
                st.code(report, language=None)
            except Exception as exc:
                st.error(f"Diff failed: {exc}")
            finally:
                for p in (path_a, path_b):
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
    else:
        st.info("Need at least two stored results to compute a diff.")


# ── Plugins ───────────────────────────────────────────────────────────────────

elif page == "Plugins":
    st.header("Installed Plugins")
    st.caption("MeshFlow extension plugins discovered via `importlib.metadata` entry-points.")

    group_options = ["all", "agent", "tool", "compliance", "ledger"]
    group_sel = st.selectbox("Filter by group", group_options)

    if st.button("Refresh", key="plugins_refresh"):
        st.cache_data.clear()

    group_arg = "" if group_sel == "all" else group_sel
    plugins = fetch_plugins(group=group_arg)

    if not plugins:
        st.info("No MeshFlow plugins installed.")
        st.caption(
            "A plugin package must declare entry_points in `meshflow.agents`, "
            "`meshflow.tools`, `meshflow.compliance`, or `meshflow.ledger`."
        )
        st.stop()

    rows = []
    for p in plugins:
        rows.append({
            "name":        p.get("name", "?"),
            "group":       p.get("group", "?"),
            "version":     p.get("version", "?"),
            "package":     p.get("dist_name", "?"),
            "description": (p.get("description") or "")[:60],
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Load verify")
    st.caption("Select a plugin to attempt loading it and report any import errors.")

    plugin_names = [p.get("name", "?") for p in plugins]
    sel_name = st.selectbox("Plugin", plugin_names)
    sel_plugin = next((p for p in plugins if p.get("name") == sel_name), None)

    if sel_plugin and st.button("Verify"):
        from meshflow.plugins import verify_plugin
        ok, msg = verify_plugin(sel_plugin.get("name", ""), sel_plugin.get("ep_group", "meshflow.agents"))
        if ok:
            st.success(f"OK — {msg}")
        else:
            st.error(f"FAIL — {msg}")


# ── Graph export ──────────────────────────────────────────────────────────────

elif page == "Graph":
    st.header("Workflow Graph")
    st.caption("Visualise the execution path of any stored run as a Mermaid flowchart.")

    runs = fetch_runs()
    if not runs:
        st.info("No runs in ledger yet.")
        st.stop()

    col_sel, col_fmt = st.columns([3, 1])
    selected_run = col_sel.selectbox("Run", list(reversed(runs)))
    fmt = col_fmt.selectbox("Format", ["mermaid", "dot"])

    if st.button("Load graph"):
        import urllib.request as _ureq
        url = f"{SERVER}/graph/{selected_run}?format={fmt}"
        req = _ureq.Request(url, headers=_headers())
        try:
            with _ureq.urlopen(req, timeout=10) as r:
                content = r.read().decode()
            if fmt == "mermaid":
                st.code(content, language="mermaid")
            else:
                st.code(content, language=None)
        except Exception as e:
            st.error(f"Failed: {e}")

    st.caption("Copy the Mermaid code and paste it into mermaid.live to render.")


# ── Audit export ───────────────────────────────────────────────────────────────

elif page == "Audit":
    st.header("Audit Trail Export")
    st.caption(
        "Download the tamper-evident audit log for any run as CSV or JSON "
        "(SOX / HIPAA compliance artifact)."
    )

    runs = fetch_runs()

    col_rid, col_fmt = st.columns([3, 1])
    run_id_sel = col_rid.selectbox(
        "Run", ["— all runs —"] + list(reversed(runs))
    )
    audit_fmt = col_fmt.selectbox("Format", ["json", "csv"])

    if st.button("Download"):
        import urllib.request as _ureq
        rid_param = "" if run_id_sel.startswith("—") else f"&run_id={run_id_sel}"
        url = f"{SERVER}/audit/export?format={audit_fmt}{rid_param}"
        req = _ureq.Request(url, headers=_headers())
        try:
            with _ureq.urlopen(req, timeout=15) as r:
                content = r.read().decode()
            if audit_fmt == "csv":
                st.download_button(
                    "Save CSV",
                    data=content,
                    file_name=f"audit_{run_id_sel[:12]}.csv",
                    mime="text/csv",
                )
            else:
                st.download_button(
                    "Save JSON",
                    data=content,
                    file_name=f"audit_{run_id_sel[:12]}.json",
                    mime="application/json",
                )
            st.code(content[:2000] + ("…" if len(content) > 2000 else ""), language=audit_fmt)
        except Exception as e:
            st.error(f"Failed: {e}")


# ── SLA monitoring ─────────────────────────────────────────────────────────────

elif page == "SLA":
    st.header("SLA Monitoring")
    st.caption("p50 / p95 / p99 step latency recorded by the global NodeLatencyTracker.")

    if st.button("Refresh", key="sla_refresh"):
        pass  # falls through to fetch below

    import urllib.request as _ureq

    def _fetch_sla() -> list[dict[str, Any]]:
        req = _ureq.Request(f"{SERVER}/sla", headers=_headers())
        try:
            with _ureq.urlopen(req, timeout=5) as r:
                payload = json.loads(r.read()).get("sla", [])
                return payload if isinstance(payload, list) else [payload]
        except Exception:
            return []

    sla_data = _fetch_sla()

    if not sla_data:
        st.info("No latency data yet. Run some tasks first.")
        st.caption(
            "The SLA tracker is populated automatically when `StepRuntime` "
            "executes any node."
        )
    else:
        rows = [
            {
                "node":    d.get("node_id", "?"),
                "count":   d.get("count", 0),
                "p50 ms":  d.get("p50_ms", 0.0),
                "p95 ms":  d.get("p95_ms", 0.0),
                "p99 ms":  d.get("p99_ms", 0.0),
                "min ms":  d.get("min_ms", 0.0),
                "max ms":  d.get("max_ms", 0.0),
                "mean ms": d.get("mean_ms", 0.0),
            }
            for d in sla_data
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)

        # Bar chart of p95 per node
        if len(sla_data) > 1:
            chart_dict = {d.get("node_id", "?"): d.get("p95_ms", 0.0) for d in sla_data}
            st.bar_chart(chart_dict, x_label="Node", y_label="p95 ms")

    st.divider()
    st.subheader("Rate limiter")
    st.caption("Token-bucket status per API key (capacity and remaining tokens).")

    def _fetch_rl() -> list[dict[str, Any]]:
        req = _ureq.Request(f"{SERVER}/rate-limit/status", headers=_headers())
        try:
            with _ureq.urlopen(req, timeout=5) as r:
                return json.loads(r.read()).get("buckets", [])
        except Exception:
            return []

    buckets = _fetch_rl()
    if buckets:
        st.dataframe(buckets, use_container_width=True, hide_index=True)
    else:
        st.info("No rate-limiter buckets initialised yet.")


# ── OTEL configuration ─────────────────────────────────────────────────────────

elif page == "OTEL":
    st.header("OpenTelemetry Configuration")
    st.caption(
        "Live OTELExporter stats and distributed-tracing setup. "
        "Set `MESHFLOW_OTLP_ENDPOINT` to export spans to Jaeger / Grafana Tempo / Datadog / Honeycomb."
    )

    import urllib.request as _ureq

    def _fetch_otel_config() -> dict[str, Any]:
        req = _ureq.Request(f"{SERVER}/otel/config", headers=_headers())
        try:
            with _ureq.urlopen(req, timeout=5) as r:
                return json.loads(r.read())
        except Exception as exc:
            return {"error": str(exc)}

    if st.button("Refresh", key="otel_refresh"):
        st.cache_data.clear()

    cfg = _fetch_otel_config()

    if "error" in cfg:
        st.error(f"Could not reach server: {cfg['error']}")
    else:
        # ── Live exporter stats ──────────────────────────────────────────────
        st.subheader("Live export stats")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("OTLP enabled", "Yes" if cfg.get("otlp_enabled") else "No")
        c2.metric("W3C traceparent", "Yes" if cfg.get("w3c_traceparent") else "No")
        c3.metric("Spans exported", cfg.get("exported_count", 0))
        c4.metric("Export errors", cfg.get("error_count", 0))

        if cfg.get("otlp_enabled"):
            st.success(
                f"Exporting to: `{cfg.get('otlp_endpoint', '?')}`  "
                f"service=`{cfg.get('service_name', '?')}`"
            )
        else:
            st.warning(
                "OTLP disabled — spans are recorded in-process only.  "
                "Set `MESHFLOW_OTLP_ENDPOINT` to enable export."
            )

        if cfg.get("error_count", 0) > 0:
            st.error(
                f"{cfg['error_count']} span export error(s) — check your OTLP endpoint "
                "and that the collector is reachable from the MeshFlow server."
            )

        with st.expander("Environment variables"):
            env_vars = cfg.get("env_vars", {})
            for k, v in (env_vars or {}).items():
                st.markdown(f"`{k}` — {v}")

    st.divider()
    st.subheader("Trace Context propagation")
    st.markdown(
        "MeshFlow injects W3C `traceparent` into every `StepRuntime.run()` context dict.  \n"
        "Pass `context={'_traceparent': '00-<trace_id>-<span_id>-01'}` to link an "
        "incoming request trace to the MeshFlow execution tree."
    )


# ── Compliance reporting ───────────────────────────────────────────────────────

elif page == "Compliance":
    st.header("Compliance Reports")
    st.caption(
        "Generate tamper-evident compliance artifacts for HIPAA, SOX, GDPR, PCI, and NERC CIP. "
        "Reports are derived from the ledger audit trail in real time."
    )

    runs = fetch_runs()
    framework_options = ["hipaa", "sox", "gdpr", "pci", "nerc"]

    col_fw, col_run, col_fmt = st.columns([2, 3, 1])
    framework_sel = col_fw.selectbox("Framework", framework_options)
    run_options = ["— all runs (last 50) —"] + list(reversed(runs))
    run_sel = col_run.selectbox("Scope", run_options)
    report_fmt = col_fmt.selectbox("Format", ["JSON", "Text"])

    if st.button("Generate report"):
        st.cache_data.clear()
        run_id_param = "" if run_sel.startswith("—") else run_sel
        with st.spinner("Generating compliance report…"):
            report = fetch_compliance_report(framework_sel, run_id=run_id_param)

        if "error" in report and not report.get("summary"):
            st.error(f"Error: {report['error']}")
        else:
            summary = report.get("summary", {})
            overall = summary.get("overall_status", "unknown").upper()
            if overall == "COMPLIANT":
                st.success(f"Overall status: {overall}")
            elif overall == "PARTIAL":
                st.warning(f"Overall status: {overall}")
            else:
                st.error(f"Overall status: {overall}")

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Controls", summary.get("total", 0))
            c2.metric("Passed", summary.get("passed", 0))
            c3.metric("Failed", summary.get("failed", 0))
            c4.metric("Warnings", summary.get("warnings", 0))

            st.caption(
                f"Framework: {report.get('framework_version', '')}  |  "
                f"Generated: {report.get('generated_at', '')[:19]}  |  "
                f"Steps audited: {report.get('total_steps', 0)}"
            )

            st.divider()
            st.subheader("Findings")
            findings = report.get("findings", [])
            if findings:
                status_icon = {"pass": "✅", "fail": "❌", "warning": "⚠️", "na": "➖"}
                rows = [
                    {
                        "status": status_icon.get(f.get("status", ""), "?"),
                        "control_id": f.get("control_id", ""),
                        "category": f.get("category", ""),
                        "detail": f.get("detail", "")[:120],
                    }
                    for f in findings
                ]
                st.dataframe(rows, use_container_width=True, hide_index=True)

            st.divider()
            if report_fmt == "Text":
                # Re-fetch as text
                import urllib.request as _ureq
                url = f"{SERVER}/compliance/report?framework={framework_sel}&format=text"
                if run_id_param:
                    url += f"&run_id={run_id_param}"
                try:
                    req = _ureq.Request(url, headers=_headers())
                    with _ureq.urlopen(req, timeout=15) as r:
                        text_report = r.read().decode()
                    st.download_button(
                        "Download text report",
                        data=text_report,
                        file_name=f"compliance_{framework_sel}.txt",
                        mime="text/plain",
                    )
                    st.code(text_report, language=None)
                except Exception as exc:
                    st.error(f"Failed to fetch text report: {exc}")
            else:
                report_json = json.dumps(report, indent=2)
                st.download_button(
                    "Download JSON report",
                    data=report_json,
                    file_name=f"compliance_{framework_sel}.json",
                    mime="application/json",
                )
                with st.expander("Raw JSON"):
                    st.json(report)
    else:
        st.info("Select a framework and scope, then click **Generate report**.")


# ── Webhook alerts ─────────────────────────────────────────────────────────────

elif page == "Alerts":
    st.header("Webhook Alerts")
    st.caption(
        "Register HTTP webhooks to receive real-time notifications for policy violations, "
        "budget overruns, HITL events, and run failures.  "
        "Payloads are HMAC-SHA256 signed with your webhook secret."
    )

    if st.button("Refresh", key="alerts_refresh"):
        st.cache_data.clear()

    data = fetch_webhooks()
    if "error" in data:
        st.error(f"Could not reach server: {data['error']}")
    else:
        stats = data.get("stats", {})
        c1, c2, c3 = st.columns(3)
        c1.metric("Registered webhooks", stats.get("registered", 0))
        c2.metric("Total deliveries", stats.get("total_deliveries", 0))
        c3.metric("Total failures", stats.get("total_failures", 0))

        st.divider()
        st.subheader("Registered webhooks")
        hooks = data.get("webhooks", [])
        if hooks:
            rows = [
                {
                    "id": h.get("id", "")[:12] + "…",
                    "url": h.get("url", "")[:60],
                    "events": ", ".join(h.get("events", [])),
                    "deliveries": h.get("delivery_count", 0),
                    "failures": h.get("failure_count", 0),
                    "last_delivery": (h.get("last_delivery_at") or "—")[:19],
                    "last_error": (h.get("last_error") or "")[:40],
                }
                for h in hooks
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)

            st.subheader("Remove webhook")
            hook_ids = [h.get("id", "") for h in hooks]
            hook_labels = [f"{h.get('id', '')[:12]}… — {h.get('url', '')[:40]}" for h in hooks]
            sel_idx = st.selectbox("Select webhook to remove", range(len(hooks)), format_func=lambda i: hook_labels[i])
            if st.button("Remove selected webhook", type="secondary"):
                if delete_webhook(hook_ids[sel_idx]):
                    st.success("Webhook removed.")
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.error("Failed to remove webhook.")
        else:
            st.info("No webhooks registered yet.")

    st.divider()
    st.subheader("Register new webhook")

    _valid_events = [
        "policy_violation", "budget_exceeded", "hitl_pending",
        "run_failed", "run_completed", "collusion_alert", "*",
    ]

    with st.form("register_webhook_form"):
        hook_url = st.text_input("Target URL", placeholder="https://hooks.example.com/meshflow")
        sel_events = st.multiselect(
            "Events to subscribe to",
            _valid_events,
            default=["*"],
        )
        hook_secret = st.text_input("Signing secret (optional)", type="password", placeholder="leave blank to use server default")
        submitted = st.form_submit_button("Register")

    if submitted:
        if not hook_url.strip():
            st.warning("Enter a URL first.")
        elif not sel_events:
            st.warning("Select at least one event type.")
        else:
            result = post_webhook(hook_url.strip(), sel_events, hook_secret.strip())
            if "error" in result:
                st.error(f"Registration failed: {result['error']}")
            else:
                st.success(f"Webhook registered — ID: `{result.get('id', '?')}`")
                st.caption("The signing secret is not stored server-side in plaintext; save it now.")
                st.cache_data.clear()
                st.rerun()


# ── API Key management ─────────────────────────────────────────────────────────

elif page == "API Keys":
    st.header("API Key Management")
    st.caption(
        "Create, list, and revoke API keys. Requires **admin** role. "
        "The raw key value is shown exactly once on creation — store it immediately."
    )

    tenant_filter = st.text_input("Filter by tenant (leave blank for all)", placeholder="acme-corp")

    col_r, col_g = st.columns([1, 1])
    if col_r.button("Refresh keys"):
        st.cache_data.clear()

    data = fetch_api_keys(tenant=tenant_filter.strip())

    if "error" in data and data.get("keys", []) == []:
        st.error(f"Could not fetch keys: {data['error']}")
        st.caption("Only admin-role keys can list API keys. Check your API key configuration.")
    else:
        keys = data.get("keys", [])
        st.metric("Active keys", data.get("count", len(keys)))

        if keys:
            rows = [
                {
                    "key_id": k.get("key_id", "")[:12] + "…",
                    "name": k.get("name", ""),
                    "role": k.get("role", ""),
                    "tenant": k.get("tenant_id", "") or "global",
                    "created": (k.get("created_at") or "")[:19],
                    "last_used": (k.get("last_used_at") or "—")[:19],
                }
                for k in keys
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)

            st.subheader("Revoke a key")
            key_labels = [
                f"{k.get('key_id', '')[:12]}… — {k.get('name', '')} ({k.get('role', '')})"
                for k in keys
            ]
            sel_idx = st.selectbox("Key to revoke", range(len(keys)), format_func=lambda i: key_labels[i])
            if st.button("Revoke selected key", type="secondary"):
                kid = keys[sel_idx].get("key_id", "")
                result = revoke_api_key(kid)
                if "error" in result:
                    st.error(f"Revoke failed: {result['error']}")
                else:
                    st.success(f"Key `{kid[:12]}…` revoked.")
                    st.cache_data.clear()
                    st.rerun()
        else:
            st.info("No active API keys found.")

    st.divider()
    st.subheader("Generate new key")

    with st.form("create_key_form"):
        col_n, col_role, col_tid = st.columns([3, 1, 2])
        key_name = col_n.text_input("Key name", placeholder="ci-bot")
        key_role = col_role.selectbox("Role", ["operator", "viewer", "admin"])
        key_tenant = col_tid.text_input("Tenant ID (optional)", placeholder="acme-corp")
        create_submitted = st.form_submit_button("Generate key")

    if create_submitted:
        if not key_name.strip():
            st.warning("Enter a key name.")
        else:
            result = create_api_key(key_name.strip(), key_role, key_tenant.strip())
            if "error" in result:
                st.error(f"Failed: {result['error']}")
            else:
                raw = result.get("raw_key", "")
                st.success(f"Key created — ID: `{result.get('key_id', '?')[:16]}…`")
                st.warning("**Copy the raw key now — it will not be shown again.**")
                st.code(raw, language=None)
                st.caption(
                    f"Role: `{result.get('role', '?')}` "
                    f"| Tenant: `{result.get('tenant_id', '') or 'global'}`"
                )
                st.cache_data.clear()


# ── Auto-refresh ──────────────────────────────────────────────────────────────

time.sleep(0)  # yield to Streamlit event loop
