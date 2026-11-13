"""창(GUI) 프로그램."""

import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import __version__
from .auth import AuthError, GoogleAuth, MicrosoftAuth, TokenStore
from .config import app_dir, load_json, save_json
from .engine import SyncEngine
from .gphotos import GooglePhotos
from .onedrive import OneDrive
from .state import DONE, FAILED, SKIPPED, State

SCOPES = [
    ("photos", "사진 폴더 전체 (삼성 갤러리 포함, 권장)"),
    ("all", "원드라이브 전체"),
    ("custom", "직접 지정한 폴더"),
]
DEFAULT_ALBUM = "원드라이브에서 가져옴"


def human_size(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.dir = app_dir()
        self.config_path = self.dir / "config.json"
        self.config = load_json(self.config_path)
        self.tokens = TokenStore(self.dir / "tokens.json")
        self.events: queue.Queue = queue.Queue()
        self.engine: SyncEngine | None = None
        self.login_cancel = threading.Event()
        self.busy = False

        root.title(f"원드라이브 → 구글 포토 사진 옮기기 v{__version__}")
        root.minsize(680, 640)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build()
        self._refresh_login_labels()
        self._show_saved_progress()
        self.root.after(100, self._drain)

    # ---- 화면 구성 ------------------------------------------------------

    def _build(self) -> None:
        pad = {"padx": 8, "pady": 4}
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)

        # 1단계: 원드라이브
        ms = ttk.LabelFrame(main, text="1단계  원드라이브(Microsoft) 로그인")
        ms.grid(row=0, column=0, sticky="ew", pady=4)
        ms.columnconfigure(1, weight=1)
        ttk.Label(ms, text="클라이언트 ID").grid(row=0, column=0, sticky="w", **pad)
        self.ms_id = tk.StringVar(value=self.config.get("ms_client_id", ""))
        ttk.Entry(ms, textvariable=self.ms_id).grid(row=0, column=1, sticky="ew", **pad)
        self.ms_button = ttk.Button(ms, text="로그인", command=self.login_microsoft)
        self.ms_button.grid(row=0, column=2, **pad)
        self.ms_status = ttk.Label(ms, text="")
        self.ms_status.grid(row=1, column=1, sticky="w", **pad)
        ttk.Button(ms, text="로그아웃", command=lambda: self.logout("microsoft")).grid(row=1, column=2, **pad)

        # 2단계: 구글
        gg = ttk.LabelFrame(main, text="2단계  구글 포토 로그인")
        gg.grid(row=1, column=0, sticky="ew", pady=4)
        gg.columnconfigure(1, weight=1)
        ttk.Label(gg, text="클라이언트 ID").grid(row=0, column=0, sticky="w", **pad)
        self.g_id = tk.StringVar(value=self.config.get("google_client_id", ""))
        ttk.Entry(gg, textvariable=self.g_id).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Label(gg, text="보안 비밀번호").grid(row=1, column=0, sticky="w", **pad)
        self.g_secret = tk.StringVar(value=self.config.get("google_client_secret", ""))
        ttk.Entry(gg, textvariable=self.g_secret, show="•").grid(row=1, column=1, sticky="ew", **pad)
        self.g_button = ttk.Button(gg, text="로그인", command=self.login_google)
        self.g_button.grid(row=0, column=2, **pad)
        ttk.Button(gg, text="로그아웃", command=lambda: self.logout("google")).grid(row=1, column=2, **pad)
        self.g_status = ttk.Label(gg, text="")
        self.g_status.grid(row=2, column=1, sticky="w", **pad)

        # 3단계: 옵션
        opt = ttk.LabelFrame(main, text="3단계  옮길 범위")
        opt.grid(row=2, column=0, sticky="ew", pady=4)
        opt.columnconfigure(1, weight=1)
        self.scope = tk.StringVar(value=self.config.get("scope", "photos"))
        for row, (value, label) in enumerate(SCOPES):
            ttk.Radiobutton(opt, text=label, value=value, variable=self.scope).grid(
                row=row, column=0, sticky="w", **pad
            )
        self.custom_path = tk.StringVar(value=self.config.get("custom_path", "사진/Samsung Gallery"))
        ttk.Entry(opt, textvariable=self.custom_path).grid(row=2, column=1, sticky="ew", **pad)
        self.use_album = tk.BooleanVar(value=self.config.get("use_album", True))
        ttk.Checkbutton(opt, text="구글 포토에 앨범을 만들어 모으기:", variable=self.use_album).grid(
            row=3, column=0, sticky="w", **pad
        )
        self.album = tk.StringVar(value=self.config.get("album", DEFAULT_ALBUM))
        ttk.Entry(opt, textvariable=self.album).grid(row=3, column=1, sticky="ew", **pad)

        # 실행
        run = ttk.Frame(main)
        run.grid(row=3, column=0, sticky="ew", pady=6)
        run.columnconfigure(2, weight=1)
        self.start_button = ttk.Button(run, text="옮기기 시작", command=self.start)
        self.start_button.grid(row=0, column=0, padx=4)
        self.stop_button = ttk.Button(run, text="중지", command=self.stop, state="disabled")
        self.stop_button.grid(row=0, column=1, padx=4)
        self.summary = ttk.Label(run, text="")
        self.summary.grid(row=0, column=2, sticky="e", padx=4)
        self.bar = ttk.Progressbar(main, mode="determinate")
        self.bar.grid(row=4, column=0, sticky="ew", pady=2)

        self.log_box = ScrolledText(main, height=14, state="disabled", wrap="word")
        self.log_box.grid(row=5, column=0, sticky="nsew", pady=4)
        main.rowconfigure(5, weight=1)

    # ---- 공통 ----------------------------------------------------------

    def log(self, text: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _save_config(self) -> None:
        self.config.update(
            ms_client_id=self.ms_id.get().strip(),
            google_client_id=self.g_id.get().strip(),
            google_client_secret=self.g_secret.get().strip(),
            scope=self.scope.get(),
            custom_path=self.custom_path.get().strip(),
            use_album=self.use_album.get(),
            album=self.album.get().strip(),
        )
        save_json(self.config_path, self.config, private=True)

    def _ms_auth(self) -> MicrosoftAuth:
        return MicrosoftAuth(self.tokens, self.ms_id.get())

    def _g_auth(self) -> GoogleAuth:
        return GoogleAuth(self.tokens, self.g_id.get(), self.g_secret.get())

    def _refresh_login_labels(self) -> None:
        ms = bool(self.tokens.get("microsoft").get("refresh_token"))
        gg = bool(self.tokens.get("google").get("refresh_token"))
        self.ms_status.configure(text="✔ 로그인됨" if ms else "로그인 필요")
        self.g_status.configure(text="✔ 로그인됨" if gg else "로그인 필요")

    def _set_busy(self, busy: bool, running: bool = False) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        for widget in (self.ms_button, self.g_button, self.start_button):
            widget.configure(state=state)
        self.stop_button.configure(state="normal" if running else "disabled")

    def _show_progress(self, counts: dict) -> None:
        total = counts["total"]
        finished = counts[DONE] + counts[FAILED] + counts[SKIPPED]
        self.bar.configure(maximum=max(total, 1), value=finished)
        if total:
            self.summary.configure(
                text=f"완료 {counts[DONE]} / 전체 {total}  (실패 {counts[FAILED]}, 제외 {counts[SKIPPED]})  "
                f"{human_size(counts['done_bytes'])}"
            )

    def _show_saved_progress(self) -> None:
        path = self.dir / "state.db"
        if path.exists():
            state = State(path)
            counts = state.counts()
            state.close()
            self._show_progress(counts)
            if counts["total"]:
                self.log("이전 진행 기록이 있습니다. '옮기기 시작'을 누르면 남은 파일부터 이어서 옮깁니다.")

    def _drain(self) -> None:
        """작업 스레드가 보낸 메시지를 화면에 반영한다."""
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self.log(payload)
                elif kind == "progress":
                    self._show_progress(payload)
                elif kind == "login_done":
                    self._set_busy(False)
                    self._refresh_login_labels()
                    if payload:
                        messagebox.showerror("로그인 실패", payload)
                elif kind == "device_code":
                    self._show_device_code(payload)
                elif kind == "sync_done":
                    self.engine = None
                    self._set_busy(False)
                    self._refresh_login_labels()
                    self._finish_message(payload)
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    # ---- 로그인 ---------------------------------------------------------

    def _show_device_code(self, flow: dict) -> None:
        code = flow.get("user_code", "")
        url = flow.get("verification_uri", "https://microsoft.com/link")
        self.root.clipboard_clear()
        self.root.clipboard_append(code)
        self.log(f"브라우저에서 {url} 을 열고 코드 {code} 를 입력하세요. (코드는 복사해 두었습니다)")
        webbrowser.open(url)
        messagebox.showinfo(
            "원드라이브 로그인",
            f"브라우저가 열리면 아래 코드를 입력하고 로그인하세요.\n\n코드:  {code}\n\n"
            "(코드는 이미 복사되어 있어 붙여넣기만 하면 됩니다)",
        )

    def login_microsoft(self) -> None:
        if self.busy:
            return
        self._save_config()
        auth = self._ms_auth()
        self.login_cancel.clear()
        self._set_busy(True)

        def work():
            error = ""
            try:
                flow = auth.start_device_flow()
                self.events.put(("device_code", flow))
                auth.finish_device_flow(flow, self.login_cancel)
                self.events.put(("log", "원드라이브 로그인 완료."))
            except (AuthError, OSError) as exc:
                error = str(exc)
            self.events.put(("login_done", error))

        threading.Thread(target=work, daemon=True).start()

    def login_google(self) -> None:
        if self.busy:
            return
        self._save_config()
        auth = self._g_auth()
        self.login_cancel.clear()
        self._set_busy(True)
        self.log("브라우저에서 구글 로그인을 진행하세요.")

        def work():
            error = ""
            try:
                auth.login(webbrowser.open, self.login_cancel)
                self.events.put(("log", "구글 로그인 완료."))
            except (AuthError, OSError) as exc:
                error = str(exc)
            self.events.put(("login_done", error))

        threading.Thread(target=work, daemon=True).start()

    def logout(self, service: str) -> None:
        if self.busy:
            return
        self.tokens.clear(service)
        self._refresh_login_labels()

    # ---- 옮기기 ---------------------------------------------------------

    def start(self) -> None:
        if self.busy:
            return
        self._save_config()
        ms_auth, g_auth = self._ms_auth(), self._g_auth()
        if not ms_auth.is_logged_in() or not g_auth.is_logged_in():
            messagebox.showwarning("로그인 필요", "원드라이브와 구글 모두 로그인해야 합니다.")
            return
        if self.scope.get() == "custom" and not self.custom_path.get().strip():
            messagebox.showwarning("폴더 지정", "옮길 폴더 경로를 입력하세요.")
            return

        events = self.events
        state = State(self.dir / "state.db")
        engine = SyncEngine(
            OneDrive(ms_auth),
            GooglePhotos(g_auth),
            state,
            scope=self.scope.get(),
            custom_path=self.custom_path.get(),
            album_title=(self.album.get() or DEFAULT_ALBUM) if self.use_album.get() else "",
            log=lambda text: events.put(("log", text)),
            progress=lambda counts: events.put(("progress", counts)),
        )
        self.engine = engine
        self._set_busy(True, running=True)

        def work():
            try:
                reason = engine.run()
                problems = state.problems()
            finally:
                state.close()
            events.put(("sync_done", (reason, problems)))

        threading.Thread(target=work, daemon=True).start()

    def stop(self) -> None:
        if self.engine:
            self.engine.stop()
            self.stop_button.configure(state="disabled")
            self.log("중지하는 중... (진행 중인 파일을 정리합니다)")

    def _finish_message(self, payload) -> None:
        reason, problems = payload
        if problems:
            report = self.dir / "문제_파일_목록.txt"
            lines = [f"[{'실패' if s == FAILED else '제외'}] {path}  - {error}" for path, s, error in problems]
            report.write_text("\n".join(lines), encoding="utf-8")
            self.log(f"옮기지 못한 파일 {len(problems)}개의 목록: {report}")
        messages = {
            "done": "작업이 끝났습니다. 구글 포토에서 확인해 보세요.",
            "stopped": "중지했습니다. 다시 시작하면 이어서 옮깁니다.",
            "quota": "구글 API의 하루 한도에 도달했습니다.\n내일 다시 '옮기기 시작'을 누르면 이어서 옮깁니다.",
            "storage": "구글 저장 용량이 부족합니다.\n용량을 확보한 뒤 다시 시작하세요.",
            "error": "오류로 중단했습니다. 아래 기록을 확인한 뒤 다시 시작하세요.",
        }
        text = messages.get(reason, reason)
        if reason == "done" and problems:
            text += f"\n\n단, {len(problems)}개는 옮기지 못했습니다. 기록의 목록 파일을 확인하세요."
        messagebox.showinfo("안내", text)

    def on_close(self) -> None:
        if self.engine and not messagebox.askyesno(
            "종료", "옮기는 중입니다. 중지하고 종료할까요?\n(다음에 이어서 옮길 수 있습니다)"
        ):
            return
        if self.engine:
            self.engine.stop()
        self.login_cancel.set()
        try:
            self._save_config()
        except OSError:
            pass
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()
