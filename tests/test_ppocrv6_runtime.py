from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from deepcat.ppocrv6_runtime import (
    PpOcrV6ModelError,
    _deduplicate_entries,
    _extract_result_entries,
    resolve_ppocrv6_model_root,
    verify_ppocrv6_models,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_bundled_ppocrv6_small_models_match_manifest() -> None:
    model_root = resolve_ppocrv6_model_root(PROJECT_ROOT)

    manifest = verify_ppocrv6_models(model_root)

    assert manifest["models"]["detection"]["revision"] == "28fe5895c24fd108c19eb3e8479f4ab385fbfc62"
    assert manifest["models"]["recognition"]["revision"] == "b8f84f0b80c529de40b4fbb3544b84fa7233a513"
    assert {entry["path"] for entry in manifest["files"]} == {
        "detection/inference.onnx",
        "detection/inference.yml",
        "detection/inference.json",
        "recognition/inference.onnx",
        "recognition/inference.yml",
        "recognition/inference.json",
    }


def test_model_verification_rejects_modified_file(tmp_path: Path) -> None:
    model_root = tmp_path / "models" / "pp-ocrv6-small"
    model_root.mkdir(parents=True)
    model_file = model_root / "inference.onnx"
    model_file.write_bytes(b"expected")
    manifest = {
        "files": [
            {
                "path": "inference.onnx",
                "size": len(b"expected"),
                "sha256": hashlib.sha256(b"expected").hexdigest(),
            }
        ]
    }
    (model_root / "model_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    model_file.write_bytes(b"modified")

    with pytest.raises(PpOcrV6ModelError, match="大小不匹配|校验失败"):
        verify_ppocrv6_models(model_root)


def test_result_adapter_uses_official_ppocrv6_fields() -> None:
    result = {
        "rec_texts": ["第一行", "second"],
        "rec_scores": np.array([0.98, 0.91]),
        "rec_boxes": np.array([[10, 20, 80, 42], [12, 60, 92, 84]]),
        "rec_polys": [],
    }

    entries = _extract_result_entries([result], y_offset=100.0)

    assert entries == [
        {"text": "第一行", "box": [10.0, 120.0, 80.0, 142.0], "score": pytest.approx(0.98)},
        {"text": "second", "box": [12.0, 160.0, 92.0, 184.0], "score": pytest.approx(0.91)},
    ]


def test_overlapping_tiles_keep_higher_confidence_duplicate() -> None:
    entries = [
        {"text": "重复文字", "box": [10.0, 2900.0, 120.0, 2940.0], "score": 0.7},
        {"text": "重复文字", "box": [11.0, 2901.0, 121.0, 2941.0], "score": 0.95},
        {"text": "下一行", "box": [10.0, 3000.0, 100.0, 3040.0], "score": 0.8},
    ]

    deduplicated = _deduplicate_entries(entries)

    assert [entry["text"] for entry in deduplicated] == ["重复文字", "下一行"]
    assert deduplicated[0]["score"] == 0.95
