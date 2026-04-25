"""
run_all_demo.py — Runs all 6 scenarios and captures sample output to files.
Used to generate README sample outputs.
"""

import sys, os, time, io, threading
sys.path.insert(0, os.path.dirname(__file__))

from ipc_core import IPCLogger, IPCType, LogLevel, ProcessState
from scenarios import SCENARIOS
from debugger import IPCDebugger

OUTPUTS = {}

def run_and_capture(key):
    sc = SCENARIOS[key]
    logger = IPCLogger()
    step_ev = threading.Event()
    step_ev.set()

    processes, debugger = sc["fn"](logger, step_mode=False, step_event=step_ev)
    for p in processes:
        p.start()

    time.sleep(sc["duration"] + 1)

    for p in processes:
        p.join(timeout=0.5)

    issues = debugger.analyse()
    metrics = debugger.performance_metrics()

    lines = []
    lines.append(f"=== Scenario {key}: {sc['name']} ===")
    lines.append(f"Description: {sc['description']}")
    lines.append("")
    lines.append("--- Process Final States ---")
    for p in processes:
        lines.append(f"  {p.pid:<20} state={p.state.value:<10} ops={p.ops_done}  blocked={p.blocked_time:.2f}s")
    lines.append("")
    lines.append("--- Debugger Issues ---")
    if not issues:
        lines.append("  ✔  No synchronisation issues detected.")
    else:
        for iss in issues:
            lines.append(f"  [{iss.severity}] [{iss.category}] {iss.description}")
            if iss.pids:
                lines.append(f"       Procs: {', '.join(iss.pids)}")
    lines.append("")
    lines.append("--- Performance Metrics ---")
    if metrics:
        lines.append(f"  Throughput : {metrics['throughput_ops_per_sec']} ops/s")
        lines.append(f"  Total ops  : {metrics['total_ops']}")
        lines.append(f"  Writes     : {metrics['writes']}")
        lines.append(f"  Reads      : {metrics['reads']}")
        lines.append(f"  Errors     : {metrics['errors']}")
        lines.append(f"  Blocks     : {metrics['blocks']}")
    lines.append("")
    lines.append("--- Recent Log (last 10 events) ---")
    for entry in logger.get_recent(10):
        ts = time.strftime('%H:%M:%S', time.localtime(entry.timestamp))
        lines.append(f"  [{ts}] {entry.pid:<20} {entry.ipc_type.value:<16} {entry.operation:<20} {str(entry.data)[:30]}")
    lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    output_path = os.path.join(os.path.dirname(__file__), "sample_output.txt")
    all_output = []
    all_output.append("=" * 70)
    all_output.append("  IPC DEBUGGER — Sample Execution Output")
    all_output.append("  Generated automatically by run_all_demo.py")
    all_output.append("=" * 70)
    all_output.append("")

    for key in ["1", "2", "3", "4", "5", "6"]:
        print(f"Running scenario {key}...")
        try:
            result = run_and_capture(key)
            all_output.append(result)
        except Exception as e:
            all_output.append(f"Scenario {key} error: {e}\n")

    full_output = "\n".join(all_output)
    with open(output_path, "w") as f:
        f.write(full_output)
    print(f"\nSample output written to: {output_path}")
    print(full_output[:3000])
