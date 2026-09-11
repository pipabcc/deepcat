from __future__ import annotations

import re
from typing import Any


def format_ocr_entries(entries: list[dict[str, Any]]) -> str:
    """按识别框的空间位置恢复自然段和行内间距。

    无有效框的条目（如 rec_only 兜底或框解析失败）不参与空间分行，
    按识别顺序以空格拼接后追加在末尾，避免被聚成 y=0 的"幽灵首行"。
    """

    rows: list[dict[str, Any]] = []
    unboxed_texts: list[str] = []
    for entry in entries:
        text = str(entry.get("text") or "").strip()
        if not text:
            continue
        box = entry.get("box")
        parsed: tuple[float, float, float, float] | None = None
        if isinstance(box, (list, tuple)) and len(box) == 4:
            try:
                x1, y1, x2, y2 = (float(value) for value in box)
            except (TypeError, ValueError):
                parsed = None
            else:
                if x1 == 0.0 and y1 == 0.0 and x2 == 0.0 and y2 == 0.0:
                    parsed = None  # (0,0,0,0) 是缺失框的占位值，不是真实坐标
                else:
                    parsed = (x1, y1, x2, y2)
        if parsed is None:
            unboxed_texts.append(text)
        else:
            rows.append({"text": text, "x1": parsed[0], "y1": parsed[1], "x2": parsed[2], "y2": parsed[3]})

    if not rows and not unboxed_texts:
        return ""

    output_lines: list[str] = []
    if rows:
        rows.sort(key=lambda row: (float(row["y1"]), float(row["x1"])))
        heights = sorted(max(1.0, float(row["y2"]) - float(row["y1"])) for row in rows)
        median_height = heights[len(heights) // 2] if heights else 18.0
        line_threshold = max(6.0, median_height * 0.5)
        lines: list[list[dict[str, Any]]] = []
        for row in rows:
            if not lines:
                lines.append([row])
                continue
            last_line = lines[-1]
            y_reference = sum(float(item["y1"]) for item in last_line) / float(len(last_line))
            if abs(float(row["y1"]) - y_reference) <= line_threshold:
                last_line.append(row)
            else:
                lines.append([row])

        for line in lines:
            line.sort(key=lambda row: float(row["x1"]))

        min_x = min(float(line[0]["x1"]) for line in lines if line)
        last_y: float | None = None
        for line in lines:
            if not line:
                continue
            y_line = min(float(row["y1"]) for row in line)
            line_height = max(1.0, max(float(row["y2"]) - float(row["y1"]) for row in line))
            if last_y is not None and (y_line - last_y) > max(14.0, line_height * 1.35):
                output_lines.append("")

            indent_level = int(max(0.0, (float(line[0]["x1"]) - min_x) / 26.0))
            pieces: list[str] = []
            previous_x2: float | None = None
            for token in line:
                text = str(token["text"]).strip()
                if not text:
                    continue
                if previous_x2 is not None:
                    gap = float(token["x1"]) - previous_x2
                    if gap >= max(3.0, line_height * 0.28):
                        pieces.append(" ")
                pieces.append(text)
                previous_x2 = float(token["x2"])

            rendered_line = "".join(pieces).strip()
            if indent_level > 0:
                rendered_line = ("  " * indent_level) + rendered_line
            output_lines.append(rendered_line)
            last_y = y_line

    if unboxed_texts:
        if output_lines:
            output_lines.append("")
        output_lines.append(" ".join(unboxed_texts))

    return re.sub(r"\n{3,}", "\n\n", "\n".join(output_lines).strip())
