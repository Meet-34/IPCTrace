"""
debugger.py — IPC Debugger Engine
Analyses logs + runtime state to detect:
  • Race conditions
  • Deadlocks
  • Bottlenecks / blocked processes
  • Data consistency issues
"""

import time
from typing import List, Dict, Tuple
from dataclasses import dataclass

from ipc_core import (
    IPCLogger, IPCType, LogLevel, LogEntry,
    Pipe, MessageQueue, SharedMemory
)
from process_sim import SimProcess, ProcessState


# ─────────────────────────────────────────────
#  Issue Taxonomy
# ─────────────────────────────────────────────

@dataclass
class DebugIssue:
    category:    str     # "DEADLOCK" | "RACE_CONDITION" | "BOTTLENECK" | "CONSISTENCY"
    severity:    str     # "CRITICAL" | "WARNING" | "INFO"
    description: str
    pids:        List[str]
    timestamp:   float

    def __str__(self):
        pid_str = ", ".join(self.pids) if self.pids else "N/A"
        ts = time.strftime('%H:%M:%S', time.localtime(self.timestamp))
        sev_icon = {"CRITICAL": "🔴", "WARNING": "🟡", "INFO": "🔵"}.get(self.severity, "⚪")
        return (f"[{ts}] {sev_icon} [{self.category}] {self.description} "
                f"| Procs: {pid_str}")


# ─────────────────────────────────────────────
#  Debugger
# ─────────────────────────────────────────────

class IPCDebugger:
    """
    Continuously polls process states + IPC objects + event log
    to detect synchronisation problems.
    """

    BLOCKED_THRESHOLD = 2.0    # seconds before labelling a process as "hung"
    FILL_WARN_RATIO   = 0.80   # pipe/queue fill ≥ 80% → bottleneck warning

    def __init__(self, logger: IPCLogger):
        self.logger  = logger
        self.issues: List[DebugIssue] = []

        # References populated by the scenario runner
        self.processes:      Dict[str, SimProcess]    = {}
        self.pipes:          Dict[str, Pipe]           = {}
        self.message_queues: Dict[str, MessageQueue]   = {}
        self.shared_memories:Dict[str, SharedMemory]   = {}

    # ── Registration ──────────────────────────────

    def register_process(self, p: SimProcess):
        self.processes[p.pid] = p

    def register_pipe(self, pipe: Pipe):
        self.pipes[pipe.name] = pipe

    def register_mq(self, mq: MessageQueue):
        self.message_queues[mq.name] = mq

    def register_shm(self, shm: SharedMemory):
        self.shared_memories[shm.name] = shm

    # ── Analysis Passes ───────────────────────────

    def _check_deadlocks(self) -> List[DebugIssue]:
        """
        Heuristic: ≥2 processes BLOCKED for > BLOCKED_THRESHOLD seconds
        while no process is RUNNING → likely deadlock.
        """
        found = []
        now = time.time()
        blocked_pids = []
        for pid, proc in self.processes.items():
            if proc.state == ProcessState.BLOCKED:
                since = proc._blocked_since or now
                if now - since > self.BLOCKED_THRESHOLD:
                    blocked_pids.append(pid)

        running_count = sum(
            1 for p in self.processes.values()
            if p.state == ProcessState.RUNNING
        )

        if len(blocked_pids) >= 2 and running_count == 0:
            found.append(DebugIssue(
                category    = "DEADLOCK",
                severity    = "CRITICAL",
                description = f"{len(blocked_pids)} processes mutually blocked — circular wait detected.",
                pids        = blocked_pids,
                timestamp   = now,
            ))

        # Also detect via log: look for consecutive DEADLOCK_DETECTED entries
        recent = self.logger.get_recent(30)
        dl_pids = [e.pid for e in recent if e.operation == "DEADLOCK_DETECTED"]
        if dl_pids:
            found.append(DebugIssue(
                category    = "DEADLOCK",
                severity    = "CRITICAL",
                description = "Deadlock confirmed via mutex timeout chain.",
                pids        = list(set(dl_pids)),
                timestamp   = now,
            ))
        return found

    def _check_race_conditions(self) -> List[DebugIssue]:
        """Detect UNSAFE_WRITE events in log."""
        found = []
        recent = self.logger.get_recent(50)
        race_entries = [e for e in recent if e.operation == "UNSAFE_WRITE"]
        if race_entries:
            pids = list({e.pid for e in race_entries})
            found.append(DebugIssue(
                category    = "RACE_CONDITION",
                severity    = "CRITICAL",
                description = (f"{len(race_entries)} unprotected write(s) to shared memory detected. "
                               "Data integrity compromised."),
                pids        = pids,
                timestamp   = time.time(),
            ))
        # Check shared memory objects directly
        for name, shm in self.shared_memories.items():
            if shm.race_detected:
                found.append(DebugIssue(
                    category    = "RACE_CONDITION",
                    severity    = "CRITICAL",
                    description = f"Shared memory '{name}' was written without mutex protection.",
                    pids        = [],
                    timestamp   = time.time(),
                ))
        return found

    def _check_bottlenecks(self) -> List[DebugIssue]:
        """High buffer fill ratios → bottleneck / queue overflow."""
        found = []
        now = time.time()

        for name, pipe in self.pipes.items():
            if pipe.fill_ratio >= self.FILL_WARN_RATIO:
                found.append(DebugIssue(
                    category    = "BOTTLENECK",
                    severity    = "WARNING",
                    description = (f"Pipe '{name}' at {pipe.fill_ratio*100:.0f}% capacity "
                                   f"({len(pipe._buffer)}/{pipe.capacity}). Producer outpacing consumer."),
                    pids        = [],
                    timestamp   = now,
                ))

        for name, mq in self.message_queues.items():
            fill = mq.size / mq.maxsize if mq.maxsize else 0
            if fill >= self.FILL_WARN_RATIO:
                found.append(DebugIssue(
                    category    = "BOTTLENECK",
                    severity    = "WARNING",
                    description = f"Message queue '{name}' at {fill*100:.0f}% capacity ({mq.size}/{mq.maxsize}).",
                    pids        = [],
                    timestamp   = now,
                ))
            if mq.overflow_count > 0:
                found.append(DebugIssue(
                    category    = "BOTTLENECK",
                    severity    = "CRITICAL",
                    description = f"Message queue '{name}' OVERFLOWED {mq.overflow_count} time(s). Messages lost.",
                    pids        = [],
                    timestamp   = now,
                ))

        return found

    def _check_consistency(self) -> List[DebugIssue]:
        """
        Look for READ_TIMEOUT / WRITE_BLOCKED patterns that suggest
        processes waiting on stale data or broken communication.
        """
        found = []
        recent = self.logger.get_recent(60)
        timeouts = [e for e in recent if "TIMEOUT" in e.operation or "BLOCKED" in e.operation]
        if len(timeouts) > 5:
            pids = list({e.pid for e in timeouts})
            found.append(DebugIssue(
                category    = "CONSISTENCY",
                severity    = "WARNING",
                description = f"{len(timeouts)} timeout/block events in recent log. Check synchronisation logic.",
                pids        = pids,
                timestamp   = time.time(),
            ))
        return found

    # ── Public API ────────────────────────────────

    def analyse(self) -> List[DebugIssue]:
        """Run all analysis passes and return deduplicated issues."""
        new_issues: List[DebugIssue] = []
        new_issues += self._check_deadlocks()
        new_issues += self._check_race_conditions()
        new_issues += self._check_bottlenecks()
        new_issues += self._check_consistency()
        self.issues = new_issues
        return new_issues

    def performance_metrics(self) -> Dict:
        """Compute throughput / latency metrics from log."""
        entries = self.logger.get_recent(200)
        if not entries:
            return {}

        span = entries[-1].timestamp - entries[0].timestamp if len(entries) > 1 else 1.0
        writes  = [e for e in entries if e.operation in ("WRITE", "SEND", "UNSAFE_WRITE")]
        reads   = [e for e in entries if e.operation in ("READ", "RECEIVE")]
        errors  = [e for e in entries if e.level in (LogLevel.ERROR, LogLevel.WARNING)]
        blocked = [e for e in entries if "BLOCKED" in e.operation or "TIMEOUT" in e.operation]

        throughput = (len(writes) + len(reads)) / max(span, 0.001)

        proc_ops = {}
        for e in entries:
            proc_ops.setdefault(e.pid, 0)
            proc_ops[e.pid] += 1

        return {
            "total_ops":    len(entries),
            "writes":       len(writes),
            "reads":        len(reads),
            "errors":       len(errors),
            "blocks":       len(blocked),
            "throughput_ops_per_sec": round(throughput, 2),
            "observation_span_sec":   round(span, 2),
            "ops_per_process":        proc_ops,
        }
