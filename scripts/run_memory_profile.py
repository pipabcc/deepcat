from __future__ import annotations

import argparse
import atexit
import datetime as dt
import gc
import os
import sys
import threading
import time
import tracemalloc
from pathlib import Path
from typing import Iterable


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover - optional diagnostic dependency
    psutil = None


def _timestamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _format_bytes(size: int | float) -> str:
    return f"{float(size) / (1024 * 1024):.2f} MiB"


def _process_memory_lines() -> list[str]:
    if psutil is None:
        return ["process_memory: psutil unavailable"]
    process = psutil.Process(os.getpid())
    info = process.memory_info()
    lines = [
        f"rss: {_format_bytes(info.rss)}",
        f"vms: {_format_bytes(info.vms)}",
    ]
    private = getattr(info, "private", None)
    if private is not None:
        lines.append(f"private: {_format_bytes(private)}")
    return lines


def _snapshot_report_lines(snapshot: tracemalloc.Snapshot, limit: int) -> Iterable[str]:
    stats = snapshot.statistics("lineno")
    for index, stat in enumerate(stats[:limit], start=1):
        frame = stat.traceback[0]
        path = frame.filename
        try:
            path = str(Path(path).resolve().relative_to(ROOT_DIR))
        except Exception:
            pass
        yield f"{index:02d}. {path}:{frame.lineno} - {_format_bytes(stat.size)} in {stat.count} blocks"


def write_profile_sample(profile_dir: Path, label: str, limit: int, dump_snapshot: bool) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    gc.collect()

    current, peak = tracemalloc.get_traced_memory()
    snapshot = tracemalloc.take_snapshot()
    now = _timestamp()
    base_name = f"{now}-{label}"

    if dump_snapshot:
        snapshot.dump(str(profile_dir / f"{base_name}.tracemalloc"))

    report_path = profile_dir / f"{base_name}.txt"
    lines = [
        f"label: {label}",
        f"time: {dt.datetime.now().isoformat(timespec='seconds')}",
        f"tracemalloc_current: {_format_bytes(current)}",
        f"tracemalloc_peak: {_format_bytes(peak)}",
        "",
        "[process memory]",
        *_process_memory_lines(),
        "",
        "[top Python allocations by line]",
        *_snapshot_report_lines(snapshot, limit),
        "",
        "note: tracemalloc tracks Python allocator activity. Native memory from Qt, OpenCV, numpy, "
        "onnxruntime, and graphics resources may appear in RSS/private memory but not in the Python "
        "allocation table above.",
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")


def start_periodic_sampler(
    profile_dir: Path,
    interval_seconds: float,
    limit: int,
    dump_snapshot: bool,
) -> threading.Event:
    stop_event = threading.Event()

    def run() -> None:
        sample_index = 1
        while not stop_event.wait(interval_seconds):
            try:
                write_profile_sample(
                    profile_dir,
                    f"sample-{sample_index:04d}",
                    limit=limit,
                    dump_snapshot=dump_snapshot,
                )
            except Exception as exc:
                print(f"[memory-profile] failed to write periodic sample: {exc!r}", file=sys.stderr)
            sample_index += 1

    thread = threading.Thread(target=run, name="memory-profile-sampler", daemon=True)
    thread.start()
    return stop_event


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run DeepCat with tracemalloc sampling enabled.",
        add_help=True,
    )
    parser.add_argument(
        "--profile-dir",
        default=str(ROOT_DIR / "memory_profiles"),
        help="Directory used for text reports and .tracemalloc snapshots.",
    )
    parser.add_argument(
        "--profile-interval",
        type=float,
        default=30.0,
        help="Seconds between periodic memory samples.",
    )
    parser.add_argument(
        "--profile-depth",
        type=int,
        default=25,
        help="Traceback depth recorded by tracemalloc.",
    )
    parser.add_argument(
        "--profile-limit",
        type=int,
        default=40,
        help="Number of top allocation lines written to each report.",
    )
    parser.add_argument(
        "--profile-no-snapshots",
        action="store_true",
        help="Write text reports only, without binary .tracemalloc snapshots.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    profile_args, deepcat_args = parser.parse_known_args()

    profile_dir = Path(profile_args.profile_dir).resolve()
    interval_seconds = max(1.0, float(profile_args.profile_interval))
    depth = max(1, int(profile_args.profile_depth))
    limit = max(1, int(profile_args.profile_limit))
    dump_snapshot = not bool(profile_args.profile_no_snapshots)

    tracemalloc.start(depth)
    stop_sampler = start_periodic_sampler(profile_dir, interval_seconds, limit, dump_snapshot)

    def write_final_sample() -> None:
        stop_sampler.set()
        try:
            write_profile_sample(profile_dir, "final", limit=limit, dump_snapshot=dump_snapshot)
        except Exception as exc:
            print(f"[memory-profile] failed to write final sample: {exc!r}", file=sys.stderr)

    atexit.register(write_final_sample)
    write_profile_sample(profile_dir, "startup", limit=limit, dump_snapshot=dump_snapshot)

    from deepcat.main import main as deepcat_main

    return int(deepcat_main(deepcat_args))


if __name__ == "__main__":
    raise SystemExit(main())
