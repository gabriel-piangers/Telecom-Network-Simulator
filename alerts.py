from node import offline
from topology import Topology

_SEVERITY_ORDER = {"CRITICAL": 0, "WARNING": 1}


def get_active_alerts(topology: Topology) -> list[dict]:
    """Return one alert dict per threshold breach per node.

    Mirrors a Zabbix-style dashboard: alerts are fired per observable symptom,
    with no knowledge of root cause or grouping.  The ``fault`` field is
    intentionally absent — the agent must infer it through investigation.

    PON nodes are passive fiber infrastructure with no IP. Their alerts are
    surfaced as OLT port-level events (e.g. "OLT-01 port PON-01 signal degraded")
    since that is how a real monitoring system would report them.

    Check precedence per device:
    - UNREACHABLE (CRITICAL) — suppresses all metric/ping checks for that node.
    - DEGRADED_SIGNAL / LOW_BANDWIDTH — from metrics().
    - HIGH_LOSS / HIGH_LATENCY — from ping("1.1.1.1") (IP devices only).
    """
    # Snapshot the clock once so all alert IDs share the same base timestamp
    # regardless of how much ping() advances the clock during the scan.
    t0 = topology.clock.t
    timestamp = topology.clock.stamp(t0)

    alerts = []

    for node in topology.all_nodes():
        status = node.status()
        is_pon = node.type == "PON"

        # --- UNREACHABLE check (offline or hung) ---
        if status in offline or status == "hung":
            if is_pon:
                olt_name = node.parent.name if node.parent else "OLT"
                msg = f"{olt_name} port {node.name} is down — downstream ONTs unreachable."
            else:
                msg = f"{node.name} ({node.ip}) is not responding to ping."
            alerts.append(_make_alert(node, "UNREACHABLE", "CRITICAL", msg, t0, timestamp))
            continue   # skip metric/ping checks — device is unreachable

        # --- Metric checks ---
        metrics = node.metrics()

        if metrics.get("signal_dbm") is not None and metrics["signal_dbm"] < -28.0:
            if is_pon:
                olt_name = node.parent.name if node.parent else "OLT"
                msg = (f"{olt_name} port {node.name} optical signal degraded "
                       f"({metrics['signal_dbm']:.1f} dBm, threshold -28.0 dBm).")
            else:
                msg = (f"{node.name} ({node.ip}) optical signal degraded "
                       f"({metrics['signal_dbm']:.1f} dBm, threshold -28.0 dBm).")
            alerts.append(_make_alert(node, "DEGRADED_SIGNAL", "WARNING", msg, t0, timestamp))

        if metrics.get("bandwidth_pct") is not None and metrics["bandwidth_pct"] < 50:
            if is_pon:
                olt_name = node.parent.name if node.parent else "OLT"
                msg = (f"{olt_name} port {node.name} bandwidth low "
                       f"({metrics['bandwidth_pct']:.1f}%, threshold 50%).")
            else:
                msg = (f"{node.name} ({node.ip}) bandwidth utilisation low "
                       f"({metrics['bandwidth_pct']:.1f}%, threshold 50%).")
            alerts.append(_make_alert(node, "LOW_BANDWIDTH", "WARNING", msg, t0, timestamp))

        # --- Ping checks (IP devices only — PONs have no IP to ping from) ---
        if not is_pon:
            ping = node.ping("1.1.1.1")

            if ping.get("loss_pct") is not None and ping["loss_pct"] > 20:
                alerts.append(_make_alert(
                    node, "HIGH_LOSS", "WARNING",
                    f"{node.name} ({node.ip}) high packet loss ({ping['loss_pct']:.0f}%, threshold 20%).",
                    t0, timestamp,
                ))

            if ping.get("ping_avg") is not None and ping["ping_avg"] > 80:
                alerts.append(_make_alert(
                    node, "HIGH_LATENCY", "WARNING",
                    f"{node.name} ({node.ip}) high latency ({ping['ping_avg']:.1f} ms, threshold 80 ms).",
                    t0, timestamp,
                ))

    alerts.sort(key=lambda a: _SEVERITY_ORDER[a["severity"]])
    return alerts


def _make_alert(node, check: str, severity: str, message: str,
                t0: int, timestamp: str) -> dict:
    return {
        "id": f"alert-{node.name}-{check}-{t0}",
        "severity": severity,
        "node_name": node.name,
        "node_type": node.type,
        "check": check,
        "message": message,
        "timestamp": timestamp,
    }
