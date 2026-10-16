from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from alerts import get_active_alerts
from topology import Topology

app = FastAPI(
    title="Telecom Network Simulator",
    description=(
        "Simulates a GPON network (OLT → PON → ONT) for testing AI support agents. "
        "Load a fault scenario, then use the inspection and action endpoints to investigate."
    ),
    version="0.1.0",
)

topology = Topology()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Maps internal Node.status() values to OLT-observable status labels.
# "hung"     → the OLT sees OMCI timeouts but the device looks optically alive;
#              from the operator's view the port is "unreachable".
# "flapping" → resolves dynamically inside status(); never reaches here as-is.
_STATUS_MAP = {
    "normal":    "online",
    "degraded":  "excessive_ber",   # degraded signal → high bit-error-rate
    "los":       "los",
    "power off": "power_off",
    "hung":      "unreachable",
}

def _observable_status(node) -> str:
    s = node.status()
    return _STATUS_MAP.get(s) or s


def _get_by_ip(ip: str):
    """Resolve an IP to a node, raising 404 on miss or if the node is a PON."""
    try:
        node = topology.get_node_by_ip(ip)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No device found with IP {ip}.")
    if node.type == "PON":
        raise HTTPException(status_code=404, detail=f"No device found with IP {ip}.")
    return node


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

@app.get("/alerts", summary="List all active alerts")
def get_alerts():
    """Return one alert per threshold breach per node.

    Mirrors a Zabbix-style dashboard: no root-cause grouping, no fault labels.
    The agent must infer the root cause through investigation.
    """
    return get_active_alerts(topology)


# ---------------------------------------------------------------------------
# Topology / node inspection
# ---------------------------------------------------------------------------

@app.get("/topology", summary="List all reachable devices")
def get_topology():
    """Return every addressable device (OLT and ONTs) with status and metrics.

    PON nodes are passive fiber junctions with no IP and are excluded from
    this view — they are invisible infrastructure from the agent's perspective.
    """
    return [
        {**node.to_dict(), "status": _observable_status(node)}
        for node in topology.all_nodes()
        if node.ip is not None
    ]


@app.get("/devices/{ip}", summary="Get a single device by IP")
def get_device(ip: str):
    """Full detail for one device including metrics. Returns 404 if unknown."""
    node = _get_by_ip(ip)
    return {**node.to_dict(), "status": _observable_status(node)}


@app.get("/devices/{ip}/logs", summary="Get device logs")
def get_logs(ip: str, minutes: int = Query(default=60, ge=1, le=1440)):
    """Log lines for the last *minutes* from this device.

    Returns **503** if the device is currently unreachable (offline or hung).
    """
    node = _get_by_ip(ip)
    lines = node.get_logs(minutes)
    if lines is None:
        return JSONResponse(
            status_code=503,
            content={"detail": "Device unreachable — cannot retrieve logs."},
        )
    return {"ip": ip, "name": node.name, "minutes": minutes, "lines": lines}


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

@app.post("/devices/{ip}/ping", summary="Ping from a device to a destination IP")
def ping_device(
    ip: str,
    dst_ip: str = Query(default="1.1.1.1", description="Destination IP address to ping"),
):
    """Run a simulated 10-packet ping from the device at *ip* to *dst_ip*.

    Returns **503** if the source device is unreachable (all ping results are None).
    Advances the shared clock by 10 seconds as a side effect.
    """
    node = _get_by_ip(ip)
    result = node.ping(dst_ip)
    if result.get("ping_avg") is None:
        return JSONResponse(
            status_code=503,
            content={"detail": "Device unreachable — ping failed.", "result": result},
        )
    return {"ip": ip, "name": node.name, "dst_ip": dst_ip, "result": result}


@app.post("/devices/{ip}/reboot", summary="Reboot a device")
def reboot_device(ip: str):
    """Reboot the device at *ip*.

    - If the device is **hung**, the fault is cleared and recovery is logged.
    - For any other state an operator-reboot log entry is added but the fault
      state does not change (consistent with `Node.reboot()` behaviour).

    Returns the updated device dict.
    """
    node = _get_by_ip(ip)
    node.reboot()
    return {**node.to_dict(), "status": _observable_status(node)}


# ---------------------------------------------------------------------------
# Scenario control
# ---------------------------------------------------------------------------

@app.post("/scenario", summary="Load a fault scenario")
def load_scenario(scenario: dict):
    """Apply a fault scenario and return the resulting active alerts.

    The network is reset to normal before the scenario faults are applied.

    Scenario format::

        {
            "name": "...",
            "description": "...",
            "faults": [
                {"node": "PON-01", "fault": "los", "active_for_seconds": 2400}
            ]
        }

    Note: faults are still referenced by **node name** (e.g. ``"PON-01"``),
    not IP, so scenarios can target PON infrastructure nodes that have no IP.

    Returns **400** if a node name or fault value in the scenario is invalid.
    """
    try:
        topology.apply_scenario(scenario)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown node: {exc}.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return get_active_alerts(topology)


@app.post("/reset", summary="Reset all faults to normal")
def reset():
    """Clear every fault in the network and return the clean topology."""
    topology.reset()
    return [
        {**node.to_dict(), "status": _observable_status(node)}
        for node in topology.all_nodes()
        if node.ip is not None
    ]
