from datetime import datetime, timezone
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
_tickets: list[dict] = []          # in-memory ticket store


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
# Health
# ---------------------------------------------------------------------------

@app.get("/health", summary="Health check")
def health():
    """Returns 200 when the simulator is running and ready."""
    return {"status": "ok", "nodes": len(topology.all_nodes())}


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------

@app.get("/clock", summary="Get current simulated time")
def get_clock():
    """Return the current simulated clock time."""
    return {
        "t": topology.clock.t,
        "timestamp": topology.clock.stamp(),
    }


@app.post("/clock/advance", summary="Advance the simulated clock")
def advance_clock(seconds: int = Query(ge=1, le=86400)):
    """Advance the simulated clock by *seconds* and return the new time.

    Useful for testing time-dependent behaviour such as a reboot completing
    or a flapping fault stabilising after the clock moves forward.
    """
    topology.clock.advance(seconds)
    return {
        "t": topology.clock.t,
        "timestamp": topology.clock.stamp(),
    }


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


@app.get("/devices/{ip}/neighbors", summary="Get device neighbors")
def get_neighbors(ip: str):
    """Return the upstream device and sibling ONTs sharing the same PON port.

    Helps the agent correlate multiple unreachable ONTs to a common parent.
    The PON itself is not returned (it has no IP) but its sibling ONTs are,
    so the agent can check whether other devices on the same branch are also
    affected.
    """
    node = _get_by_ip(ip)

    # Walk up to nearest IP-bearing ancestor (skips the PON)
    ancestor = node.parent
    while ancestor and ancestor.ip is None:
        ancestor = ancestor.parent

    # Siblings share the same direct parent (the PON)
    siblings = []
    if node.parent:
        for child in node.parent.children:
            if child and child is not node and child.ip is not None:
                siblings.append({
                    "name": child.name,
                    "ip": child.ip,
                    "status": _observable_status(child),
                })

    return {
        "node": node.name,
        "ip": node.ip,
        "upstream": {
            "name": ancestor.name,
            "ip": ancestor.ip,
            "status": _observable_status(ancestor),
        } if ancestor else None,
        "siblings": siblings,
    }


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


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------

VALID_ACTIONS = {
    "reboot_device",
    "dispatch_technician",
    "check_cabinet_power",
    "contact_customer",
    "inspect_fiber",
}


@app.post("/tickets", summary="Submit an investigation ticket", status_code=201)
def submit_ticket(ticket: dict):
    """Submit the agent's investigation result as a structured ticket.

    The ticket is stored in memory and can be retrieved later for evaluation
    against the scenario's ``expected`` ground truth.

    Required fields::

        {
            "scenario_name":    "PON-01 fiber loss",
            "alert_ids":        ["alert-ONT-01-01-UNREACHABLE-10800"],
            "summary":          "Short description of the incident",
            "root_causes": [
                {
                    "node":               "PON-01",
                    "fault":              "los",
                    "affected_nodes":     ["ONT-01-01", "ONT-01-02"],
                    "recommended_action": "dispatch_technician"
                }
            ],
            "evidence":    ["OLT-01 log: PON-01 deregistered (reason: LOS)"],
            "confidence":  "high"
        }

    ``scenario_name`` is optional but strongly recommended — it is used by the
    evaluation harness to match tickets to scenarios for scoring.

    Valid ``recommended_action`` values: ``reboot_device``, ``dispatch_technician``,
    ``check_cabinet_power``, ``contact_customer``, ``inspect_fiber``.

    Returns **400** if required fields are missing or action values are invalid.
    """
    # Validate required top-level fields
    for field in ("alert_ids", "summary", "root_causes", "evidence", "confidence"):
        if field not in ticket:
            raise HTTPException(status_code=400, detail=f"Missing required field: '{field}'.")

    # Validate each root cause entry
    for rc in ticket.get("root_causes", []):
        for field in ("node", "fault", "affected_nodes", "recommended_action"):
            if field not in rc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Root cause entry missing required field: '{field}'.",
                )
        if rc["recommended_action"] not in VALID_ACTIONS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid recommended_action '{rc['recommended_action']}'. "
                    f"Valid values: {sorted(VALID_ACTIONS)}."
                ),
            )

    ticket["submitted_at"] = datetime.now(timezone.utc).isoformat()
    ticket["id"] = f"ticket-{len(_tickets) + 1:04d}"
    _tickets.append(ticket)
    return ticket


@app.get("/tickets", summary="List all submitted tickets")
def list_tickets():
    """Return all tickets submitted in the current session."""
    return _tickets


@app.get("/tickets/{ticket_id}", summary="Get a single ticket")
def get_ticket(ticket_id: str):
    """Return the ticket with the given id. Returns 404 if not found."""
    for t in _tickets:
        if t["id"] == ticket_id:
            return t
    raise HTTPException(status_code=404, detail=f"Ticket '{ticket_id}' not found.")


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

@app.post("/reset", summary="Reset all faults to normal")
def reset():
    """Clear every fault in the network and return the clean topology."""
    topology.reset()
    return [
        {**node.to_dict(), "status": _observable_status(node)}
        for node in topology.all_nodes()
        if node.ip is not None
    ]
