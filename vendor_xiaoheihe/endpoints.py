"""Central Xiaoheihe HTTP contract.

The paths are isolated here because Xiaoheihe does not publish a stable public
automation API. See docs/xiaoheihe-api-contract.md for verification status.
"""

from dataclasses import dataclass
from enum import StrEnum

API_BASE_URL = "https://api.xiaoheihe.cn"
WORKSHOP_API_BASE_URL = "https://workshopapi.xiaoheihe.cn"


class EndpointName(StrEnum):
    REQUEST_QR = "request_qr"
    QR_STATE = "qr_state"
    CURRENT_USER = "current_user"
    USER_MESSAGES = "user_messages"
    POST_TREE = "post_tree"
    CREATE_COMMENT = "create_comment"
    CREATE_POST = "create_post"
    UPLOAD_INFO = "upload_info"
    UPLOAD_TOKEN = "upload_token"
    UPLOAD_CALLBACK = "upload_callback"
    RECENT_COMMENTS = "recent_comments"
    FEED = "feed"
    TOPIC_SELECTION_SEARCH = "topic_selection_search"
    TOPIC_INDEX = "topic_index"
    TOPIC_FEED = "topic_feed"
    DIRECT_MESSAGE_INBOX = "direct_message_inbox"
    DIRECT_MESSAGE_HISTORY = "direct_message_history"


@dataclass(frozen=True, slots=True)
class Endpoint:
    method: str
    path: str
    authenticated: bool
    retry_safe: bool
    base_url: str = API_BASE_URL


ENDPOINTS: dict[EndpointName, Endpoint] = {
    EndpointName.REQUEST_QR: Endpoint("GET", "/account/get_qrcode_url/", False, True),
    EndpointName.QR_STATE: Endpoint("GET", "/account/qr_state/", False, True),
    EndpointName.CURRENT_USER: Endpoint(
        "GET",
        "/bbs/app/api/user/permission",
        True,
        True,
    ),
    EndpointName.USER_MESSAGES: Endpoint("GET", "/bbs/app/user/message", True, True),
    EndpointName.POST_TREE: Endpoint("GET", "/bbs/app/link/tree", True, True),
    EndpointName.CREATE_COMMENT: Endpoint(
        "POST",
        "/bbs/app/comment/create",
        True,
        False,
        API_BASE_URL,
    ),
    EndpointName.CREATE_POST: Endpoint(
        "POST",
        "/bbs/app/api/link/post",
        True,
        False,
        WORKSHOP_API_BASE_URL,
    ),
    EndpointName.UPLOAD_INFO: Endpoint(
        "POST",
        "/bbs/app/api/qcloud/cos/upload/info/v2",
        True,
        False,
        WORKSHOP_API_BASE_URL,
    ),
    EndpointName.UPLOAD_TOKEN: Endpoint(
        "POST",
        "/bbs/app/api/qcloud/cos/upload/token/v2",
        True,
        False,
        WORKSHOP_API_BASE_URL,
    ),
    EndpointName.UPLOAD_CALLBACK: Endpoint(
        "POST",
        "/bbs/app/api/qcloud/cos/upload/callback/v2",
        True,
        False,
        WORKSHOP_API_BASE_URL,
    ),
    EndpointName.RECENT_COMMENTS: Endpoint("GET", "/bbs/app/comment/user", True, True),
    EndpointName.FEED: Endpoint("GET", "/bbs/app/feeds", True, True),
    EndpointName.TOPIC_SELECTION_SEARCH: Endpoint(
        "GET", "/bbs/app/api/post_editor/topic_selection/search", True, True
    ),
    EndpointName.TOPIC_INDEX: Endpoint("GET", "/bbs/app/api/topic/index/", True, True),
    EndpointName.TOPIC_FEED: Endpoint("GET", "/bbs/app/topic/feeds", True, True),
    # These private-message routes are read-only probes until the live
    # response contract is confirmed for the logged-in account.
    EndpointName.DIRECT_MESSAGE_INBOX: Endpoint(
        "GET", "/chatroom/v2/msg/user", True, True
    ),
    EndpointName.DIRECT_MESSAGE_HISTORY: Endpoint(
        "GET", "/chat/stranger_messages/", True, True
    ),
}


def endpoint(name: EndpointName) -> Endpoint:
    return ENDPOINTS[name]
