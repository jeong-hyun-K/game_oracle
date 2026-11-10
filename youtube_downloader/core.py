"""유튜브 다운로드 핵심 로직 (GUI와 분리되어 있어 단독 테스트 가능).

- 영상: 프리미어 프로 호환 MP4 (H.264 영상 + AAC 오디오)
- 오디오: 오디션용 MP3 / WAV
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable

import yt_dlp

# ----------------------------------------------------------------------------
# 선택지
# ----------------------------------------------------------------------------
VIDEO_QUALITIES: list[tuple[str, int | None]] = [
    ("최고 화질", None),
    ("2160p (4K)", 2160),
    ("1440p (2K)", 1440),
    ("1080p (FHD)", 1080),
    ("720p (HD)", 720),
    ("480p", 480),
    ("360p", 360),
]

AUDIO_FORMATS: list[tuple[str, str]] = [
    ("MP3", "mp3"),
    ("WAV (무손실 · 48kHz, 편집용 추천)", "wav"),
]

MP3_BITRATES = ["320", "256", "192", "128"]

ENCODERS: list[tuple[str, str]] = [
    ("CPU (x264 · 느리지만 어디서나 동작)", "libx264"),
    ("NVIDIA 그래픽카드 (NVENC · 빠름)", "h264_nvenc"),
    ("Intel 내장그래픽 (QSV · 빠름)", "h264_qsv"),
    ("AMD 그래픽카드 (AMF · 빠름)", "h264_amf"),
]

COOKIE_SOURCES: list[tuple[str, str | None]] = [
    ("사용 안 함", None),
    ("Firefox (추천)", "firefox"),
    ("Chrome", "chrome"),
    ("Edge", "edge"),
    ("Whale (네이버 웨일)", "whale"),
    ("Brave", "brave"),
    ("cookies.txt 파일 사용", "file"),
]

# 프리미어 프로가 안정적으로 읽는 코덱
PREMIERE_VCODECS = {"h264"}
PREMIERE_ACODECS = {"aac"}


class Cancelled(Exception):
    """사용자가 취소 버튼을 눌렀을 때."""


@dataclass
class Job:
    url: str
    out_dir: str
    mode: str = "video"            # "video" | "audio"
    height: int | None = 1080      # None = 최고 화질
    reencode: bool = True          # 비호환 코덱이면 H.264로 변환
    encoder: str = "libx264"
    audio_format: str = "mp3"      # "mp3" | "wav"
    mp3_bitrate: str = "320"
    playlist: bool = False
    cookie_browser: str | None = None
    cookie_file: str | None = None


LogFn = Callable[[str], None]
ProgressFn = Callable[[float | None, str], None]


# ----------------------------------------------------------------------------
# 유틸
# ----------------------------------------------------------------------------
def _hidden_popen_kwargs() -> dict:
    """윈도우에서 ffmpeg 실행 시 검은 콘솔 창이 뜨지 않게 한다."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def _fmt_bytes(n: float | None) -> str:
    if not n:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def _fmt_time(sec: float | None) -> str:
    if sec is None:
        return "?"
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def friendly_error(msg: str) -> str:
    """yt-dlp 오류 메시지에 한국어 해결 방법을 덧붙인다."""
    hints = []
    low = msg.lower()
    if "sign in to confirm" in low or "not a bot" in low:
        hints.append("유튜브가 봇 확인을 요구합니다. '쿠키' 옵션에서 유튜브에 로그인된 "
                     "브라우저를 선택하세요. (Chrome/Edge는 브라우저를 완전히 닫아야 할 수 있으며, "
                     "Firefox가 가장 잘 됩니다)")
    if "could not copy" in low and "cookie" in low:
        hints.append("브라우저가 쿠키 파일을 잠그고 있습니다. 해당 브라우저를 완전히 종료한 뒤 "
                     "다시 시도하거나 Firefox를 사용하세요.")
    if "http error 403" in low or "unable to extract" in low or "nsig" in low:
        hints.append("유튜브 변경으로 yt-dlp 업데이트가 필요할 수 있습니다. "
                     "프로그램을 닫고 '실행.bat'을 다시 실행하면 자동 업데이트됩니다.")
    if "private video" in low:
        hints.append("비공개 영상입니다.")
    if "age" in low and ("restricted" in low or "confirm your age" in low):
        hints.append("연령 제한 영상입니다. 로그인된 브라우저 쿠키를 선택하세요.")
    if "ffmpeg" in low and "not found" in low:
        hints.append("ffmpeg가 없습니다. 프로그램을 다시 실행해 자동 설치를 진행하세요.")
    if hints:
        return msg + "\n  → " + "\n  → ".join(hints)
    return msg


class _YdlLogger:
    def __init__(self, log: LogFn):
        self.log = log

    def debug(self, msg: str) -> None:
        if msg.startswith("[debug] "):
            return
        # 진행률 줄은 진행 막대로 따로 보여주므로 생략
        if msg.startswith("[download]") and "%" in msg:
            return
        self.log(msg)

    def info(self, msg: str) -> None:
        self.log(msg)

    def warning(self, msg: str) -> None:
        self.log("⚠ " + msg)

    def error(self, msg: str) -> None:
        self.log("❌ " + msg)


# ----------------------------------------------------------------------------
# 다운로더
# ----------------------------------------------------------------------------
class Downloader:
    def __init__(self, ffmpeg_path: str, ffprobe_path: str,
                 log: LogFn, progress: ProgressFn,
                 cancel_event: threading.Event | None = None):
        self.ffmpeg = ffmpeg_path
        self.ffprobe = ffprobe_path
        self.log = log
        self.progress = progress
        self.cancel = cancel_event or threading.Event()
        self._proc: subprocess.Popen | None = None

    # ---------------- 옵션 구성 ----------------
    def base_opts(self, job: Job) -> dict:
        opts: dict = {
            "quiet": True,
            "noprogress": True,
            "logger": _YdlLogger(self.log),
            "ffmpeg_location": os.path.dirname(self.ffmpeg) or None,
            "windowsfilenames": True,
            "noplaylist": not job.playlist,
            "retries": 10,
            "fragment_retries": 10,
            "concurrent_fragment_downloads": 4,
            "progress_hooks": [self._progress_hook],
            "postprocessor_hooks": [self._pp_hook],
        }
        if job.cookie_browser:
            opts["cookiesfrombrowser"] = (job.cookie_browser,)
        elif job.cookie_file:
            opts["cookiefile"] = job.cookie_file
        return opts

    def build_opts(self, job: Job) -> dict:
        opts = self.base_opts(job)
        name = "%(title).150B [%(id)s].%(ext)s"
        if job.playlist:
            name = "%(playlist_title,playlist_id|재생목록).80B/%(playlist_index)03d - " + name
        opts["paths"] = {"home": job.out_dir}
        opts["outtmpl"] = {"default": name}

        if job.mode == "video":
            # 해상도(지정값 이하에서 가장 큰 것) → 프레임레이트 → SDR → H.264 → AAC 순으로 선호.
            # 즉 원하는 해상도를 먼저 맞추고, 같은 해상도라면 프리미어 호환 코덱을 고른다.
            res = f"res:{job.height}" if job.height else "res"
            opts["format"] = "bv*+ba/b"
            opts["format_sort"] = [res, "fps", "hdr:sdr", "vcodec:h264", "acodec:aac"]
            opts["merge_output_format"] = "mp4"
        else:
            opts["format"] = "ba/b"
            pp: dict = {"key": "FFmpegExtractAudio", "preferredcodec": job.audio_format}
            if job.audio_format == "mp3":
                pp["preferredquality"] = job.mp3_bitrate
                opts["postprocessors"] = [pp, {"key": "FFmpegMetadata", "add_metadata": True}]
            else:
                opts["postprocessors"] = [pp]
                # 영상 편집 표준 샘플레이트(48kHz), 16bit PCM
                opts["postprocessor_args"] = {"extractaudio": ["-ar", "48000"]}
        return opts

    # ---------------- 훅 ----------------
    def _check_cancel(self) -> None:
        if self.cancel.is_set():
            raise Cancelled()

    def _progress_hook(self, d: dict) -> None:
        self._check_cancel()
        info = d.get("info_dict") or {}
        prefix = ""
        if info.get("playlist_index") and info.get("n_entries"):
            prefix = f"[{info['playlist_index']}/{info['n_entries']}] "
        if info.get("vcodec") == "none":
            kind = "오디오 "
        elif info.get("acodec") == "none":
            kind = "영상 "
        else:
            kind = ""

        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            frac = (done / total) if total else None
            pct = f"{frac * 100:.1f}%" if frac is not None else _fmt_bytes(done)
            speed = _fmt_bytes(d.get("speed")) + "/s" if d.get("speed") else "-"
            self.progress(frac, f"{prefix}{kind}다운로드 중 {pct} · {speed} · "
                                f"남은 시간 {_fmt_time(d.get('eta'))}")
        elif d["status"] == "finished":
            self.progress(1.0, f"{prefix}{kind}다운로드 완료, 처리 중...")

    _PP_NAMES = {
        "Merger": "영상과 오디오 합치는 중",
        "ExtractAudio": "오디오 변환 중",
        "Metadata": "제목 정보 넣는 중",
        "FixupM4a": "파일 정리 중",
        "FixupM3u8": "파일 정리 중",
        "FixupDuplicateMoov": "파일 정리 중",
    }

    def _pp_hook(self, d: dict) -> None:
        self._check_cancel()
        if d.get("status") == "started":
            name = self._PP_NAMES.get(d.get("postprocessor", ""))
            if name:
                self.progress(None, name + "...")

    # ---------------- 정보 가져오기 ----------------
    def fetch_info(self, job: Job) -> dict:
        opts = self.base_opts(job)
        opts["skip_download"] = True
        if job.playlist:
            opts["extract_flat"] = "in_playlist"
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(job.url, download=False)
        return summarize_info(info)

    # ---------------- 다운로드 ----------------
    def run(self, job: Job) -> list[str]:
        os.makedirs(job.out_dir, exist_ok=True)
        files: list[str] = []
        opts = self.build_opts(job)
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.add_post_hook(files.append)  # 후처리까지 끝난 최종 파일 경로
            try:
                ret = ydl.download([job.url])
            except yt_dlp.utils.DownloadError:
                if self.cancel.is_set():
                    raise Cancelled()
                raise
        if self.cancel.is_set():
            raise Cancelled()
        if ret and not files:
            raise RuntimeError("다운로드에 실패했습니다. 위 로그를 확인하세요.")

        if job.mode == "video":
            for i, path in enumerate(files):
                self._check_cancel()
                tag = f"[{i + 1}/{len(files)}] " if len(files) > 1 else ""
                files[i] = self.ensure_premiere_compatible(path, job, tag)
        return files

    # ---------------- 프리미어 호환 처리 ----------------
    def probe(self, path: str) -> dict:
        out = subprocess.run(
            [self.ffprobe, "-v", "error", "-show_entries",
             "stream=codec_type,codec_name:format=duration", "-of", "json", path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            **_hidden_popen_kwargs(),
        )
        data = json.loads(out.stdout or "{}")
        res = {"vcodec": None, "acodec": None, "duration": None}
        for s in data.get("streams", []):
            key = {"video": "vcodec", "audio": "acodec"}.get(s.get("codec_type"))
            if key and res[key] is None and s.get("codec_name") not in ("mjpeg", "png"):
                res[key] = s.get("codec_name")
        try:
            res["duration"] = float(data.get("format", {}).get("duration"))
        except (TypeError, ValueError):
            pass
        return res

    def ensure_premiere_compatible(self, path: str, job: Job, tag: str = "") -> str:
        """프리미어용 MP4(H.264/AAC)로 맞추고 최종 파일 경로를 돌려준다."""
        info = self.probe(path)
        v, a = info["vcodec"], info["acodec"]
        need_v = v is not None and v not in PREMIERE_VCODECS
        need_a = a is not None and a not in PREMIERE_ACODECS
        need_remux = os.path.splitext(path)[1].lower() != ".mp4"
        base = os.path.basename(path)

        if not job.reencode and (need_v or need_a):
            self.log(f"⚠ {tag}이 파일은 {v}/{a} 코덱이라 프리미어에서 안 열릴 수 있습니다. "
                     f"('H.264로 자동 변환' 옵션을 켜면 해결됩니다): {base}")
            need_v = need_a = False
        if not (need_v or need_a or need_remux):
            self.log(f"✔ {tag}프리미어 호환 확인 (영상 {v or '-'} / 오디오 {a or '-'}): {base}")
            return path

        what = []
        if need_v:
            what.append(f"영상 {v}→H.264")
        if need_a:
            what.append(f"오디오 {a}→AAC")
        if what:
            self.log(f"{tag}프리미어 호환 변환 시작 ({', '.join(what)}). 영상 길이·화질에 따라 "
                     f"시간이 걸립니다...")
        else:
            self.log(f"{tag}MP4 컨테이너로 옮기는 중...")

        encoder = job.encoder if need_v else None
        try:
            out = self._transcode(path, need_v, need_a, encoder, info["duration"], tag)
        except Cancelled:
            raise
        except Exception as e:  # 하드웨어 인코더 실패 시 CPU로 재시도
            if encoder and encoder != "libx264":
                self.log(f"⚠ {encoder} 인코더를 쓸 수 없어 CPU(x264)로 다시 변환합니다. ({e})")
                out = self._transcode(path, need_v, need_a, "libx264", info["duration"], tag)
            else:
                raise
        self.log(f"✔ {tag}변환 완료: {os.path.basename(out)}")
        return out

    @staticmethod
    def encoder_args(encoder: str) -> list[str]:
        if encoder == "h264_nvenc":
            return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "19",
                    "-b:v", "0", "-pix_fmt", "yuv420p"]
        if encoder == "h264_qsv":
            return ["-c:v", "h264_qsv", "-preset", "slow", "-global_quality", "20",
                    "-pix_fmt", "nv12"]
        if encoder == "h264_amf":
            return ["-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp",
                    "-qp_i", "18", "-qp_p", "20", "-pix_fmt", "yuv420p"]
        return ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"]

    def _transcode(self, path: str, need_v: bool, need_a: bool, encoder: str | None,
                   duration: float | None, tag: str) -> str:
        root, _ = os.path.splitext(path)
        final = root + ".mp4"
        tmp = root + ".변환중.mp4"
        cmd = [self.ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", path,
               "-map", "0:v:0?", "-map", "0:a:0?", "-map_metadata", "0"]
        cmd += self.encoder_args(encoder) if need_v else ["-c:v", "copy"]
        cmd += ["-c:a", "aac", "-b:a", "320k", "-ar", "48000"] if need_a else ["-c:a", "copy"]
        cmd += ["-movflags", "+faststart", "-progress", "pipe:1", "-nostats",
                "-loglevel", "error", tmp]

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", **_hidden_popen_kwargs())
        self._proc = proc
        err_lines: list[str] = []
        t = threading.Thread(target=lambda: err_lines.extend(proc.stderr), daemon=True)
        t.start()
        try:
            for line in proc.stdout:
                if self.cancel.is_set():
                    proc.kill()
                    break
                m = re.match(r"out_time_us=(\d+)", line.strip())
                if m and duration:
                    done = int(m.group(1)) / 1_000_000
                    frac = max(0.0, min(1.0, done / duration))
                    self.progress(frac, f"{tag}프리미어 호환 변환 중 {frac * 100:.1f}% "
                                        f"({_fmt_time(done)} / {_fmt_time(duration)})")
            rc = proc.wait()
            t.join(timeout=2)
        finally:
            self._proc = None

        if self.cancel.is_set():
            _silent_remove(tmp)
            raise Cancelled()
        if rc != 0 or not os.path.exists(tmp):
            _silent_remove(tmp)
            raise RuntimeError("ffmpeg 변환 실패: " + "".join(err_lines[-5:]).strip())
        os.replace(tmp, final)
        if os.path.abspath(final) != os.path.abspath(path):
            _silent_remove(path)
        return final

    def kill_running(self) -> None:
        proc = self._proc
        if proc and proc.poll() is None:
            proc.kill()


def _silent_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def summarize_info(info: dict) -> dict:
    """extract_info 결과에서 화면에 보여줄 정보만 추린다."""
    if info.get("_type") == "playlist" or "entries" in info:
        entries = list(info.get("entries") or [])
        return {"playlist": True, "title": info.get("title") or "재생목록",
                "count": len(entries), "heights": [], "h264_heights": []}
    heights, h264 = set(), set()
    for f in info.get("formats") or []:
        h = f.get("height")
        vc = f.get("vcodec") or ""
        if h and vc != "none":
            heights.add(h)
            if vc.startswith(("avc1", "h264")):
                h264.add(h)
    return {
        "playlist": False,
        "title": info.get("title") or "",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "duration": info.get("duration"),
        "duration_str": _fmt_time(info.get("duration")) if info.get("duration") else "?",
        "heights": sorted(heights, reverse=True),
        "h264_heights": sorted(h264, reverse=True),
    }
