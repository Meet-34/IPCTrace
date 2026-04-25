"""
process_sim.py — Simulated Process classes
Each SimProcess runs in its own thread and communicates via IPC mechanisms.
"""

import threading
import time
import random
from enum import Enum
from typing import Optional, Callable, List

from ipc_core import (
    ProcessState, IPCType, LogLevel,
    IPCLogger, Pipe, MessageQueue, SharedMemory
)


class SimProcess(threading.Thread):
    """
    Base class for a simulated OS process.
    Subclasses implement _run_logic() with their IPC behaviour.
    """

    def __init__(self, pid: str, logger: IPCLogger, step_mode: bool = False,
                 step_event: Optional[threading.Event] = None):
        super().__init__(daemon=True, name=pid)
        self.pid         = pid
        self.logger      = logger
        self.step_mode   = step_mode
        self.step_event  = step_event or threading.Event()
        self._state      = ProcessState.READY
        self._state_lock = threading.Lock()
        self._stop_flag  = threading.Event()
        self.ops_done    = 0
        self.blocked_time = 0.0
        self._blocked_since: Optional[float] = None

    @property
    def state(self) -> ProcessState:
        with self._state_lock:
            return self._state

    @state.setter
    def state(self, new_state: ProcessState):
        with self._state_lock:
            if new_state == ProcessState.BLOCKED:
                self._blocked_since = time.time()
            elif self._blocked_since is not None:
                self.blocked_time += time.time() - self._blocked_since
                self._blocked_since = None
            self._state = new_state

    def stop(self):
        self._stop_flag.set()

    def _step(self, description: str = ""):
        """In step mode, pause until user presses Enter (or step_event is set)."""
        if self.step_mode:
            self.state = ProcessState.WAITING
            self.step_event.clear()
            self.step_event.wait()   # unblocked by CLI stepper
            self.state = ProcessState.RUNNING

    def run(self):
        self.state = ProcessState.RUNNING
        try:
            self._run_logic()
        finally:
            self.state = ProcessState.DONE

    def _run_logic(self):
        raise NotImplementedError


# ─────────────────────────────────────────────
#  Concrete Process Types
# ─────────────────────────────────────────────

class ProducerProcess(SimProcess):
    """Writes data to a Pipe (classic Producer role)."""

    def __init__(self, pid: str, pipe: Pipe, logger: IPCLogger,
                 items: int = 10, delay: float = 0.3, **kw):
        super().__init__(pid, logger, **kw)
        self.pipe  = pipe
        self.items = items
        self.delay = delay

    def _run_logic(self):
        for i in range(self.items):
            if self._stop_flag.is_set():
                break
            data = f"{self.pid}:item-{i}"
            self._step(f"About to WRITE '{data}' to pipe")
            self.state = ProcessState.RUNNING
            ok = self.pipe.write(self.pid, data)
            if not ok:
                self.state = ProcessState.BLOCKED
                time.sleep(0.5)
                self.state = ProcessState.RUNNING
            else:
                self.ops_done += 1
            time.sleep(self.delay + random.uniform(0, 0.1))


class ConsumerProcess(SimProcess):
    """Reads data from a Pipe (classic Consumer role)."""

    def __init__(self, pid: str, pipe: Pipe, logger: IPCLogger,
                 items: int = 10, delay: float = 0.4, **kw):
        super().__init__(pid, logger, **kw)
        self.pipe  = pipe
        self.items = items
        self.delay = delay
        self.consumed: List = []

    def _run_logic(self):
        for _ in range(self.items):
            if self._stop_flag.is_set():
                break
            self._step("About to READ from pipe")
            self.state = ProcessState.RUNNING
            data = self.pipe.read(self.pid)
            if data is None:
                self.state = ProcessState.BLOCKED
                time.sleep(0.3)
                self.state = ProcessState.RUNNING
            else:
                self.consumed.append(data)
                self.ops_done += 1
            time.sleep(self.delay)


class SenderProcess(SimProcess):
    """Sends messages via a MessageQueue."""

    def __init__(self, pid: str, mq: MessageQueue, recipients: List[str],
                 logger: IPCLogger, msgs: int = 8, delay: float = 0.25, **kw):
        super().__init__(pid, logger, **kw)
        self.mq         = mq
        self.recipients = recipients
        self.msgs       = msgs
        self.delay      = delay

    def _run_logic(self):
        for i in range(self.msgs):
            if self._stop_flag.is_set():
                break
            recipient = random.choice(self.recipients)
            data = {"type": "DATA", "seq": i, "payload": random.randint(100, 999)}
            self._step(f"About to SEND msg seq={i} to {recipient}")
            self.state = ProcessState.RUNNING
            ok = self.mq.send(self.pid, recipient, data)
            if ok:
                self.ops_done += 1
            else:
                self.state = ProcessState.BLOCKED
                time.sleep(0.5)
                self.state = ProcessState.RUNNING
            time.sleep(self.delay + random.uniform(0, 0.15))


class ReceiverProcess(SimProcess):
    """Receives messages from a MessageQueue."""

    def __init__(self, pid: str, mq: MessageQueue, logger: IPCLogger,
                 msgs: int = 8, delay: float = 0.35, **kw):
        super().__init__(pid, logger, **kw)
        self.mq    = mq
        self.msgs  = msgs
        self.delay = delay
        self.received_messages = []

    def _run_logic(self):
        for _ in range(self.msgs):
            if self._stop_flag.is_set():
                break
            self._step("About to RECEIVE from message queue")
            self.state = ProcessState.RUNNING
            msg = self.mq.receive(self.pid)
            if msg:
                self.received_messages.append(msg)
                self.ops_done += 1
            else:
                self.state = ProcessState.BLOCKED
                time.sleep(0.3)
                self.state = ProcessState.RUNNING
            time.sleep(self.delay)


class SharedMemoryWriter(SimProcess):
    """Writes to shared memory — can be safe or unsafe (race condition)."""

    def __init__(self, pid: str, shm: SharedMemory, logger: IPCLogger,
                 ops: int = 8, safe: bool = True, delay: float = 0.2, **kw):
        super().__init__(pid, logger, **kw)
        self.shm   = shm
        self.ops   = ops
        self.safe  = safe
        self.delay = delay

    def _run_logic(self):
        for i in range(self.ops):
            if self._stop_flag.is_set():
                break
            key   = f"counter"
            value = i * 10 + random.randint(1, 9)
            self._step(f"About to {'SAFE' if self.safe else 'UNSAFE'} WRITE shm[{key}]={value}")
            self.state = ProcessState.RUNNING
            if self.safe:
                ok = self.shm.safe_write(self.pid, key, value)
                if not ok:
                    self.state = ProcessState.BLOCKED
            else:
                self.shm.unsafe_write(self.pid, key, value)
            self.ops_done += 1
            time.sleep(self.delay + random.uniform(0, 0.1))


class SharedMemoryReader(SimProcess):
    """Reads from shared memory concurrently."""

    def __init__(self, pid: str, shm: SharedMemory, logger: IPCLogger,
                 ops: int = 10, delay: float = 0.15, **kw):
        super().__init__(pid, logger, **kw)
        self.shm   = shm
        self.ops   = ops
        self.delay = delay
        self.readings = []

    def _run_logic(self):
        for _ in range(self.ops):
            if self._stop_flag.is_set():
                break
            self._step("About to READ from shared memory")
            self.state = ProcessState.RUNNING
            val = self.shm.safe_read(self.pid, "counter")
            self.readings.append(val)
            self.ops_done += 1
            time.sleep(self.delay)


class DeadlockProcess(SimProcess):
    """
    Simulates a classic deadlock:
    Two resources (shm_a, shm_b), two processes each acquire in opposite order.
    """

    def __init__(self, pid: str, res_first: SharedMemory, res_second: SharedMemory,
                 logger: IPCLogger, **kw):
        super().__init__(pid, logger, **kw)
        self.res_first  = res_first
        self.res_second = res_second

    def _run_logic(self):
        self.logger.log(self.pid, IPCType.SHARED_MEMORY, "LOCK_REQ",
                        self.res_first.name, detail=f"Requesting lock on '{self.res_first.name}'")
        self.state = ProcessState.BLOCKED

        got_first = self.res_first._mutex.acquire(timeout=5.0)
        if not got_first:
            self.logger.log(self.pid, IPCType.SHARED_MEMORY, "DEADLOCK_DETECTED",
                            None, LogLevel.ERROR,
                            f"💀 DEADLOCK: '{self.pid}' could not acquire '{self.res_first.name}'")
            self.state = ProcessState.DONE
            return

        self.logger.log(self.pid, IPCType.SHARED_MEMORY, "LOCK_ACQ",
                        self.res_first.name, detail=f"Acquired '{self.res_first.name}', now waiting for '{self.res_second.name}'")
        time.sleep(0.5)   # ← hold first lock, let other process grab second

        got_second = self.res_second._mutex.acquire(timeout=3.0)
        if not got_second:
            self.logger.log(self.pid, IPCType.SHARED_MEMORY, "DEADLOCK_DETECTED",
                            None, LogLevel.ERROR,
                            f"💀 DEADLOCK: '{self.pid}' blocked waiting for '{self.res_second.name}' — circular wait!")
            self.state = ProcessState.BLOCKED
            time.sleep(1.0)
        else:
            self.res_second._mutex.release()

        self.res_first._mutex.release()
        self.state = ProcessState.DONE
