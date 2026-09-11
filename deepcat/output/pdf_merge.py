from __future__ import annotations

from pathlib import Path
from typing import Iterable

from deepcat.output.naming import unique_output_path


def merge_pdfs(pdf_paths: Iterable[str], output_path: str) -> str:
    try:
        from pypdf import PdfReader, PdfWriter
    except Exception as e:
        raise RuntimeError("缺少依赖 pypdf，请先安装：pip install pypdf") from e

    writer = PdfWriter()
    paths = [str(p) for p in pdf_paths if str(p).lower().endswith(".pdf")]
    if not paths:
        raise RuntimeError("没有可合并的 PDF 文件")

    failures: list[str] = []
    for p in paths:
        try:
            # 上下文管理器确保每个文件句柄及时释放（合并数百个 PDF 时避免句柄堆积）
            with PdfReader(p) as reader:
                for page in reader.pages:
                    writer.add_page(page)
        except Exception as e:
            # 加密/损坏的 PDF 单独报告文件名，不中断其余文件的合并
            failures.append(f"{Path(p).name}: {e}")

    if not writer.pages and failures:
        raise RuntimeError("所有 PDF 合并失败：" + "；".join(failures))

    if not writer.pages:
        # 一页都没合进来：直接报错，不产出空 PDF
        if failures:
            raise RuntimeError("所有 PDF 合并失败：" + "；".join(failures))
        raise RuntimeError("没有可合并的 PDF 页面")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    # 输出路径冲突时自动改名，避免静默覆盖已有文件
    if out.exists():
        out = unique_output_path(str(out.parent), out.suffix.lstrip(".").lower() or "pdf", prefix=out.stem)
    with out.open("wb") as f:
        writer.write(f)

    if failures:
        # 已生成合并结果（可能缺页），同时把失败明细抛给上层展示
        raise RuntimeError("部分 PDF 合并失败：" + "；".join(failures))

    return str(out)
