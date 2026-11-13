"""설정 파일과 저장 위치 관리."""

import json
import os
from pathlib import Path


def app_dir() -> Path:
    """설정·토큰·진행 기록이 저장되는 폴더 (없으면 만든다)."""
    path = Path(os.environ.get("PHOTOSYNC_HOME") or Path.home() / ".onedrive-photo-sync")
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_json(path: Path, data: dict, private: bool = False) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if private:
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
    os.replace(tmp, path)
