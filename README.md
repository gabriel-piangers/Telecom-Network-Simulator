# Telecom Network Simulator

A proof-of-concept GPON network simulator for training and evaluating AI support agents. The simulator exposes a REST API that an agent can use to investigate network faults, identify root causes, and submit structured tickets — without ever seeing the underlying fault state directly.

---

## Quick start

```bash
# Create virtual environment and install dependencies
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Start the server
.venv/bin/uvicorn main:app --reload
```

The API is available at **http://localhost:8000**.  
Interactive documentation (Swagger UI) is at **http://localhost:8000/docs**.

---

## Architecture

```
OLT-01 (10.0.0.1)
├── PON-01  ──── ONT-01-01 (10.0.1.1)  600 Mbps
│               ONT-01-02 (10.0.1.2)  300 Mbps
│               ONT-01-03 (10.0.1.3)  300 Mbps
│               ONT-01-04 (10.0.1.4)  100 Mbps
└── PON-02  ──── ONT-02-01 (10.0.2.1)  600 Mbps
                ONT-02-02 (10.0.2.2)  300 Mbps
                ONT-02-03 (10.0.2.3)  300 Mbps
                ONT-02-04 (10.0.2.4)  100 Mbps
```

PON nodes are passive fiber junctions — they have no IP address and are not directly queryable. They can still carry faults (e.g. fiber cut) that affect all downstream ONTs.

---

## Fault types

| Fault | Description | OLT sees |
|---|---|---|
| `los` | Fiber cut — loss of optical signal | Deregistration with reason: LOS |
| `power off` | Device lost power | Deregistration with reason: dying gasp |
| `hung` | Firmware freeze — optically alive but OMCI silent | Repeated OMCI timeout messages |
| `degraded` | Weak signal — high bit-error-rate | Degraded metrics, CRC errors in logs |
| `flapping` | Intermittent link — drops and recovers repeatedly | Rapid deregister/register cycles |

---

## Observable device status

The API translates internal fault states to OLT-observable labels:

| API status | Meaning |
|---|---|
| `online` | Device is healthy |
| `los` | Device is unreachable — fiber cut |
| `power_off` | Device is unreachable — no power |
| `unreachable` | Device is unreachable — OMCI timeout (hung) |
| `excessive_ber` | Device is reachable but signal is degraded |

---

## API reference

### Health & control

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Readiness check — returns `{"status": "ok"}` |
| `GET` | `/clock` | Current simulated time |
| `POST` | `/clock/advance?seconds=N` | Advance simulated time by N seconds |
| `POST` | `/scenario` | Load a fault scenario (see format below) |
| `POST` | `/reset` | Reset all faults to normal |

### Investigation

| Method | Path | Description |
|---|---|---|
| `GET` | `/alerts` | Active alerts — agent's entry point |
| `GET` | `/topology` | All devices with current status and metrics |
| `GET` | `/devices/{ip}` | Single device detail including metrics |
| `GET` | `/devices/{ip}/neighbors` | Upstream device + sibling IPs and statuses |
| `GET` | `/devices/{ip}/logs?minutes=60` | Device logs for the last N minutes |
| `POST` | `/devices/{ip}/ping?dst_ip=1.1.1.1` | Run a 10-packet ping test from this device |
| `POST` | `/devices/{ip}/reboot` | Reboot a device (clears `hung` state) |

### Tickets

| Method | Path | Description |
|---|---|---|
| `POST` | `/tickets` | Submit an investigation ticket |
| `GET` | `/tickets` | List all submitted tickets |
| `GET` | `/tickets/{id}` | Retrieve a single ticket |

---

## Agent investigation flow

```
1. GET /health               — confirm simulator is ready
2. POST /scenario            — load a fault scenario
3. GET /alerts               — receive the alert dashboard (entry point)
4. GET /topology             — get the full device list with statuses
5. GET /devices/{ip}         — inspect a specific device
6. GET /devices/{ip}/neighbors — find siblings on the same PON branch
7. GET /devices/{ip}/logs    — read device logs for evidence
8. POST /devices/{ip}/ping   — test connectivity
9. POST /devices/{ip}/reboot — take remediation action if hung
10. POST /tickets            — submit findings
```

> **Note:** `GET /alerts` and `POST /devices/{ip}/ping` each advance the simulated clock by ~10 seconds per device polled. This is expected behaviour.

---

## Important behaviours

**Alerts do not expose fault types.** The `fault` field is intentionally absent from all alerts. The agent must infer the root cause by reading logs, checking metrics, and running ping tests.

**PON faults surface as OLT port alerts.** When a PON has a fault, the alert message reads "OLT-01 port PON-01 is down" — the agent must recognise this as a branch-level issue affecting multiple ONTs.

**Flapping nodes are intermittently unreachable.** A device in `flapping` state alternates between online and offline. `GET /devices/{ip}/logs` may return `503` (unreachable) or a log list depending on where the device is in its cycle when called.

**Logs are time-windowed and backfilled.** The simulated clock starts at 3 hours (10800 s). Scenarios use `active_for_seconds` to backdate faults, so log history is immediately available after loading a scenario.

---

## Scenario format

```json
{
  "name": "PON-01 fiber loss",
  "description": "Human-readable description of what this simulates.",
  "faults": [
    {
      "node": "PON-01",
      "fault": "los",
      "active_for_seconds": 2400
    }
  ]
}
```

- `node` — node name (e.g. `"PON-01"`, `"ONT-02-03"`). PON names are valid even though they have no IP.
- `fault` — one of: `los`, `power off`, `hung`, `degraded`, `flapping`
- `active_for_seconds` — optional (default `0`). Backdates the fault so the node has log history.

---

## Ticket format

```json
{
  "scenario_name":  "PON-01 fiber loss",
  "alert_ids":      ["alert-PON-01-UNREACHABLE-10800"],
  "summary":        "PON-01 fiber cut — 4 ONTs offline",
  "root_causes": [
    {
      "node":               "PON-01",
      "fault":              "los",
      "affected_nodes":     ["ONT-01-01", "ONT-01-02", "ONT-01-03", "ONT-01-04"],
      "recommended_action": "dispatch_technician"
    }
  ],
  "evidence":   ["2026-10-14 02:20:00 ERROR pon: PON-01 (unknown): deregistered (reason: LOS)"],
  "confidence": "high"
}
```

`scenario_name` is optional but recommended — the evaluation harness uses it to match tickets to ground truth.

**Valid `recommended_action` values:**

| Value | When to use |
|---|---|
| `reboot_device` | Device is hung (frozen firmware) |
| `dispatch_technician` | Fiber cut or PON-level hardware fault |
| `check_cabinet_power` | PON or cabinet lost power |
| `contact_customer` | ONT lost power at customer premises |
| `inspect_fiber` | Degraded signal or flapping (physical inspection) |

---

## Available scenarios

| File | Fault(s) | Expected alerts | Agent challenge |
|---|---|---|---|
| `pon_los.json` | PON-01 LOS (40 min) | 5 CRITICAL | Fiber cut root cause |
| `pon_power_off.json` | PON-02 power off (15 min) | 5 CRITICAL | Distinguish dying gasp from LOS |
| `ont_power_off.json` | ONT-02-03 power off (25 min) | 1 CRITICAL | Customer premises power |
| `ont_hung.json` | ONT-01-03 hung (12 min) | 1 CRITICAL | OMCI silent → reboot |
| `ont_flapping.json` | ONT-02-02 flapping (55 min) | 0–1 WARNING | Intermittent reconnect pattern |
| `ont_degraded.json` | degraded + flapping + hung (mixed) | 1C + 3W | Three independent fault types |
| `cascading_degraded.json` | PON-01 degraded + 2 ONTs degraded | 14 WARNING | Common ancestor or independent? |
| `split_branch_failure.json` | PON-01 LOS + ONT-02-02 hung | 6 CRITICAL | Two unrelated root causes |
| `mass_power_outage.json` | Both PONs power off | 10 CRITICAL | Full network blackout |
| `noisy_network.json` | 4 ONTs with 4 different faults | 2C + 3W | High-volume triage |

---

## Evaluation harness

```bash
# List ground truth for all scenarios
python3 evaluate.py --list

# Score a specific ticket against a scenario
python3 evaluate.py --scenario scenarios/pon_los.json --ticket ticket-0001

# Batch report across all scenarios
python3 evaluate.py --report
```

Scoring dimensions (each weighted equally, overall 0.0–1.0):
1. **Node identification** — did the agent identify the correct root-cause node(s)?
2. **Fault classification** — did the agent name the correct fault type?
3. **Action recommendation** — did the agent choose the correct action?

---

## Project structure

```
.
├── main.py           — FastAPI app (all endpoints)
├── node.py           — Node and Clock classes (complete, do not modify)
├── topology.py       — Network graph, scenario loading, reset
├── alerts.py         — Alert generation engine
├── evaluate.py       — Evaluation harness
├── requirements.txt  — Pinned dependencies
└── scenarios/        — 10 JSON scenario files with ground truth
```
