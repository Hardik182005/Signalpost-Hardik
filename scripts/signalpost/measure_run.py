#!/usr/bin/env python3
"""Run a command and record what it actually used: wall time, CPU time, peak memory, disk.

    python scripts/signalpost/measure_run.py --report out/holdout100-resources.json \
        --disk out/holdout100.jsonl --disk out/snapshots-holdout100 -- \
        .venv/Scripts/python.exe main.py --input data/holdout/corpus.jsonl ...

Standard library only, so measuring never changes the environment being measured. Peak
memory is the operating system's own high-water mark: on Windows a job object's peak
committed memory across every process the command starts; on POSIX ru_maxrss of the
largest waited-for descendant. The report states which one it holds.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def portable(arg: str) -> str:
    try:
        return Path(arg).resolve().relative_to(Path.cwd()).as_posix() if Path(arg).exists() else arg
    except ValueError:
        return arg


def tree_bytes(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def windows_run(command: list[str]) -> tuple[int, dict]:
    """Run inside a job object, so the figures cover every process the command starts.

    A venv's python.exe is a redirector that starts the real interpreter as a child, so
    measuring only the direct child would describe the redirector. The child is created
    suspended, placed in the job, then resumed, so nothing it spawns escapes the job.
    """
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_uint64 * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class Accounting(ctypes.Structure):
        _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                    ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                    ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                    ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    create_suspended = 0x00000004
    process = subprocess.Popen(command, creationflags=create_suspended)
    handle = wintypes.HANDLE(int(process._handle))
    if not kernel32.AssignProcessToJobObject(wintypes.HANDLE(job), handle):
        process.kill()
        raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
    ctypes.WinDLL("ntdll").NtResumeProcess(handle)
    process.wait()

    extended, accounting = Extended(), Accounting()
    kernel32.QueryInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(extended), ctypes.sizeof(extended), None)
    kernel32.QueryInformationJobObject(wintypes.HANDLE(job), 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None)
    kernel32.CloseHandle(wintypes.HANDLE(job))
    return process.returncode, {
        "peak_memory_bytes": extended.PeakJobMemoryUsed,
        "peak_memory_source": "Windows job object PeakJobMemoryUsed: peak committed memory of all processes combined",
        "peak_single_process_memory_bytes": extended.PeakProcessMemoryUsed,
        "processes": accounting.TotalProcesses,
        "cpu_user_seconds": round(accounting.TotalUserTime / 1e7, 3),
        "cpu_system_seconds": round(accounting.TotalKernelTime / 1e7, 3),
        "scope_note": "Covers every process in the job, including the venv redirector and the interpreter it starts.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--disk", type=Path, action="append", default=[], help="Output path to size after the run")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("no command given")
    # Windows CreateProcess does not resolve a relative, forward-slash interpreter path itself.
    command[0] = str(Path(command[0]).resolve()) if Path(command[0]).exists() else (shutil.which(command[0]) or command[0])

    started_at, started = utc_now(), time.monotonic()
    if os.name == "nt":
        returncode, usage = windows_run(command)
    else:
        import resource

        returncode = subprocess.run(command).returncode
        children = resource.getrusage(resource.RUSAGE_CHILDREN)
        scale = 1 if sys.platform == "darwin" else 1024
        usage = {
            "peak_memory_bytes": children.ru_maxrss * scale,
            "peak_memory_source": "POSIX ru_maxrss: the largest single waited-for descendant, not a sum",
            "cpu_user_seconds": round(children.ru_utime, 3),
            "cpu_system_seconds": round(children.ru_stime, 3),
            "scope_note": "CPU covers waited-for descendants; peak memory is the largest one of them.",
        }
    wall = time.monotonic() - started
    cpu = usage["cpu_user_seconds"] + usage["cpu_system_seconds"]
    report = {
        "schema_version": "signalpost.resource_report/1",
        # Relative to the working directory, so the report carries no machine-specific path.
        "command": [portable(arg) for arg in command],
        "working_directory": "repository root (the directory the command was run from)",
        "started_at": started_at,
        "completed_at": utc_now(),
        "exit_code": returncode,
        "wall_seconds": round(wall, 3),
        **usage,
        "peak_memory_mib": round(usage["peak_memory_bytes"] / 1048576, 1),
        "mean_cpu_cores_used": round(cpu / wall, 3) if wall else None,
        "disk_bytes": {str(p).replace("\\", "/"): tree_bytes(p) for p in args.disk if p.exists()},
        "host": {"platform": platform.platform(), "python": platform.python_version(), "logical_cpus": os.cpu_count()},
    }
    report["disk_mib_total"] = round(sum(report["disk_bytes"].values()) / 1048576, 1)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("exit_code", "wall_seconds", "peak_memory_mib", "mean_cpu_cores_used", "disk_mib_total")}))
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
