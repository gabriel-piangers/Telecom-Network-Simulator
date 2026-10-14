from typing import Any, Literal
from collections import deque

import random

seed = 42

class Node:
    def __init__(self, name, type, ip, ports=1, parent=None, parent_port=None):
        self.name = name
        self.type = type # OLT, PON, ONT
        self.ip = ip #string
        self.ports = ports
        self.parent = parent
        self.parent_port = parent_port
        self.children = [None] * ports
        self.fault = "normal" # normal, down, hung, degraded
        if parent:
            if (parent.children[parent_port] is not None):
                raise ValueError("Parent port {} is already occupied".format(parent_port))
            parent.children[parent_port] = self

    def set_fault(self, fault) -> None:
        if (fault not in ["normal", "down", "hung", "degraded"]):
            raise ValueError("Invalid fault type: {}".format(fault))
        self.fault = fault

    def status(self) -> Literal['offline', 'online']:
        node = self
        while node:
            if (node.fault == "down" or node.fault == "hung"):
                return "offline"
            node = node.parent
        return "online"

    def metrics(self) -> dict[str, Any] | dict[str, None]:
            rng = random.Random(seed + hash(self.name))
            if(self.status() == "offline"):
                return {
                    "signal_dbm": None, 
                    "bandwidth": None, 
                    "cpu_pct": None, 
                    "memory_pct": None
                    }
            elif(self.fault == "normal"):
                return {
                    "signal_dbm": rng.gauss(-22, 1),
                    "bandwidth": rng.gauss(80, 10),
                    "cpu_pct": rng.gauss(30, 10), 
                    "memory_pct": rng.gauss(40, 15)
                    }
            elif(self.fault == "degraded"):
                return {
                    "signal_dbm": rng.gauss(-31, 1.5), 
                    "bandwidth": rng.gauss(30, 5),
                    "cpu_pct": rng.gauss(50, 10), 
                    "memory_pct": rng.gauss(50, 15)
                    }
            elif(self.fault == "hung"):
                return {
                    "signal_dbm": rng.gauss(-22, 1), 
                    "bandwidth": rng.gauss(10, 2),
                    "cpu_pct": rng.gauss(10, 5), 
                    "memory_pct": rng.gauss(10, 5)}
            else:
                raise ValueError("Unknown fault: {}".format(self.fault))

    def restart(self) -> None:
        if(self.fault == "hung"):
            self.fault = "normal"

    def ping(self, dst_ip) -> dict[str, Any] | dict[str, None]:
        if(self.status() == "offline"):
            return self.get_ping("offline")
        elif(dst_ip == "1.1.1.1"): # network/dns test
            if(self.fault == "degraded"): return self.get_ping("degraded")
            else: return self.get_ping("normal")
        else:
            path = self.path_to_ip(dst_ip)
            if(path is  None): return self.get_ping("offline")
            elif("degraded" in [n.fault for n in path]): return self.get_ping("degraded")
            else: return self.get_ping("normal")

    def get_ping(self, status) -> dict[str, Any] | dict[str, None]:
        rng = random.Random(seed + hash(self.name))
        if(status == "offline"):
            return {
                "ping_max": None, 
                "ping_avg": None, 
                "ping_min": None, 
                "loss_pct": 100 
            }
        elif(status == "degraded"):
            ping_max = int(rng.gauss(200, 20))
            ping_min = int(rng.gauss(60, 15))
            return {
                "ping_max": ping_max,
                "ping_min": ping_min,
                "ping_avg": ping_max*0.3 + ping_min*0.7,
                "loss_pct": rng.gauss(40, 15)
            }
        else:
            ping_max = int(rng.gauss(30, 6))
            ping_min = int(rng.gauss(20, 3))
            return {
                "ping_max": ping_max,
                "ping_min": ping_min,
                "ping_avg": ping_max*0.2 +  ping_min*0.8,
                "loss_pct": 0
            }

    def path_to_ip(self, dst_ip) -> list[Node] | None:
        previous = {}
        q= deque()
        q.append(self)
        tgt = None
        while(q):
            cur = q.pop()
            if(cur.ip == dst_ip):
                tgt = cur
                break
            if(cur.parent and cur.parent not in previous):
                previous[cur.parent] = cur
                q.append(cur.parent)
            for child in cur.children:
                if(child and child not in previous):
                    previous[child] = cur
                    q.append(child)
        if(not tgt): return None
        path = []
        while(tgt != self):
            path.append(tgt)
            tgt = previous[tgt]
        path.reverse()
        return path


        
