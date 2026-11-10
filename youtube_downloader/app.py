"""유튜브 다운로더 (프리미어 프로 / 오디션용) - 윈도우 GUI.

실행: 같은 폴더의 '실행.bat' 더블클릭
"""
from __future__ import annotations

import os
import sys

# pythonw.exe(콘솔 없음)로 실행하면 stdout/stderr가 None이라 print 시 오류가 난다.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import json
import queue
import subprocess
import threading
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import tools
from core import (AUDIO_FORMATS, COOKIE_SOURCES, ENCODERS, MP3_BITRATES, VIDEO_QUALITIES,
                  Cancelled, Downloader, Job, friendly_error)

APP_TITLE = "유튜브 다운로더 · 프리미어 / 오디션용"
SETTINGS_FILE = tools.APP_DIR / "settings.json"


def default_download_dir() -> str:
    d = Path.home() / "Downloads"
    return str(d if d.exists() else Path.home())


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.downloader: Downloader | None = None
        self.quality_map: dict[str, int | None] = {lbl: h for lbl, h in VIDEO_QUALITIES}
        self.settings = self._load_settings()

        root.title(APP_TITLE)
        root.minsize(720, 640)
        self._setup_style()
        self._build_ui()
        self._apply_settings()
        self._on_mode_change()

        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(80, self._poll_queue)
        self._start_tool_check()

    # ------------------------------------------------------------------ UI
    def _setup_style(self) -> None:
        family = "Malgun Gothic" if os.name == "nt" else "TkDefaultFont"
        self.font = (family, 10)
        self.font_bold = (family, 10, "bold")
        self.root.option_add("*Font", self.font)
        style = ttk.Style()
        if os.name == "nt" and "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure(".", font=self.font)
        style.configure("Big.TButton", font=(family, 11, "bold"), padding=(16, 8))
        style.configure("Info.TLabel", foreground="#1f5fbf")

    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 4}
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)

        # --- URL
        f_url = ttk.LabelFrame(main, text=" 1. 유튜브 주소 ", padding=8)
        f_url.grid(row=0, column=0, sticky="ew", **pad)
        f_url.columnconfigure(0, weight=1)
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(f_url, textvariable=self.url_var)
        self.url_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6), ipady=3)
        self.url_entry.bind("<Return>", lambda e: self.fetch_info())
        ttk.Button(f_url, text="붙여넣기", command=self._paste).grid(row=0, column=1, padx=2)
        self.btn_info = ttk.Button(f_url, text="정보 가져오기", command=self.fetch_info)
        self.btn_info.grid(row=0, column=2, padx=2)
        self.playlist_var = tk.BooleanVar()
        ttk.Checkbutton(f_url, text="재생목록 전체 받기 (주소에 list= 가 있을 때)",
                        variable=self.playlist_var).grid(row=1, column=0, columnspan=3,
                                                         sticky="w", pady=(6, 0))
        self.info_var = tk.StringVar(value="주소를 넣고 '정보 가져오기'를 누르면 받을 수 있는 화질이 표시됩니다.")
        ttk.Label(f_url, textvariable=self.info_var, style="Info.TLabel",
                  wraplength=640, justify="left").grid(row=2, column=0, columnspan=3,
                                                      sticky="w", pady=(6, 0))

        # --- 형식
        f_fmt = ttk.LabelFrame(main, text=" 2. 받을 형식 ", padding=8)
        f_fmt.grid(row=1, column=0, sticky="ew", **pad)
        f_fmt.columnconfigure(1, weight=1)
        self.mode_var = tk.StringVar(value="video")
        ttk.Radiobutton(f_fmt, text="🎬 영상  (MP4 · H.264/AAC → 프리미어 프로용)",
                        value="video", variable=self.mode_var,
                        command=self._on_mode_change).grid(row=0, column=0, columnspan=2, sticky="w")

        self.f_video = ttk.Frame(f_fmt)
        self.f_video.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(24, 0), pady=(2, 8))
        self.f_video.columnconfigure(1, weight=1)
        ttk.Label(self.f_video, text="화질").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.quality_cb = ttk.Combobox(self.f_video, state="readonly", width=34,
                                       values=[lbl for lbl, _ in VIDEO_QUALITIES])
        self.quality_cb.grid(row=0, column=1, sticky="w")
        self.reencode_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(self.f_video, variable=self.reencode_var,
                        text="프리미어가 못 여는 코덱(VP9/AV1)이면 H.264로 자동 변환  (1440p·4K는 대부분 변환 필요)",
                        command=self._on_mode_change).grid(row=1, column=0, columnspan=2,
                                                          sticky="w", pady=(4, 0))
        ttk.Label(self.f_video, text="변환 방식").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=(4, 0))
        self.encoder_cb = ttk.Combobox(self.f_video, state="readonly", width=34,
                                       values=[lbl for lbl, _ in ENCODERS])
        self.encoder_cb.grid(row=2, column=1, sticky="w", pady=(4, 0))

        ttk.Radiobutton(f_fmt, text="🎵 오디오만  (오디션용)", value="audio",
                        variable=self.mode_var,
                        command=self._on_mode_change).grid(row=2, column=0, columnspan=2, sticky="w")
        self.f_audio = ttk.Frame(f_fmt)
        self.f_audio.grid(row=3, column=0, columnspan=2, sticky="ew", padx=(24, 0), pady=(2, 0))
        ttk.Label(self.f_audio, text="형식").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.afmt_cb = ttk.Combobox(self.f_audio, state="readonly", width=34,
                                    values=[lbl for lbl, _ in AUDIO_FORMATS])
        self.afmt_cb.grid(row=0, column=1, sticky="w")
        self.afmt_cb.bind("<<ComboboxSelected>>", lambda e: self._on_mode_change())
        ttk.Label(self.f_audio, text="음질").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(4, 0))
        self.bitrate_cb = ttk.Combobox(self.f_audio, state="readonly", width=34,
                                       values=[f"{b} kbps" + (" (최고)" if b == "320" else "")
                                               for b in MP3_BITRATES])
        self.bitrate_cb.grid(row=1, column=1, sticky="w", pady=(4, 0))

        # --- 저장 / 쿠키
        f_opt = ttk.LabelFrame(main, text=" 3. 저장 위치 · 기타 ", padding=8)
        f_opt.grid(row=2, column=0, sticky="ew", **pad)
        f_opt.columnconfigure(1, weight=1)
        ttk.Label(f_opt, text="저장 폴더").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.out_var = tk.StringVar()
        ttk.Entry(f_opt, textvariable=self.out_var).grid(row=0, column=1, sticky="ew", ipady=2)
        ttk.Button(f_opt, text="찾아보기", command=self._choose_dir).grid(row=0, column=2, padx=(6, 2))
        ttk.Button(f_opt, text="폴더 열기", command=self._open_dir).grid(row=0, column=3, padx=2)

        ttk.Label(f_opt, text="쿠키").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(6, 0))
        self.cookie_cb = ttk.Combobox(f_opt, state="readonly", width=28,
                                      values=[lbl for lbl, _ in COOKIE_SOURCES])
        self.cookie_cb.grid(row=1, column=1, sticky="w", pady=(6, 0))
        self.cookie_cb.bind("<<ComboboxSelected>>", self._on_cookie_change)
        self.cookie_file = ""
        self.cookie_hint = tk.StringVar(
            value="'봇이 아닌지 확인' 오류가 나면 유튜브에 로그인된 브라우저를 고르세요.")
        ttk.Label(f_opt, textvariable=self.cookie_hint, foreground="#666",
                  wraplength=640).grid(row=2, column=0, columnspan=4, sticky="w", pady=(2, 0))

        # --- 버튼
        f_btn = ttk.Frame(main)
        f_btn.grid(row=3, column=0, sticky="ew", padx=10, pady=(8, 4))
        self.btn_dl = ttk.Button(f_btn, text="⬇  다운로드", style="Big.TButton", command=self.start_download)
        self.btn_dl.pack(side="left")
        self.btn_cancel = ttk.Button(f_btn, text="취소", command=self.cancel_job, state="disabled")
        self.btn_cancel.pack(side="left", padx=8)

        # --- 진행
        self.pbar = ttk.Progressbar(main, mode="determinate", maximum=1000)
        self.pbar.grid(row=4, column=0, sticky="ew", padx=10, pady=(4, 2))
        self.status_var = tk.StringVar(value="준비 중...")
        ttk.Label(main, textvariable=self.status_var).grid(row=5, column=0, sticky="w", padx=10)

        # --- 로그
        self.log_box = ScrolledText(main, height=10, state="disabled", wrap="word",
                                    font=("Consolas" if os.name == "nt" else "TkFixedFont", 9))
        self.log_box.grid(row=6, column=0, sticky="nsew", padx=10, pady=(6, 0))
        main.rowconfigure(6, weight=1)

    # ------------------------------------------------------------ 설정
    def _load_settings(self) -> dict:
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _apply_settings(self) -> None:
        s = self.settings

        def pick(cb: ttk.Combobox, value, default_idx=0):
            vals = list(cb["values"])
            cb.set(value if value in vals else vals[default_idx])

        pick(self.quality_cb, s.get("quality"), 3)        # 1080p 기본
        pick(self.encoder_cb, s.get("encoder"))
        pick(self.afmt_cb, s.get("audio_format"))
        pick(self.bitrate_cb, s.get("bitrate"))
        pick(self.cookie_cb, s.get("cookie"))
        self.mode_var.set(s.get("mode", "video"))
        self.reencode_var.set(s.get("reencode", True))
        self.cookie_file = s.get("cookie_file", "")
        out = s.get("out_dir") or default_download_dir()
        self.out_var.set(out)
        self._on_cookie_change(ask=False)

    def _save_settings(self) -> None:
        q = self.quality_cb.get()
        s = {
            "quality": q if q in [lbl for lbl, _ in VIDEO_QUALITIES] else self.settings.get("quality"),
            "encoder": self.encoder_cb.get(),
            "audio_format": self.afmt_cb.get(),
            "bitrate": self.bitrate_cb.get(),
            "cookie": self.cookie_cb.get(),
            "cookie_file": self.cookie_file,
            "mode": self.mode_var.get(),
            "reencode": self.reencode_var.get(),
            "out_dir": self.out_var.get(),
        }
        try:
            SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    # ------------------------------------------------------- 이벤트 처리
    def _on_mode_change(self) -> None:
        video = self.mode_var.get() == "video"

        def set_state(frame: ttk.Frame, on: bool):
            for w in frame.winfo_children():
                if isinstance(w, ttk.Combobox):
                    w.configure(state="readonly" if on else "disabled")
                else:
                    try:
                        w.configure(state="normal" if on else "disabled")
                    except tk.TclError:
                        pass

        set_state(self.f_video, video)
        set_state(self.f_audio, not video)
        if video:
            self.encoder_cb.configure(state="readonly" if self.reencode_var.get() else "disabled")
        else:
            is_mp3 = self._audio_format() == "mp3"
            self.bitrate_cb.configure(state="readonly" if is_mp3 else "disabled")

    def _on_cookie_change(self, event=None, ask: bool = True) -> None:
        src = dict(COOKIE_SOURCES).get(self.cookie_cb.get())
        if src == "file":
            if ask or not self.cookie_file:
                path = filedialog.askopenfilename(
                    title="cookies.txt 선택", filetypes=[("쿠키 파일", "*.txt"), ("모든 파일", "*.*")])
                if path:
                    self.cookie_file = path
            if self.cookie_file:
                self.cookie_hint.set(f"쿠키 파일: {self.cookie_file}")
            else:
                self.cookie_cb.set(COOKIE_SOURCES[0][0])
                self.cookie_hint.set("쿠키 파일을 선택하지 않았습니다.")
        elif src in ("chrome", "edge", "whale", "brave"):
            self.cookie_hint.set("※ 크롬 계열 브라우저는 쿠키를 잠그므로 다운로드 중에는 브라우저를 "
                                 "완전히 닫아 주세요. 안 되면 Firefox를 추천합니다.")
        elif src == "firefox":
            self.cookie_hint.set("Firefox에서 유튜브에 로그인되어 있어야 합니다.")
        else:
            self.cookie_hint.set("'봇이 아닌지 확인' 오류가 나면 유튜브에 로그인된 브라우저를 고르세요.")

    def _paste(self) -> None:
        try:
            text = self.root.clipboard_get().strip()
        except tk.TclError:
            return
        self.url_var.set(text)
        if "list=" in text and "watch?v=" not in text:
            self.playlist_var.set(True)

    def _choose_dir(self) -> None:
        d = filedialog.askdirectory(initialdir=self.out_var.get() or default_download_dir())
        if d:
            self.out_var.set(os.path.normpath(d))

    def _open_dir(self, path: str | None = None) -> None:
        path = path or self.out_var.get()
        if not os.path.isdir(path):
            return
        if os.name == "nt":
            os.startfile(path)  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", path])

    def _on_close(self) -> None:
        if self.busy and not messagebox.askyesno(APP_TITLE, "다운로드 중입니다. 종료할까요?"):
            return
        self.cancel.set()
        if self.downloader:
            self.downloader.kill_running()
        self._save_settings()
        self.root.destroy()

    # ------------------------------------------------------- 값 읽기
    def _audio_format(self) -> str:
        return dict(AUDIO_FORMATS).get(self.afmt_cb.get(), "mp3")

    def _make_job(self) -> Job | None:
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning(APP_TITLE, "유튜브 주소를 입력하세요.")
            return None
        out = self.out_var.get().strip() or default_download_dir()
        src = dict(COOKIE_SOURCES).get(self.cookie_cb.get())
        return Job(
            url=url,
            out_dir=out,
            mode=self.mode_var.get(),
            height=self.quality_map.get(self.quality_cb.get()),
            reencode=self.reencode_var.get(),
            encoder=dict(ENCODERS).get(self.encoder_cb.get(), "libx264"),
            audio_format=self._audio_format(),
            mp3_bitrate=self.bitrate_cb.get().split()[0],
            playlist=self.playlist_var.get(),
            cookie_browser=src if src not in (None, "file") else None,
            cookie_file=self.cookie_file if src == "file" else None,
        )

    # ------------------------------------------------------- 작업 스레드
    def _post(self, *msg) -> None:
        self.q.put(msg)

    def _log(self, text: str) -> None:
        self._post("log", text)

    def _progress(self, frac: float | None, text: str) -> None:
        self._post("progress", frac, text)

    def _make_downloader(self) -> Downloader:
        self.downloader = Downloader(
            tools.find_tool("ffmpeg") or "ffmpeg", tools.find_tool("ffprobe") or "ffprobe",
            self._log, self._progress, self.cancel)
        return self.downloader

    def _run_in_thread(self, target, *args) -> None:
        self.cancel.clear()
        self._set_busy(True)

        def wrapper():
            try:
                target(*args)
            except Cancelled:
                self._post("done", "cancel", None)
            except Exception as e:  # noqa: BLE001
                if self.cancel.is_set():
                    self._post("done", "cancel", None)
                else:
                    self._post("done", "error", friendly_error(str(e)))
            finally:
                self._post("idle")

        threading.Thread(target=wrapper, daemon=True).start()

    def _start_tool_check(self) -> None:
        tools.setup_path()
        if not tools.missing_tools():
            self.status_var.set("준비 완료")
            self._log("준비 완료. 유튜브 주소를 넣어 주세요.")
            return
        self._run_in_thread(self._install_tools)

    def _install_tools(self) -> None:
        still = tools.install_missing(self._log, self._progress, self.cancel)
        if still:
            raise RuntimeError("필수 도구 설치 실패: " + ", ".join(still)
                               + " — 인터넷 연결을 확인하고 프로그램을 다시 실행하세요.")
        self._post("done", "tools", None)

    def fetch_info(self) -> None:
        if self.busy:
            return
        job = self._make_job()
        if not job:
            return
        self._log(f"정보 가져오는 중: {job.url}")
        self._progress(None, "영상 정보 가져오는 중...")

        def work():
            if tools.missing_tools(include_optional=False):
                self._install_tools()
            info = self._make_downloader().fetch_info(job)
            self._post("info", info)

        self._run_in_thread(work)

    def start_download(self) -> None:
        if self.busy:
            return
        job = self._make_job()
        if not job:
            return
        self._save_settings()
        if job.mode == "video":
            q = self.quality_cb.get()
            desc = f"영상 MP4 · {q}"
        else:
            desc = "오디오 " + (f"MP3 {job.mp3_bitrate}kbps" if job.audio_format == "mp3" else "WAV 48kHz")
        self._log("─" * 50)
        self._log(f"다운로드 시작 ({desc}) → {job.out_dir}")

        def work():
            if tools.missing_tools(include_optional=False):
                self._install_tools()
            files = self._make_downloader().run(job)
            self._post("done", "download", (files, job.out_dir))

        self._run_in_thread(work)

    def cancel_job(self) -> None:
        if not self.busy:
            return
        self.cancel.set()
        if self.downloader:
            self.downloader.kill_running()
        self.status_var.set("취소하는 중...")

    # ------------------------------------------------------- UI 갱신
    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.btn_dl.configure(state="disabled" if busy else "normal")
        self.btn_info.configure(state="disabled" if busy else "normal")
        self.btn_cancel.configure(state="normal" if busy else "disabled")
        if not busy and str(self.pbar["mode"]) == "indeterminate":
            self.pbar.stop()
            self.pbar.configure(mode="determinate")

    def _append_log(self, text: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _show_info(self, info: dict) -> None:
        if info["playlist"]:
            self.playlist_var.set(True)
            self.info_var.set(f"📃 재생목록: {info['title']}  ·  {info['count']}개 영상")
            self.quality_map = {lbl: h for lbl, h in VIDEO_QUALITIES}
            self.quality_cb.configure(values=list(self.quality_map))
            return
        heights, h264 = info["heights"], set(info["h264_heights"])
        text = f"🎬 {info['title']}"
        if info.get("uploader"):
            text += f"  ·  {info['uploader']}"
        text += f"  ·  길이 {info['duration_str']}"
        if heights:
            text += f"\n받을 수 있는 화질: {', '.join(f'{h}p' for h in heights)}"
            if h264:
                text += f"   (H.264 원본: {max(h264)}p 이하 → 변환 없이 바로 프리미어용)"
        self.info_var.set(text)

        if heights:
            prev = self.quality_map.get(self.quality_cb.get())
            self.quality_map = {}
            for h in heights:
                tag = "변환 없음 · 빠름" if h in h264 else "H.264 변환 필요"
                self.quality_map[f"{h}p  ({tag})"] = h
            self.quality_cb.configure(values=list(self.quality_map))
            # 이전에 고른 화질 이하에서 가장 높은 것 선택
            choice = next((lbl for lbl, h in self.quality_map.items() if prev is None or h <= prev),
                          list(self.quality_map)[-1])
            self.quality_cb.set(choice)

    def _poll_queue(self) -> None:
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self._append_log(msg[1])
                elif kind == "progress":
                    frac, text = msg[1], msg[2]
                    if frac is None:
                        if str(self.pbar["mode"]) != "indeterminate":
                            self.pbar.configure(mode="indeterminate")
                            self.pbar.start(12)
                    else:
                        if str(self.pbar["mode"]) != "determinate":
                            self.pbar.stop()
                            self.pbar.configure(mode="determinate")
                        self.pbar["value"] = frac * 1000
                    self.status_var.set(text)
                elif kind == "info":
                    self._show_info(msg[1])
                    self.status_var.set("정보를 가져왔습니다. 화질/형식을 고르고 다운로드를 누르세요.")
                    self.pbar["value"] = 0
                elif kind == "idle":
                    self._set_busy(False)
                elif kind == "done":
                    self._on_done(msg[1], msg[2])
        except queue.Empty:
            pass
        self.root.after(80, self._poll_queue)

    def _on_done(self, kind: str, payload) -> None:
        if kind == "tools":
            self.status_var.set("준비 완료")
            self._log("필수 도구 준비 완료.")
        elif kind == "download":
            files, out_dir = payload
            if str(self.pbar["mode"]) == "indeterminate":
                self.pbar.stop()
                self.pbar.configure(mode="determinate")
            self.pbar["value"] = 1000
            self.status_var.set(f"✔ 완료! {len(files)}개 파일")
            for f in files:
                self._log(f"✔ 저장됨: {f}")
            if messagebox.askyesno(APP_TITLE, f"다운로드 완료! ({len(files)}개)\n\n저장 폴더를 열까요?"):
                folder = os.path.dirname(files[0]) if files else out_dir
                self._open_dir(folder)
        elif kind == "cancel":
            self.pbar["value"] = 0
            self.status_var.set("취소되었습니다.")
            self._log("취소되었습니다.")
        elif kind == "error":
            self.pbar["value"] = 0
            self.status_var.set("❌ 오류가 발생했습니다. 아래 로그를 확인하세요.")
            self._log("❌ " + str(payload))
            messagebox.showerror(APP_TITLE, str(payload)[:1500])


def main() -> None:
    if os.name == "nt":
        import ctypes  # 고해상도 모니터에서 글자가 흐리지 않게 (Tk 생성 전에 설정)
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    try:
        App(root)
        root.mainloop()
    except Exception:
        err = traceback.format_exc()
        (tools.APP_DIR / "error.log").write_text(err, encoding="utf-8")
        messagebox.showerror(APP_TITLE, "프로그램 오류가 발생했습니다.\n\n" + err[-1500:])
        raise


if __name__ == "__main__":
    main()
