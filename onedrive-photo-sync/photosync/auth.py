"""Microsoft / Google 로그인 (OAuth 2.0).

비밀번호는 이 프로그램을 거치지 않는다. 로그인은 항상 브라우저의 공식
로그인 화면에서 이뤄지고, 프로그램은 발급된 토큰만 로컬에 저장한다.
"""

import base64
import hashlib
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests

from .config import load_json, save_json

MS_AUTH_BASE = "https://login.microsoftonline.com/consumers/oauth2/v2.0"
MS_SCOPE = "Files.Read offline_access"

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_SCOPE = "https://www.googleapis.com/auth/photoslibrary.appendonly"

TIMEOUT = 30


class AuthError(Exception):
    """로그인 실패 또는 로그인이 다시 필요한 상태."""


class TokenStore:
    """서비스별 토큰을 하나의 JSON 파일에 보관한다."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def get(self, service: str) -> dict:
        with self._lock:
            return load_json(self.path).get(service, {})

    def put(self, service: str, token: dict) -> None:
        with self._lock:
            data = load_json(self.path)
            data[service] = token
            save_json(self.path, data, private=True)

    def clear(self, service: str) -> None:
        with self._lock:
            data = load_json(self.path)
            data.pop(service, None)
            save_json(self.path, data, private=True)


def _error_text(resp: requests.Response) -> str:
    try:
        body = resp.json()
        return body.get("error_description") or body.get("error") or resp.text[:300]
    except ValueError:
        return resp.text[:300]


class _BaseAuth:
    service = ""

    def __init__(self, store: TokenStore):
        self.store = store
        self._lock = threading.Lock()

    def is_logged_in(self) -> bool:
        return bool(self.store.get(self.service).get("refresh_token"))

    def logout(self) -> None:
        self.store.clear(self.service)

    def _save(self, body: dict) -> dict:
        old = self.store.get(self.service)
        token = {
            "access_token": body["access_token"],
            # 갱신 응답에는 refresh_token이 없을 수 있으므로 기존 값을 유지한다.
            "refresh_token": body.get("refresh_token") or old.get("refresh_token", ""),
            "expires_at": time.time() + int(body.get("expires_in", 3600)),
        }
        self.store.put(self.service, token)
        return token

    def _refresh(self, refresh_token: str) -> dict:
        raise NotImplementedError

    def get_token(self, force_refresh: bool = False) -> str:
        """유효한 액세스 토큰을 돌려준다. 만료가 가까우면 자동 갱신."""
        with self._lock:
            token = self.store.get(self.service)
            if not token.get("refresh_token"):
                raise AuthError(f"{self.label} 로그인이 필요합니다.")
            if not force_refresh and token.get("expires_at", 0) - time.time() > 120:
                return token["access_token"]
            return self._save(self._refresh(token["refresh_token"]))["access_token"]


class MicrosoftAuth(_BaseAuth):
    """기기 코드 방식 로그인: 화면에 뜬 코드를 브라우저에 입력한다."""

    service = "microsoft"
    label = "원드라이브(Microsoft)"

    def __init__(self, store: TokenStore, client_id: str, auth_base: str = MS_AUTH_BASE):
        super().__init__(store)
        self.client_id = client_id.strip()
        self.auth_base = auth_base

    def start_device_flow(self) -> dict:
        if not self.client_id:
            raise AuthError("Microsoft 클라이언트 ID를 먼저 입력하세요.")
        resp = requests.post(
            f"{self.auth_base}/devicecode",
            data={"client_id": self.client_id, "scope": MS_SCOPE},
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            raise AuthError(f"Microsoft 로그인 시작 실패: {_error_text(resp)}")
        return resp.json()

    def finish_device_flow(self, flow: dict, cancel: threading.Event | None = None) -> None:
        """사용자가 브라우저에서 코드를 입력할 때까지 기다린다."""
        interval = int(flow.get("interval", 5))
        deadline = time.time() + int(flow.get("expires_in", 900))
        while time.time() < deadline:
            if cancel is not None and cancel.wait(interval):
                raise AuthError("로그인을 취소했습니다.")
            if cancel is None:
                time.sleep(interval)
            resp = requests.post(
                f"{self.auth_base}/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": self.client_id,
                    "device_code": flow["device_code"],
                },
                timeout=TIMEOUT,
            )
            if resp.status_code == 200:
                self._save(resp.json())
                return
            try:
                error = resp.json().get("error", "")
            except ValueError:
                error = ""
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            raise AuthError(f"Microsoft 로그인 실패: {_error_text(resp)}")
        raise AuthError("로그인 시간이 초과되었습니다. 다시 시도하세요.")

    def _refresh(self, refresh_token: str) -> dict:
        resp = requests.post(
            f"{self.auth_base}/token",
            data={
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "refresh_token": refresh_token,
                "scope": MS_SCOPE,
            },
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            raise AuthError(f"원드라이브 로그인이 만료되었습니다. 다시 로그인하세요. ({_error_text(resp)})")
        return resp.json()


class GoogleAuth(_BaseAuth):
    """브라우저 로그인 후 내 PC(127.0.0.1)로 돌아오는 방식 (PKCE)."""

    service = "google"
    label = "구글"

    def __init__(
        self,
        store: TokenStore,
        client_id: str,
        client_secret: str,
        auth_url: str = GOOGLE_AUTH_URL,
        token_url: str = GOOGLE_TOKEN_URL,
    ):
        super().__init__(store)
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.auth_url = auth_url
        self.token_url = token_url

    def login(self, open_browser, cancel: threading.Event | None = None, timeout: int = 300) -> None:
        if not self.client_id or not self.client_secret:
            raise AuthError("구글 클라이언트 ID와 보안 비밀번호를 먼저 입력하세요.")

        result: dict = {}
        state = secrets.token_urlsafe(16)
        verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        )

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if query.get("state", [""])[0] != state:
                    self.send_response(400)
                    self.end_headers()
                    return
                result["code"] = query.get("code", [""])[0]
                result["error"] = query.get("error", [""])[0]
                message = "로그인이 완료되었습니다. 이 창을 닫고 프로그램으로 돌아가세요."
                if result["error"]:
                    message = "로그인이 취소되었거나 실패했습니다. 프로그램으로 돌아가 다시 시도하세요."
                body = f"<html><body style='font-family:sans-serif'><h2>{message}</h2></body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body.encode("utf-8"))

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        server.timeout = 1
        redirect_uri = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            open_browser(
                self.auth_url
                + "?"
                + urllib.parse.urlencode(
                    {
                        "client_id": self.client_id,
                        "redirect_uri": redirect_uri,
                        "response_type": "code",
                        "scope": GOOGLE_SCOPE,
                        "access_type": "offline",
                        "prompt": "consent",
                        "state": state,
                        "code_challenge": challenge,
                        "code_challenge_method": "S256",
                    }
                )
            )
            deadline = time.time() + timeout
            while "code" not in result:
                if cancel is not None and cancel.is_set():
                    raise AuthError("로그인을 취소했습니다.")
                if time.time() > deadline:
                    raise AuthError("로그인 시간이 초과되었습니다. 다시 시도하세요.")
                server.handle_request()
        finally:
            server.server_close()

        if result.get("error") or not result.get("code"):
            raise AuthError(f"구글 로그인 실패: {result.get('error') or '코드를 받지 못했습니다.'}")

        resp = requests.post(
            self.token_url,
            data={
                "grant_type": "authorization_code",
                "code": result["code"],
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            raise AuthError(f"구글 로그인 실패: {_error_text(resp)}")
        body = resp.json()
        if not body.get("refresh_token"):
            raise AuthError("구글이 갱신 토큰을 주지 않았습니다. 다시 로그인하세요.")
        self._save(body)

    def _refresh(self, refresh_token: str) -> dict:
        resp = requests.post(
            self.token_url,
            data={
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": refresh_token,
            },
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            raise AuthError(f"구글 로그인이 만료되었습니다. 다시 로그인하세요. ({_error_text(resp)})")
        return resp.json()
