"""用独立临时数据库测量剪贴板列表与搜索，不读取真实剪贴板记录。"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timedelta
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deepcat.clipboard_history.clipboard_database import ClipboardDatabase


def benchmark(count: int, repeats: int, root: Path) -> dict:
    database = ClipboardDatabase(str(root / f"clipboard-{count}.db"), auto_cleanup_enabled=False)
    base = datetime(2026, 9, 5)
    body = "普通剪贴板内容 alpha beta gamma " + "x" * 2000
    with closing(database._connect()) as connection, connection:
        connection.executemany(
            "INSERT INTO clipboard_records(content, content_type, timestamp, normalized_hash, data_size_bytes) "
            "VALUES (?, 'text', ?, ?, 2100)",
            (
                (
                    f"{i} {body}" + (" audit_unique_target " if i == 0 else ""),
                    (base + timedelta(seconds=i)).isoformat(),
                    str(i),
                )
                for i in range(count)
            ),
        )
        connection.execute(
            "INSERT INTO clipboard_records_fts(rowid, content, file_path, tags) "
            "SELECT id, content, file_path, tags FROM clipboard_records"
        )
    search_times, list_times = [], []
    for _ in range(repeats):
        start = time.perf_counter()
        found = database.search_records("audit_unique_target", limit=50)
        search_times.append((time.perf_counter() - start) * 1000)
        assert len(found) == 1
        start = time.perf_counter()
        database.get_records(limit=50)
        list_times.append((time.perf_counter() - start) * 1000)
    return {
        "记录数": count,
        "重复次数": repeats,
        "子串索引可用": database._substring_index_available,
        "首屏列表中位数_ms": round(statistics.median(list_times), 2),
        "搜索中位数_ms": round(statistics.median(search_times), 2),
        "搜索最慢_ms": round(max(search_times), 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--counts", default="1000,10000,50000", help="用逗号分隔的记录数")
    parser.add_argument("--repeats", type=int, default=5, help="每种规模重复测量次数")
    parser.add_argument("--output", type=Path, help="将结果写入 JSON 文件")
    args = parser.parse_args()
    counts = [int(value) for value in args.counts.split(",")]
    if args.repeats < 1 or any(value < 1 for value in counts):
        parser.error("记录数和重复次数必须大于零")
    with tempfile.TemporaryDirectory(prefix="deepcat-search-benchmark-") as directory:
        results = []
        for count in counts:
            result = benchmark(count, args.repeats, Path(directory))
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
