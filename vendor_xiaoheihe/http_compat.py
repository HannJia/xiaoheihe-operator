from __future__ import annotations

import asyncio
import json
import socket
import urllib.error
import urllib.request
from http.cookies import SimpleCookie
from types import SimpleNamespace
from typing import Any, Mapping
from urllib.parse import urlencode, urljoin


class TimeoutException(OSError):
    pass


class NetworkError(OSError):
    pass


class HTTPStatusError(RuntimeError):
    def __init__(self, message: str, response: Response) -> None:
        super().__init__(message)
        self.response = response


class Timeout:
    def __init__(self, timeout: float = 20, **kwargs: Any) -> None:
        self.total = float(timeout)
        self.read = float(kwargs.get("read", timeout))


class Limits:
    def __init__(self, **kwargs: Any) -> None:
        del kwargs


class CookieJar:
    def __init__(self) -> None:
        self.jar: list[SimpleNamespace] = []

    def set(self, name: str, value: str) -> None:
        self.jar = [cookie for cookie in self.jar if cookie.name != name]
        self.jar.append(SimpleNamespace(name=str(name), value=str(value)))

    def header_value(self) -> str:
        return "; ".join(f"{cookie.name}={cookie.value}" for cookie in self.jar)

    def update_from_headers(self, headers: Mapping[str, str]) -> None:
        for value in _all_set_cookie_headers(headers):
            parsed = SimpleCookie()
            parsed.load(value)
            for name, morsel in parsed.items():
                self.set(name, morsel.value)


class Response:
    def __init__(self, status_code: int, headers: Mapping[str, str], body: bytes, cookies: CookieJar) -> None:
        self.status_code = int(status_code)
        self.headers = dict(headers)
        self.content = body
        self.cookies = cookies

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if self.status_code < 200 or self.status_code >= 300:
            raise HTTPStatusError(f"HTTP {self.status_code}", self)


class AsyncClient:
    def __init__(
        self,
        *,
        base_url: str = "",
        timeout: Timeout | None = None,
        follow_redirects: bool = False,
        headers: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        del kwargs
        self.base_url = str(base_url or "")
        self.timeout = timeout or Timeout()
        self.follow_redirects = follow_redirects
        self.headers = dict(headers or {})
        self.cookies = CookieJar()
        self._closed = False

    async def __aenter__(self) -> AsyncClient:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.aclose()

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        data: Mapping[str, Any] | bytes | str | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: Timeout | None = None,
    ) -> Response:
        if self._closed:
            raise RuntimeError("HTTP Client 已关闭")
        return await asyncio.to_thread(
            self._request_sync,
            method,
            url,
            params,
            json,
            data,
            headers,
            timeout or self.timeout,
        )

    async def put(
        self,
        url: str,
        *,
        content: bytes,
        headers: Mapping[str, str] | None = None,
    ) -> Response:
        return await self.request("PUT", url, data=content, headers=headers)

    def _request_sync(
        self,
        method: str,
        url: str,
        params: Mapping[str, Any] | None,
        json_body: Any,
        data: Mapping[str, Any] | bytes | str | None,
        headers: Mapping[str, str] | None,
        timeout: Timeout,
    ) -> Response:
        request_url = urljoin(self.base_url.rstrip("/") + "/", str(url))
        if params:
            query = urlencode([(str(k), str(v)) for k, v in params.items() if v is not None])
            request_url += ("&" if "?" in request_url else "?") + query
        body: bytes | None = None
        request_headers = dict(self.headers)
        request_headers.update({str(k): str(v) for k, v in (headers or {}).items()})
        cookie_header = self.cookies.header_value()
        if cookie_header and "Cookie" not in request_headers:
            request_headers["Cookie"] = cookie_header
        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        elif isinstance(data, Mapping):
            body = urlencode([(str(k), str(v)) for k, v in data.items()]).encode("utf-8")
        elif isinstance(data, str):
            body = data.encode("utf-8")
        elif isinstance(data, bytes):
            body = data
        request = urllib.request.Request(request_url, data=body, headers=request_headers, method=str(method).upper())
        opener = urllib.request.build_opener(_NoRedirectHandler()) if not self.follow_redirects else urllib.request.build_opener()
        try:
            with opener.open(request, timeout=max(1.0, float(timeout.read))) as response:
                response_headers = {str(k): str(v) for k, v in response.headers.items()}
                response_body = response.read()
                self.cookies.update_from_headers(response.headers)
                response_cookies = CookieJar()
                response_cookies.update_from_headers(response.headers)
                return Response(response.status, response_headers, response_body, response_cookies)
        except urllib.error.HTTPError as exc:
            response_headers = {str(k): str(v) for k, v in exc.headers.items()} if exc.headers else {}
            response_body = exc.read()
            self.cookies.update_from_headers(response_headers)
            response_cookies = CookieJar()
            response_cookies.update_from_headers(response_headers)
            return Response(exc.code, response_headers, response_body, response_cookies)
        except (TimeoutError, socket.timeout) as exc:
            raise TimeoutException(str(exc)) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise NetworkError(str(exc)) from exc

    async def aclose(self) -> None:
        self._closed = True


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _all_set_cookie_headers(headers: Mapping[str, str]) -> list[str]:
    values: list[str] = []
    for key, value in headers.items():
        if str(key).casefold() == "set-cookie":
            values.append(str(value))
    return values
