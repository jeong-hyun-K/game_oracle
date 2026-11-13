"""이전 작업 본체: 목록 수집 → 내려받기 → 구글 포토 업로드 → 기록."""

import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .gphotos import ALBUM_LIMIT, GooglePhotos, QuotaExceeded, StorageFull
from .auth import AuthError
from .http import ApiError, Cancelled
from .onedrive import OneDrive
from .state import DONE, FAILED, SKIPPED, State

# 구글 포토 API가 받는 최대 크기
MAX_IMAGE_BYTES = 200 * 1024 * 1024
MAX_VIDEO_BYTES = 20 * 1024 * 1024 * 1024

BATCH_SIZE = 20
WORKERS = 3


class SyncEngine:
    def __init__(
        self,
        onedrive: OneDrive,
        gphotos: GooglePhotos,
        state: State,
        scope: str = "photos",
        custom_path: str = "",
        album_title: str = "",
        log=print,
        progress=lambda counts: None,
        tmp_dir: str | None = None,
    ):
        self.onedrive = onedrive
        self.gphotos = gphotos
        self.state = state
        self.scope = scope
        self.custom_path = custom_path
        self.album_title = album_title.strip()
        self.log = log
        self.progress = progress
        self.tmp_dir = tmp_dir
        self.cancel = threading.Event()

    def stop(self) -> None:
        self.cancel.set()

    # ---- 전체 흐름 ------------------------------------------------------

    def run(self) -> str:
        """작업을 수행하고 종료 사유를 돌려준다: done / stopped / quota / storage / error"""
        try:
            self._scan()
            reason = self._transfer()
        except Cancelled:
            reason = "stopped"
            self.log("중지했습니다. 다시 시작하면 남은 파일부터 이어서 옮깁니다.")
        except QuotaExceeded:
            reason = "quota"
            self.log("구글 API의 하루 사용 한도에 도달했습니다. 내일 다시 시작하면 이어서 옮깁니다.")
        except StorageFull:
            reason = "storage"
            self.log("구글 저장 용량이 부족합니다. 용량을 확보한 뒤 다시 시작하세요.")
        except Exception as exc:  # 로그인 만료, 네트워크 단절 등
            reason = "error"
            self.log(f"오류로 중단했습니다: {exc}")
        counts = self.state.counts()
        self.progress(counts)
        self.log(
            f"결과: 전체 {counts['total']}개 중 완료 {counts[DONE]}개, 실패 {counts[FAILED]}개, "
            f"제외 {counts[SKIPPED]}개, 남음 {counts['pending']}개"
        )
        return reason

    def _scan(self) -> None:
        self.log("원드라이브에서 사진·동영상 목록을 가져오는 중...")
        found = 0
        batch: list[dict] = []
        for item in self.onedrive.iter_media(self.scope, self.custom_path, self.cancel):
            batch.append(item)
            found += 1
            if len(batch) >= 500:
                self.state.add_items(batch)
                batch = []
                self.log(f"  목록 수집 중... {found}개")
        self.state.add_items(batch)
        self.log(f"원드라이브에서 사진·동영상 {found}개를 찾았습니다.")

    def _transfer(self) -> str:
        todo = []
        for item in self.state.todo():
            limit = MAX_VIDEO_BYTES if item["kind"] == "video" else MAX_IMAGE_BYTES
            if item["size"] <= 0:
                self.state.mark(item["id"], SKIPPED, error="빈 파일(0바이트)")
            elif item["size"] > limit:
                self.state.mark(item["id"], SKIPPED, error="구글 포토 허용 크기 초과")
            else:
                todo.append(item)
        self.progress(self.state.counts())
        if not todo:
            self.log("새로 옮길 파일이 없습니다.")
            return "done"
        self.log(f"{len(todo)}개를 구글 포토로 옮깁니다.")

        bad_chunks = 0
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for start in range(0, len(todo), BATCH_SIZE):
                if self.cancel.is_set():
                    raise Cancelled()
                chunk = todo[start : start + BATCH_SIZE]
                uploaded = []
                fatal: BaseException | None = None
                for item, future in [(i, pool.submit(self._move_bytes, i)) for i in chunk]:
                    try:
                        uploaded.append((item, future.result()))
                    except (Cancelled, QuotaExceeded, AuthError) as exc:
                        fatal = fatal or exc
                    except Exception as exc:
                        self.state.mark(item["id"], FAILED, error=str(exc))
                        self.log(f"  실패: {item['path']} ({exc})")
                # 이미 올린 조각은 한도 초과·중지 상황에서도 등록을 시도해 낭비를 줄인다.
                if uploaded:
                    try:
                        self._register(uploaded, None if fatal else self.cancel)
                    except QuotaExceeded as exc:
                        fatal = fatal or exc
                self.progress(self.state.counts())
                if fatal:
                    raise fatal
                # 묶음 전체가 연달아 실패하면 연결 문제로 보고 멈춘다 (기록은 남아 이어하기 가능).
                bad_chunks = 0 if uploaded else bad_chunks + 1
                if bad_chunks >= 3:
                    raise ApiError("연속으로 실패하고 있습니다. 인터넷 연결을 확인한 뒤 다시 시작하세요.")
        return "done"

    # ---- 세부 단계 ------------------------------------------------------

    def _move_bytes(self, item: dict) -> str:
        """한 파일을 임시로 내려받아 구글에 올리고 업로드 토큰을 돌려준다."""
        fd, tmp_name = tempfile.mkstemp(prefix="photosync-", dir=self.tmp_dir)
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            written = self.onedrive.download(item["id"], tmp, self.cancel)
            if written != item["size"]:
                raise ApiError(f"내려받은 크기가 다릅니다 ({written} / {item['size']}바이트)")
            return self.gphotos.upload(tmp, item["mime"], self.cancel)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass

    def _album_id(self, adding: int) -> str:
        """앨범 옵션이 켜져 있으면 앨범 ID를 돌려준다. 2만 개가 차면 새 앨범을 만든다."""
        if not self.album_title:
            return ""
        album_id = self.state.get_meta("album_id")
        count = int(self.state.get_meta("album_count", "0"))
        if not album_id or count + adding > ALBUM_LIMIT:
            number = int(self.state.get_meta("album_number", "0")) + 1
            title = self.album_title if number == 1 else f"{self.album_title} ({number})"
            album_id = self.gphotos.create_album(title)
            self.state.set_meta("album_id", album_id)
            self.state.set_meta("album_number", str(number))
            self.state.set_meta("album_count", "0")
            self.log(f"구글 포토에 앨범 '{title}'을(를) 만들었습니다.")
        return album_id

    def _register(self, uploaded: list[tuple[dict, str]], cancel) -> None:
        entries = [
            {"token": token, "name": item["name"], "description": f"OneDrive: {item['path']}"}
            for item, token in uploaded
        ]
        album_id = self._album_id(len(entries))
        try:
            results = self.gphotos.batch_create(entries, album_id, cancel)
        except ApiError as exc:
            if not album_id or exc.status not in (400, 403, 404):
                raise
            # 앨범이 구글 포토에서 삭제된 경우: 새 앨범을 만들어 한 번 더 시도한다.
            self.state.set_meta("album_id", "")
            album_id = self._album_id(len(entries))
            results = self.gphotos.batch_create(entries, album_id, cancel)

        ok_count = 0
        for (item, _), (ok, value) in zip(uploaded, results):
            if ok:
                ok_count += 1
                self.state.mark(item["id"], DONE, google_id=value)
            else:
                self.state.mark(item["id"], FAILED, error=value)
                self.log(f"  실패: {item['path']} ({value})")
        if album_id:
            count = int(self.state.get_meta("album_count", "0"))
            self.state.set_meta("album_count", str(count + ok_count))
