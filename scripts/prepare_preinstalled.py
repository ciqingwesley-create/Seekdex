"""Stage a strict, verified model-only payload; never collect user cache trees."""
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from seekdex.ai import model_cache as ai
from seekdex.ocr import model_cache as ocr
from seekdex.bundled_models import required_files, sha256
from seekdex.paths import get_model_cache_dir, get_ocr_model_root


def prepare(destination: Path, ai_root: Path, ocr_root: Path) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    entries = {}
    for name, expected in required_files().items():
        relative = Path(name)
        source = (ocr_root / Path(*relative.parts[1:]) if relative.parts[0] == "ocr"
                  else ai_root / relative)
        digest = sha256(source)
        if expected and digest != expected:
            raise ValueError(f"模型校验失败：{source.name}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        entries[name] = {"bytes": target.stat().st_size, "sha256": digest}
    manifest = dict(schema=1, ai_model_id=ai.MODEL_ID, ocr_model_id=ocr.MODEL_ID, files=entries)
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf8")
    shutil.copyfile(ROOT / "THIRD_PARTY_LICENSES.md", destination / "MODEL-NOTICES.md")
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ai-root", type=Path, default=get_model_cache_dir())
    parser.add_argument("--ocr-root", type=Path, default=get_ocr_model_root())
    args = parser.parse_args()
    payload = ROOT / "build" / "preinstalled-models"
    if payload.exists():
        if not payload.resolve().is_relative_to((ROOT / "build").resolve()):
            raise ValueError("Invalid staging directory")
        shutil.rmtree(payload)
    manifest = prepare(payload, args.ai_root, args.ocr_root)
    print(json.dumps({"files": len(manifest["files"]),
                      "bytes": sum(value["bytes"] for value in manifest["files"].values())}))


if __name__ == "__main__":
    main()
