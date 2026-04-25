"""
cli.py — CLI Dashboard + Main Entry Point
Inter-Process Communication Debugger
"""

import os
import sys
import time
import threading
import argparse
from typing import List, Optional

from ipc_core import IPCLogger, IPCType, LogLevel, LogEntry, ProcessState
from process_sim import SimProcess
from debugger import IPCDebugger, DebugIssue
from scenarios import SCENARIOS


# ─────────────────────────────────────────────
#  ANSI Colour Helpers
# ─────────────────────────────────────────────

RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"

RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
CYAN   = "\033[96m"
WHITE  = "\033[97m"
GRAY   = "\033[90m"

def col(text, *codes):
    return "".join(codes) + str(text) + RESET

def clear():
    os.system("cls" if os.name == "nt" else "clear")


# ─────────────────────────────────────────────
#  Dashboard Renderer
# ─────────────────────────────────────────────

W = 80   # terminal width

def _banner():
    print(col("═" * W, CYAN, BOLD))
    title = "  IPC DEBUGGER  — Inter-Process Communication Analyser"
    print(col(title.center(W), CYAN, BOLD))
    print(col("═" * W, CYAN, BOLD))

def _section(title: str):
    bar = f" {title} ".center(W, "─")
    print(col(bar, BLUE, BOLD))

def _state_colour(state: ProcessState) -> str:
    return {
        ProcessState.READY:   col(state.value, GREEN),
        ProcessState.RUNNING: col(state.value, CYAN, BOLD),
        ProcessState.WAITING: col(state.value, YELLOW),
        ProcessState.BLOCKED: col(state.value, RED, BOLD),
        ProcessState.DONE:    col(state.value, GRAY),
    }.get(state, state.value)

def _level_colour(level: LogLevel) -> str:
    return {
        LogLevel.INFO:    col("INFO",    GREEN),
        LogLevel.DEBUG:   col("DEBUG",   GRAY),
        LogLevel.WARNING: col("WARN",    YELLOW),
        LogLevel.ERROR:   col("ERROR",   RED, BOLD),
    }.get(level, level.value)

def _bar(ratio: float, width: int = 20) -> str:
    filled = int(ratio * width)
    empty  = width - filled
    colour = RED if ratio >= 0.8 else YELLOW if ratio >= 0.5 else GREEN
    return col("█" * filled, colour) + col("░" * empty, GRAY)


def render_dashboard(processes: List[SimProcess], debugger: IPCDebugger,
                     logger: IPCLogger, scenario_name: str,
                     elapsed: float):
    clear()
    _banner()
    print(col(f"  Scenario : {scenario_name}", WHITE, BOLD))
    print(col(f"  Elapsed  : {elapsed:.1f}s", GRAY))
    print()

    # ── Process States ────────────────────────────
    _section("PROCESS STATES")
    fmt = "  {:<18} {:<12} {:>6}  {}"
    print(col(fmt.format("PID", "STATE", "OPS", "Blocked-time"), BOLD))
    print(col("  " + "─" * 56, GRAY))
    for p in processes:
        blk = f"{p.blocked_time:.1f}s"
        print(fmt.format(
            col(p.pid, BOLD),
            _state_colour(p.state),
            col(str(p.ops_done), WHITE),
            col(blk, RED if p.blocked_time > 1 else GRAY),
        ))
    print()

    # ── IPC Buffer States ─────────────────────────
    _section("IPC BUFFER STATES")

    pipes = debugger.pipes
    mqs   = debugger.message_queues
    shms  = debugger.shared_memories

    if pipes:
        print(col("  PIPES:", BOLD))
        for name, pipe in pipes.items():
            ratio = pipe.fill_ratio
            snap  = pipe.buffer_snapshot[:4]
            items = ", ".join(str(d) for _, d in snap)
            if len(pipe._buffer) > 4:
                items += "…"
            print(f"    {col(name, CYAN):<24} [{_bar(ratio)}] "
                  f"{len(pipe._buffer):>2}/{pipe.capacity}  {col(items, GRAY)}")
        print()

    if mqs:
        print(col("  MESSAGE QUEUES:", BOLD))
        for name, mq in mqs.items():
            ratio = mq.size / mq.maxsize if mq.maxsize else 0
            snaps = mq.snapshot[:3]
            items = ", ".join(f"{m.sender}→{m.recipient}" for m in snaps)
            of_warn = col(f" [OVERFLOW×{mq.overflow_count}]", RED, BOLD) if mq.overflow_count else ""
            print(f"    {col(name, CYAN):<24} [{_bar(ratio)}] "
                  f"{mq.size:>2}/{mq.maxsize}{of_warn}  {col(items, GRAY)}")
        print()

    if shms:
        print(col("  SHARED MEMORY:", BOLD))
        for name, shm in shms.items():
            snap = shm.snapshot
            items = ", ".join(f"{k}={v}" for k, v in list(snap.items())[:4])
            race  = col(" ⚠RACE!", RED, BOLD) if shm.race_detected else ""
            print(f"    {col(name, CYAN):<24} W:{shm.write_count} R:{shm.read_count}"
                  f"  [{col(items, GRAY)}]{race}")
        print()

    # ── Recent Events ─────────────────────────────
    _section("RECENT IPC EVENTS  (last 12)")
    print(col("  Time     PID                IPC_TYPE       OP              DATA", BOLD))
    print(col("  " + "─" * 72, GRAY))
    for entry in logger.get_recent(12):
        ts     = time.strftime('%H:%M:%S', time.localtime(entry.timestamp))
        ipc_c  = {
            IPCType.PIPE:          col("PIPE ", BLUE),
            IPCType.MESSAGE_QUEUE: col("MQ   ", CYAN),
            IPCType.SHARED_MEMORY: col("SHM  ", YELLOW),
        }.get(entry.ipc_type, str(entry.ipc_type))
        data_s = str(entry.data)[:18] if entry.data is not None else ""
        op_c   = col(entry.operation[:14], RED if "RACE" in entry.operation
                     or "DEAD" in entry.operation or "OVERFLOW" in entry.operation
                     or "BLOCKED" in entry.operation else WHITE)
        print(f"  {col(ts, GRAY)} {col(entry.pid[:18], BOLD):<28} {ipc_c}   {op_c:<24} {col(data_s, GRAY)}")
    print()

    # ── Debugger Issues ───────────────────────────
    issues = debugger.analyse()
    _section("DEBUGGER ANALYSIS")
    if not issues:
        print(col("  ✔  No synchronisation issues detected.", GREEN, BOLD))
    else:
        for issue in issues:
            icon = {"CRITICAL": col("●", RED, BOLD),
                    "WARNING":  col("●", YELLOW),
                    "INFO":     col("●", BLUE)}.get(issue.severity, "●")
            print(f"  {icon} [{col(issue.category, BOLD)}] {issue.description}")
            if issue.pids:
                print(f"     Processes: {col(', '.join(issue.pids), CYAN)}")
    print()

    # ── Performance Metrics ───────────────────────
    _section("PERFORMANCE METRICS")
    m = debugger.performance_metrics()
    if m:
        print(f"  Throughput : {col(str(m['throughput_ops_per_sec']), CYAN)} ops/s   "
              f"Total ops: {col(str(m['total_ops']), WHITE)}   "
              f"Errors: {col(str(m['errors']), RED if m['errors'] else GREEN)}")
        print(f"  Writes: {m['writes']}   Reads: {m['reads']}   Blocks: {m['blocks']}")
        ops = m.get("ops_per_process", {})
        line = "  Per-proc: " + "  ".join(f"{col(pid, BOLD)}:{n}" for pid, n in ops.items())
        print(line[:W])
    print()

    print(col("  Press Ctrl+C to stop.", GRAY))
    print(col("═" * W, CYAN))


# ─────────────────────────────────────────────
#  Runner
# ─────────────────────────────────────────────

def run_scenario(scenario_key: str, step_mode: bool = False,
                 refresh_rate: float = 1.0):
    if scenario_key not in SCENARIOS:
        print(col(f"Unknown scenario '{scenario_key}'", RED))
        return

    sc      = SCENARIOS[scenario_key]
    logger  = IPCLogger()
    step_ev = threading.Event()
    step_ev.set()   # auto-release unless step_mode

    processes, debugger = sc["fn"](logger, step_mode=step_mode,
                                   step_event=step_ev)

    print(col(f"\n▶  Starting: {sc['name']}", GREEN, BOLD))
    print(col(f"   {sc['description']}", GRAY))
    if step_mode:
        print(col("   [STEP MODE] Press Enter to advance each operation.", YELLOW, BOLD))
    time.sleep(1.0)

    # Start all processes
    for p in processes:
        p.start()

    start = time.time()
    duration = sc["duration"]

    # Stepper thread: in step mode, release one step at a time
    def stepper():
        while any(p.is_alive() for p in processes):
            input()   # wait for Enter
            step_ev.set()

    if step_mode:
        t = threading.Thread(target=stepper, daemon=True)
        t.start()

    try:
        while True:
            elapsed = time.time() - start
            render_dashboard(processes, debugger, logger,
                             sc["name"], elapsed)

            if not step_mode:
                all_done = all(not p.is_alive() for p in processes)
                if all_done or elapsed > duration + 4:
                    break
                time.sleep(refresh_rate)
            else:
                time.sleep(refresh_rate)
                if all(not p.is_alive() for p in processes):
                    break

    except KeyboardInterrupt:
        print(col("\n\nStopped by user.", YELLOW))
        for p in processes:
            p.stop()

    # Final render
    for p in processes:
        p.join(timeout=1.0)

    elapsed = time.time() - start
    render_dashboard(processes, debugger, logger, sc["name"], elapsed)
    print(col("\n✔  Scenario complete.\n", GREEN, BOLD))


def print_scenario_menu():
    print()
    _banner()
    print(col("  Available Test Scenarios:", WHITE, BOLD))
    print()
    for key, sc in SCENARIOS.items():
        print(f"    {col(key, CYAN, BOLD)}.  {col(sc['name'], BOLD)}")
        print(f"        {col(sc['description'], GRAY)}")
        print()
    print(col("  Options:", WHITE, BOLD))
    print(f"    {col('--step', YELLOW)}        Enable step-by-step execution mode")
    print(f"    {col('--refresh N', YELLOW)}   Dashboard refresh rate in seconds (default 1.0)")
    print()
    print(col("  Usage:", WHITE, BOLD))
    print(col("    python cli.py --scenario 1", GRAY))
    print(col("    python cli.py --scenario 4 --step", GRAY))
    print(col("    python cli.py --scenario 5 --refresh 0.5", GRAY))
    print()


# ─────────────────────────────────────────────
#  Entry Point
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="IPC Debugger — Simulate and debug IPC mechanisms."
    )
    parser.add_argument("--scenario", "-s", type=str, default=None,
                        help="Scenario number to run (1-6). Omit to see menu.")
    parser.add_argument("--step", action="store_true",
                        help="Step-by-step execution (press Enter to advance).")
    parser.add_argument("--refresh", type=float, default=1.0,
                        help="Dashboard refresh interval in seconds.")
    args = parser.parse_args()

    if args.scenario is None:
        print_scenario_menu()
        choice = input(col("  Enter scenario number: ", CYAN)).strip()
        if choice not in SCENARIOS:
            print(col("Invalid choice. Exiting.", RED))
            sys.exit(1)
        args.scenario = choice

    run_scenario(args.scenario, step_mode=args.step, refresh_rate=args.refresh)


if __name__ == "__main__":
    main()
