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
├── topology.py       ← Network graph: builds and holds all Node instances
├── node.py           ← Node class (extended from current implementation)
├── alerts.py         ← Alert generation logic (derives alerts from node state)
├── scenarios/
│   ├── pon_down.json       ← Example scenario file
│   └── ont_degraded.json   ← Example scenario file
└── requirements.txt
```

---

## Sub-Tasks

---

### Sub-Task 1 — Finish fixing the Node class

**Status:** [ ] pending

**Intent:**
You already fixed the main bugs (`fault="normal"` default, `signal_dbm` typo,
`status()`-based offline check in `metrics()`). Two small issues remain before
the class is API-ready.

**Expected Outcomes:**
- `turn_on()` and `restart()` leave the node in `fault="normal"` (not `None`)
- `Node.to_dict()` returns all metadata + metrics as a JSON-serializable dict

**Todo List:**
1. Fix `turn_on()`: change `self.fault = None` → `self.fault = "normal"`
2. Fix `restart()`: change `self.fault = None` → `self.fault = "normal"`
3. Add a `to_dict()` method that returns:
   `{ "name", "type", "ports", "parent", "fault", "status", "metrics" }`
   where `parent` is `node.parent.name` (or `None`) and `metrics` calls `self.metrics()`

**Relevant Context:**
- [`node.py`](node.py) lines 82–88 — `turn_on()` and `restart()` still set `fault = None`
- Metric splitting by device type is **not needed** — all 7 metrics are valid for OLT,
  PON, and ONT in this PoC context
---

### Sub-Task 2 — Build the Topology module

**Status:** [ ] pending

**Intent:**
Extract topology construction from `main.py` into a dedicated `topology.py` module that
builds the network graph and acts as the single source of truth for all node state. It
must be importable by the API layer without side effects.

**Expected Outcomes:**
- A `Topology` class (or module-level singleton) that holds all nodes by name
- Methods to look up a node by name, list all nodes, list nodes by type
- The topology is built once at startup and lives in memory for the process lifetime
- `main.py` becomes the FastAPI app entrypoint only, with no topology logic

**Todo List:**
1. Create `topology.py` with a `Topology` class
2. In `__init__`, build the default GPON tree: 1 OLT → 2 PONs → 4 ONTs (same as current
   `main.py`) — name them meaningfully (e.g. `OLT-01`, `PON-01`, `ONT-01-01`)
3. Add `get_node(name) -> Node` — raises `KeyError` if not found
4. Add `all_nodes() -> list[Node]` — returns all nodes
5. Add `nodes_by_type(type: str) -> list[Node]`
6. Add `apply_scenario(scenario: dict)` — receives a parsed scenario dict and calls
   `set_fault()` on the named nodes (see Sub-Task 4 for scenario format)
7. Add `reset()` — sets all node faults back to `"normal"`
8. Import and instantiate `Topology` once at the top of `main.py` (the FastAPI app)

**Relevant Context:**
- [`main.py`](main.py) — topology construction to migrate
- [`node.py`](node.py) — `Node.__init__` signature

---

### Sub-Task 3 — Build the Alert Engine

**Status:** [ ] pending

**Intent:**
Create an `alerts.py` module that scans the topology and generates a list of active alerts
based on node states. Alerts are the trigger for the AI agent — they represent the
"notification from Zabbix" in the real-world flow. Each alert must carry enough context
for the agent to know where to start investigating.

**Expected Outcomes:**
- `get_active_alerts(topology)` returns a list of alert dicts
- Each alert has: `id`, `severity`, `node_name`, `node_type`, `fault`, `message`, `timestamp`
- Severity levels: `CRITICAL` (down), `MAJOR` (hung), `WARNING` (degraded)
- Nodes with `fault="normal"` produce no alert
- Downstream nodes that are offline due to a parent fault are NOT independently alerted
  (the root-cause node generates the alert, not its children)

**Expected alert JSON shape:**
```json
{
  "id": "alert-PON-01-1721000000",
  "severity": "CRITICAL",
  "node_name": "PON-01",
  "node_type": "PON",
  "fault": "down",
  "message": "Node PON-01 is down. Downstream nodes affected: ONT-01-01, ONT-01-02",
  "timestamp": "2024-07-14T20:00:00Z"
}
```

**Todo List:**
1. Create `alerts.py` with a `get_active_alerts(topology) -> list[dict]` function
2. Iterate over `topology.all_nodes()`; skip nodes whose fault is `"normal"`
3. Skip nodes that are offline only because of a parent fault (i.e. their own `fault`
   is `"normal"` but `status()` returns `"offline"`) — the parent already has an alert
4. Map fault types to severities: `down` → `CRITICAL`, `hung` → `MAJOR`,
   `degraded` → `WARNING`
5. Build the downstream-affected list by walking `node.children` recursively
6. Generate a stable `id` from `node_name + timestamp` (can be a simple f-string)
7. Return the full list sorted by severity (CRITICAL first)

**Relevant Context:**
- Will be called by the `/alerts` API endpoint (Sub-Task 5)
- `Node.status()` and `Node.fault` in [`node.py`](node.py)

---

### Sub-Task 4 — Define and implement the Scenario format

**Status:** [ ] pending

**Intent:**
Define a simple JSON format for scenario files that describe which nodes should have
which faults. This allows testing multiple failure situations without changing code.
The API will accept a scenario at runtime via a POST endpoint.

**Expected Outcomes:**
- A documented JSON schema for scenario files
- At least 2 example scenario files under `scenarios/`
- `Topology.apply_scenario()` correctly parses and applies the scenario

**Scenario JSON format:**
```json
{
  "name": "PON-01 complete failure",
  "description": "Simulates a PON port going completely offline",
  "faults": [
    { "node": "PON-01", "fault": "down" },
    { "node": "ONT-02-01", "fault": "degraded" }
  ]
}
```

**Todo List:**
1. Define the schema above (document it in a comment inside the scenario files)
2. Create `scenarios/pon_down.json` — PON-01 set to `down`
3. Create `scenarios/ont_degraded.json` — two ONTs set to `degraded`
4. Ensure `Topology.apply_scenario()` (from Sub-Task 2) calls `reset()` before applying,
   so loading a new scenario always starts from a clean state
5. Validate node names and fault values when applying — return a clear error message
   if a node name in the scenario does not exist in the topology

**Relevant Context:**
- `Topology.apply_scenario()` and `reset()` defined in Sub-Task 2
- `Node.set_fault()` in [`node.py`](node.py)

---

### Sub-Task 5 — Build the FastAPI REST layer

**Status:** [ ] pending

**Intent:**
Expose the simulator state and actions through a REST API that the AI agent will call.
The API is the only interface between the agent and the simulator — it must cover
topology inspection, metric reading, alert listing, action execution, and scenario loading.

**Expected Outcomes:**
- A running FastAPI app reachable at `http://localhost:8000`
- Swagger UI available at `/docs`
- All endpoints return clean JSON

**Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/alerts` | List all active alerts |
| GET | `/topology` | List all nodes with type, status, and fault |
| GET | `/nodes/{name}` | Full detail for a single node including metrics |
| POST | `/nodes/{name}/restart` | Restart a hung node |
| POST | `/nodes/{name}/turn_off` | Turn off a node |
| POST | `/nodes/{name}/turn_on` | Turn on a node that is down |
| POST | `/scenario` | Load a scenario from a JSON body |
| POST | `/reset` | Reset all faults to normal |

**Todo List:**
1. Replace `main.py` content with a FastAPI app (`app = FastAPI()`)
2. Instantiate `Topology` once as a module-level variable in `main.py`
3. Implement `GET /alerts` — calls `get_active_alerts(topology)` and returns the list
4. Implement `GET /topology` — returns `[node.to_dict() for node in topology.all_nodes()]`
5. Implement `GET /nodes/{name}` — calls `topology.get_node(name).to_dict()`, returns
   404 if the node does not exist
6. Implement `POST /nodes/{name}/restart`, `/turn_off`, `/turn_on` — call the
   corresponding `Node` methods; return the updated node dict
7. Implement `POST /scenario` — accepts the scenario JSON body and calls
   `topology.apply_scenario()`; returns the list of resulting active alerts
8. Implement `POST /reset` — calls `topology.reset()` and confirms
9. Add a `requirements.txt` with `fastapi` and `uvicorn`

**Relevant Context:**
- `Topology` from Sub-Task 2, `get_active_alerts` from Sub-Task 3
- `Node.to_dict()` from Sub-Task 1
- Run with: `uvicorn main:app --reload`
