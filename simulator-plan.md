# Telecom Network Simulator — Plan

## Overview

Build a proof-of-concept telecom network simulator in Python that an AI agent can interact
with via a REST API (FastAPI). The simulator models a realistic GPON network topology
(OLT → PON → ONT), supports fault injection via scenario files, exposes inspection and
action endpoints, and generates alerts that act as the trigger for agent investigation.

**Goals:**
- Provide a realistic enough environment to train/test an AI support agent
- Keep the implementation simple — this is a PoC, not production software
- The agent accesses the network through HTTP calls only (no direct Python imports)

**Non-goals:**
- Real protocol simulation (SNMP, OMCI, etc.)
- Persistent storage / database
- Authentication / multi-tenancy
- High-fidelity hardware accuracy

---

## Architecture

```
simulator/
├── main.py           ← FastAPI app entrypoint
├── topology.py       ← Network graph: builds and holds all Node instances + Clock
├── node.py           ← Node class (COMPLETE — do not modify)
├── alerts.py         ← Alert generation logic (derives alerts from node state)
├── scenarios/
│   ├── pon_los.json          ← PON complete fiber loss
│   └── ont_degraded.json     ← Two ONTs with degraded signal
└── requirements.txt
```

---

## Key facts about node.py (COMPLETE — read before implementing)

`node.py` is fully implemented and must not be changed. The agent and remaining modules
must be built around its existing API:

- **`Clock`** — shared simulated time object. Must be created once in `Topology.__init__`
  and passed to the root OLT node; child nodes inherit it automatically via `parent.clock`.
- **Fault types** — valid values are: `"normal"`, `"los"`, `"power off"`, `"hung"`,
  `"degraded"`, `"flapping"`. There is no `"down"` fault.
- **Offline states** — `offline = ["los", "power off"]` (module-level constant). A node
  is offline if its own fault or any ancestor's fault is in this list.
- **`set_fault(fault, at=None)`** — `at` allows backdating a fault to a past clock time,
  so scenarios can present the agent with a fault that has been active for some time.
- **`get_logs(minutes=60)`** — returns a list of formatted log strings for the last N
  minutes, or `None` if the device is currently unreachable (offline or hung).
- **`ping(dst_ip)`** — runs a simulated 10-ping test to a destination IP. Returns a dict
  with `ping_max`, `ping_avg`, `ping_min`, `loss_pct`, or all `None` if unreachable.
  **Side effect:** advances `clock.t` by 10 seconds.
- **`reboot()`** — the only recovery action. Clears a `"hung"` fault and logs the reboot.
  Calling it on a non-hung node only adds a log entry (no fault state change).
- **`to_dict()`** — returns all node metadata + current metrics as a JSON-serializable dict.
- **`descendants()`** — returns all downstream nodes recursively (useful in `alerts.py`).

---

## Sub-Tasks

---

### Sub-Task 1 — Build the Topology module

**Status:** [x] done

**Intent:**
Create `topology.py` to construct the network graph and act as the single in-memory source
of truth for all node state. It must own the shared `Clock`, assign IPs to nodes, and
provide the scenario/reset interface used by the API layer.

**Expected Outcomes:**
- A `Topology` class that builds a GPON tree: 1 OLT → 2 PONs → 4 ONTs per PON (9 nodes total)
- Each node has a meaningful name, an IP address, and a bandwidth plan where applicable
- All nodes share a single `Clock` instance (created in `Topology.__init__`)
- `Clock` is initialized with `start=3*3600` so there are 3 hours of log history available
- Methods to look up, list, and filter nodes are available
- Scenarios can be applied and reset cleanly

**Node layout:**

| Name     | Type | IP         | plan_mbps | Parent   | Parent port |
|----------|------|------------|-----------|----------|-------------|
| OLT-01   | OLT  | 10.0.0.1   | —         | —        | —           |
| PON-01   | PON  | 10.0.1.0   | —         | OLT-01   | 0           |
| PON-02   | PON  | 10.0.2.0   | —         | OLT-01   | 1           |
| ONT-01-01| ONT  | 10.0.1.1   | 600       | PON-01   | 0           |
| ONT-01-02| ONT  | 10.0.1.2   | 300       | PON-01   | 1           |
| ONT-01-03| ONT  | 10.0.1.3   | 300       | PON-01   | 2           |
| ONT-01-04| ONT  | 10.0.1.4   | 100       | PON-01   | 3           |
| ONT-02-01| ONT  | 10.0.2.1   | 600       | PON-02   | 0           |
| ONT-02-02| ONT  | 10.0.2.2   | 300       | PON-02   | 1           |
| ONT-02-03| ONT  | 10.0.2.3   | 300       | PON-02   | 2           |
| ONT-02-04| ONT  | 10.0.2.4   | 100       | PON-02   | 3           |

**Todo List:**
1. Create `topology.py` with a `Topology` class
2. In `__init__`, create `Clock(start=3*3600)` and assign to `self.clock`
3. Build the full node tree from the table above, passing `clock=self.clock` only to
   the OLT (children inherit it automatically)
4. Store all nodes in `self._nodes: dict[str, Node]` keyed by name
5. Add `get_node(name) -> Node` — raises `KeyError` if not found
6. Add `all_nodes() -> list[Node]` — returns all nodes
7. Add `nodes_by_type(type: str) -> list[Node]`
8. Add `apply_scenario(scenario: dict)` — calls `reset()` first, then for each entry in
   `scenario["faults"]`, calls `node.set_fault(fault, at=clock.t - backdate_seconds)` where
   `backdate_seconds` comes from the optional `"active_for_seconds"` field (default `0`)
9. Add `reset()` — calls `node.set_fault("normal")` on all nodes

**Relevant Context:**
- [`node.py`](node.py) — `Node.__init__` signature, `Clock` class, `set_fault(at=)`
- OLT has `ports=2` (one per PON); each PON has `ports=4` (one per ONT)

---

### Sub-Task 2 — Build the Alert Engine

**Status:** [x] done

**Intent:**
Create `alerts.py` to simulate how a monitoring dashboard like Zabbix fires alerts:
one alert per metric threshold breach, per device, with no knowledge of root cause or
grouping. Every node that is in a non-normal observable state fires its own alert.
Discovering that multiple alerts share a common root cause is the agent's job, not the
simulator's. The `fault` field must NOT be exposed in the alert — the agent must infer
it from logs, metrics, and ping results.

**Expected Outcomes:**
- One alert per affected node — a PON going down produces 1 alert for the PON and
  1 alert per offline ONT underneath it (5 alerts total, not 1 grouped alert)
- Alert type is based on what the monitoring system would actually observe:
  - Node unreachable (no ping response) → `"UNREACHABLE"`
  - High packet loss or high latency → `"HIGH_LOSS"` / `"HIGH_LATENCY"`
  - Degraded signal or low bandwidth → `"DEGRADED_SIGNAL"` / `"LOW_BANDWIDTH"`
- Severity is based on impact: `CRITICAL` for unreachable, `WARNING` for degraded metrics
- The `fault` field is absent — the agent must determine this through investigation
- Result is sorted: CRITICAL first, then WARNING

**Alert JSON shape:**
```json
{
  "id": "alert-ONT-01-01-10800",
  "severity": "CRITICAL",
  "node_name": "ONT-01-01",
  "node_type": "ONT",
  "check": "UNREACHABLE",
  "message": "ONT-01-01 is not responding to ping.",
  "timestamp": "2026-10-14 03:00:00"
}
```

**Check types and when they fire:**

| check | severity | condition |
|-------|----------|-----------|
| `UNREACHABLE` | CRITICAL | `node.status()` is in `offline` or is `"hung"` |
| `HIGH_LOSS` | WARNING | ping `loss_pct > 20` (only if reachable) |
| `HIGH_LATENCY` | WARNING | ping `ping_avg > 80 ms` (only if reachable) |
| `DEGRADED_SIGNAL` | WARNING | `signal_dbm < -28.0` (only if reachable) |
| `LOW_BANDWIDTH` | WARNING | `bandwidth_pct < 50` (only if reachable) |

A single node can fire multiple WARNING alerts (e.g. both `DEGRADED_SIGNAL` and
`HIGH_LOSS` for a degraded ONT). A CRITICAL `UNREACHABLE` alert suppresses all other
checks for that node.

**Todo List:**
1. Create `alerts.py` with `get_active_alerts(topology) -> list[dict]`
2. For each node in `topology.all_nodes()`:
   a. If `node.status()` is in `offline` or is `"hung"`, emit one `UNREACHABLE` CRITICAL
      alert and skip the metric checks for that node
   b. Otherwise, read `node.metrics()` and emit a `DEGRADED_SIGNAL` WARNING if
      `signal_dbm < -28.0`, and a `LOW_BANDWIDTH` WARNING if `bandwidth_pct < 50`
   c. Call `node.ping("1.1.1.1")` and emit `HIGH_LOSS` if `loss_pct > 20`, and
      `HIGH_LATENCY` if `ping_avg > 80`
3. Use `topology.clock.stamp()` for the timestamp field
4. Generate `id` as `f"alert-{node.name}-{check}-{topology.clock.t}"`
5. Return all alerts sorted: CRITICAL first, then WARNING

**Relevant Context:**
- [`node.py`](node.py) — `Node.status()`, `Node.metrics()`, `Node.ping()`, `Clock.stamp()`
- `offline` list in `node.py`: `["los", "power off"]`
- Calling `ping()` advances the clock by 10 s per call — this is acceptable for alert
  generation since the dashboard would poll sequentially anyway

---

### Sub-Task 3 — Define scenario files

**Status:** [x] done

**Intent:**
Define the scenario JSON format and create example files that the agent will be tested
against. The format supports backdating faults so the node has log history when the agent
investigates.

**Scenario JSON format:**
```json
{
  "name": "scenario name",
  "description": "what this simulates",
  "faults": [
    {
      "node": "PON-01",
      "fault": "los",
      "active_for_seconds": 2400
    }
  ]
}
```
- `"active_for_seconds"` is optional (default `0`). When set, the fault is applied at
  `clock.t - active_for_seconds`, giving the node log history for that duration.

**Todo List:**
1. Create `scenarios/pon_los.json` — PON-01 with `"los"`, active for 40 minutes (2400 s)
2. Create `scenarios/ont_degraded.json` — ONT-01-02 `"degraded"` for 30 min, ONT-02-01
   `"flapping"` for 50 min, ONT-01-04 `"hung"` for 8 min
3. Verify that `Topology.apply_scenario()` correctly handles the `active_for_seconds` field

**Relevant Context:**
- `Topology.apply_scenario()` defined in Sub-Task 1
- `set_fault(at=)` in [`node.py`](node.py) — `at` must be `≥` the previous fault's timestamp

---

### Sub-Task 4 — Build the FastAPI REST layer

**Status:** [x] done

**Intent:**
Expose the simulator through a REST API that is the sole interface for the AI agent.
Endpoints cover: topology inspection, metric polling, log retrieval, ping tests, alert
listing, the reboot action, and scenario/reset control.

**Expected Outcomes:**
- FastAPI app running at `http://localhost:8000`, Swagger UI at `/docs`
- All endpoints return clean JSON; unreachable-device responses use HTTP 503
- A single `Topology` instance is shared across all requests (module-level singleton)

**Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/alerts` | List all active alerts (root-cause nodes only) |
| GET | `/topology` | All nodes: name, type, status, fault, ip |
| GET | `/nodes/{name}` | Full node detail including metrics (`to_dict()`) |
| GET | `/nodes/{name}/logs?minutes=60` | Log lines for last N minutes; 503 if unreachable |
| POST | `/nodes/{name}/ping?dst_ip=1.1.1.1` | Run ping test from this node to `dst_ip` |
| POST | `/nodes/{name}/reboot` | Reboot a node (clears `hung`; logs action otherwise) |
| POST | `/scenario` | Load scenario from JSON body; returns resulting alerts |
| POST | `/reset` | Reset all faults to normal; returns clean topology |

**Todo List:**
1. Create `main.py` with `app = FastAPI()` and a module-level `topology = Topology()`
2. Implement `GET /alerts`
3. Implement `GET /topology` using `node.to_dict()` for each node
4. Implement `GET /nodes/{name}` — return 404 if node not found
5. Implement `GET /nodes/{name}/logs` — pass `minutes` query param (default 60);
   return 503 with `{"detail": "device unreachable"}` if `get_logs()` returns `None`
6. Implement `POST /nodes/{name}/ping` — pass `dst_ip` query param;
   return 503 if result contains all `None` values
7. Implement `POST /nodes/{name}/reboot` — call `node.reboot()`, return updated `to_dict()`
8. Implement `POST /scenario` — body matches scenario JSON format; return resulting alerts
9. Implement `POST /reset` — call `topology.reset()`, return `topology.all_nodes()` as dicts
10. Add `requirements.txt` with `fastapi` and `uvicorn[standard]`

**Relevant Context:**
- `Topology` from Sub-Task 1, `get_active_alerts` from Sub-Task 2
- `Node.to_dict()`, `Node.get_logs()`, `Node.ping()`, `Node.reboot()` in [`node.py`](node.py)
- Run with: `uvicorn main:app --reload`
- `ping()` advances the clock — this is expected and intentional
