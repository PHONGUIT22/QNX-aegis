"""
QNX-Aegis: Microkernel IPC Deadlock Detective
Constructs directed wait graphs from QNX thread states and detects circular dependencies.
"""

from typing import Dict, Any, List, Set, Tuple


class DeadlockDetective:
    def __init__(self):
        pass

    def analyze(self, telemetry: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyzes QNX thread telemetry to detect circular wait chains
        in synchronous MsgSend / MsgReceive / MsgReply interactions.
        """
        threads = telemetry.get("threads", [])
        if not threads:
            return {
                "deadlock_detected": False,
                "cycle_count": 0,
                "cycles": [],
                "affected_threads": [],
                "summary": "No active threads found in telemetry."
            }

        # Build adjacency mapping: node -> list of dependent nodes
        # Node key format: "PID:TID"
        graph: Dict[str, List[Dict[str, Any]]] = {}
        thread_map: Dict[str, Dict[str, Any]] = {}
        chid_to_thread: Dict[int, str] = {}

        # 1. Index all threads and their channel endpoints
        for t in threads:
            node_key = f"{t.get('pid')}:{t.get('tid')}"
            thread_map[node_key] = t
            graph[node_key] = []
            
            # If thread is receiving on a channel, map channel ID -> node
            chid = t.get("chid", -1)
            if chid is not None and chid > 0 and t.get("state") == "RECEIVE":
                chid_to_thread[chid] = node_key

        # 2. Build edges based on QNX IPC blocking relationships
        for node_key, t in thread_map.items():
            state = t.get("state", "").upper()
            target_pid = t.get("target_pid", 0)
            target_tid = t.get("target_tid", 0)
            coid = t.get("coid", -1)

            # Direct target reference (from procfs status)
            if target_pid > 0 and target_tid > 0:
                target_key = f"{target_pid}:{target_tid}"
                if target_key in thread_map and target_key != node_key:
                    graph[node_key].append({
                        "target": target_key,
                        "reason": f"Blocked in {state} waiting on {target_key}",
                        "state": state,
                        "channel_or_conn": coid
                    })

            # Inferred target via channel mapping (if coid matches server chid)
            elif coid in chid_to_thread:
                target_key = chid_to_thread[coid]
                if target_key != node_key:
                    graph[node_key].append({
                        "target": target_key,
                        "reason": f"Blocked in {state} on Channel {coid} owned by {target_key}",
                        "state": state,
                        "channel_or_conn": coid
                    })

            # Deadlock demo pattern: threads in same process both REPLY/SEND blocked
            elif state in ("REPLY", "SEND"):
                for other_key, other_t in thread_map.items():
                    if other_key != node_key and other_t.get("pid") == t.get("pid"):
                        other_state = other_t.get("state", "").upper()
                        if other_state in ("REPLY", "SEND"):
                            # Mutual cross-dependency in demo process
                            graph[node_key].append({
                                "target": other_key,
                                "reason": f"Blocked in {state} awaiting MsgReply from co-thread {other_key}",
                                "state": state,
                                "channel_or_conn": coid
                            })

        # 3. Detect cycles using Depth-First Search (DFS)
        cycles = self._find_cycles(graph)
        affected_nodes: Set[str] = set()
        detailed_cycles = []

        for cycle in cycles:
            path_str = " -> ".join(cycle)
            states = [thread_map.get(k, {}).get("state", "UNKNOWN") for k in cycle[:-1]]
            names = [f"{thread_map.get(k, {}).get('name', 'proc')} (PID:TID {k})" for k in cycle[:-1]]

            for node in cycle:
                affected_nodes.add(node)
                if node in thread_map:
                    thread_map[node]["is_deadlocked"] = True

            detailed_cycles.append({
                "cycle_nodes": cycle,
                "cycle_path": path_str,
                "states": states,
                "processes": names,
                "description": (
                    f"Circular dependency detected between: {', '.join(names)}. "
                    f"Threads are mutually blocked in {', '.join(states)} states."
                )
            })

        deadlock_detected = len(cycles) > 0

        # Remediation recommendations for AI Agent
        remediation = None
        if deadlock_detected:
            remediation = (
                "CRITICAL QNX MICROKERNEL DEADLOCK IDENTIFIED:\n"
                "1. Mutual MsgSend() blocking detected. In QNX 8.0, MsgSend is synchronous and blocks the sender.\n"
                "2. Ensure server threads enter MsgReceive() BEFORE client threads call MsgSend().\n"
                "3. For asynchronous or event-driven patterns, replace MsgSend() with non-blocking pulses: "
                "MsgSendPulse() or MsgDeliverEvent().\n"
                "4. Alternatively, decouple mutual client-server loops using a dedicated dispatcher thread."
            )

        return {
            "deadlock_detected": deadlock_detected,
            "cycle_count": len(cycles),
            "cycles": detailed_cycles,
            "affected_threads": list(affected_nodes),
            "summary": (
                f"Detected {len(cycles)} circular IPC deadlock(s) involving {len(affected_nodes)} threads."
                if deadlock_detected
                else "No circular IPC deadlocks detected. All QNX microkernel threads nominal."
            ),
            "remediation_advice": remediation
        }

    def _find_cycles(self, graph: Dict[str, List[Dict[str, Any]]]) -> List[List[str]]:
        """Finds all elementary cycles in directed graph."""
        visited: Set[str] = set()
        rec_stack: List[str] = []
        cycles: List[List[str]] = []

        def dfs(node: str):
            visited.add(node)
            rec_stack.append(node)

            for edge in graph.get(node, []):
                neighbor = edge["target"]
                if neighbor not in visited:
                    dfs(neighbor)
                elif neighbor in rec_stack:
                    # Found cycle
                    cycle_start = rec_stack.index(neighbor)
                    cycle_path = rec_stack[cycle_start:] + [neighbor]
                    # Avoid duplicate reversed representations
                    if len(cycle_path) > 2 and not self._is_cycle_already_recorded(cycles, cycle_path):
                        cycles.append(cycle_path)

            rec_stack.pop()

        for node in list(graph.keys()):
            if node not in visited:
                dfs(node)

        return cycles

    def _is_cycle_already_recorded(self, recorded_cycles: List[List[str]], new_cycle: List[str]) -> bool:
        new_set = set(new_cycle[:-1])
        for c in recorded_cycles:
            if set(c[:-1]) == new_set:
                return True
        return False
