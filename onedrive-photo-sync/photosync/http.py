"""재시도와 토큰 갱신을 처리하는 공통 HTTP 호출."""

import threading
import time

import requests

TIMEOUT = (30, 300)
MAX_TRIES = 5


class Cancelled(Exception):
    """사용자가 중지를 눌렀다."""


class ApiError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def _sleep(seconds: float, cancel: threading.Event | None) -> None:
    if cancel is not None:
        if cancel.wait(seconds):
            raise Cancelled()
    else:
        time.sleep(seconds)


def _detail(resp: requests.Response) -> str:
    try:
        error = resp.json().get("error", {})
        if isinstance(error, dict):
            return error.get("message") or resp.text[:300]
        return str(error)
    except ValueError:
        return resp.text[:300]


def request(
    session: requests.Session,
    auth,
    method: str,
    url: str,
    cancel: threading.Event | None = None,
    make_body=None,
    retry_statuses=(429, 500, 502, 503, 504),
    **kwargs,
) -> requests.Response:
    """인증 헤더를 붙여 호출하고, 일시적 오류는 잠시 기다렸다가 다시 시도한다.

    make_body: 재시도 때마다 본문을 새로 만들어야 할 때(파일 스트림 등) 쓰는 함수.
    """
    headers = dict(kwargs.pop("headers", {}) or {})
    refreshed = False
    last_error = ""
    last_status = 0
    for attempt in range(MAX_TRIES):
        check_cancel(cancel)
        headers["Authorization"] = "Bearer " + auth.get_token()
        body = None
        try:
            if make_body is not None:
                body = make_body()
                kwargs["data"] = body
            kwargs.setdefault("timeout", TIMEOUT)
            resp = session.request(method, url, headers=headers, **kwargs)
        except requests.RequestException as exc:
            last_error, last_status = f"네트워크 오류: {exc}", 0
            _sleep(min(2 ** attempt * 2, 60), cancel)
            continue
        finally:
            if body is not None and hasattr(body, "close"):
                body.close()

        if resp.status_code == 401 and not refreshed:
            refreshed = True
            resp.close()
            auth.get_token(force_refresh=True)
            continue
        if resp.status_code in retry_statuses:
            last_error, last_status = _detail(resp), resp.status_code
            try:
                wait = float(resp.headers.get("Retry-After", ""))
            except ValueError:
                wait = 2 ** attempt * 2
            resp.close()
            if attempt < MAX_TRIES - 1:
                _sleep(min(wait, 120), cancel)
            continue
        if resp.status_code >= 400:
            detail = _detail(resp)
            status = resp.status_code
            resp.close()
            raise ApiError(f"HTTP {status}: {detail}", status)
        return resp
    raise ApiError(last_error or "요청 실패", last_status)
