from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from deepcat.ppocrv6_runtime import resolve_ppocrv6_model_root, verify_ppocrv6_models


def main() -> int:
    parser = argparse.ArgumentParser(description="校验安装包内 PP-OCRv6 Small 官方 ONNX 模型")
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()

    project_root = args.project_root.resolve()
    model_root = resolve_ppocrv6_model_root(project_root)
    manifest = verify_ppocrv6_models(model_root, check_hashes=True)
    files = manifest.get("files") if isinstance(manifest, dict) else []
    print(
        json.dumps(
            {
                "model_root": str(model_root),
                "file_count": len(files) if isinstance(files, list) else 0,
                "status": "ok",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
