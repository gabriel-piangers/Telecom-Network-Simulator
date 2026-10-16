from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from typing import Any
import math
import random

seed = 42
offline = ["los", "power off"]
BASE_TIME = datetime(2026, 10, 14, 0, 0, 0)   # simulated t=0


def clamp_pct(val) -> float:
    return round(max(0, min(100, val)), 2)


# --------------------------------------------------------------------------
# NEW: shared simulated time (seconds since BASE_TIME). One Clock per network.
# --------------------------------------------------------------------------
class Clock:
    def __init__(self, start: int = 0):
        self.t = start

    def advance(self, seconds: int) -> None:
        self.t += seconds

    def stamp(self, t: float | None = None) -> str:
        t = self.t if t is None else t
        return (BASE_TIME + timedelta(seconds=t)).strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------
# NEW: log templates. (level, source, message)
# --------------------------------------------------------------------------
# What the DEVICE ITSELF logs when a fault starts
FAULT_START_LOG = {
    "degraded": ("WARN", "optics", "Rx power below warning threshold (-27.0 dBm)"),
    "los": ("CRIT", "optics", "Loss of signal (LOS) detected on pon0"),
}
# What the device logs when the fault ends, keyed by the PREVIOUS fault
RECOVERY_LOG = {
    "degraded": ("INFO", "optics", "Rx power back within normal range"),
    "los": ("INFO", "optics", "LOS cleared, pon0 link up"),
    "power off": ("NOTICE", "system", "System boot (reset reason: power loss)"),
    "hung": ("NOTICE", "system", "System boot (reset reason: reboot)"),
    "flapping": ("INFO", "kernel", "pon0: link stable"),
}
# What the PARENT (e.g. the OLT) logs about this node. Note the clues:
# "dying gasp" = power failure, "LOS" = fiber, "OMCI timeout" = device alive but not responding
PARENT_VIEW = {
    "los": ("ERROR", "pon", "{n} ({ip}): deregistered (reason: LOS)"),
    "power off": ("WARN", "pon", "{n} ({ip}): deregistered (reason: dying gasp)"),
    "hung": ("WARN", "omci", "{n} ({ip}): OMCI keepalive timeout, no response"),
    "registered": ("INFO", "pon", "{n} ({ip}): registered"),
}
# Routine background noise: (weight, level, source, message). Formatted with random values.
BACKGROUND = {
    "ONT": [
        (4, "INFO", "dhcpd", "DHCP lease renewed for {ip}"),
        (4, "INFO", "ntp", "Time synchronized (offset {ms} ms)"),
        (3, "INFO", "optics", "Rx power {rx} dBm (normal)"),
        (2, "INFO", "wifi", "Client {mac} associated"),
        (1, "WARN", "ntp", "Server timeout, retrying"),            # harmless red herring
    ],
    "OLT": [
        (4, "INFO", "snmp", "Poll from 10.0.0.5 completed"),
        (3, "INFO", "ntp", "Time synchronized (offset {ms} ms)"),
        (2, "INFO", "aaa", "User admin logged in from 10.0.0.9"),
        (1, "WARN", "fan", "Fan 2 speed increased"),                # harmless red herring
    ],
    "PON": [
        (3, "INFO", "pon", "Port statistics collected"),
    ],
}


class Node:
    def __init__(self, name, type, plan_mbps=None, ip=None, ports=1, parent=None,
                 parent_port=None, clock: Clock | None = None):
        self.name = name
        self.type = type  # OLT, PON, ONT
        self.plan_mbps = plan_mbps
        self.ip = ip
        self.ports = ports
        self.parent = parent
        self.parent_port = parent_port
        self.children = [None] * ports
        self.fault = "normal"  # normal, los, power off, hung, degraded, flapping
        # NEW: children share the parent's clock automatically
        self.clock = clock if clock is not None else (parent.clock if parent else Clock())
        self.fault_history = [(0, "normal")]   # NEW: [(time, fault), ...] in time order
        self.extra = []                        # NEW: one-off logs (e.g. operator actions)
        if parent:
            if parent_port is None or not 0 <= parent_port < parent.ports:
                raise ValueError("Invalid parent port: {}".format(parent_port))
            if parent.children[parent_port] is not None:
                raise ValueError("Parent port {} is already occupied".format(parent_port))
            parent.children[parent_port] = self

    # ------------------------------------------------------------------ faults
    def set_fault(self, fault, at=None) -> None:
        """`at` lets you backdate: set_fault('degraded', at=clock.t - 2400) = 40 min ago."""
        if fault not in ["normal", "los", "power off", "hung", "degraded", "flapping"]:
            raise ValueError("Invalid fault type: {}".format(fault))
        t = self.clock.t if at is None else at
        if t < self.fault_history[-1][0]:
            raise ValueError("Faults must be set in time order")
        self.fault_history.append((t, fault))
        self.fault = fault

    def _fault_at(self, t) -> str:
        f = "normal"
        for ts, fault in self.fault_history:
            if ts <= t:
                f = fault
        return f

    def _segments(self, until):
        """[(start, end, fault)] built from the fault history."""
        h = self.fault_history
        return [(h[i][0], h[i + 1][0] if i + 1 < len(h) else until, h[i][1])
                for i in range(len(h))]

    def _flap_windows(self, a, b):
        """Down windows [(down_at, up_at)] for a flapping segment, deterministic from the seed."""
        rng = random.Random(f"{seed}-{self.name}-flap-{a}")
        t, out = a, []
        while True:
            t += rng.uniform(120, 480)      # stays up for 2-8 min
            if t >= b:
                return out
            down = rng.uniform(10, 60)      # stays down for 10-60 s
            out.append((t, t + down))
            t += down

    def _own_state(self) -> str:
        """The node's own state right now (flapping resolves to los/normal)."""
        if self.fault != "flapping":
            return self.fault
        now = self.clock.t
        start = self.fault_history[-1][0]
        down = any(d <= now < u for d, u in self._flap_windows(start, now + 1))
        return "los" if down else "normal"

    def status(self) -> str:
        res = self._own_state()
        node = self.parent
        while node:
            s = node._own_state()
            if s in offline:
                return "los"
            if res == "normal" and s == "degraded":
                res = "degraded"
            node = node.parent
        return res

    # ------------------------------------------------------------------- logs
    def add_log(self, level, source, msg, delay=0) -> None:
        """One-off log, e.g. an operator action. `delay` schedules it in the future."""
        self.extra.append((self.clock.t + delay, level, source, msg))

    def _buckets(self, tag, a, b, period):
        """Deterministic 'random' event times: one candidate per `period` seconds."""
        for k in range(int(a // period), int(b // period) + 1):
            rng = random.Random(f"{seed}-{self.name}-{tag}-{k}")
            t = k * period + rng.uniform(0, period)
            if a <= t < b:
                yield t, rng

    def descendants(self) -> list[Node]:
        out, stack = [], [c for c in self.children if c]
        while stack:
            n = stack.pop()
            out.append(n)
            stack.extend(c for c in n.children if c)
        return out

    def _own_logs(self, since, until):
        out = []
        # 1) routine background noise (not while powered off or hung)
        templates = BACKGROUND.get(self.type, BACKGROUND["ONT"])
        for t, rng in self._buckets("bg", since, until, 300):
            if self._fault_at(t) in ("power off", "hung") or rng.random() > 0.6:
                continue
            _, lvl, src, msg = rng.choices(templates, weights=[w[0] for w in templates])[0]
            mac = ":".join("%02x" % rng.randrange(256) for _ in range(3))
            out.append((t, lvl, src, msg.format(ip="192.168.1.%d" % rng.randint(2, 50),
                                                ms=rng.randint(1, 15),
                                                rx=round(rng.gauss(-22, 1), 1), mac=mac)))

        # 2) logs driven by the fault history
        segs = self._segments(until)
        for i, (a, b, fault) in enumerate(segs):
            prev = segs[i - 1][2] if i else "normal"
            if since <= a <= until:
                if fault in FAULT_START_LOG:
                    out.append((a,) + FAULT_START_LOG[fault])
                elif fault == "normal" and prev in RECOVERY_LOG:
                    out.append((a,) + RECOVERY_LOG[prev])
            if fault == "hung":   # warning signs just BEFORE it froze
                for dt, lvl, src, msg in [(240, "WARN", "system", "Memory usage at 94%"),
                                          (120, "ERROR", "system", "watchdog: process 'omcid' not responding")]:
                    if since <= a - dt <= until:
                        out.append((a - dt, lvl, src, msg))
            if fault == "degraded":   # precise, repeating degradation logs
                for t, rng in self._buckets("deg", max(a, since), min(b, until), 120):
                    if rng.random() < 0.7:
                        out.append((t, "WARN", "optics",
                                    f"Rx power {round(rng.gauss(-31, 1.5), 1)} dBm below threshold (-27.0 dBm)"))
                    if rng.random() < 0.4:   # counter that grows with time since the fault began
                        out.append((t + 1, "ERROR", "pon0",
                                    f"CRC errors: {int((t - a) * 0.8 + rng.uniform(0, 20))} since fault"))
            if fault == "flapping":   # repetitive reconnections
                for d, u in self._flap_windows(a, min(b, until)):
                    if since <= d <= until:
                        out.append((d, "ERROR", "kernel", "pon0: link down"))
                    if since <= u <= until:
                        out.append((u, "INFO", "kernel", "pon0: link up"))

        return out

    def _descendant_logs(self, since, until):
        """What this node logs about the nodes below it (the OLT sees its ONTs)."""
        out = []
        for d in self.descendants():
            segs = d._segments(until)
            for i, (a, b, fault) in enumerate(segs):
                prev = segs[i - 1][2] if i else "normal"
                ip_str = d.ip or "unknown"
                if since <= a <= until:
                    if fault in PARENT_VIEW and fault != "registered":
                        out.append((a,) + tuple(PARENT_VIEW[fault][:2]) + (PARENT_VIEW[fault][2].format(n=d.name, ip=ip_str),))
                    elif fault == "normal" and prev in ("los", "power off", "hung", "flapping"):
                        out.append((a,) + tuple(PARENT_VIEW["registered"][:2]) + (PARENT_VIEW["registered"][2].format(n=d.name, ip=ip_str),))
                if fault == "hung":   # repeated while it stays frozen
                    for t, _ in d._buckets("omci", max(a, since), min(b, until), 120):
                        out.append((t, "WARN", "omci", f"{d.name} ({ip_str}): OMCI timeout, retrying"))
                if fault == "flapping":
                    for dn, up in d._flap_windows(a, min(b, until)):
                        if since <= dn <= until:
                            out.append((dn, "ERROR", "pon", f"{d.name} ({ip_str}): deregistered (reason: LOS)"))
                        if since <= up <= until:
                            out.append((up, "INFO", "pon", f"{d.name} ({ip_str}): registered"))
        return out

    def get_logs(self, minutes=60) -> list[str] | None:
        """Log lines for the last `minutes`. None = device unreachable right now."""
        s = self.status()
        if s in offline or s == "hung":
            return None
        until = self.clock.t
        since = max(0, until - minutes * 60)
        # one-off logs (operator actions) go first so they sort before same-second events
        entries = [e for e in self.extra if since <= e[0] <= until]
        entries += self._own_logs(since, until)
        entries += self._descendant_logs(since, until)
        entries.sort(key=lambda e: e[0])   # stable: equal times keep insertion order
        return [f"{self.clock.stamp(t)} {lvl:<6} {src}: {msg}" for t, lvl, src, msg in entries]

    # ---------------------------------------------------------------- metrics
    def metrics(self) -> dict[str, Any] | dict[str, None]:
        # seeded by simulated time: same time -> same values, no hidden side effects
        rng = random.Random(f"{seed}-{self.name}-m-{self.clock.t}")
        s = self.status()
        if s in offline:
            return {"signal_dbm": None, "bandwidth_pct": None, "cpu_pct": None, "memory_pct": None}
        elif s == "normal":
            return {
                "signal_dbm": round(rng.gauss(-22, 1), 2),
                "bandwidth_pct": clamp_pct(rng.gauss(80, 10)),
                "cpu_pct": clamp_pct(rng.gauss(30, 10)),
                "memory_pct": clamp_pct(rng.gauss(40, 15)),
            }
        elif s == "degraded":
            return {
                "signal_dbm": round(rng.gauss(-31, 1.5), 2),
                "bandwidth_pct": clamp_pct(rng.gauss(30, 5)),
                "cpu_pct": clamp_pct(rng.gauss(50, 10)),
                "memory_pct": clamp_pct(rng.gauss(50, 15)),
            }
        elif s == "hung":
            return {
                "signal_dbm": round(rng.gauss(-22, 1), 2),
                "bandwidth_pct": clamp_pct(rng.gauss(10, 2)),
                "cpu_pct": None,
                "memory_pct": None,
            }
        raise ValueError("Unknown status: {}".format(s))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "plan_mbps": self.plan_mbps,
            "ip": self.ip,
            "ports": self.ports,
            "parent": self.parent.name if self.parent else None,
            "parent_port": self.parent_port,
            "children": [c.name if c else None for c in self.children],
            "status": self.status(),
            "metrics": self.metrics(),
        }

    # ---------------------------------------------------------------- actions
    def reboot(self) -> None:
        self.add_log("NOTICE", "system", "Reboot requested by operator")
        if self.fault == "hung":
            self.set_fault("normal")      # recovery log comes from the fault history
        else:
            self.add_log("NOTICE", "system", "System boot (reset reason: operator reboot)", delay=60)

    # ------------------------------------------------------------------- ping
    def ping(self, dst_ip) -> dict[str, Any] | dict[str, None]:
        latency = 0
        if dst_ip == "1.1.1.1":  # network/dns test
            path = self.ancestors()
            latency += 20
        else:
            path = self.path_to_ip(dst_ip)
        if path is None:
            result = self.get_ping("offline")
        else:
            latency += len(path)   # (fix: latency was computed but never passed on)
            states = [n._own_state() for n in [self] + path]
            if any(f in offline or f == "hung" for f in states):
                result = self.get_ping("offline")
            elif "degraded" in states:
                result = self.get_ping("degraded", latency)
            else:
                result = self.get_ping("normal", latency)
        self.clock.advance(10)   # 10 pings take ~10 s; remove if you prefer a manual clock
        return result

    def get_ping(self, status, latency=0) -> dict[str, Any] | dict[str, None]:
        rng = random.Random(f"{seed}-{self.name}-ping-{self.clock.t}")
        if status == "offline":
            return {"ping_max": None, "ping_avg": None, "ping_min": None, "loss_pct": 100}
        elif status == "degraded":
            rtts = [round(rng.lognormvariate(math.log(40 + latency), 0.5))
                    for _ in range(10) if rng.random() > 0.4]
        else:
            rtts = [round(rng.lognormvariate(math.log(1 + latency), 0.2))
                    for _ in range(10) if rng.random() > 0.01]
        if not rtts:
            return self.get_ping("offline")
        return {
            "ping_max": max(rtts),
            "ping_avg": round(sum(rtts) / len(rtts), 2),
            "ping_min": min(rtts),
            "loss_pct": 100 * (10 - len(rtts)) / 10,
        }

    def ancestors(self) -> list[Node]:
        out, node = [], self.parent
        while node:
            out.append(node)
            node = node.parent
        return out

    def path_to_ip(self, dst_ip) -> list[Node] | None:
        previous = {self: None}
        q = deque([self])
        tgt = None
        while q:
            cur = q.popleft()
            if cur.ip == dst_ip:
                tgt = cur
                break
            if cur.parent and cur.parent not in previous:
                previous[cur.parent] = cur
                q.append(cur.parent)
            for child in cur.children:
                if child and child not in previous:
                    previous[child] = cur
                    q.append(child)
        if not tgt:
            return None
        path = []
        while tgt != self:
            path.append(tgt)
            tgt = previous[tgt]
        path.reverse()
        return path


if __name__ == "__main__":
    clock = Clock(start=3 * 3600)   # scenario "now" = 03:00, so there are 3 h of history
    olt = Node("OLT-1", "OLT", ip="10.0.0.1", ports=2, clock=clock)
    pon = Node("PON-1", "PON", ports=4, parent=olt, parent_port=0)
    ont1 = Node("ONT-1", "ONT", ip="10.0.1.1", plan_mbps=500, parent=pon, parent_port=0)
    ont2 = Node("ONT-2", "ONT", ip="10.0.1.2", plan_mbps=300, parent=pon, parent_port=1)
    ont3 = Node("ONT-3", "ONT", ip="10.0.1.3", plan_mbps=300, parent=pon, parent_port=2)

    ont1.set_fault("degraded", at=clock.t - 40 * 60)   # degraded 40 min ago
    ont2.set_fault("flapping", at=clock.t - 50 * 60)   # flapping for 50 min
    ont3.set_fault("hung", at=clock.t - 8 * 60)        # hung 8 min ago

    def show(title, lines, limit=14):
        print(f"\n=== {title} ===")
        if lines is None:
            print("(device unreachable)")
            return
        for l in lines[-limit:]:
            print(l)

    show("ONT-1 (degraded) last 60 min", ont1.get_logs(60), 14)
    show("ONT-2 (flapping) last 60 min", ont2.get_logs(60), 14)
    show("ONT-3 (hung) logs", ont3.get_logs(60))
    show("OLT-1 logs about its ONTs (last 15 min)", olt.get_logs(15), 14)

    ont3.reboot()
    clock.advance(90)
    show("ONT-3 after reboot", ont3.get_logs(15), 10)