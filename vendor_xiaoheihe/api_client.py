from __future__ import annotations

import asyncio
import email.utils
import hashlib
import hmac
import json
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, quote, urlsplit

from . import http_compat as httpx

from .endpoints import API_BASE_URL, EndpointName, endpoint
from .models import (
    ApiPage,
    Credentials,
    LoginState,
    NotificationType,
    QRLoginSession,
    RoutingTarget,
    SendResult,
    DirectMessagePage,
    ThreadContext,
)
from .parsers import (
    ResponseShapeError,
    parse_credentials,
    parse_direct_message_page,
    parse_feed,
    parse_login_state,
    parse_notifications,
    parse_qr_response,
    parse_send_result,
    parse_thread_context,
)
from .rate_limiter import AsyncRateLimiter
from .request_signing import (
    RequestSigner,
    ensure_client_identity,
    generate_device_id,
    generate_workshop_rnd,
)
from .security import redact_data

AuthInvalidCallback = Callable[[str, int], Awaitable[None]]

WEB_CLIENT_PARAMS = {
    "os_type": "web",
    "app": "web",
    "client_type": "web",
    "version": "999.0.4",
    "web_version": "3.0",
    "x_client_type": "web",
    "x_app": "heybox_website",
    "x_os_type": "Windows",
    "device_info": "Chrome",
    "_notip": "true",
}


class XiaoheiheApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 0,
        category: str = "api",
        retry_after: float | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.category = category
        self.retry_after = retry_after
        self.details = redact_data(details)


class CredentialInvalidError(XiaoheiheApiError):
    """Credentials were rejected; real sends must stop immediately."""


class RateLimitedError(XiaoheiheApiError):
    """The service requested a slower request rate."""


class ResponseContractError(XiaoheiheApiError):
    """The upstream JSON shape changed."""


class SendUncertainError(XiaoheiheApiError):
    """A comment request timed out after dispatch and must not be blindly retried."""


def _compact_topic_name(value: object) -> str:
    return "".join(str(value or "").split()).casefold()


@dataclass(slots=True)
class ApiResponse:
    payload: Mapping[str, Any]
    cookies: dict[str, str]
    headers: Mapping[str, str]


class XiaoheiheApiClient:
    def __init__(
        self,
        profile_id: str,
        *,
        credentials: Credentials | None = None,
        base_url: str = API_BASE_URL,
        request_timeout_seconds: float = 20,
        connect_timeout_seconds: float = 8,
        read_timeout_seconds: float = 15,
        min_request_interval_seconds: float = 1.0,
        max_retries: int = 3,
        signer: RequestSigner | None = None,
        client: httpx.AsyncClient | None = None,
        on_auth_invalid: AuthInvalidCallback | None = None,
    ) -> None:
        self.profile_id = profile_id
        self.credentials = credentials
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(
            timeout=request_timeout_seconds,
            connect=connect_timeout_seconds,
            read=read_timeout_seconds,
            write=request_timeout_seconds,
            pool=connect_timeout_seconds,
        )
        self._max_retries = max(0, min(int(max_retries), 8))
        self._signer = signer or RequestSigner()
        self._client = client
        self._owns_client = client is None
        self._device_id = credentials.device_id if credentials else generate_device_id()
        if credentials is not None:
            ensure_client_identity(credentials, device_id=self._device_id)
            self._device_id = credentials.device_id
        self._limiter = AsyncRateLimiter(min_request_interval_seconds)
        self._on_auth_invalid = on_auth_invalid
        self._closed = False
        self.last_success_at: str | None = None
        self.last_error: dict[str, Any] | None = None
        self.last_notification_polls: dict[str, dict[str, Any]] = {}
        self.last_direct_message_probe: dict[str, Any] = {}
        self.consecutive_status: dict[int, int] = {401: 0, 403: 0, 429: 0}

    @property
    def closed(self) -> bool:
        return self._closed

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("HTTP Client 已关闭")
        if self._client is None:
            limits = httpx.Limits(
                max_connections=10,
                max_keepalive_connections=5,
                keepalive_expiry=30,
            )
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                limits=limits,
                follow_redirects=False,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "AstrBot-Xiaoheihe-Adapter/1.1.3",
                },
            )
        if self.credentials:
            for key, value in self.credentials.cookies.items():
                self._client.cookies.set(key, value)

    async def request_qr(self) -> QRLoginSession:
        response = await self._request(EndpointName.REQUEST_QR)
        return parse_qr_response(self.profile_id, response.payload)

    async def check_qr(
        self, qr_session: QRLoginSession
    ) -> tuple[LoginState, str, Credentials | None]:
        params = dict(qr_session.poll_params)
        if not params:
            params = dict(
                parse_qsl(
                    urlsplit(qr_session.qr_content).query,
                    keep_blank_values=True,
                )
            )
        if not params and qr_session.request_id:
            params["request_id"] = qr_session.request_id
        response = await self._request(EndpointName.QR_STATE, params=params)
        state, message = parse_login_state(response.payload)
        credentials = None
        if state is LoginState.SUCCESS:
            logged_in_at = datetime.now(UTC).isoformat()
            try:
                credentials = parse_credentials(
                    self.profile_id,
                    response.payload,
                    response.cookies,
                    logged_in_at=logged_in_at,
                )
            except ResponseShapeError as exc:
                raise ResponseContractError(
                    str(exc),
                    category="response_shape",
                    details={
                        "qr_result_fields": _result_field_names(response.payload),
                        "session_cookie_names": sorted(response.cookies)[:30],
                        "session_credential_count": len(response.cookies),
                    },
                ) from exc
            ensure_client_identity(credentials, device_id=self._device_id)
            self.credentials = credentials
        return state, message, credentials

    async def check_credentials(self) -> dict[str, Any]:
        response = await self._request(EndpointName.CURRENT_USER)
        body = response.payload.get("result", response.payload.get("data", response.payload))
        if not isinstance(body, Mapping):
            raise ResponseContractError("账号检查响应不是对象", category="response_shape")
        return dict(body)

    async def fetch_notifications(
        self,
        event_type: NotificationType,
        *,
        cursor: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> ApiPage:
        normalized_page_size = max(1, min(page_size, 100))
        offset = (max(1, page) - 1) * normalized_page_size
        if cursor:
            try:
                offset = max(0, int(cursor))
            except ValueError:
                offset = (max(1, page) - 1) * normalized_page_size
        params: dict[str, Any] = {
            "offset": offset,
            "limit": normalized_page_size,
            "no_more": "false",
            "os_type": "web",
        }
        if self.credentials and self.credentials.uid:
            params["heybox_id"] = self.credentials.uid
        if event_type is NotificationType.MENTION:
            params["message_type"] = 16
        else:
            params["list_type"] = 0
        response = await self._request(EndpointName.USER_MESSAGES, params=params)
        summary = _notification_response_summary(response.payload)
        try:
            parsed = parse_notifications(
                self.profile_id,
                response.payload,
                event_type,
                page_size=normalized_page_size,
                offset=offset,
            )
        except ResponseShapeError as exc:
            summary["error"] = str(exc)
            self.last_notification_polls[event_type.value] = summary
            error = ResponseContractError(str(exc), category="response_shape", details=summary)
            self._remember_error(error)
            raise error from exc
        summary["accepted_count"] = len(parsed.items)
        summary["missing_sender_uid_count"] = sum(
            1 for wrapper in parsed.items if not wrapper["notification"].sender_uid_verified
        )
        self.last_notification_polls[event_type.value] = summary
        return parsed

    async def fetch_direct_messages(
        self,
        *,
        cursor: str = "",
        page: int = 1,
        page_size: int = 20,
        peer_uid: str = "",
    ) -> DirectMessagePage:
        """Read private messages using the known read-only route candidates.

        The endpoint is intentionally isolated from notification polling. The
        first route that returns a recognizable message collection wins; no
        private-message write route is called here.
        """
        normalized_page_size = max(1, min(int(page_size), 50))
        normalized_page = max(1, int(page))
        offset = (normalized_page - 1) * normalized_page_size
        if cursor:
            try:
                offset = max(0, int(cursor))
            except (TypeError, ValueError):
                offset = (normalized_page - 1) * normalized_page_size
        base_params: dict[str, Any] = {
            "offset": offset,
            "limit": normalized_page_size,
            "page": normalized_page,
        }
        if peer_uid:
            base_params.update(
                {
                    "user_id": str(peer_uid),
                    "uid": str(peer_uid),
                    "peer_uid": str(peer_uid),
                }
            )
        errors: list[dict[str, Any]] = []
        for endpoint_name in (
            EndpointName.DIRECT_MESSAGE_INBOX,
            EndpointName.DIRECT_MESSAGE_HISTORY,
        ):
            try:
                response = await self._request(endpoint_name, params=dict(base_params))
                parsed = parse_direct_message_page(
                    self.profile_id,
                    response.payload,
                    owner_uid=str(self.credentials.uid if self.credentials else ""),
                    conversation_id=f"dm!{peer_uid}" if peer_uid else "",
                    page_size=normalized_page_size,
                    source=endpoint_name.value,
                )
                self.last_direct_message_probe = {
                    "source": endpoint_name.value,
                    "accepted_count": len(parsed.items),
                    "next_cursor": parsed.next_cursor,
                    "has_more": parsed.has_more,
                }
                return parsed
            except ResponseShapeError as exc:
                errors.append({"source": endpoint_name.value, "category": "response_shape", "error": str(exc)})
                continue
            except XiaoheiheApiError as exc:
                errors.append({"source": endpoint_name.value, "category": exc.category, "error": str(exc)[:180]})
                continue
        self.last_direct_message_probe = {"source": "none", "errors": errors}
        raise ResponseContractError(
            "小黑盒私信读取接口尚未返回可识别的消息结构",
            category="response_shape",
            details={"probes": errors},
        )

    async def fetch_thread_context(
        self,
        post_id: str,
        *,
        root_comment_id: str = "",
        post_context: ThreadContext | None = None,
    ) -> ThreadContext:
        # The reference mobile-web route returns the complete original post
        # when queried by link_id alone. A root-specific query may return a
        # narrowed comment tree, so keep the two sources separate and merge.
        if post_context is None:
            post_response = await self._request(
                EndpointName.POST_TREE,
                params={"link_id": post_id, "h_src": ""},
            )
            try:
                post_context = parse_thread_context(post_response.payload, post_id)
            except ResponseShapeError as exc:
                error = ResponseContractError(
                    str(exc),
                    category="response_shape",
                    details={
                        "context_source": "post",
                        **_response_shape_summary(post_response.payload),
                    },
                )
                self._remember_error(error)
                raise error from exc
        if not root_comment_id:
            return post_context

        thread_response = await self._request(
            EndpointName.POST_TREE,
            params={
                "link_id": post_id,
                "root_comment_id": root_comment_id,
                "h_src": "",
            },
        )
        try:
            thread_context = parse_thread_context(thread_response.payload, post_id)
        except ResponseShapeError as exc:
            error = ResponseContractError(
                str(exc),
                category="response_shape",
                details={
                    "context_source": "thread",
                    **_response_shape_summary(thread_response.payload),
                },
            )
            self._remember_error(error)
            raise error from exc
        return _merge_post_and_thread_context(post_context, thread_context)

    async def send_comment(self, route: RoutingTarget, content: str) -> SendResult:
        body = {
            "is_cy": "0",
            "link_id": route.post_id,
            "reply_id": route.parent_comment_id or "-1",
            "root_id": route.root_comment_id or "-1",
            "text": content,
        }
        response = await self._request(EndpointName.CREATE_COMMENT, form_body=body)
        try:
            return parse_send_result(response.payload)
        except ResponseShapeError as exc:
            error = ResponseContractError(
                str(exc),
                category="response_shape",
                details=_response_shape_summary(response.payload),
            )
            self._remember_error(error)
            raise error from exc

    async def create_post(
        self,
        *,
        title: str,
        content_blocks: list[dict[str, Any]],
        topic_ids: str = "",
        hashtags: list[str] | None = None,
        draft: bool = False,
        edit_link_id: str = "",
    ) -> dict[str, Any]:
        """创建图文/文章帖子；发送结果必须由调用方继续读回核验。"""

        form: dict[str, Any] = {
            "title": str(title).strip(),
            "desc": "",
            "text": json.dumps(content_blocks, ensure_ascii=False),
            "post_type": "1",
            "link_tag": "27" if any(_block_is_image(item) for item in content_blocks) else "21",
            "draft": "1" if draft else "0",
            "view_limit": "1",
            "original": "1",
            "declaration": "1",
            "extra_declaration": "-1",
            "words_count": str(sum(len(str(item.get("text", ""))) for item in content_blocks)),
        }
        if topic_ids:
            form["topic_ids"] = str(topic_ids)
        if hashtags:
            form["hashtags"] = json.dumps(list(hashtags), ensure_ascii=False)
        if str(edit_link_id).strip():
            form["edit"] = "1"
            form["link_id"] = str(edit_link_id).strip()
        response = await self._request(EndpointName.CREATE_POST, form_body=form)
        payload = response.payload
        link_id = _find_link_id(payload)
        return {
            "accepted": True,
            "link_id": link_id,
            "draft": bool(draft),
            "raw": {"keys": sorted(str(key) for key in payload.keys())},
        }

    async def upload_image_bytes(
        self,
        image_bytes: bytes,
        *,
        filename: str = "xiaoheihe.png",
        mime_type: str = "image/png",
    ) -> dict[str, Any]:
        """Upload one image through Xiaoheihe's COS handshake."""
        if not image_bytes:
            raise ResponseContractError("图片内容为空", category="upload_contract")
        safe_filename = str(filename or "xiaoheihe.png").replace("/", "_").replace("\\", "_")
        detected_mime, width, height = _image_info(image_bytes, mime_type)
        info_response = await self._request(
            EndpointName.UPLOAD_INFO,
            form_body={
                "file_infos": json.dumps(
                    [
                        {
                            "name": safe_filename,
                            "mimetype": detected_mime,
                            "fsize": len(image_bytes),
                            "width": width,
                            "height": height,
                        }
                    ],
                    ensure_ascii=False,
                ),
                "scope": "bbs",
                "need_cache": "0",
            },
        )
        info = _result_mapping(info_response.payload)
        keys = info.get("keys")
        bucket = info.get("bucket")
        region = info.get("region") or "ap-shanghai"
        if (
            not isinstance(keys, list)
            or len(keys) != 1
            or not isinstance(keys[0], str)
            or not keys[0]
            or not isinstance(bucket, str)
            or not isinstance(region, str)
            or "-" not in bucket
            or "-" not in region
        ):
            raise ResponseContractError(
                "图片上传信息响应格式已变化",
                category="upload_contract",
                details={"fields": sorted(str(key) for key in info)},
            )
        key = str(keys[0])
        if "?" in key or "#" in key or "\\" in key or ".." in key.split("/"):
            raise ResponseContractError("图片上传对象路径无效", category="upload_contract")
        token_response = await self._request(
            EndpointName.UPLOAD_TOKEN,
            form_body={
                "bucket": bucket,
                "keys": json.dumps([key], ensure_ascii=False),
                "mimetypes": json.dumps([detected_mime], ensure_ascii=False),
                "is_multipart_upload": "0",
            },
        )
        token = _result_mapping(token_response.payload)
        credentials = token.get("credentials")
        if not isinstance(credentials, Mapping) or not all(
            isinstance(credentials.get(name), str) and credentials.get(name)
            for name in ("tmpSecretId", "tmpSecretKey", "sessionToken")
        ):
            raise ResponseContractError(
                "图片上传临时凭据响应格式已变化",
                category="upload_contract",
                details={"fields": sorted(str(key) for key in token)},
            )
        try:
            start = int(token["startTime"])
            end = int(token["expiredTime"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ResponseContractError("图片上传临时凭据有效期无效", category="upload_contract") from exc
        if end <= start or end <= time.time():
            raise ResponseContractError("图片上传临时凭据已经过期", category="upload_contract")
        host = f"{bucket}.cos.{region}.myqcloud.com"
        path = "/" + key.lstrip("/")
        signed_headers = {"host": host, "content-type": detected_mime}
        upload_headers = {
            **signed_headers,
            "Content-Length": str(len(image_bytes)),
            "x-cos-security-token": str(credentials["sessionToken"]),
            "Authorization": _cos_authorization(
                credentials, "PUT", path, signed_headers, start, end
            ),
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=False) as uploader:
                response = await uploader.put(
                    "https://" + host + quote(path, safe="/-_.~"),
                    content=image_bytes,
                    headers=upload_headers,
                )
                if response.status_code < 200 or response.status_code >= 300:
                    raise httpx.HTTPStatusError(f"COS HTTP {response.status_code}", response)
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            raise SendUncertainError(
                "图片上传结果未知，未自动重试",
                category="upload_unknown",
            ) from exc

        callback_response = await self._request(
            EndpointName.UPLOAD_CALLBACK,
            params={"is_finished": "true"},
            form_body={"keys": json.dumps([key], ensure_ascii=False)},
        )
        callback = _result_mapping(callback_response.payload)
        urls = callback.get("preview_urls")
        if not isinstance(urls, list) or len(urls) != 1 or not isinstance(urls[0], str):
            raise ResponseContractError(
                "图片回调响应缺少可发布 URL",
                category="upload_contract",
                details={"fields": sorted(str(key) for key in callback)},
            )
        thumbs = callback.get("thumbs") or []
        image_url = _validate_media_url(urls[0])
        thumb = _validate_media_url(thumbs[0]) if thumbs and isinstance(thumbs[0], str) else ""
        return {"url": image_url, "thumb": thumb, "key": key, "width": width, "height": height}

    async def recent_comments(
        self, route: RoutingTarget, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        response = await self._request(
            EndpointName.RECENT_COMMENTS,
            params={
                "link_id": route.post_id,
                "root_comment_id": route.root_comment_id,
                "limit": max(1, min(limit, 50)),
            },
        )
        body = response.payload.get("result", response.payload.get("data", response.payload))
        if not isinstance(body, Mapping):
            raise ResponseContractError("近期评论响应不是对象", category="response_shape")
        items = body.get("items", body.get("comments", body.get("list", [])))
        if not isinstance(items, list):
            raise ResponseContractError("近期评论列表字段不是数组", category="response_shape")
        return [dict(item) for item in items if isinstance(item, Mapping)]

    async def fetch_feed(self, *, offset: int = 0) -> ApiPage:
        params: dict[str, Any] = {
            "app": "heybox",
            "pull": 0,
            "offset": max(0, offset),
        }
        response = await self._request(EndpointName.FEED, params=params)
        try:
            return parse_feed(response.payload, offset=max(0, offset))
        except ResponseShapeError as exc:
            raise ResponseContractError(str(exc), category="response_shape") from exc

    async def resolve_topic_names(self, names: list[str]) -> dict[str, str]:
        """Resolve configured Chinese topic names to Xiaoheihe topic IDs.

        Only exact, unique names are accepted. Falling back to the first search
        result would make a typo publish into an unintended community.
        """

        resolved: dict[str, str] = {}
        for raw_name in names:
            name = str(raw_name or "").strip()
            if not name or name in resolved:
                continue
            response = await self._request(
                EndpointName.TOPIC_SELECTION_SEARCH,
                params={"q": name, "only_hashtag": "0"},
            )
            result = response.payload.get("result", response.payload.get("data", response.payload))
            items = result.get("search_result", []) if isinstance(result, Mapping) else []
            exact_matches: list[str] = []
            normalized_matches: list[str] = []
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, Mapping):
                    continue
                item_name = str(item.get("name") or item.get("title") or "").strip()
                search_type = str(item.get("search_type") or "").strip().lower()
                topic_id = str(item.get("topic_id") or item.get("id") or "").strip()
                if item_name == name and search_type in {"topic", "community"} and topic_id.isdigit():
                    if topic_id not in exact_matches:
                        exact_matches.append(topic_id)
                elif (
                    _compact_topic_name(item_name) == _compact_topic_name(name)
                    and search_type in {"topic", "community"}
                    and topic_id.isdigit()
                    and topic_id not in normalized_matches
                ):
                    normalized_matches.append(topic_id)
            matches = exact_matches or normalized_matches
            if len(matches) != 1:
                raise ResponseContractError(
                    f"小黑盒社区或话题“{name}”不存在或结果不唯一",
                    category="topic_selection",
                    details={"name": name, "match_count": len(matches)},
                )
            resolved[name] = matches[0]
        return resolved

    async def _request(
        self,
        name: EndpointName,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        form_body: dict[str, Any] | None = None,
    ) -> ApiResponse:
        await self.start()
        if self._client is None:
            raise RuntimeError("HTTP Client 未初始化")
        contract = endpoint(name)
        if contract.authenticated and self.credentials is None:
            raise CredentialInvalidError(
                "账号未登录", status_code=401, category="credential_invalid"
            )
        is_comment_write = name is EndpointName.CREATE_COMMENT
        if is_comment_write:
            # The web comment endpoint uses its own v3 parameter contract and
            # Cookie authentication. Do not inherit the generic heybox_id or
            # device_id URL parameters used by read endpoints.
            request_params = {
                "app": "heybox",
                "os_type": "web",
                "x_app": "heybox_website",
                "x_client_type": "web",
                "x_os_type": "Windows",
                "x_client_version": "",
                "client_type": "web",
                "web_version": "3.0",
                "version": "999.0.4",
            }
        else:
            request_params = dict(params or {})
            for key, value in WEB_CLIENT_PARAMS.items():
                request_params.setdefault(key, value)
            if self.credentials and self.credentials.uid:
                request_params.setdefault("heybox_id", self.credentials.uid)
        signed = self._signer.sign(
            contract.method,
            contract.path,
            params=request_params,
            json_body=json_body or form_body,
            signing_key=self.credentials.signing_key if self.credentials else "",
            device_id=self._device_id,
            include_device_id=not is_comment_write,
        )
        if is_comment_write:
            signed.params["_rnd"] = generate_workshop_rnd(
                int(signed.params["_time"]), signed.params["nonce"]
            )
        headers = dict(signed.headers)
        headers["Referer"] = "https://www.xiaoheihe.cn/"
        if self.credentials and self.credentials.access_token:
            headers["Authorization"] = f"Bearer {self.credentials.access_token}"
        if form_body is not None:
            headers.update(
                {
                    "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
                    "Origin": "https://www.xiaoheihe.cn",
                }
            )
        request_url = (
            contract.path
            if contract.base_url.rstrip("/") == self._base_url
            else f"{contract.base_url.rstrip('/')}{contract.path}"
        )

        retry_count = 0
        while True:
            await self._limiter.wait()
            try:
                response = await self._client.request(
                    contract.method,
                    request_url,
                    params=signed.params,
                    json=json_body,
                    data=form_body,
                    headers=headers,
                    timeout=self._timeout,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if not contract.retry_safe:
                    error = SendUncertainError(
                        "评论请求超时或连接中断，服务端可能已接收",
                        category="send_unknown",
                    )
                    self._remember_error(error)
                    raise error from exc
                if retry_count >= self._max_retries:
                    error = XiaoheiheApiError("网络请求在重试后仍失败", category="network")
                    self._remember_error(error)
                    raise error from exc
                await asyncio.sleep(_backoff(retry_count))
                retry_count += 1
                continue

            if response.status_code in {401, 403}:
                self._count_status(response.status_code)
                error_type = (
                    CredentialInvalidError if response.status_code == 401 else XiaoheiheApiError
                )
                error = error_type(
                    f"小黑盒返回 HTTP {response.status_code}",
                    status_code=response.status_code,
                    category=("credential_invalid" if response.status_code == 401 else "forbidden"),
                    details=_safe_response_payload(response),
                )
                self._remember_error(error)
                if self._on_auth_invalid is not None:
                    await self._on_auth_invalid(self.profile_id, response.status_code)
                raise error

            if response.status_code == 429:
                self._count_status(429)
                retry_after = _retry_after(response.headers.get("Retry-After"))
                if contract.retry_safe and retry_count < self._max_retries:
                    await asyncio.sleep(min(retry_after or _backoff(retry_count), 120))
                    retry_count += 1
                    continue
                error = RateLimitedError(
                    "小黑盒请求频率受限",
                    status_code=429,
                    category="rate_limited",
                    retry_after=retry_after,
                )
                self._remember_error(error)
                raise error

            if 500 <= response.status_code < 600:
                if contract.retry_safe and retry_count < self._max_retries:
                    await asyncio.sleep(_backoff(retry_count))
                    retry_count += 1
                    continue
                error = XiaoheiheApiError(
                    f"小黑盒服务暂时不可用: HTTP {response.status_code}",
                    status_code=response.status_code,
                    category="server",
                    details=_safe_response_payload(response),
                )
                self._remember_error(error)
                raise error

            if response.status_code < 200 or response.status_code >= 300:
                error = XiaoheiheApiError(
                    f"小黑盒请求失败: HTTP {response.status_code}",
                    status_code=response.status_code,
                    category="http",
                    details=_safe_response_payload(response),
                )
                self._remember_error(error)
                raise error

            try:
                payload = response.json()
            except json.JSONDecodeError as exc:
                error = ResponseContractError(
                    "小黑盒响应不是有效 JSON",
                    status_code=response.status_code,
                    category="response_shape",
                )
                self._remember_error(error)
                raise error from exc
            if not isinstance(payload, Mapping):
                error = ResponseContractError(
                    "小黑盒 JSON 顶层不是对象",
                    status_code=response.status_code,
                    category="response_shape",
                )
                self._remember_error(error)
                raise error
            upstream_error = _upstream_error(payload) if contract.authenticated else ""
            if upstream_error:
                credential_invalid = _is_relogin_response(payload)
                error_type = CredentialInvalidError if credential_invalid else XiaoheiheApiError
                error = error_type(
                    upstream_error,
                    status_code=401 if credential_invalid else response.status_code,
                    category=("credential_invalid" if credential_invalid else "upstream_rejected"),
                    details=_response_shape_summary(payload),
                )
                self._remember_error(error)
                if credential_invalid and self._on_auth_invalid is not None:
                    await self._on_auth_invalid(self.profile_id, 401)
                raise error
            self._reset_status_counts()
            self.last_success_at = datetime.now(UTC).isoformat()
            self.last_error = None
            return ApiResponse(
                payload=payload,
                cookies=_cookie_jar_values(self._client, response),
                headers=response.headers,
            )

    def _remember_error(self, error: XiaoheiheApiError) -> None:
        self.last_error = {
            "category": error.category,
            "status_code": error.status_code,
            "message": str(error),
            "details": error.details,
        }

    def _count_status(self, status: int) -> None:
        if status in self.consecutive_status:
            self.consecutive_status[status] += 1
        for other in self.consecutive_status:
            if other != status:
                self.consecutive_status[other] = 0

    def _reset_status_counts(self) -> None:
        for status in self.consecutive_status:
            self.consecutive_status[status] = 0

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        client = self._client
        self._client = None
        if client is not None and self._owns_client:
            await client.aclose()


def _merge_post_and_thread_context(
    post_context: ThreadContext,
    thread_context: ThreadContext,
) -> ThreadContext:
    return ThreadContext(
        post_id=post_context.post_id or thread_context.post_id,
        title=post_context.title or thread_context.title,
        body=post_context.body or thread_context.body,
        author_uid=post_context.author_uid or thread_context.author_uid,
        author_name=post_context.author_name or thread_context.author_name,
        comments=thread_context.comments or post_context.comments,
        image_urls=list(dict.fromkeys(post_context.image_urls + thread_context.image_urls)),
        post_created_at=post_context.post_created_at or thread_context.post_created_at,
    )


def _backoff(attempt: int) -> float:
    return min(30.0, 0.5 * (2**attempt)) + random.uniform(0.0, 0.35)


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(value)
            return max(0.0, parsed.timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return None


def _safe_response_payload(response: httpx.Response) -> Any:
    try:
        payload = response.json()
    except json.JSONDecodeError:
        return {"body": response.text[:500]}
    return redact_data(payload)


def _cookie_jar_values(
    client: httpx.AsyncClient,
    response: httpx.Response,
) -> dict[str, str]:
    """Return the full session cookie jar without logging or exposing it."""

    cookies: dict[str, str] = {}
    for cookie in client.cookies.jar:
        cookies[str(cookie.name)] = str(cookie.value)
    for cookie in response.cookies.jar:
        cookies[str(cookie.name)] = str(cookie.value)
    return cookies


def _result_field_names(payload: Mapping[str, Any]) -> list[str]:
    candidate = payload.get("result", payload.get("data", payload))
    if not isinstance(candidate, Mapping):
        return []
    return sorted(str(key) for key in candidate)[:50]


def _find_link_id(value: Any) -> str:
    if isinstance(value, Mapping):
        for key in ("link_id", "linkid", "id"):
            item = value.get(key)
            if item not in (None, ""):
                return str(item)
        for key in ("result", "data", "link", "post"):
            found = _find_link_id(value.get(key))
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_link_id(item)
            if found:
                return found
    return ""


def _result_mapping(payload: Mapping[str, Any]) -> dict[str, Any]:
    candidate = payload.get("result", payload.get("data", payload))
    return dict(candidate) if isinstance(candidate, Mapping) else {}


def _image_info(data: bytes, requested_mime: str) -> tuple[str, int, int]:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        mime = "image/png"
    elif data.startswith(b"\xff\xd8"):
        width, height = _jpeg_dimensions(data)
        mime = "image/jpeg"
    else:
        raise ResponseContractError(
            f"不支持的绘图输出格式: {requested_mime}",
            category="upload_contract",
        )
    if width <= 0 or height <= 0:
        raise ResponseContractError("图片尺寸无效", category="upload_contract")
    return mime, width, height


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    index = 2
    sof_markers = set(range(0xC0, 0xC4)) | set(range(0xC5, 0xC8)) | set(range(0xC9, 0xCC)) | set(range(0xCD, 0xD0))
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        while index < len(data) and data[index] == 0xFF:
            index += 1
        if index >= len(data):
            break
        marker = data[index]
        index += 1
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(data):
            break
        segment_length = int.from_bytes(data[index : index + 2], "big")
        if segment_length < 2 or index + segment_length > len(data):
            break
        if marker in sof_markers and segment_length >= 7:
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            return width, height
        index += segment_length
    raise ResponseContractError("无法读取 JPEG 图片尺寸", category="upload_contract")


def _cos_authorization(
    credentials: Mapping[str, Any],
    method: str,
    path: str,
    headers: Mapping[str, str],
    start: int,
    end: int,
) -> str:
    key_time = f"{start};{end}"
    canonical = {str(key).lower(): str(value) for key, value in headers.items()}
    names = sorted(canonical)
    header_text = "&".join(
        quote(key, safe="-_.~") + "=" + quote(canonical[key], safe="-_.~") for key in names
    )
    http_string = f"{method.lower()}\n{path}\n\n{header_text}\n"
    sign_key = hmac.new(
        str(credentials["tmpSecretKey"]).encode(), key_time.encode(), hashlib.sha1
    ).hexdigest()
    to_sign = f"sha1\n{key_time}\n{hashlib.sha1(http_string.encode()).hexdigest()}\n"
    signature = hmac.new(sign_key.encode(), to_sign.encode(), hashlib.sha1).hexdigest()
    return (
        f"q-sign-algorithm=sha1&q-ak={credentials['tmpSecretId']}&q-sign-time={key_time}"
        f"&q-key-time={key_time}&q-header-list={';'.join(names)}"
        f"&q-url-param-list=&q-signature={signature}"
    )


def _validate_media_url(value: str) -> str:
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    allowed = ("max-c.com", "xiaoheihe.cn", "myqcloud.com")
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or not any(host == domain or host.endswith("." + domain) for domain in allowed)
        or any(key in parsed.query.casefold() for key in ("token", "secret", "signature", "q-ak", "q-sign"))
    ):
        raise ResponseContractError("平台返回了不受支持的媒体地址", category="upload_contract")
    return value


def _block_is_image(block: Mapping[str, Any]) -> bool:
    kind = str(block.get("type", block.get("kind", ""))).casefold()
    return kind in {"image", "img", "picture", "photo"} or bool(
        block.get("image_url") or block.get("image")
    )


def _find_first_value(value: Any, *keys: str) -> str:
    wanted = {str(key).casefold() for key in keys}
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).casefold() in wanted and item not in (None, ""):
                if isinstance(item, (str, int, float)):
                    return str(item)
        for item in value.values():
            found = _find_first_value(item, *keys)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_first_value(item, *keys)
            if found:
                return found
    return ""


def _upstream_error(payload: Mapping[str, Any]) -> str:
    marker = payload.get("status", payload.get("stat"))
    if marker is None or isinstance(marker, (Mapping, list)):
        return ""
    normalized = str(marker).strip().casefold()
    if not normalized or normalized in {"ok", "success", "200"}:
        return ""
    raw_message = payload.get("msg", payload.get("message", payload.get("error", "")))
    message = redact_data(str(raw_message)) if raw_message else ""
    suffix = f": {str(message)[:300]}" if message else ""
    return f"小黑盒 API 返回非成功状态 {str(marker)[:80]}{suffix}"


def _is_relogin_response(payload: Mapping[str, Any]) -> bool:
    marker = str(payload.get("status", payload.get("stat", ""))).strip().casefold()
    message = str(payload.get("msg", payload.get("message", payload.get("error", ""))))
    hint = f"{marker} {message}".casefold()
    return any(
        token in hint
        for token in (
            "relogin",
            "login required",
            "not login",
            "unauthorized",
            "重新登录",
            "请登录",
            "未登录",
            "登录失效",
        )
    )


def _response_shape_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    candidate = payload.get("result", payload.get("data", payload))
    summary: dict[str, Any] = {
        "top_fields": sorted(str(key) for key in payload)[:50],
        "result_type": type(candidate).__name__,
    }
    marker = payload.get("status", payload.get("stat"))
    if marker is not None and not isinstance(marker, (Mapping, list)):
        summary["status"] = str(marker)[:80]
    if isinstance(candidate, Mapping):
        summary["result_fields"] = sorted(str(key) for key in candidate)[:50]
    elif isinstance(candidate, list):
        summary["result_count"] = len(candidate)
    return summary


def _notification_response_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    summary = _response_shape_summary(payload)
    candidate = payload.get("result", payload.get("data", payload))
    raw_items: Any = []
    if isinstance(candidate, Mapping):
        for key in ("items", "messages", "list"):
            if key in candidate:
                raw_items = candidate[key]
                summary["list_field"] = key
                break
    elif isinstance(candidate, list):
        raw_items = candidate
        summary["list_field"] = "result"
    if isinstance(raw_items, list):
        summary["raw_count"] = len(raw_items)
        field_names: set[str] = set()
        message_types: set[str] = set()
        for item in raw_items[:5]:
            if not isinstance(item, Mapping):
                continue
            field_names.update(str(key) for key in item)
            marker = item.get("message_type", item.get("type"))
            if marker is not None:
                message_types.add(str(marker)[:40])
        summary["item_fields"] = sorted(field_names)[:80]
        summary["message_types"] = sorted(message_types)[:20]
    else:
        summary["list_type"] = type(raw_items).__name__
    summary["checked_at"] = datetime.now(UTC).isoformat()
    return summary
