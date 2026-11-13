"""원드라이브(Microsoft Graph)에서 사진·동영상 목록을 읽고 내려받는다.

읽기 전용 권한(Files.Read)만 쓰므로 원드라이브의 파일을 지우거나 바꾸지 않는다.
"""

import os
import threading
import urllib.parse
from pathlib import Path

import requests

from .http import ApiError, check_cancel, request

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

IMAGE_EXT = {
    "jpg", "jpeg", "png", "gif", "heic", "heif", "webp", "bmp", "tif", "tiff", "avif", "ico",
    "dng", "cr2", "cr3", "nef", "arw", "orf", "rw2", "raf", "srw",
}
VIDEO_EXT = {
    "mp4", "mov", "m4v", "3gp", "3g2", "avi", "mkv", "wmv", "asf", "mpg", "mts", "m2ts", "m2t",
    "divx", "mmv", "mod", "tod",
}
MIME_BY_EXT = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif",
    "heic": "image/heic", "heif": "image/heif", "webp": "image/webp", "bmp": "image/bmp",
    "tif": "image/tiff", "tiff": "image/tiff", "avif": "image/avif",
    "mp4": "video/mp4", "mov": "video/quicktime", "m4v": "video/x-m4v", "3gp": "video/3gpp",
    "avi": "video/x-msvideo", "mkv": "video/x-matroska", "wmv": "video/x-ms-wmv",
}

SELECT = "id,name,size,file,folder,parentReference"


def media_kind(name: str) -> str:
    """'image', 'video' 또는 ''(사진·동영상 아님)."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    return ""


def mime_type(name: str, reported: str = "") -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in MIME_BY_EXT:
        return MIME_BY_EXT[ext]
    if reported.startswith(("image/", "video/")):
        return reported
    return "application/octet-stream"


class OneDrive:
    def __init__(self, auth, base: str = GRAPH_BASE):
        self.auth = auth
        self.base = base
        self.session = requests.Session()

    def _get_json(self, url: str, cancel=None) -> dict:
        resp = request(self.session, self.auth, "GET", url, cancel=cancel)
        try:
            return resp.json()
        finally:
            resp.close()

    def _root(self, scope: str, custom_path: str, cancel=None) -> dict:
        if scope == "all":
            url = f"{self.base}/me/drive/root"
        elif scope == "custom":
            path = custom_path.strip().strip("/\\").replace("\\", "/")
            if not path:
                raise ApiError("폴더 경로가 비어 있습니다.")
            url = f"{self.base}/me/drive/root:/{urllib.parse.quote(path)}"
        else:
            # 언어 설정과 무관하게 '사진(Pictures)' 폴더를 가리킨다.
            url = f"{self.base}/me/drive/special/photos"
        try:
            return self._get_json(url + f"?$select={SELECT}", cancel)
        except ApiError as exc:
            if exc.status == 404:
                raise ApiError("원드라이브에서 해당 폴더를 찾지 못했습니다. 범위를 '전체'로 바꿔 보세요.", 404)
            raise

    def iter_media(self, scope: str = "photos", custom_path: str = "", cancel=None, on_folder=None):
        """범위 안의 사진·동영상을 하나씩 돌려준다 (하위 폴더 포함)."""
        root = self._root(scope, custom_path, cancel)
        if "folder" not in root:
            raise ApiError("지정한 경로가 폴더가 아닙니다.")
        stack = [(root["id"], root.get("name", ""))]
        while stack:
            folder_id, folder_path = stack.pop()
            if on_folder:
                on_folder(folder_path)
            url = f"{self.base}/me/drive/items/{folder_id}/children?$top=200&$select={SELECT}"
            while url:
                check_cancel(cancel)
                page = self._get_json(url, cancel)
                for item in page.get("value", []):
                    name = item.get("name", "")
                    path = f"{folder_path}/{name}" if folder_path else name
                    if "folder" in item:
                        stack.append((item["id"], path))
                        continue
                    if "file" not in item:
                        continue
                    kind = media_kind(name)
                    if not kind:
                        continue
                    yield {
                        "id": item["id"],
                        "name": name,
                        "path": path,
                        "size": int(item.get("size", 0)),
                        "kind": kind,
                        "mime": mime_type(name, item["file"].get("mimeType", "")),
                    }
                url = page.get("@odata.nextLink")

    def download(self, item_id: str, dest: Path, cancel: threading.Event | None = None) -> int:
        """파일을 dest에 저장하고 받은 바이트 수를 돌려준다."""
        resp = request(
            self.session,
            self.auth,
            "GET",
            f"{self.base}/me/drive/items/{item_id}/content",
            cancel=cancel,
            stream=True,
        )
        written = 0
        try:
            with open(dest, "wb") as out:
                for chunk in resp.iter_content(1024 * 1024):
                    check_cancel(cancel)
                    out.write(chunk)
                    written += len(chunk)
        except BaseException:
            try:
                os.remove(dest)
            except OSError:
                pass
            raise
        finally:
            resp.close()
        return written
