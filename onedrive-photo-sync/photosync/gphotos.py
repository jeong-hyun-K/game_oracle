"""구글 포토(Photos Library API)에 업로드한다.

추가 전용 권한(photoslibrary.appendonly)만 쓰므로 기존 구글 포토 사진을
읽거나 지울 수 없다.
"""

import threading
from pathlib import Path

import requests

from .http import ApiError, check_cancel, request

PHOTOS_BASE = "https://photoslibrary.googleapis.com/v1"
BATCH_LIMIT = 50
ALBUM_LIMIT = 20000
RESUMABLE_THRESHOLD = 32 * 1024 * 1024
CHUNK_SIZE = 16 * 1024 * 1024


class QuotaExceeded(Exception):
    """구글 API 일일 호출 한도 초과."""


class StorageFull(Exception):
    """구글 저장 용량 부족."""


class _Slice:
    """파일의 일부 구간만 읽어 보내는 스트림 (메모리에 통째로 올리지 않는다)."""

    def __init__(self, path: Path, offset: int, length: int):
        self._file = open(path, "rb")
        self._file.seek(offset)
        self._left = length
        self.len = length  # requests가 Content-Length로 사용

    def read(self, size: int = -1) -> bytes:
        if self._left <= 0:
            return b""
        if size < 0 or size > self._left:
            size = self._left
        data = self._file.read(size)
        self._left -= len(data)
        return data

    def close(self) -> None:
        self._file.close()


class GooglePhotos:
    def __init__(
        self,
        auth,
        base: str = PHOTOS_BASE,
        resumable_threshold: int = RESUMABLE_THRESHOLD,
        chunk_size: int = CHUNK_SIZE,
    ):
        self.auth = auth
        self.base = base
        self.resumable_threshold = resumable_threshold
        self.chunk_size = chunk_size
        self.session = requests.Session()

    def _call(self, method: str, url: str, cancel=None, **kwargs) -> requests.Response:
        try:
            return request(self.session, self.auth, method, url, cancel=cancel, **kwargs)
        except ApiError as exc:
            if exc.status == 429:
                raise QuotaExceeded(str(exc)) from exc
            raise

    # ---- 업로드 -------------------------------------------------------

    def upload(self, path: Path, mime: str, cancel: threading.Event | None = None) -> str:
        """파일 바이트를 올리고 업로드 토큰을 돌려준다 (아직 보관함에는 없음)."""
        size = path.stat().st_size
        if size > self.resumable_threshold:
            return self._upload_resumable(path, mime, size, cancel)
        resp = self._call(
            "POST",
            f"{self.base}/uploads",
            cancel=cancel,
            make_body=lambda: open(path, "rb"),
            headers={
                "Content-Type": "application/octet-stream",
                "X-Goog-Upload-Content-Type": mime,
                "X-Goog-Upload-Protocol": "raw",
            },
        )
        return self._token(resp)

    @staticmethod
    def _token(resp: requests.Response) -> str:
        try:
            token = resp.text.strip()
        finally:
            resp.close()
        if not token:
            raise ApiError("구글이 업로드 토큰을 주지 않았습니다.")
        return token

    def _upload_resumable(self, path: Path, mime: str, size: int, cancel) -> str:
        """큰 파일은 조각으로 나눠 올리고, 끊기면 받은 지점부터 이어 올린다."""
        resp = self._call(
            "POST",
            f"{self.base}/uploads",
            cancel=cancel,
            headers={
                "Content-Length": "0",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Content-Type": mime,
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Raw-Size": str(size),
            },
        )
        upload_url = resp.headers.get("X-Goog-Upload-URL", "")
        granularity = int(resp.headers.get("X-Goog-Upload-Chunk-Granularity", "0") or 0)
        resp.close()
        if not upload_url:
            raise ApiError("구글이 이어 올리기 주소를 주지 않았습니다.")
        chunk = self.chunk_size
        if granularity > 0:
            chunk = max(granularity, chunk // granularity * granularity)

        offset = 0
        failures = 0
        while True:
            check_cancel(cancel)
            length = min(chunk, size - offset)
            last = offset + length >= size
            try:
                resp = self._call(
                    "POST",
                    upload_url,
                    cancel=cancel,
                    make_body=lambda o=offset, n=length: _Slice(path, o, n),
                    retry_statuses=(),
                    headers={
                        "X-Goog-Upload-Command": "upload, finalize" if last else "upload",
                        "X-Goog-Upload-Offset": str(offset),
                    },
                )
            except QuotaExceeded:
                raise
            except ApiError:
                failures += 1
                if failures > 5:
                    raise
                offset = self._query_offset(upload_url, cancel)
                continue
            if last:
                return self._token(resp)
            resp.close()
            offset += length

    def _query_offset(self, upload_url: str, cancel) -> int:
        resp = self._call(
            "POST",
            upload_url,
            cancel=cancel,
            headers={"Content-Length": "0", "X-Goog-Upload-Command": "query"},
        )
        try:
            if resp.headers.get("X-Goog-Upload-Status", "active") != "active":
                raise ApiError("이어 올리기 세션이 만료되었습니다.")
            return int(resp.headers.get("X-Goog-Upload-Size-Received", "0") or 0)
        finally:
            resp.close()

    # ---- 보관함 등록 ---------------------------------------------------

    def batch_create(self, entries: list[dict], album_id: str = "", cancel=None) -> list[tuple[bool, str]]:
        """업로드 토큰들을 보관함에 등록한다.

        entries: [{"token":..., "name":..., "description":...}] (최대 50개)
        반환: 입력 순서대로 (성공 여부, 미디어 ID 또는 오류 메시지)
        """
        if not entries:
            return []
        if len(entries) > BATCH_LIMIT:
            raise ValueError("한 번에 50개까지만 등록할 수 있습니다.")
        body: dict = {
            "newMediaItems": [
                {
                    "description": e.get("description", "")[:1000],
                    "simpleMediaItem": {"uploadToken": e["token"], "fileName": e["name"][:255]},
                }
                for e in entries
            ]
        }
        if album_id:
            body["albumId"] = album_id
        resp = self._call("POST", f"{self.base}/mediaItems:batchCreate", cancel=cancel, json=body)
        try:
            results = resp.json().get("newMediaItemResults", [])
        finally:
            resp.close()

        by_token = {r.get("uploadToken"): r for r in results}
        output = []
        for entry in entries:
            result = by_token.get(entry["token"])
            if result is None:
                output.append((False, "구글 응답에 결과가 없습니다."))
                continue
            status = result.get("status", {})
            media_id = result.get("mediaItem", {}).get("id", "")
            if media_id and status.get("code", 0) in (0, None):
                output.append((True, media_id))
                continue
            message = status.get("message", "알 수 없는 오류")
            if status.get("code") == 8 or "storage" in message.lower():
                raise StorageFull(message)
            output.append((False, message))
        return output

    def create_album(self, title: str, cancel=None) -> str:
        resp = self._call("POST", f"{self.base}/albums", cancel=cancel, json={"album": {"title": title}})
        try:
            return resp.json()["id"]
        finally:
            resp.close()
