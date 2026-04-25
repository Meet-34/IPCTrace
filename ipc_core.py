"""
ipc_core.py — Core IPC mechanisms: Pipes, Message Queues, Shared Memory
Inter-Process Communication Debugger | OS Academic Project
"""

import threading
import time
import queue
import random
from collections import deque
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


# ─────────────────────────────────────────────
#  Enumerations
# ─────────────────────────────────────────────

class ProcessState(Enum):
    READY    = "READY"
    RUNNING  = "RUNNING"
    WAITING  = "WAITING"
    BLOCKED  = "BLOCKED"
    DONE     = "DONE"

class IPCType(Enum):
    PIPE          = "PIPE"
    MESSAGE_QUEUE = "MESSAGE_QUEUE"
    SHARED_MEMORY = "SHARED_MEMORY"

class LogLevel(Enum):
    INFO    = "INFO"
    WARNING = "WARNING"
    ERROR   = "ERROR"
    DEBUG   = "DEBUG"


# ─────────────────────────────────────────────
#  Data Structures
# ─────────────────────────────────────────────

@dataclass
class LogEntry:
    timestamp:  float
    pid:        str
    ipc_type:   IPCType
    operation:  str          # "READ", "WRITE", "WAIT", "SIGNAL", etc.
    data:       Any
    level:      LogLevel = LogLevel.INFO
    detail:     str = ""

@dataclass
class Message:
    sender:    str
    recipient: str
    data:      Any
    timestamp: float = field(default_factory=time.time)
    msg_id:    int   = 0


# ─────────────────────────────────────────────
#  Central Event Logger
# ─────────────────────────────────────────────

class IPCLogger:
    """Thread-safe logger that records every IPC event."""

    def __init__(self):
        self._lock    = threading.Lock()
        self.entries: List[LogEntry] = []
        self._msg_counter = 0

    def log(self, pid: str, ipc_type: IPCType, operation: str,
            data: Any = None, level: LogLevel = LogLevel.INFO, detail: str = ""):
        with self._lock:
            self._msg_counter += 1
            entry = LogEntry(
                timestamp = time.time(),
                pid       = pid,
                ipc_type  = ipc_type,
                operation = operation,
                data      = data,
                level     = level,
                detail    = detail,
            )
            self.entries.append(entry)
        return entry

    def get_recent(self, n: int = 20) -> List[LogEntry]:
        with self._lock:
            return list(self.entries[-n:])

    def clear(self):
        with self._lock:
            self.entries.clear()


# ─────────────────────────────────────────────
#  1. PIPE  (Unidirectional)
# ─────────────────────────────────────────────

class Pipe:
    """
    Simulates a Unix-style unidirectional pipe.
    Writer end → buffer → Reader end.
    Capacity is fixed; writes block when full (deadlock risk).
    """

    def __init__(self, name: str, capacity: int = 8, logger: Optional[IPCLogger] = None):
        self.name     = name
        self.capacity = capacity
        self._buffer  = deque()
        self._lock    = threading.Lock()
        self._not_full  = threading.Condition(self._lock)
        self._not_empty = threading.Condition(self._lock)
        self.logger   = logger
        self.total_written = 0
        self.total_read    = 0

    def write(self, pid: str, data: Any, timeout: float = 5.0) -> bool:
        """Write to pipe; blocks if full. Returns False on timeout (deadlock indicator)."""
        deadline = time.time() + timeout
        with self._not_full:
            while len(self._buffer) >= self.capacity:
                remaining = deadline - time.time()
                if remaining <= 0:
                    if self.logger:
                        self.logger.log(pid, IPCType.PIPE, "WRITE_BLOCKED",
                                        data, LogLevel.ERROR,
                                        f"Pipe '{self.name}' FULL — possible deadlock!")
                    return False
                self._not_full.wait(timeout=remaining)
            self._buffer.append((pid, data, time.time()))
            self.total_written += 1
            if self.logger:
                self.logger.log(pid, IPCType.PIPE, "WRITE", data,
                                detail=f"pipe='{self.name}' buf={len(self._buffer)}/{self.capacity}")
            self._not_empty.notify_all()
        return True

    def read(self, pid: str, timeout: float = 5.0) -> Optional[Any]:
        """Read from pipe; blocks if empty. Returns None on timeout."""
        deadline = time.time() + timeout
        with self._not_empty:
            while not self._buffer:
                remaining = deadline - time.time()
                if remaining <= 0:
                    if self.logger:
                        self.logger.log(pid, IPCType.PIPE, "READ_TIMEOUT",
                                        None, LogLevel.WARNING,
                                        f"Pipe '{self.name}' EMPTY — process blocked")
                    return None
                self._not_empty.wait(timeout=remaining)
            _, data, _ = self._buffer.popleft()
            self.total_read += 1
            if self.logger:
                self.logger.log(pid, IPCType.PIPE, "READ", data,
                                detail=f"pipe='{self.name}' buf={len(self._buffer)}/{self.capacity}")
            self._not_full.notify_all()
        return data

    @property
    def buffer_snapshot(self) -> list:
        with self._lock:
            return [(s, d) for s, d, _ in self._buffer]

    @property
    def fill_ratio(self) -> float:
        with self._lock:
            return len(self._buffer) / self.capacity


# ─────────────────────────────────────────────
#  2. MESSAGE QUEUE  (FIFO Mailbox)
# ─────────────────────────────────────────────

class MessageQueue:
    """
    FIFO message queue supporting multiple senders/receivers.
    Each message carries sender, recipient, and payload.
    Overflow detection → bottleneck warning.
    """

    def __init__(self, name: str, maxsize: int = 16, logger: Optional[IPCLogger] = None):
        self.name    = name
        self.maxsize = maxsize
        self._queue  = queue.Queue(maxsize=maxsize)
        self.logger  = logger
        self._msg_id     = 0
        self._id_lock    = threading.Lock()
        self.overflow_count = 0
        self.total_sent     = 0
        self.total_received = 0

    def _next_id(self) -> int:
        with self._id_lock:
            self._msg_id += 1
            return self._msg_id

    def send(self, sender: str, recipient: str, data: Any, timeout: float = 3.0) -> bool:
        """Enqueue a message. Returns False if queue is full (bottleneck)."""
        msg = Message(sender=sender, recipient=recipient,
                      data=data, msg_id=self._next_id())
        try:
            self._queue.put(msg, timeout=timeout)
            self.total_sent += 1
            if self.logger:
                self.logger.log(sender, IPCType.MESSAGE_QUEUE, "SEND", data,
                                detail=f"mq='{self.name}' to={recipient} id={msg.msg_id} sz={self._queue.qsize()}/{self.maxsize}")
            return True
        except queue.Full:
            self.overflow_count += 1
            if self.logger:
                self.logger.log(sender, IPCType.MESSAGE_QUEUE, "OVERFLOW",
                                data, LogLevel.ERROR,
                                f"Queue '{self.name}' OVERFLOW — bottleneck detected!")
            return False

    def receive(self, pid: str, timeout: float = 3.0) -> Optional[Message]:
        """Dequeue a message. Returns None on timeout."""
        try:
            msg = self._queue.get(timeout=timeout)
            self.total_received += 1
            if self.logger:
                self.logger.log(pid, IPCType.MESSAGE_QUEUE, "RECEIVE", msg.data,
                                detail=f"mq='{self.name}' from={msg.sender} id={msg.msg_id}")
            return msg
        except queue.Empty:
            if self.logger:
                self.logger.log(pid, IPCType.MESSAGE_QUEUE, "RECEIVE_TIMEOUT",
                                None, LogLevel.WARNING,
                                f"Queue '{self.name}' empty — {pid} waiting")
            return None

    @property
    def snapshot(self) -> List[Message]:
        """Non-destructive peek at queued messages."""
        try:
            items = list(self._queue.queue)
            return items
        except Exception:
            return []

    @property
    def size(self) -> int:
        return self._queue.qsize()


# ─────────────────────────────────────────────
#  3. SHARED MEMORY  (with Semaphore + Mutex)
# ─────────────────────────────────────────────

class SharedMemory:
    """
    Simulates shared memory segment protected by:
      - A mutex (binary semaphore) for exclusive write access.
      - A counting semaphore for concurrent read access.
    Race condition injection: 'unsafe_write' bypasses the lock.
    """

    def __init__(self, name: str, size: int = 64, logger: Optional[IPCLogger] = None):
        self.name    = name
        self.size    = size          # max stored items
        self._data: Dict[str, Any] = {}
        self._mutex       = threading.Lock()
        self._read_sem    = threading.Semaphore(5)  # max 5 concurrent readers
        self.logger       = logger
        self.write_count  = 0
        self.read_count   = 0
        self.race_detected = False
        self._writers_active = 0
        self._readers_active = 0
        self._state_lock = threading.Lock()

    def safe_write(self, pid: str, key: str, value: Any) -> bool:
        """Write with mutex — correct synchronization."""
        acquired = self._mutex.acquire(timeout=4.0)
        if not acquired:
            if self.logger:
                self.logger.log(pid, IPCType.SHARED_MEMORY, "MUTEX_TIMEOUT",
                                value, LogLevel.ERROR,
                                f"SHM '{self.name}': mutex acquire timed out — deadlock risk!")
            return False

        with self._state_lock:
            self._writers_active += 1

        try:
            time.sleep(random.uniform(0.01, 0.05))   # simulate memory op latency
            self._data[key] = value
            self.write_count += 1
            if self.logger:
                self.logger.log(pid, IPCType.SHARED_MEMORY, "WRITE", value,
                                detail=f"shm='{self.name}' key='{key}' [LOCKED]")
        finally:
            with self._state_lock:
                self._writers_active -= 1
            self._mutex.release()
        return True

    def unsafe_write(self, pid: str, key: str, value: Any):
        """Write WITHOUT lock — simulates a race condition bug."""
        if self.logger:
            self.logger.log(pid, IPCType.SHARED_MEMORY, "UNSAFE_WRITE",
                            value, LogLevel.ERROR,
                            f"⚠ RACE CONDITION: '{pid}' writing '{key}' WITHOUT lock!")
        time.sleep(random.uniform(0.005, 0.02))
        self._data[key] = value      # ← data race here
        self.race_detected = True
        self.write_count += 1

    def safe_read(self, pid: str, key: str) -> Any:
        """Read with semaphore — allows multiple concurrent readers."""
        acquired = self._read_sem.acquire(timeout=3.0)
        if not acquired:
            if self.logger:
                self.logger.log(pid, IPCType.SHARED_MEMORY, "READ_SEM_TIMEOUT",
                                None, LogLevel.WARNING,
                                f"SHM '{self.name}': semaphore exhausted")
            return None

        with self._state_lock:
            self._readers_active += 1

        try:
            time.sleep(random.uniform(0.005, 0.02))
            value = self._data.get(key)
            self.read_count += 1
            if self.logger:
                self.logger.log(pid, IPCType.SHARED_MEMORY, "READ", value,
                                detail=f"shm='{self.name}' key='{key}'")
            return value
        finally:
            with self._state_lock:
                self._readers_active -= 1
            self._read_sem.release()

    @property
    def snapshot(self) -> Dict[str, Any]:
        with self._mutex:
            return dict(self._data)

    @property
    def active_readers(self) -> int:
        with self._state_lock:
            return self._readers_active

    @property
    def active_writers(self) -> int:
        with self._state_lock:
            return self._writers_active
