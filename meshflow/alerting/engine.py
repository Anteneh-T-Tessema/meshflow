"""Sprint 54 — Alert engine: evaluate rules against the metric store.

AlertEngine  — evaluates all enabled rules, fires new alerts when thresholds
               are breached, auto-resolves alerts when conditions clear,
               and optionally delivers webhook notifications via the
               WebhookRetryQueue from Sprint 53.

Usage
-----
    from meshflow.alerting.engine import AlertEngine
    from meshflow.alerting.metrics import MetricStore
    from meshflow.alerting.rules import AlertRuleStore, AlertStore

    metrics    = MetricStore(":memory:")
    rule_store = AlertRuleStore(":memory:")
    alert_store = AlertStore(":memory:")

    engine = AlertEngine(metrics, rule_store, alert_store)

    # Record some data
    metrics.record("billing-agent", "latency_ms", 620.0)

    # Add a rule
    rule_store.add("high-latency", "billing-agent", "latency_ms", "gt", 500.0)

    # Evaluate — fires the alert automatically
    fired, resolved = engine.evaluate()
"""

from __future__ import annotations

import time
from typing import Optional

from meshflow.alerting.metrics import MetricStore
from meshflow.alerting.rules import AlertRecord, AlertRule, AlertRuleStore, AlertStore


class AlertEngine:
    """Evaluate alert rules against current metrics; manage alert lifecycle.

    Parameters
    ----------
    metrics:       Source of truth for metric data.
    rule_store:    Store of :class:`AlertRule` definitions.
    alert_store:   Store of fired :class:`AlertRecord` objects.
    webhook_queue: Optional ``WebhookRetryQueue`` for delivering notifications.
                   If supplied, a webhook delivery is enqueued for each new
                   firing alert whose rule has a ``webhook_url`` set.
    """

    def __init__(
        self,
        metrics: MetricStore,
        rule_store: AlertRuleStore,
        alert_store: AlertStore,
        webhook_queue: Optional[object] = None,
    ) -> None:
        self._metrics      = metrics
        self._rules        = rule_store
        self._alerts       = alert_store
        self._webhook_queue = webhook_queue

    # ── Main evaluation loop ──────────────────────────────────────────────────

    def evaluate(
        self, now: Optional[float] = None
    ) -> tuple[list[AlertRecord], list[AlertRecord]]:
        """Evaluate all enabled rules once.

        Returns ``(newly_fired, newly_resolved)``.

        For each rule:
        - Compute aggregate over the rule's window.
        - If threshold is breached and no alert is already firing → fire.
        - If threshold is NOT breached and an alert IS firing → resolve.
        """
        ts = now if now is not None else time.time()
        fired: list[AlertRecord] = []
        resolved: list[AlertRecord] = []

        for rule in self._rules.list_rules(enabled_only=True):
            value = self._metrics.aggregate(
                rule.agent_name,
                rule.metric,
                window_s=rule.window_s,
                fn=rule.agg_fn,
                now=ts,
            )
            if value is None:
                # No data in window — skip; don't resolve existing alerts
                continue

            breached = rule.evaluate(value)
            already_firing = self._alerts.has_firing(rule.rule_id)

            if breached and not already_firing:
                record = self._alerts.fire(rule, value)
                fired.append(record)
                self._maybe_deliver_webhook(rule, record)

            elif not breached and already_firing:
                count = self._alerts.resolve_for_rule(rule.rule_id)
                if count:
                    newly_resolved = self._alerts.list_alerts(
                        status="resolved", agent_name=rule.agent_name
                    )
                    resolved.extend(newly_resolved[:count])

        return fired, resolved

    def evaluate_rule(
        self, rule_id: str, now: Optional[float] = None
    ) -> Optional[AlertRecord]:
        """Evaluate a single rule by ID.  Returns a new AlertRecord if fired, else None."""
        rule = self._rules.get(rule_id)
        if rule is None or not rule.enabled:
            return None

        ts = now if now is not None else time.time()
        value = self._metrics.aggregate(
            rule.agent_name,
            rule.metric,
            window_s=rule.window_s,
            fn=rule.agg_fn,
            now=ts,
        )
        if value is None:
            return None

        if rule.evaluate(value) and not self._alerts.has_firing(rule.rule_id):
            record = self._alerts.fire(rule, value)
            self._maybe_deliver_webhook(rule, record)
            return record
        return None

    # ── Webhook delivery ──────────────────────────────────────────────────────

    def _maybe_deliver_webhook(self, rule: AlertRule, record: AlertRecord) -> None:
        if not rule.webhook_url or self._webhook_queue is None:
            return
        try:
            self._webhook_queue.enqueue(  # type: ignore[union-attr]
                webhook_id=rule.rule_id,
                url=rule.webhook_url,
                event_type="alert_fired",
                payload=record.to_dict(),
                secret=rule.webhook_secret,
            )
        except Exception:
            pass  # webhook delivery is best-effort; don't break evaluation

    # ── Convenience helpers ───────────────────────────────────────────────────

    def summary(self) -> dict[str, int]:
        """Return alert counts by status."""
        return self._alerts.counts()

    def firing_count(self) -> int:
        return len(self._alerts.firing())
