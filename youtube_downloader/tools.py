"""필수 외부 도구(ffmpeg, ffprobe, deno) 확인 및 윈도우용 자동 설치.

- ffmpeg/ffprobe : 영상+오디오 합치기, MP3/WAV 변환, H.264 재인코딩
- deno          : 최신 유튜브 다운로드에 필요한 JavaScript 실행기 (yt-dlp 권장)

처음 실행할 때 프로그램 폴더의 bin\\ 에 자동으로 받아 둔다.
"""
from __future__ import annotations

import os
import shutil
import threading
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

APP_DIR = Path(__file__).resolve().parent
BIN_DIR = APP_DIR / "bin"

IS_WINDOWS = os.name == "nt"

FFMPEG_URL = ("https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/"
              "ffmpeg-master-latest-win64-gpl.zip")
DENO_URL = ("https://github.com/denoland/deno/releases/latest/download/"
            "deno-x86_64-pc-windows-msvc.zip")


def _exe(name: str) -> str:
    return name + ".exe" if IS_WINDOWS else name


def setup_path() -> None:
    """bin 폴더를 PATH 맨 앞에 넣어 yt-dlp가 ffmpeg/deno를 찾게 한다."""
    BIN_DIR.mkdir(exist_ok=True)
    path = os.environ.get("PATH", "")
    if str(BIN_DIR) not in path.split(os.pathsep):
        os.environ["PATH"] = str(BIN_DIR) + os.pathsep + path


def find_tool(name: str) -> str | None:
    local = BIN_DIR / _exe(name)
    if local.exists():
        return str(local)
    return shutil.which(name)


REQUIRED = ("ffmpeg", "ffprobe")
# deno는 없어도 동작은 하지만 일부 화질이 빠질 수 있다. 윈도우에서만 자동 설치.
OPTIONAL = ("deno",) if IS_WINDOWS else ()


def missing_tools(include_optional: bool = True) -> list[str]:
    names = REQUIRED + (OPTIONAL if include_optional else ())
    return [n for n in names if not find_tool(n)]


def _download(url: str, dest: Path, label: str,
              progress: Callable[[float | None, str], None],
              cancel: threading.Event) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as f:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            if cancel.is_set():
                raise RuntimeError("취소됨")
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            frac = done / total if total else None
            tot = f" / {total / 1024 / 1024:.0f}MB" if total else ""
            progress(frac, f"{label} 받는 중... {done / 1024 / 1024:.0f}MB{tot}")
    tmp.replace(dest)


def _extract(zip_path: Path, names: list[str]) -> None:
    """zip 안에서 원하는 exe 파일만 bin 폴더로 꺼낸다 (하위 폴더 무시)."""
    wanted = {n.lower() for n in names}
    found = set()
    with zipfile.ZipFile(zip_path) as z:
        for member in z.infolist():
            base = member.filename.replace("\\", "/").rsplit("/", 1)[-1]
            if base.lower() in wanted and not member.is_dir():
                with z.open(member) as src, open(BIN_DIR / base, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                found.add(base.lower())
    if found != wanted:
        raise RuntimeError(f"압축 파일에서 {', '.join(sorted(wanted - found))} 을(를) 찾지 못했습니다.")


def install_missing(log: Callable[[str], None],
                    progress: Callable[[float | None, str], None],
                    cancel: threading.Event | None = None) -> list[str]:
    """빠진 도구를 설치하고, 그래도 없는 도구 목록을 돌려준다."""
    cancel = cancel or threading.Event()
    setup_path()
    missing = missing_tools()
    if not missing:
        return []
    if not IS_WINDOWS:
        log("ffmpeg를 직접 설치해 주세요 (자동 설치는 윈도우만 지원): " + ", ".join(missing))
        return missing

    if "ffmpeg" in missing or "ffprobe" in missing:
        log("ffmpeg를 내려받습니다 (최초 1회, 약 200MB)...")
        z = BIN_DIR / "_ffmpeg.zip"
        try:
            _download(FFMPEG_URL, z, "ffmpeg", progress, cancel)
            _extract(z, ["ffmpeg.exe", "ffprobe.exe"])
            log("✔ ffmpeg 설치 완료")
        finally:
            z.unlink(missing_ok=True)

    if "deno" in missing:
        log("Deno(유튜브 해독용 자바스크립트 실행기)를 내려받습니다 (최초 1회, 약 45MB)...")
        z = BIN_DIR / "_deno.zip"
        try:
            _download(DENO_URL, z, "Deno", progress, cancel)
            _extract(z, ["deno.exe"])
            log("✔ Deno 설치 완료")
        except Exception as e:  # 필수는 아니므로 경고만
            if cancel.is_set():
                raise
            log(f"⚠ Deno 설치 실패 ({e}). 다운로드는 되지만 일부 화질이 안 보일 수 있습니다. "
                "다음 실행 때 다시 시도합니다.")
        finally:
            z.unlink(missing_ok=True)

    return missing_tools(include_optional=False)
