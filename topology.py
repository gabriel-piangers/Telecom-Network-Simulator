from node import Clock, Node


class Topology:
    def __init__(self):
        self.clock = Clock(start=3 * 3600)

        # --- build the GPON tree ---
        # OLT — 2 PON ports
        olt = Node("OLT-01", "OLT", ip="10.0.0.1", ports=2, clock=self.clock)

        # PON branches — 4 ONT ports each (clock inherited)
        pon1 = Node("PON-01", "PON", ip="10.0.1.0", ports=4, parent=olt, parent_port=0)
        pon2 = Node("PON-02", "PON", ip="10.0.2.0", ports=4, parent=olt, parent_port=1)

        # ONTs under PON-01
        ont_01_01 = Node("ONT-01-01", "ONT", ip="10.0.1.1", plan_mbps=600, parent=pon1, parent_port=0)
        ont_01_02 = Node("ONT-01-02", "ONT", ip="10.0.1.2", plan_mbps=300, parent=pon1, parent_port=1)
        ont_01_03 = Node("ONT-01-03", "ONT", ip="10.0.1.3", plan_mbps=300, parent=pon1, parent_port=2)
        ont_01_04 = Node("ONT-01-04", "ONT", ip="10.0.1.4", plan_mbps=100, parent=pon1, parent_port=3)

        # ONTs under PON-02
        ont_02_01 = Node("ONT-02-01", "ONT", ip="10.0.2.1", plan_mbps=600, parent=pon2, parent_port=0)
        ont_02_02 = Node("ONT-02-02", "ONT", ip="10.0.2.2", plan_mbps=300, parent=pon2, parent_port=1)
        ont_02_03 = Node("ONT-02-03", "ONT", ip="10.0.2.3", plan_mbps=300, parent=pon2, parent_port=2)
        ont_02_04 = Node("ONT-02-04", "ONT", ip="10.0.2.4", plan_mbps=100, parent=pon2, parent_port=3)

        self._nodes: dict[str, Node] = {
            n.name: n for n in [
                olt,
                pon1, pon2,
                ont_01_01, ont_01_02, ont_01_03, ont_01_04,
                ont_02_01, ont_02_02, ont_02_03, ont_02_04,
            ]
        }

    # ------------------------------------------------------------------ lookups

    def get_node(self, name: str) -> Node:
        """Return the node with the given name, or raise KeyError."""
        return self._nodes[name]

    def all_nodes(self) -> list[Node]:
        return list(self._nodes.values())

    def nodes_by_type(self, type: str) -> list[Node]:
        return [n for n in self._nodes.values() if n.type == type]

    # ---------------------------------------------------------------- scenarios

    def apply_scenario(self, scenario: dict) -> None:
        """Reset the network then apply the faults described in *scenario*.

        Scenario format::

            {
                "name": "...",
                "description": "...",
                "faults": [
                    {"node": "PON-01", "fault": "los", "active_for_seconds": 2400}
                ]
            }

        ``active_for_seconds`` is optional (default 0). When set the fault is
        backdated so the node already has log history when the scenario starts.
        """
        self.reset()
        for entry in scenario.get("faults", []):
            node_name = entry["node"]
            fault = entry["fault"]
            backdate = entry.get("active_for_seconds", 0)
            node = self._nodes[node_name]   # raises KeyError for unknown names
            node.set_fault(fault, at=self.clock.t - backdate)

    def reset(self) -> None:
        """Set all nodes back to fault='normal' at the current clock time."""
        for node in self._nodes.values():
            if node.fault != "normal":
                node.set_fault("normal")
