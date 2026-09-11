from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from deepcat.ocr_engines import OCR_ENGINE_PPOCRV6
from deepcat.ocr_text_layout import format_ocr_entries
from deepcat.utils.paths import get_app_dir


PPOCRV6_MODEL_SUBDIR = Path("models") / "pp-ocrv6-small"
PPOCRV6_MANIFEST_NAME = "model_manifest.json"
PPOCRV6_VERIFY_MARKER_NAME = ".verified.marker.json"
PPOCRV6_DETECTION_MODEL_NAME = "PP-OCRv6_small_det"
PPOCRV6_RECOGNITION_MODEL_NAME = "PP-OCRv6_small_rec"


class PpOcrV6ModelError(RuntimeError):
    """PP-OCRv6 本地模型缺失或完整性校验失败。"""


def _model_root_candidates(app_dir: Optional[Path] = None) -> Iterable[Path]:
    bases: list[Path] = []
    if app_dir is not None:
        bases.append(Path(app_dir))
    else:
        bases.append(get_app_dir())
        module_root = Path(__file__).resolve().parents[1]
        bases.append(module_root)
        if bool(getattr(sys, "frozen", False)):
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                bases.append(Path(meipass))
            executable_dir = Path(sys.executable).resolve().parent
            bases.extend((executable_dir, executable_dir / "_internal"))

    seen: set[str] = set()
    for base in bases:
        candidate = (base / PPOCRV6_MODEL_SUBDIR).resolve()
        key = os.path.normcase(str(candidate))
        if key in seen:
            continue
        seen.add(key)
        yield candidate


def resolve_ppocrv6_model_root(app_dir: Optional[Path] = None) -> Path:
    tried: list[str] = []
    for candidate in _model_root_candidates(app_dir):
        tried.append(str(candidate))
        if (candidate / PPOCRV6_MANIFEST_NAME).is_file():
            return candidate
    joined = "；".join(tried)
    raise PpOcrV6ModelError(f"PP-OCRv6 Small 模型目录不存在，请重新安装 DeepCat。已检查：{joined}")


def _manifest_fingerprint(manifest_path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    stat = manifest_path.stat()
    digest = hashlib.sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return {
        "manifest_sha256": digest,
        "manifest_size": int(stat.st_size),
        "manifest_mtime_ns": int(stat.st_mtime_ns),
    }


def _verification_marker_valid(root: Path, manifest_path: Path, manifest: dict[str, Any]) -> bool:
    """安装后已完成一次全量 SHA-256 校验且清单未变化时，跳过重复校验。"""
    marker_path = root / PPOCRV6_VERIFY_MARKER_NAME
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        expected = _manifest_fingerprint(manifest_path, manifest)
        return (
            str(marker.get("manifest_sha256", "")) == expected["manifest_sha256"]
            and int(marker.get("manifest_size", -1)) == expected["manifest_size"]
            and int(marker.get("manifest_mtime_ns", -1)) == expected["manifest_mtime_ns"]
        )
    except Exception:
        return False


def _write_verification_marker(root: Path, manifest_path: Path, manifest: dict[str, Any]) -> None:
    try:
        marker_path = root / PPOCRV6_VERIFY_MARKER_NAME
        marker_path.write_text(
            json.dumps(_manifest_fingerprint(manifest_path, manifest), ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        # 模型目录可能只读（如 Program Files），写不进去就每次启动重新校验
        pass


def verify_ppocrv6_models(model_root: Path, *, check_hashes: bool = True) -> dict[str, Any]:
    root = Path(model_root).resolve()
    manifest_path = root / PPOCRV6_MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PpOcrV6ModelError(f"PP-OCRv6 模型清单缺失：{manifest_path}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PpOcrV6ModelError(f"PP-OCRv6 模型清单无法读取：{manifest_path}") from exc

    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise PpOcrV6ModelError("PP-OCRv6 模型清单格式无效")

    need_hashes = check_hashes and not _verification_marker_valid(root, manifest_path, manifest)

    for entry in files:
        if not isinstance(entry, dict):
            raise PpOcrV6ModelError("PP-OCRv6 模型清单包含无效条目")
        relative_path = Path(str(entry.get("path") or ""))
        expected_size = int(entry.get("size") or 0)
        expected_hash = str(entry.get("sha256") or "").strip().lower()
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise PpOcrV6ModelError(f"PP-OCRv6 模型清单路径无效：{relative_path}")
        file_path = (root / relative_path).resolve()
        try:
            file_path.relative_to(root)
        except ValueError as exc:
            raise PpOcrV6ModelError(f"PP-OCRv6 模型清单路径越界：{relative_path}") from exc
        if not file_path.is_file():
            raise PpOcrV6ModelError(f"PP-OCRv6 模型文件缺失：{relative_path}")
        actual_size = file_path.stat().st_size
        if expected_size <= 0 or actual_size != expected_size:
            raise PpOcrV6ModelError(
                f"PP-OCRv6 模型文件大小不匹配：{relative_path}（期望 {expected_size}，实际 {actual_size}）"
            )
        if need_hashes:
            digest = hashlib.sha256()
            with file_path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            actual_hash = digest.hexdigest()
            if not expected_hash or actual_hash != expected_hash:
                raise PpOcrV6ModelError(f"PP-OCRv6 模型文件校验失败：{relative_path}")
    if check_hashes and need_hashes:
        # 全量校验通过后写入标记，后续 worker 启动只做大小校验
        _write_verification_marker(root, manifest_path, manifest)
    return manifest


def prepare_ppocrv6_offline_environment() -> Path:
    cache_root = Path(tempfile.gettempdir()) / "DeepCat" / "paddlex-runtime"
    cache_root.mkdir(parents=True, exist_ok=True)
    # PaddleX 在导入阶段就会创建缓存目录。固定到临时目录可避免安装目录只读，
    # 本地模型目录会显式传给产线，因此这里不会存放或下载模型。
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(cache_root)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["PADDLEOCR_DISABLE_AUTO_LOGGING_CONFIG"] = "1"
    return cache_root


def _normalise_box(value: Any) -> Optional[list[float]]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, (list, tuple)) or not value:
        return None
    try:
        if len(value) == 4 and not isinstance(value[0], (list, tuple)):
            return [float(value[0]), float(value[1]), float(value[2]), float(value[3])]
        points = [point.tolist() if hasattr(point, "tolist") else point for point in value]
        xs = [float(point[0]) for point in points if isinstance(point, (list, tuple)) and len(point) >= 2]
        ys = [float(point[1]) for point in points if isinstance(point, (list, tuple)) and len(point) >= 2]
        if xs and ys:
            return [min(xs), min(ys), max(xs), max(ys)]
    except (TypeError, ValueError, IndexError):
        return None
    return None


def _sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _extract_result_entries(results: Any, *, y_offset: float = 0.0) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for result in _sequence(results):
        if not isinstance(result, Mapping) and not hasattr(result, "get"):
            continue
        texts = _sequence(result.get("rec_texts"))
        scores = _sequence(result.get("rec_scores"))
        boxes = _sequence(result.get("rec_boxes"))
        polygons = _sequence(result.get("rec_polys"))
        for index, raw_text in enumerate(texts):
            text = str(raw_text or "").strip()
            if not text:
                continue
            raw_box = boxes[index] if index < len(boxes) else None
            if raw_box is None and index < len(polygons):
                raw_box = polygons[index]
            box = _normalise_box(raw_box)
            if box is not None and y_offset:
                box[1] += y_offset
                box[3] += y_offset
            try:
                score = float(scores[index]) if index < len(scores) else 0.0
            except (TypeError, ValueError):
                score = 0.0
            entries.append({"text": text, "box": box, "score": score})
    return entries


def _box_iou(first: list[float], second: list[float]) -> float:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    first_area = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
    second_area = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
    union = first_area + second_area - intersection
    return intersection / union if union > 0.0 else 0.0


def _deduplicate_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for entry in sorted(entries, key=lambda item: (float((item.get("box") or [0, 0])[1]), str(item.get("text")))):
        text = str(entry.get("text") or "").strip()
        box = entry.get("box")
        duplicate_index: Optional[int] = None
        if isinstance(box, list) and len(box) == 4:
            for index in range(len(output) - 1, -1, -1):
                existing = output[index]
                existing_box = existing.get("box")
                if not isinstance(existing_box, list) or len(existing_box) != 4:
                    continue
                if float(box[1]) - float(existing_box[3]) > 220.0:
                    break
                if text == str(existing.get("text") or "").strip() and _box_iou(box, existing_box) >= 0.45:
                    duplicate_index = index
                    break
        if duplicate_index is None:
            output.append(entry)
        elif float(entry.get("score") or 0.0) > float(output[duplicate_index].get("score") or 0.0):
            output[duplicate_index] = entry
    return output


class PpOcrV6Runner:
    """PaddleOCR 3.7 官方产线的 PP-OCRv6 Small ONNX 离线运行器。"""

    _ocr_engine_name = OCR_ENGINE_PPOCRV6
    _TILE_HEIGHT = 3000
    _TILE_OVERLAP = 192
    _TILE_THRESHOLD = 4000

    def __init__(self, model_root: Optional[Path] = None, *, verify_hashes: bool = True) -> None:
        prepare_ppocrv6_offline_environment()
        root = Path(model_root).resolve() if model_root is not None else resolve_ppocrv6_model_root()
        verify_ppocrv6_models(root, check_hashes=verify_hashes)

        from paddleocr import PaddleOCR

        self._model_root = root
        self._pipeline = PaddleOCR(
            text_detection_model_name=PPOCRV6_DETECTION_MODEL_NAME,
            text_detection_model_dir=str(root / "detection"),
            text_recognition_model_name=PPOCRV6_RECOGNITION_MODEL_NAME,
            text_recognition_model_dir=str(root / "recognition"),
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            text_rec_score_thresh=0.1,
            device="cpu",
            engine="onnxruntime",
            engine_config={
                "device_type": "cpu",
                "providers": ["CPUExecutionProvider"],
                "intra_op_num_threads": 2,
                "inter_op_num_threads": 1,
                "execution_mode": "sequential",
            },
        )

    def _predict(self, image_bgr: Any, *, y_offset: int, metrics: dict[str, Any], pass_name: str):
        started_at = time.perf_counter()
        try:
            results = self._pipeline.predict(
                image_bgr,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
            return _extract_result_entries(results, y_offset=float(y_offset))
        finally:
            metrics["pass_count"] = int(metrics.get("pass_count", 0)) + 1
            metrics.setdefault("passes", []).append(pass_name)
            metrics["inference_ms"] = float(metrics.get("inference_ms", 0.0)) + (
                time.perf_counter() - started_at
            ) * 1000.0

    def recognize(
        self,
        image_bgr: Any,
        *,
        budget_seconds: float,
        metrics: Optional[dict[str, Any]] = None,
    ) -> tuple[str, str]:
        perf = metrics if metrics is not None else {}
        started_at = time.perf_counter()
        perf.update({"engine": OCR_ENGINE_PPOCRV6, "pass_count": 0, "passes": [], "inference_ms": 0.0})
        try:
            import cv2

            if image_bgr is None or getattr(image_bgr, "size", 0) <= 0:
                return "", ""
            blank_started_at = time.perf_counter()
            gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
            standard_deviation = float(cv2.meanStdDev(gray)[1][0][0])
            perf["blank_check_ms"] = (time.perf_counter() - blank_started_at) * 1000.0
            if standard_deviation < 2.0:
                perf["blank_fast_path"] = True
                return "", ""

            height, width = image_bgr.shape[:2]
            perf["input_shape"] = [int(height), int(width)]
            perf["bucket"] = "long" if height > self._TILE_THRESHOLD else "normal"
            deadline = time.perf_counter() + max(1.0, float(budget_seconds))
            entries: list[dict[str, Any]] = []
            if height <= self._TILE_THRESHOLD:
                entries.extend(self._predict(image_bgr, y_offset=0, metrics=perf, pass_name="ppocrv6"))
            else:
                step = self._TILE_HEIGHT - self._TILE_OVERLAP
                top = 0
                tile_index = 0
                # 文本行被 tile 内部边界切割时会产生重复/残缺行；重叠带（行高 ≤ overlap）
                # 内上一块/下一块总有完整版本，因此丢弃贴着内部上/下边缘的检测框。
                edge_eps = 3.0
                while top < height and time.perf_counter() < deadline:
                    bottom = min(height, top + self._TILE_HEIGHT)
                    has_prev = tile_index > 0
                    has_next = bottom < height
                    tile_entries = self._predict(
                        image_bgr[top:bottom],
                        y_offset=top,
                        metrics=perf,
                        pass_name=f"ppocrv6_tile_{tile_index}",
                    )
                    if has_prev or has_next:
                        for entry in tile_entries:
                            box = entry.get("box")
                            if isinstance(box, list) and len(box) == 4:
                                if has_prev and float(box[1]) - float(top) <= edge_eps:
                                    perf["tile_edge_dropped"] = int(perf.get("tile_edge_dropped", 0)) + 1
                                    continue
                                if has_next and float(bottom) - float(box[3]) <= edge_eps:
                                    perf["tile_edge_dropped"] = int(perf.get("tile_edge_dropped", 0)) + 1
                                    continue
                            entries.append(entry)
                    else:
                        entries.extend(tile_entries)
                    if bottom >= height:
                        break
                    top += step
                    tile_index += 1

                if top < height:
                    # 预算耗尽：剩余分块未识别。明确标记并回传提示，避免用户误以为长图已识别完整。
                    perf["truncated"] = True
                    perf["truncated_pending_height"] = int(height - top)
                    perf["line_count"] = len(_deduplicate_entries(entries))
                    return format_ocr_entries(_deduplicate_entries(entries)), (
                        f"长图过大，识别预算耗尽，剩余 {int(height - top)}px 未识别，结果可能不完整"
                    )

            filtered = _deduplicate_entries(entries)
            perf["line_count"] = len(filtered)
            return format_ocr_entries(filtered), ""
        except Exception as exc:
            return "", str(exc)
        finally:
            perf["total_ms"] = (time.perf_counter() - started_at) * 1000.0
