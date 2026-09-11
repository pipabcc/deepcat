from __future__ import annotations


OCR_ENGINE_RAPIDOCR = "rapidocr"
OCR_ENGINE_PPOCRV6 = "ppocrv6"
SUPPORTED_OCR_ENGINES = frozenset({OCR_ENGINE_RAPIDOCR, OCR_ENGINE_PPOCRV6})


def normalize_ocr_engine(value: object) -> str:
    engine = str(value or OCR_ENGINE_RAPIDOCR).strip().lower()
    if engine not in SUPPORTED_OCR_ENGINES:
        raise ValueError(f"不支持的 OCR 引擎：{engine}")
    return engine


def ocr_engine_display_name(value: object) -> str:
    engine = normalize_ocr_engine(value)
    if engine == OCR_ENGINE_PPOCRV6:
        return "PP-OCRv6"
    return "OCR"
