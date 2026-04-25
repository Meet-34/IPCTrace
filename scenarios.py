"""
scenarios.py — Predefined IPC test scenarios
Each scenario constructs processes + IPC objects and returns them ready to run.
"""

import threading
import time
from typing import List, Tuple, Dict

from ipc_core import IPCLogger, Pipe, MessageQueue, SharedMemory
from process_sim import (
    ProducerProcess, ConsumerProcess,
    SenderProcess, ReceiverProcess,
    SharedMemoryWriter, SharedMemoryReader,
    DeadlockProcess, SimProcess
)
from debugger import IPCDebugger


ScenarioResult = Tuple[List[SimProcess], IPCDebugger]


# ─────────────────────────────────────────────
#  Helper
# ─────────────────────────────────────────────

def _make_debugger(logger, processes=(), pipes=(), mqs=(), shms=()) -> IPCDebugger:
    dbg = IPCDebugger(logger)
    for p  in processes: dbg.register_process(p)
    for pi in pipes:     dbg.register_pipe(pi)
    for mq in mqs:       dbg.register_mq(mq)
    for sm in shms:      dbg.register_shm(sm)
    return dbg


# ─────────────────────────────────────────────
#  Scenario 1 — Proper Pipe Synchronisation
# ─────────────────────────────────────────────

def scenario_pipe_sync(logger: IPCLogger, step_mode=False,
                       step_event=None) -> ScenarioResult:
    """
    3 producers → shared pipe → 2 consumers.
    Correctly synchronised; should run cleanly.
    """
    pipe = Pipe("main_pipe", capacity=6, logger=logger)
    kw   = dict(step_mode=step_mode, step_event=step_event)

    procs = [
        ProducerProcess("Producer-A", pipe, logger, items=6, delay=0.2, **kw),
        ProducerProcess("Producer-B", pipe, logger, items=6, delay=0.3, **kw),
        ProducerProcess("Producer-C", pipe, logger, items=4, delay=0.25, **kw),
        ConsumerProcess("Consumer-X", pipe, logger, items=8, delay=0.35, **kw),
        ConsumerProcess("Consumer-Y", pipe, logger, items=8, delay=0.4,  **kw),
    ]
    dbg = _make_debugger(logger, procs, pipes=[pipe])
    return procs, dbg


# ─────────────────────────────────────────────
#  Scenario 2 — Message Queue Normal Operation
# ─────────────────────────────────────────────

def scenario_message_queue(logger: IPCLogger, step_mode=False,
                            step_event=None) -> ScenarioResult:
    """
    2 senders → message queue → 2 receivers.
    Normal throughput; queue sized comfortably.
    """
    mq = MessageQueue("task_queue", maxsize=12, logger=logger)
    kw = dict(step_mode=step_mode, step_event=step_event)

    receivers = ["Receiver-1", "Receiver-2"]
    procs = [
        SenderProcess  ("Sender-1",   mq, receivers, logger, msgs=8, delay=0.2, **kw),
        SenderProcess  ("Sender-2",   mq, receivers, logger, msgs=8, delay=0.25, **kw),
        ReceiverProcess("Receiver-1", mq,            logger, msgs=8, delay=0.3,  **kw),
        ReceiverProcess("Receiver-2", mq,            logger, msgs=8, delay=0.35, **kw),
    ]
    dbg = _make_debugger(logger, procs, mqs=[mq])
    return procs, dbg


# ─────────────────────────────────────────────
#  Scenario 3 — Shared Memory (Safe)
# ─────────────────────────────────────────────

def scenario_shared_memory_safe(logger: IPCLogger, step_mode=False,
                                 step_event=None) -> ScenarioResult:
    """
    3 writers (mutex-protected) + 2 readers (semaphore).
    No race conditions — illustrates correct usage.
    """
    shm = SharedMemory("shared_counter", logger=logger)
    kw  = dict(step_mode=step_mode, step_event=step_event)

    procs = [
        SharedMemoryWriter("Writer-A", shm, logger, ops=6, safe=True, delay=0.2, **kw),
        SharedMemoryWriter("Writer-B", shm, logger, ops=6, safe=True, delay=0.25, **kw),
        SharedMemoryWriter("Writer-C", shm, logger, ops=4, safe=True, delay=0.3,  **kw),
        SharedMemoryReader("Reader-1", shm, logger, ops=10, delay=0.15, **kw),
        SharedMemoryReader("Reader-2", shm, logger, ops=10, delay=0.18, **kw),
    ]
    dbg = _make_debugger(logger, procs, shms=[shm])
    return procs, dbg


# ─────────────────────────────────────────────
#  Scenario 4 — Race Condition (UNSAFE writes)
# ─────────────────────────────────────────────

def scenario_race_condition(logger: IPCLogger, step_mode=False,
                             step_event=None) -> ScenarioResult:
    """
    Multiple processes write to shared memory WITHOUT a lock.
    Demonstrates race conditions and data corruption.
    """
    shm = SharedMemory("race_memory", logger=logger)
    kw  = dict(step_mode=step_mode, step_event=step_event)

    procs = [
        SharedMemoryWriter("Racer-1", shm, logger, ops=8, safe=False, delay=0.1, **kw),
        SharedMemoryWriter("Racer-2", shm, logger, ops=8, safe=False, delay=0.1, **kw),
        SharedMemoryWriter("Racer-3", shm, logger, ops=8, safe=False, delay=0.1, **kw),
        SharedMemoryReader("Reader-R", shm, logger, ops=12, delay=0.08, **kw),
    ]
    dbg = _make_debugger(logger, procs, shms=[shm])
    return procs, dbg


# ─────────────────────────────────────────────
#  Scenario 5 — Deadlock
# ─────────────────────────────────────────────

def scenario_deadlock(logger: IPCLogger, step_mode=False,
                      step_event=None) -> ScenarioResult:
    """
    Classic two-resource deadlock:
      Process-A acquires Lock-1, then tries Lock-2.
      Process-B acquires Lock-2, then tries Lock-1.
    → Circular wait → deadlock.
    """
    res_lock1 = SharedMemory("Resource-LOCK1", logger=logger)
    res_lock2 = SharedMemory("Resource-LOCK2", logger=logger)
    kw = dict(step_mode=step_mode, step_event=step_event)

    proc_a = DeadlockProcess("DeadProc-A", res_lock1, res_lock2, logger, **kw)
    proc_b = DeadlockProcess("DeadProc-B", res_lock2, res_lock1, logger, **kw)
    procs = [proc_a, proc_b]
    dbg = _make_debugger(logger, procs, shms=[res_lock1, res_lock2])
    return procs, dbg


# ─────────────────────────────────────────────
#  Scenario 6 — Bottleneck (Queue Overflow)
# ─────────────────────────────────────────────

def scenario_bottleneck(logger: IPCLogger, step_mode=False,
                         step_event=None) -> ScenarioResult:
    """
    Fast producers flood a tiny queue faster than slow consumers drain it.
    Triggers overflow detection.
    """
    mq = MessageQueue("tiny_queue", maxsize=4, logger=logger)
    kw = dict(step_mode=step_mode, step_event=step_event)

    receivers = ["SlowReceiver-1"]
    procs = [
        SenderProcess  ("FastSender-1", mq, receivers, logger, msgs=10, delay=0.05, **kw),
        SenderProcess  ("FastSender-2", mq, receivers, logger, msgs=10, delay=0.05, **kw),
        SenderProcess  ("FastSender-3", mq, receivers, logger, msgs=10, delay=0.05, **kw),
        ReceiverProcess("SlowReceiver-1", mq,          logger, msgs=10, delay=1.5,  **kw),
    ]
    dbg = _make_debugger(logger, procs, mqs=[mq])
    return procs, dbg


# ─────────────────────────────────────────────
#  Registry
# ─────────────────────────────────────────────

SCENARIOS: Dict[str, dict] = {
    "1": {
        "name":        "Pipe — Proper Synchronisation",
        "description": "3 producers + 2 consumers over a shared pipe. Clean run, no issues.",
        "fn":          scenario_pipe_sync,
        "duration":    5,
    },
    "2": {
        "name":        "Message Queue — Normal Operation",
        "description": "2 senders + 2 receivers via FIFO message queue.",
        "fn":          scenario_message_queue,
        "duration":    5,
    },
    "3": {
        "name":        "Shared Memory — Safe (Mutex + Semaphore)",
        "description": "3 writers (mutex) + 2 readers (semaphore). No race conditions.",
        "fn":          scenario_shared_memory_safe,
        "duration":    6,
    },
    "4": {
        "name":        "⚠  Race Condition (Unsafe Shared Memory)",
        "description": "3 processes write shared memory WITHOUT synchronisation.",
        "fn":          scenario_race_condition,
        "duration":    4,
    },
    "5": {
        "name":        "💀 Deadlock (Circular Resource Wait)",
        "description": "Two processes hold one lock each and wait for the other's.",
        "fn":          scenario_deadlock,
        "duration":    7,
    },
    "6": {
        "name":        "🐢 Bottleneck (Queue Overflow)",
        "description": "3 fast senders vs 1 slow receiver → queue overflow.",
        "fn":          scenario_bottleneck,
        "duration":    6,
    },
}
