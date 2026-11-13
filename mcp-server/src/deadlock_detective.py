"""
QNX-Aegis: Microkernel IPC Deadlock Detective
Constructs directed wait graphs from QNX thread states and detects circular dependencies.
"""

from typing import Dict, Any, List, Optional, Set, Tuple


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
                "priority_inversions": [],
                "priority_inversion_count": 0,
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

        # 4. Priority Inversion Audit
        priority_inversions = self._detect_priority_inversions(threads, thread_map, chid_to_thread)

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
        elif priority_inversions:
            remediation = (
                "QNX PRIORITY INVERSION WARNING:\n"
                "1. High-priority real-time thread (prio >= 15) is blocked on a lower-priority IPC server or resource without active priority inheritance.\n"
                "2. Ensure channels do NOT set _NTO_CHF_FIXED_PRIORITY so QNX Neutrino propagates client priority to MsgReceive() worker threads.\n"
                "3. For mutex contention, initialize attributes with pthread_mutexattr_setprotocol(&attr, PTHREAD_PRIO_INHERIT)."
            )

        summary_text = (
            f"Detected {len(cycles)} circular IPC deadlock(s) involving {len(affected_nodes)} threads."
            if deadlock_detected
            else "No circular IPC deadlocks detected. All QNX microkernel threads nominal."
        )
        if not deadlock_detected and priority_inversions:
            summary_text = (
                f"No circular IPC deadlocks detected, but {len(priority_inversions)} "
                f"priority inversion risk(s) identified."
            )

        return {
            "deadlock_detected": deadlock_detected,
            "cycle_count": len(cycles),
            "cycles": detailed_cycles,
            "affected_threads": list(affected_nodes),
            "priority_inversions": priority_inversions,
            "priority_inversion_count": len(priority_inversions),
            "summary": summary_text,
            "remediation_advice": remediation
        }

    def _detect_priority_inversions(
        self,
        threads: List[Dict[str, Any]],
        thread_map: Dict[str, Dict[str, Any]],
        chid_to_thread: Dict[int, str],
    ) -> List[Dict[str, Any]]:
        """
        Detects priority inversions where a high-priority client thread (priority >= 15)
        is blocked in SEND/REPLY targeting a lower-priority server thread without active
        priority inheritance, or where multiple threads contend with inverted priorities.
        """
        pid_to_threads: Dict[int, List[Dict[str, Any]]] = {}
        server_clients: Dict[str, List[Dict[str, Any]]] = {}
        for t in threads:
            pid = int(t.get("pid") or 0)
            pid_to_threads.setdefault(pid, []).append(t)

        inversions: List[Dict[str, Any]] = []
        seen_pairs: Set[Tuple[str, str]] = set()

        def is_kernel_idle(thr: Dict[str, Any]) -> bool:
            return int(thr.get("pid") or 0) == 1 and int(thr.get("priority") or 0) == 0

        def record_inversion(client_t: Dict[str, Any], srv_t: Dict[str, Any], channel_ref: Any, kind: str) -> None:
            c_key = f"{client_t.get('pid')}:{client_t.get('tid')}"
            s_key = f"{srv_t.get('pid')}:{srv_t.get('tid')}"
            if c_key == s_key or (c_key, s_key) in seen_pairs:
                return
            c_prio = int(client_t.get("priority") or 0)
            s_prio = int(srv_t.get("priority") or 0)
            if s_prio >= c_prio:
                return
            seen_pairs.add((c_key, s_key))
            delta = c_prio - s_prio
            c_name = client_t.get("name", "proc")
            s_name = srv_t.get("name", "proc")
            c_state = (client_t.get("state") or "UNKNOWN").upper()
            s_state = (srv_t.get("state") or "UNKNOWN").upper()
            inversions.append({
                "type": kind,
                "client": c_key,
                "client_name": c_name,
                "client_priority": c_prio,
                "client_state": c_state,
                "server": s_key,
                "server_name": s_name,
                "server_priority": s_prio,
                "server_state": s_state,
                "priority_delta": delta,
                "channel_or_conn": channel_ref,
                "severity": "HIGH" if delta >= 10 else "MEDIUM",
                "description": (
                    f"{kind}: High-prio {c_name} ({c_key}, prio={c_prio}) blocked in {c_state} "
                    f"on lower-prio {s_name} ({s_key}, prio={s_prio}, state={s_state}) without active Priority Inheritance."
                )
            })

        def resolve_server_thread(t: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            c_key = f"{t.get('pid')}:{t.get('tid')}"
            c_prio = int(t.get("priority") or 0)
            target_pid = int(t.get("target_pid") or 0)
            target_tid = int(t.get("target_tid") or 0)
            coid = int(t.get("coid") or -1)

            if target_pid > 0 and target_tid > 0:
                cand = thread_map.get(f"{target_pid}:{target_tid}")
                if cand and f"{cand.get('pid')}:{cand.get('tid')}" != c_key and not is_kernel_idle(cand):
                    return cand

            if coid > 0 and coid in chid_to_thread:
                cand = thread_map.get(chid_to_thread[coid])
                if cand and f"{cand.get('pid')}:{cand.get('tid')}" != c_key and not is_kernel_idle(cand):
                    return cand

            if target_pid > 0 and target_pid in pid_to_threads:
                candidates = [
                    x for x in pid_to_threads[target_pid]
                    if f"{x.get('pid')}:{x.get('tid')}" != c_key and not is_kernel_idle(x)
                ]
                if not candidates:
                    return None
                # If a receiving/active server thread already inherited >= client priority, PI is active
                active_states = {"RECEIVE", "RUNNING", "READY", "MUTEX", "CONDVAR", "SEM"}
                if any(int(x.get("priority") or 0) >= c_prio and (x.get("state") or "").upper() in active_states for x in candidates):
                    return None
                recv_cands = [x for x in candidates if (x.get("state") or "").upper() == "RECEIVE"]
                if recv_cands:
                    return max(recv_cands, key=lambda x: int(x.get("priority") or 0))
            return None

        # Pass 1: Direct IPC client -> lower-priority server inversion & group contention tracking
        for t in threads:
            state = (t.get("state") or "").upper()
            prio = int(t.get("priority") or 0)
            coid = int(t.get("coid") or -1)
            target_pid = int(t.get("target_pid") or 0)

            if state not in ("SEND", "REPLY"):
                continue

            srv = resolve_server_thread(t)
            channel_ref = coid if coid > 0 else target_pid

            if srv is not None:
                s_key = f"{srv.get('pid')}:{srv.get('tid')}"
                server_clients.setdefault(s_key, []).append(t)
                if prio >= 15 and int(srv.get("priority") or 0) < prio:
                    record_inversion(t, srv, channel_ref, "Priority Inversion (IPC)")
            elif target_pid > 0 or coid > 0:
                group_key = f"chan:{coid}" if coid > 0 else f"pid:{target_pid}"
                server_clients.setdefault(group_key, []).append(t)

        # Pass 2: Contention between multiple threads targeting the same server / channel with inverted priorities
        for target_key, clients in server_clients.items():
            if len(clients) < 2:
                continue
            high_clients = [c for c in clients if int(c.get("priority") or 0) >= 15]
            if not high_clients:
                continue
            top_high = max(high_clients, key=lambda c: int(c.get("priority") or 0))
            top_prio = int(top_high.get("priority") or 0)
            low_contenders = [c for c in clients if int(c.get("priority") or 0) < top_prio]
            for low_c in low_contenders:
                if (low_c.get("state") or "").upper() == "REPLY" or target_key in thread_map:
                    ref = top_high.get("coid") if int(top_high.get("coid") or -1) > 0 else top_high.get("target_pid")
                    record_inversion(top_high, low_c, ref, "Priority Contention Inversion")

        return inversions

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
