"""가짜 원드라이브/구글 서버를 띄워 전체 흐름을 검증한다.

실행:  python -m unittest discover -s tests -v
"""

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from photosync.auth import GoogleAuth, MicrosoftAuth, TokenStore  # noqa: E402
from photosync.engine import SyncEngine  # noqa: E402
from photosync.gphotos import GooglePhotos  # noqa: E402
from photosync.onedrive import OneDrive  # noqa: E402
from photosync.state import State  # noqa: E402


def blob(seed: int, size: int) -> bytes:
    return bytes((seed + i * 7) % 256 for i in range(size))


class Fake:
    """가짜 서버의 상태."""

    def __init__(self):
        self.lock = threading.Lock()
        # 원드라이브 구조: Pictures/{a.jpg, 메모.txt, Samsung Gallery/{b.heic, 영상.mp4, 빈파일.jpg, DCIM/c.png}}
        self.files = {
            "f-a": ("a.jpg", blob(1, 3000)),
            "f-b": ("b.heic", blob(2, 5000)),
            "f-v": ("영상.mp4", blob(3, 700_000)),
            "f-c": ("c.png", blob(4, 1200)),
            "f-e": ("빈파일.jpg", b""),
            "f-t": ("메모.txt", b"not a photo"),
        }
        self.tree = {
            "root": ["f-a", "f-t", "d-sg"],
            "d-sg": ["f-b", "f-v", "f-e", "d-dcim"],
            "d-dcim": ["f-c"],
        }
        self.folder_names = {"d-sg": "Samsung Gallery", "d-dcim": "DCIM"}
        self.tokens: dict[str, bytes] = {}      # 업로드 토큰 → 바이트
        self.sessions: dict[str, bytearray] = {}
        self.library: list[dict] = []            # 보관함에 등록된 항목
        self.albums: list[str] = []
        self.downloads = 0
        self.fail_once = {"list429", "list401", "chunk500"}
        self.quota_exceeded = False
        self.fail_names: set[str] = set()
        self.device_polls = 0


def make_handler(fake: Fake):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _send(self, status, body=b"", headers=None, ctype="application/json"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> bytes:
            return self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))

        def _once(self, name: str) -> bool:
            with fake.lock:
                if name in fake.fail_once:
                    fake.fail_once.discard(name)
                    return True
            return False

        def _item(self, item_id):
            if item_id in fake.files:
                name, data = fake.files[item_id]
                return {"id": item_id, "name": name, "size": len(data), "file": {"mimeType": "x/y"}}
            return {"id": item_id, "name": fake.folder_names.get(item_id, "Pictures"), "folder": {}}

        def do_GET(self):  # noqa: N802
            url = urllib.parse.urlparse(self.path)
            path = urllib.parse.unquote(url.path)
            query = urllib.parse.parse_qs(url.query)
            base = f"http://127.0.0.1:{self.server.server_address[1]}"
            if path.startswith("/dl/"):
                # 실제 원드라이브처럼 다운로드 주소에는 인증 헤더가 필요 없다.
                with fake.lock:
                    fake.downloads += 1
                return self._send(200, fake.files[path[4:]][1], ctype="application/octet-stream")
            if self.headers.get("Authorization") != "Bearer good":
                return self._send(401, {"error": {"message": "bad token"}})
            if path in ("/graph/me/drive/special/photos", "/graph/me/drive/root"):
                return self._send(200, self._item("root"))
            if path == "/graph/me/drive/root:/없는폴더":
                return self._send(404, {"error": {"message": "itemNotFound"}})
            if path.endswith("/children"):
                if self._once("list429"):
                    return self._send(429, {"error": {"message": "slow"}}, {"Retry-After": "0"})
                if self._once("list401"):
                    return self._send(401, {"error": {"message": "expired"}})
                folder = path.split("/")[-2]
                children = fake.tree[folder]
                skip = int(query.get("skip", ["0"])[0])
                page = {"value": [self._item(c) for c in children[skip : skip + 2]]}
                if skip + 2 < len(children):  # 2개씩 끊어 페이지 넘김을 검증
                    page["@odata.nextLink"] = f"{base}{url.path}?skip={skip + 2}"
                return self._send(200, page)
            if path.endswith("/content"):
                item_id = path.split("/")[-2]
                return self._send(302, b"", {"Location": f"{base}/dl/{item_id}"})
            self._send(404, {"error": {"message": "unknown " + path}})

        def do_POST(self):  # noqa: N802
            path = urllib.parse.urlparse(self.path).path
            body = self._body()
            base = f"http://127.0.0.1:{self.server.server_address[1]}"

            # ---- 로그인 서버 ----
            if path == "/ms/devicecode":
                return self._send(200, {"device_code": "dev", "user_code": "ABCD", "interval": 0,
                                        "expires_in": 60, "verification_uri": "https://microsoft.com/link"})
            if path == "/ms/token":
                form = urllib.parse.parse_qs(body.decode())
                if form["grant_type"][0] == "refresh_token":
                    return self._send(200, {"access_token": "good", "expires_in": 3600})
                with fake.lock:
                    fake.device_polls += 1
                    polls = fake.device_polls
                if polls < 3:
                    return self._send(400, {"error": "authorization_pending"})
                return self._send(200, {"access_token": "good", "refresh_token": "r-ms", "expires_in": 3600})
            if path == "/google/token":
                form = urllib.parse.parse_qs(body.decode())
                if form["grant_type"][0] == "authorization_code":
                    ok = form["code"][0] == "the-code" and "code_verifier" in form
                    if not ok:
                        return self._send(400, {"error": "invalid_grant"})
                    return self._send(200, {"access_token": "good", "refresh_token": "r-g", "expires_in": 3600})
                return self._send(200, {"access_token": "good", "expires_in": 3600})

            # ---- 구글 포토 ----
            if self.headers.get("Authorization") != "Bearer good":
                return self._send(401, {"error": {"message": "bad token"}})
            if fake.quota_exceeded:
                return self._send(429, {"error": {"message": "Quota exceeded"}}, {"Retry-After": "0"})
            if path == "/photos/uploads":
                if self.headers.get("X-Goog-Upload-Protocol") == "resumable":
                    with fake.lock:
                        sid = f"s{len(fake.sessions)}"
                        fake.sessions[sid] = bytearray()
                    return self._send(200, b"", {"X-Goog-Upload-URL": f"{base}/photos/resumable/{sid}",
                                                  "X-Goog-Upload-Chunk-Granularity": "1024"})
                with fake.lock:
                    token = f"t{len(fake.tokens)}"
                    fake.tokens[token] = body
                return self._send(200, token.encode(), ctype="text/plain")
            if path.startswith("/photos/resumable/"):
                sid = path.rsplit("/", 1)[1]
                command = self.headers.get("X-Goog-Upload-Command", "")
                data = fake.sessions[sid]
                if command == "query":
                    return self._send(200, b"", {"X-Goog-Upload-Status": "active",
                                                  "X-Goog-Upload-Size-Received": str(len(data))})
                offset = int(self.headers["X-Goog-Upload-Offset"])
                if offset > 0 and self._once("chunk500"):
                    # 조각의 절반만 받은 뒤 끊긴 상황을 흉내 낸다.
                    data.extend(body[: len(body) // 2 // 1024 * 1024])
                    return self._send(500, {"error": {"message": "boom"}})
                if offset != len(data):
                    return self._send(400, {"error": {"message": "bad offset"}})
                data.extend(body)
                if "finalize" in command:
                    with fake.lock:
                        token = f"t{len(fake.tokens)}"
                        fake.tokens[token] = bytes(data)
                    return self._send(200, token.encode(), ctype="text/plain")
                return self._send(200, b"")
            if path == "/photos/albums":
                title = json.loads(body)["album"]["title"]
                with fake.lock:
                    fake.albums.append(title)
                    album_id = f"album{len(fake.albums)}"
                return self._send(200, {"id": album_id, "title": title})
            if path == "/photos/mediaItems:batchCreate":
                request = json.loads(body)
                results = []
                for new in request["newMediaItems"]:
                    simple = new["simpleMediaItem"]
                    token, name = simple["uploadToken"], simple["fileName"]
                    if name in fake.fail_names:
                        results.append({"uploadToken": token, "status": {"code": 3, "message": "Failed: bad media"}})
                        continue
                    with fake.lock:
                        media_id = f"m{len(fake.library)}"
                        fake.library.append({"id": media_id, "name": name, "data": fake.tokens[token],
                                             "album": request.get("albumId", ""),
                                             "description": new.get("description", "")})
                    results.append({"uploadToken": token, "status": {"message": "Success"},
                                    "mediaItem": {"id": media_id, "filename": name}})
                return self._send(200, {"newMediaItemResults": results})
            self._send(404, {"error": {"message": "unknown " + path}})

    return Handler


class StaticAuth:
    """401을 받으면 올바른 토큰으로 '갱신'되는 가짜 인증."""

    def __init__(self):
        self.refreshes = 0

    def get_token(self, force_refresh=False):
        if force_refresh:
            self.refreshes += 1
        return "good"


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.fake = Fake()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.fake))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)
        (self.work / "tmp").mkdir()
        self.logs: list[str] = []

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def engine(self, state, **kwargs):
        self.auth = StaticAuth()
        return SyncEngine(
            OneDrive(self.auth, base=self.base + "/graph"),
            # 임계값을 낮춰 '영상.mp4'가 이어 올리기(조각 업로드) 경로를 타게 한다.
            GooglePhotos(self.auth, base=self.base + "/photos", resumable_threshold=100_000, chunk_size=200_000),
            state,
            log=self.logs.append,
            tmp_dir=str(self.work / "tmp"),
            **kwargs,
        )

    def test_full_transfer_and_resume(self):
        state = State(self.work / "state.db")
        reason = self.engine(state, album_title="원드라이브에서 가져옴").run()
        self.assertEqual(reason, "done", self.logs)

        # 사진·동영상 4개가 바이트까지 동일하게 옮겨졌는지
        uploaded = {item["name"]: item for item in self.fake.library}
        self.assertEqual(set(uploaded), {"a.jpg", "b.heic", "영상.mp4", "c.png"})
        for name, data in self.fake.files.values():
            if name in uploaded:
                self.assertEqual(uploaded[name]["data"], data, name)
        self.assertEqual(uploaded["c.png"]["description"], "OneDrive: Pictures/Samsung Gallery/DCIM/c.png")

        # 앨범은 하나만 만들어지고 모두 그 안에 들어감
        self.assertEqual(self.fake.albums, ["원드라이브에서 가져옴"])
        self.assertEqual({item["album"] for item in self.fake.library}, {"album1"})

        counts = state.counts()
        self.assertEqual((counts["total"], counts["done"], counts["skipped"], counts["failed"]), (5, 4, 1, 0))
        self.assertEqual(self.fake.fail_once, set(), "재시도 경로(429·401·조각 실패)가 모두 실행되어야 함")
        self.assertEqual(self.auth.refreshes, 1)
        self.assertEqual(os.listdir(self.work / "tmp"), [], "임시 파일이 남으면 안 됨")

        # 다시 실행하면 아무것도 다시 받거나 올리지 않음
        downloads = self.fake.downloads
        self.assertEqual(self.engine(state, album_title="원드라이브에서 가져옴").run(), "done")
        self.assertEqual(self.fake.downloads, downloads)
        self.assertEqual(len(self.fake.library), 4)
        self.assertEqual(len(self.fake.albums), 1)
        state.close()

    def test_new_files_only_on_next_run(self):
        state = State(self.work / "state.db")
        self.engine(state).run()
        self.fake.files["f-n"] = ("새사진.jpg", blob(9, 2222))
        self.fake.tree["d-dcim"].append("f-n")
        self.assertEqual(self.engine(state).run(), "done")
        self.assertEqual([i["name"] for i in self.fake.library].count("새사진.jpg"), 1)
        self.assertEqual(len(self.fake.library), 5)
        self.assertEqual(self.fake.albums, [], "앨범 옵션을 끄면 앨범을 만들지 않음")
        state.close()

    def test_quota_then_resume(self):
        state = State(self.work / "state.db")
        self.fake.quota_exceeded = True
        self.assertEqual(self.engine(state).run(), "quota")
        self.assertEqual(self.fake.library, [])
        self.assertEqual(state.counts()["done"], 0)

        self.fake.quota_exceeded = False
        self.assertEqual(self.engine(state).run(), "done")
        self.assertEqual(len(self.fake.library), 4)
        state.close()

    def test_item_failure_is_retried_next_run(self):
        state = State(self.work / "state.db")
        self.fake.fail_names = {"b.heic"}
        self.assertEqual(self.engine(state).run(), "done")
        counts = state.counts()
        self.assertEqual((counts["done"], counts["failed"]), (3, 1))
        self.assertIn("bad media", state.problems()[0][2] + state.problems()[1][2])

        self.fake.fail_names = set()
        self.engine(state).run()
        self.assertEqual(state.counts()["failed"], 0)
        self.assertEqual(len(self.fake.library), 4)
        state.close()

    def test_stop_before_start(self):
        state = State(self.work / "state.db")
        engine = self.engine(state)
        engine.stop()
        self.assertEqual(engine.run(), "stopped")
        self.assertEqual(self.fake.library, [])
        state.close()

    def test_missing_folder(self):
        state = State(self.work / "state.db")
        self.assertEqual(self.engine(state, scope="custom", custom_path="없는폴더").run(), "error")
        self.assertTrue(any("찾지 못했습니다" in line for line in self.logs), self.logs)
        state.close()

    def test_microsoft_device_login(self):
        store = TokenStore(self.work / "tokens.json")
        auth = MicrosoftAuth(store, "client", auth_base=self.base + "/ms")
        self.assertFalse(auth.is_logged_in())
        flow = auth.start_device_flow()
        self.assertEqual(flow["user_code"], "ABCD")
        auth.finish_device_flow(flow, threading.Event())
        self.assertEqual(self.fake.device_polls, 3)
        self.assertEqual(auth.get_token(), "good")
        self.assertEqual(auth.get_token(force_refresh=True), "good")
        self.assertEqual(store.get("microsoft")["refresh_token"], "r-ms", "갱신 후에도 refresh_token 유지")

    def test_google_browser_login(self):
        store = TokenStore(self.work / "tokens.json")
        auth = GoogleAuth(store, "cid", "secret", auth_url="http://example.invalid/auth",
                          token_url=self.base + "/google/token")

        def fake_browser(url):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            self.assertEqual(query["code_challenge_method"], ["S256"])
            self.assertIn("photoslibrary.appendonly", query["scope"][0])
            back = query["redirect_uri"][0] + "/?" + urllib.parse.urlencode(
                {"code": "the-code", "state": query["state"][0]})
            threading.Thread(target=lambda: urllib.request.urlopen(back).read(), daemon=True).start()

        auth.login(fake_browser, timeout=10)
        self.assertTrue(auth.is_logged_in())
        self.assertEqual(auth.get_token(), "good")


if __name__ == "__main__":
    unittest.main()
