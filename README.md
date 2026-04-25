# IPC Debugger — Inter-Process Communication Analyser

> An academic OS project for 2nd/3rd year students to simulate, visualise, and debug
> IPC mechanisms including **Pipes**, **Message Queues**, and **Shared Memory**.

---

## Table of Contents
1. [Project Overview](#project-overview)
2. [Architecture & Design](#architecture--design)
3. [IPC Mechanisms Explained](#ipc-mechanisms-explained)
4. [Debugging Internals](#how-debugging-works-internally)
5. [File Structure](#file-structure)
6. [How to Run](#how-to-run)
7. [Test Scenarios](#test-scenarios)
8. [Sample Output](#sample-output)
9. [Performance Metrics](#performance-metrics)

---

## Project Overview

The **IPC Debugger** is a command-line tool that:

- Simulates **3–5 concurrent processes** communicating via Pipes, Message Queues, and Shared Memory
- Detects **race conditions**, **deadlocks**, **bottlenecks**, and **data consistency** problems
- Displays a **live refreshing dashboard** with process states, buffer contents, event logs, and analysis
- Supports a **step-by-step** execution mode for educational walkthroughs
- Provides **performance metrics** (throughput, latency, error rates)

---

## Architecture & Design

```
┌─────────────────────────────────────────────────────────────────┐
│                        cli.py  (Entry Point)                    │
│   • Argument parsing   • Dashboard rendering   • Scenario menu  │
└──────────────────────────────────┬──────────────────────────────┘
                                   │
         ┌─────────────────────────▼──────────────────────┐
         │              scenarios.py  (Test Cases)         │
         │  Pipe Sync │ MQ Normal │ SHM Safe │ Race │ DL  │
         └───────┬─────────────────────────────────────────┘
                 │ constructs
    ┌────────────▼────────────────────────────────────────┐
    │           process_sim.py  (SimProcess threads)       │
    │  Producer │ Consumer │ Sender │ Receiver │ Deadlock  │
    └────────────┬────────────────────────────────────────┘
                 │ uses
    ┌────────────▼────────────────────────────────────────┐
    │             ipc_core.py  (IPC Primitives)            │
    │   Pipe  │  MessageQueue  │  SharedMemory             │
    │   (all backed by threading.Condition / Semaphore)    │
    └────────────┬────────────────────────────────────────┘
                 │ writes events to
    ┌────────────▼────────────────────────────────────────┐
    │              debugger.py  (Analysis Engine)          │
    │  Deadlock detection │ Race detection │ Bottlenecks   │
    └─────────────────────────────────────────────────────┘
```

### Design Principles
| Principle | How it's applied |
|-----------|-----------------|
| Separation of concerns | IPC core, process logic, debugger, and CLI are each in their own module |
| Open/closed | Add a new IPC type by subclassing; no core changes needed |
| Thread safety | Every shared data structure uses `threading.Lock` or `Condition` |
| Observability | All IPC events emit to a central `IPCLogger` |

---

## IPC Mechanisms Explained

### 1. Pipe (`ipc_core.Pipe`)
```
Producer ──write()──▶  [item0|item1|item2|…]  ──read()──▶ Consumer
                        ◀────── capacity ──────▶
```
- Implemented as a `collections.deque` with a fixed `capacity`
- **write()** blocks (via `threading.Condition`) when the buffer is full
- **read()** blocks when the buffer is empty
- **Race detection**: if `write()` times out, a `WRITE_BLOCKED` error is emitted → possible deadlock flagged

### 2. Message Queue (`ipc_core.MessageQueue`)
```
Sender-1 ─┐                      ┌─▶ Receiver-1
Sender-2 ─┼─▶ [msg|msg|msg|…]  ──┤
Sender-3 ─┘    FIFO queue        └─▶ Receiver-2
```
- Backed by Python's thread-safe `queue.Queue`
- Each `Message` carries: `sender`, `recipient`, `data`, `seq_id`, `timestamp`
- **Overflow detection**: if the queue is full and `send()` times out, `overflow_count` increments
- Bottleneck alert fires when fill ratio ≥ 80%

### 3. Shared Memory (`ipc_core.SharedMemory`)
```
Writer-A ──safe_write()──▶ ┌──mutex──┐  ◀──safe_read()── Reader-1
Writer-B ──safe_write()──▶ │  data   │  ◀──safe_read()── Reader-2
                           └─────────┘  (semaphore, 5 concurrent readers)
```
- **Mutex (binary semaphore)** guards exclusive write access
- **Counting semaphore** allows up to 5 concurrent readers
- `unsafe_write()` deliberately skips the lock → simulates a **race condition**
- `race_detected` flag is set immediately when unsafe write occurs

---

## How Debugging Works Internally

The `IPCDebugger` class runs four analysis passes every refresh cycle:

### Pass 1 — Deadlock Detection
```python
if len(blocked_pids) >= 2 and running_count == 0:
    → DEADLOCK issue raised
```
Heuristic: if ≥2 processes are BLOCKED for >2 seconds and no process is RUNNING,
a circular wait is assumed. Confirmed via `DEADLOCK_DETECTED` log entries (mutex timeout chain).

### Pass 2 — Race Condition Detection
```python
if any entry.operation == "UNSAFE_WRITE":
    → RACE_CONDITION issue raised
```
Every `unsafe_write()` call emits an `UNSAFE_WRITE` log event at `ERROR` level.
The debugger scans the recent log window and sets the `race_detected` flag on the
shared memory object.

### Pass 3 — Bottleneck Detection
```python
if pipe.fill_ratio >= 0.80:
    → BOTTLENECK issue raised
if mq.overflow_count > 0:
    → BOTTLENECK issue raised (CRITICAL)
```
Buffer fill ratios above 80% and any message overflow trigger bottleneck warnings.

### Pass 4 — Consistency Check
```python
if count(TIMEOUT events in last 60) > 5:
    → CONSISTENCY issue raised
```
Repeated timeouts suggest processes are waiting on stale/missing data,
pointing to broken communication contracts.

---

## File Structure

```
ipc_debugger/
├── ipc_core.py       ← IPC primitives: Pipe, MessageQueue, SharedMemory, Logger
├── process_sim.py    ← SimProcess subclasses (threads)
├── debugger.py       ← Analysis engine: deadlock, race, bottleneck detection
├── scenarios.py      ← 6 predefined test scenarios
├── cli.py            ← CLI dashboard, argument parser, main entry point
├── run_all_demo.py   ← Batch runner that generates sample_output.txt
├── sample_output.txt ← Auto-generated sample execution output
└── README.md         ← This file
```

---

## How to Run

### Requirements
- Python 3.8+
- No external dependencies (uses only the standard library)

### Run a specific scenario
```bash
python cli.py --scenario 1        # Pipe — proper synchronisation
python cli.py --scenario 4        # Race condition demo
python cli.py --scenario 5        # Deadlock demo
```

### Interactive menu
```bash
python cli.py
# Then type a number (1–6) and press Enter
```

### Step-by-step mode
```bash
python cli.py --scenario 3 --step
# Press Enter in terminal to advance each IPC operation
```

### Custom refresh rate
```bash
python cli.py --scenario 6 --refresh 0.5   # update every 0.5 s
```

### Generate sample output
```bash
python run_all_demo.py
# Writes sample_output.txt
```

---

## Test Scenarios

| # | Name | IPC Type | Issue Demonstrated |
|---|------|----------|--------------------|
| 1 | Pipe — Proper Synchronisation | Pipe | ✅ Clean (no issues) |
| 2 | Message Queue — Normal | Message Queue | ✅ Clean |
| 3 | Shared Memory — Safe | Shared Memory | ✅ Clean (mutex + semaphore) |
| 4 | ⚠ Race Condition | Shared Memory | ❌ Race condition (no lock) |
| 5 | 💀 Deadlock | Shared Memory | ❌ Circular resource wait |
| 6 | 🐢 Bottleneck / Overflow | Message Queue | ❌ Queue overflow |

---

## Sample Output

```
════════════════════════════════════════════════════════════════════════════════
                    IPC DEBUGGER  — Inter-Process Communication Analyser
════════════════════════════════════════════════════════════════════════════════
  Scenario : ⚠  Race Condition (Unsafe Shared Memory)
  Elapsed  : 3.2s

─────────────────────────────── PROCESS STATES ─────────────────────────────────
  PID                STATE        OPS  Blocked-time
  ─────────────────────────────────────────────────────────
  Racer-1            RUNNING        5  0.0s
  Racer-2            RUNNING        5  0.0s
  Racer-3            RUNNING        4  0.0s
  Reader-R           RUNNING       12  0.0s

─────────────────────────────── IPC BUFFER STATES ──────────────────────────────
  SHARED MEMORY:
    race_memory       W:14 R:12  [counter=79] ⚠RACE!

─────────────────────── RECENT IPC EVENTS  (last 12) ───────────────────────────
  15:10:42  Racer-2              SHM    UNSAFE_WRITE    79
  15:10:42  Racer-1              SHM    UNSAFE_WRITE    41
  15:10:42  Reader-R             SHM    READ            79
  15:10:42  Racer-3              SHM    UNSAFE_WRITE    31

─────────────────────────── DEBUGGER ANALYSIS ──────────────────────────────────
  ● [RACE_CONDITION] 8 unprotected write(s) to shared memory detected.
       Processes: Racer-1, Racer-2, Racer-3

═══════════════════════════════════════════════════════════════════════════════
```

---

## Performance Metrics

The debugger exposes these metrics at runtime:

| Metric | Description |
|--------|-------------|
| `throughput_ops_per_sec` | Total IPC operations per second |
| `total_ops` | All logged events |
| `writes` | Total write/send operations |
| `reads` | Total read/receive operations |
| `errors` | ERROR + WARNING level log events |
| `blocks` | Timeout / blocked events |
| `ops_per_process` | Per-PID operation breakdown |

---

## Key Concepts Covered (OS Course Alignment)

- **Critical section** — mutex-protected shared memory writes
- **Producer-Consumer problem** — pipe + bounded buffer
- **Reader-Writer problem** — shared memory with semaphore
- **Deadlock** — circular resource waiting (hold and wait)
- **Starvation** — fast producers blocking slow consumers
- **Semaphore vs Mutex** — counting vs binary semaphores
- **IPC taxonomy** — pipes, message passing, shared memory
