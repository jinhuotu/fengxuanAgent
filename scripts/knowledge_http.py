"""导入和评测脚本共用的标准库知识库客户端。"""
import json
from collections.abc import Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

DEFAULT_BASE_URL = "http://127.0.0.1:8081"
KNOWLEDGE_BASES_PATH = "/knowledge-bases"
LOGIN_PATH = "/auth/login"
PAGE_SIZE = 50
TIMEOUT_SECONDS = 15
SUCCESS_CODE = 0


class KnowledgeRequestError(Exception):
    """可展示的接口失败原因，不携带凭证或完整响应。"""


class KnowledgeClient:
    # 做什么：保存本次脚本使用的接口地址和认证状态
    # 入参：后端根地址
    # 返回：无；凭证在登录成功后写入内存
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = ""
        self.opener = build_opener(ProxyHandler({}))

    # 做什么：从登录响应取出后续请求使用的凭证
    # 入参：用户名和密码
    # 返回：无；登录失败或缺少凭证时抛 KnowledgeRequestError
    def login(self, username: str, password: str) -> None:
        result = self.request("POST", LOGIN_PATH, {"username": username, "password": password})
        token = result.get("access_token")
        if not isinstance(token, str) or not token or not token.isascii() or any(map(str.isspace, token)):
            raise KnowledgeRequestError("登录响应缺少可用的 access_token")
        self.token = token

    # 做什么：发送 JSON 请求，检查状态码并取出统一响应的结果
    # 入参：HTTP 方法、接口路径和可选的 JSON 对象
    # 返回：结果对象；连接失败、错误码或响应形状错误时抛 KnowledgeRequestError
    def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        label = f"{method} {path}"
        try:
            body = json.dumps(payload).encode("utf-8") if payload is not None else None
            headers = {"Accept": "application/json"}
            if body is not None:
                headers["Content-Type"] = "application/json"
            request = Request(self.base_url + path, data=body, headers=headers, method=method)
            # 凭证只发给指定地址，跳转请求不会带上这个头。
            if self.token:
                request.add_unredirected_header("Authorization", f"Bearer {self.token}")
            with self.opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                envelope = json.load(response)
        except HTTPError as error:
            with error:
                reason = _http_error_reason(error)
            raise KnowledgeRequestError(f"{label} {reason}") from None
        except (URLError, OSError, ValueError) as error:
            raise KnowledgeRequestError(f"{label} 请求失败或响应无法读取（{type(error).__name__}）") from None
        if not isinstance(envelope, dict) or envelope.get("code") != SUCCESS_CODE:
            raise KnowledgeRequestError(f"{label} 未返回成功业务码")
        result = envelope.get("data")
        if not isinstance(result, dict):
            raise KnowledgeRequestError(f"{label} 缺少结果对象")
        return result

    # 做什么：逐页读取知识库或文档列表
    # 入参：分页接口路径
    # 返回：按服务端顺序产生条目；无下一页时结束，响应格式异常时抛错
    def iter_items(self, path: str) -> Iterator[dict]:
        page = 1
        while True:
            result = self.get_page(path, page, PAGE_SIZE)
            items = result.get("items")
            page_size = result.get("page_size")
            if not isinstance(items, list) or result.get("page") != page or type(page_size) is not int or page_size < 1:
                raise KnowledgeRequestError(f"GET {path} 缺少第 {page} 页列表")
            yield from items
            if not items or page * page_size >= result["total"]:
                return
            page += 1

    # 做什么：读取列表的指定页，保留总数供脚本使用
    # 入参：分页接口路径、页码和请求条数
    # 返回：带 total 的分页对象；接口失败或总数无效时抛错
    def get_page(self, path: str, page: int, page_size: int) -> dict:
        query = urlencode({"page": page, "page_size": page_size})
        result = self.request("GET", f"{path}?{query}")
        if type(result.get("total")) is not int or result["total"] < 0:
            raise KnowledgeRequestError(f"GET {path} 缺少有效的分页总数")
        return result


# 做什么：从失败响应提取可供排查的接口状态
# 入参：urllib 抛出的 HTTP 错误
# 返回：HTTP 状态、可用的业务码与接口消息；解析不了时只返回 HTTP 状态
def _http_error_reason(error: HTTPError) -> str:
    http_status = f"HTTP {error.code}"
    try:
        envelope = json.load(error)
    except (ValueError, OSError):
        return http_status
    if not isinstance(envelope, dict) or type(envelope.get("code")) is not int:
        return http_status
    message = envelope.get("message")
    reason = f"{http_status}/{envelope['code']}"
    return f"{reason} {message}" if isinstance(message, str) and message else reason


# 做什么：拼出指定知识库的文档资源路径
# 入参：知识库编号
# 返回：供列表、上传及切片分页共用的资源路径
def documents_path(knowledge_base_id: int) -> str:
    return f"{KNOWLEDGE_BASES_PATH}/{knowledge_base_id}/documents"
