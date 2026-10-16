#!/usr/bin/env python3
"""
evaluate.py — Offline evaluation harness for the telecom network simulator.

Loads every scenario file, submits it to the running simulator, then scores
a ticket submitted by the agent against the scenario's ground-truth ``expected``
block.

Usage (two modes):

  1. Score a specific ticket against a scenario:
       python3 evaluate.py --scenario scenarios/pon_los.json --ticket ticket-0001

  2. Run a full batch report across all scenarios (assumes one ticket per
     scenario has already been submitted, matched by scenario name):
       python3 evaluate.py --report

  3. List ground truth for all scenarios without scoring:
       python3 evaluate.py --list

The harness scores each ticket on three dimensions:
  - root_cause_nodes   : did the agent identify the correct node(s)?
  - fault_types        : did the agent name the correct fault type for each?
  - recommended_actions: did the agent choose the correct action for each?

Each dimension contributes equally to an overall score (0.0 – 1.0).
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get(path: str) -> tuple[int, object]:
    try:
        with urllib.request.urlopen(f"{BASE}{path}") as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _post(path: str, body: dict | None = None, params: str = "") -> tuple[int, object]:
    url = f"{BASE}{path}{params}"
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_ticket(ticket: dict, expected: dict) -> dict:
    """Compare a submitted ticket against scenario ground truth.

    Returns a score dict with per-dimension results and an overall 0.0–1.0 score.
    """
    exp_causes = expected.get("root_causes", [])
    got_causes = ticket.get("root_causes", [])

    exp_nodes   = {rc["node"] for rc in exp_causes}
    exp_faults  = {rc["node"]: rc["fault"] for rc in exp_causes}
    exp_actions = {rc["node"]: rc["recommended_action"] for rc in exp_causes}

    got_nodes   = {rc["node"] for rc in got_causes}
    got_faults  = {rc["node"]: rc.get("fault") for rc in got_causes}
    got_actions = {rc["node"]: rc.get("recommended_action") for rc in got_causes}

    # --- node identification ---
    correct_nodes = exp_nodes & got_nodes
    false_pos     = got_nodes - exp_nodes
    false_neg     = exp_nodes - got_nodes
    node_score    = len(correct_nodes) / max(len(exp_nodes), 1)

    # --- fault type (only for correctly identified nodes) ---
    fault_matches = sum(
        1 for n in correct_nodes if got_faults.get(n) == exp_faults.get(n)
    )
    fault_score = fault_matches / max(len(correct_nodes), 1) if correct_nodes else 0.0

    # --- recommended action (only for correctly identified nodes) ---
    action_matches = sum(
        1 for n in correct_nodes if got_actions.get(n) == exp_actions.get(n)
    )
    action_score = action_matches / max(len(correct_nodes), 1) if correct_nodes else 0.0

    overall = (node_score + fault_score + action_score) / 3

    return {
        "overall": round(overall, 3),
        "node_identification": {
            "score": round(node_score, 3),
            "correct": sorted(correct_nodes),
            "false_positives": sorted(false_pos),
            "false_negatives": sorted(false_neg),
        },
        "fault_classification": {
            "score": round(fault_score, 3),
            "details": {
                n: {"expected": exp_faults[n], "got": got_faults.get(n)}
                for n in correct_nodes
            },
        },
        "action_recommendation": {
            "score": round(action_score, 3),
            "details": {
                n: {"expected": exp_actions[n], "got": got_actions.get(n)}
                for n in correct_nodes
            },
        },
    }


# ---------------------------------------------------------------------------
# CLI modes
# ---------------------------------------------------------------------------

def cmd_score(scenario_path: str, ticket_id: str) -> None:
    """Score a single ticket against a scenario file."""
    with open(scenario_path) as f:
        scenario = json.load(f)

    expected = scenario.get("expected")
    if not expected:
        print(f"ERROR: scenario '{scenario_path}' has no 'expected' block.")
        sys.exit(1)

    s, ticket = _get(f"/tickets/{ticket_id}")
    if s != 200:
        print(f"ERROR: ticket '{ticket_id}' not found ({s}).")
        sys.exit(1)

    result = score_ticket(ticket, expected)
    print(f"\nScenario : {scenario['name']}")
    print(f"Ticket   : {ticket_id}")
    print(f"Overall  : {result['overall']:.1%}")
    print(json.dumps(result, indent=2))


def cmd_report() -> None:
    """Print a report of all tickets scored against matched scenarios."""
    import os

    s, tickets = _get("/tickets")
    if s != 200 or not tickets:
        print("No tickets found. Submit tickets via POST /tickets first.")
        sys.exit(0)

    scenario_files = sorted(
        os.path.join("scenarios", f)
        for f in os.listdir("scenarios")
        if f.endswith(".json")
    )

    rows = []
    for path in scenario_files:
        with open(path) as f:
            scenario = json.load(f)
        expected = scenario.get("expected")
        if not expected:
            continue
        # Prefer exact match on scenario_name field; fall back to summary substring
        match = next(
            (t for t in reversed(tickets)
             if t.get("scenario_name") == scenario["name"]),
            None,
        )
        if match is None:
            match = next(
                (t for t in reversed(tickets)
                 if scenario["name"].lower() in t.get("summary", "").lower()),
                None,
            )
        if not match:
            rows.append((scenario["name"], "—", "no ticket found"))
            continue
        result = score_ticket(match, expected)
        rows.append((scenario["name"], match["id"], f"{result['overall']:.1%}"))

    print(f"\n{'Scenario':<40} {'Ticket':<14} {'Score'}")
    print("─" * 62)
    for name, tid, score in rows:
        print(f"{name:<40} {tid:<14} {score}")


def cmd_list() -> None:
    """Print ground truth for all scenarios."""
    import os
    for fname in sorted(os.listdir("scenarios")):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join("scenarios", fname)) as f:
            scenario = json.load(f)
        expected = scenario.get("expected", {})
        print(f"\n{scenario['name']} ({fname})")
        for rc in expected.get("root_causes", []):
            aff = ", ".join(rc.get("affected_nodes", [])) or "none"
            print(f"  root cause : {rc['node']} — {rc['fault']}")
            print(f"  affected   : {aff}")
            print(f"  action     : {rc['recommended_action']}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate agent tickets against scenario ground truth.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scenario", metavar="FILE", help="Path to scenario JSON file")
    group.add_argument("--report",   action="store_true", help="Print full batch report")
    group.add_argument("--list",     action="store_true", help="List ground truth for all scenarios")
    parser.add_argument("--ticket",  metavar="ID", help="Ticket ID to score (required with --scenario)")
    args = parser.parse_args()

    if args.scenario:
        if not args.ticket:
            parser.error("--ticket ID is required when using --scenario")
        cmd_score(args.scenario, args.ticket)
    elif args.report:
        cmd_report()
    elif args.list:
        cmd_list()
