"""通过真实 HTTP 自检阶段二。运行前确认 8081 连接的是项目 MySQL。"""
import argparse
from collections.abc import Iterator
from contextlib import contextmanager
from getpass import GetPassWarning, getpass
import logging
import sys
import warnings

import httpx

BASE_URL = "http://127.0.0.1:8081"
TIMEOUT_SECONDS = 10
PAGE_SIZE = 100
TEST_USERS = ("accept_b", "accept_c")
ACTIVE, DISABLED = "active", "disabled"
ADMIN_ROLE, USER_ROLE = "admin", "user"
ASSIGN_PERMISSION = "users:assign_role"
PERMISSIONS = {ASSIGN_PERMISSION, "users:create", "users:disable", "users:list"}
ROUTES = {"/auth/logout": "post", "/roles": "get", "/users/{user_id}/status": "patch",
          "/users/{user_id}/roles/{role_code}": "delete"}
OK, FORBIDDEN, SELF_DENIED, INVALID_STATUS = (200, 0), (403, 2001), (403, 2003), (422, 1000)
TOKEN_INVALID, ACCOUNT_DISABLED = (401, 1002), (403, 1003)
UI_CHECKS = ("普通账号无用户管理菜单，直访 /users 被拦", "管理员本人行的停用和撤销按钮置灰",
             "重复追加角色、撤销角色时的确认弹窗文案正确")
STEPS = (
    ("A 登录并核对身份和权限", "is_admin=true、active、含四个权限码"),
    ("准备 accept_b、accept_c", "复用或新建，新建 is_admin=false、roles=[]"),
    ("B 登录、查用户及给 A 追加角色", "登录 200/0，其余两次 403/2001"),
    ("A 给 C 追加 admin", "200/0，created=true 或已绑定 false"),
    ("C 重新登录、查用户并给 B 追加 user", "200/0，created=true"),
    ("C 重复给 B 追加 user", "200/0，created=false"),
    ("C 连续两次撤销 B 的 user", "200/0，removed=true 后 false"),
    ("A 停用 B，检查旧令牌和重新登录", "停用 200/0，旧令牌 401/1002，登录 403/1003"),
    ("A 启用 B，B 重新登录", "启用和登录均 200/0"),
    ("A 尝试停用自己", "403/2003"),
    ("A 给 B 提交非法状态 pending", "422/1000"),
    ("A 撤销 C 的 admin", "200/0，removed=true"),
    ("停用 B、C 并查询最终状态", "两人均为 disabled，输出各自 ID"),
)
LOGGER = logging.getLogger("acceptance_stage2")


class CheckFailed(Exception):
    """只承载允许进入报告的诊断，不保留完整响应。"""

# 做什么：读取密码，终端无法隐藏输入时立即停止
# 入参：给用户展示的密码提示
# 返回：本次运行的密码；无交互终端、中断或空密码抛检查异常
def read_password(prompt: str) -> str:
    if not sys.stdin.isatty():
        raise CheckFailed("需要真实终端通过 getpass 输入密码")
    with warnings.catch_warnings():
        warnings.simplefilter("error", GetPassWarning)
        try:
            password = getpass(prompt)
        except (GetPassWarning, EOFError, KeyboardInterrupt):
            raise CheckFailed("密码输入未完成或终端无法隐藏输入") from None
    if not password:
        raise CheckFailed("密码不能为空")
    return password

# 做什么：发请求、解析对象响应并检查状态与业务码
# 入参：HTTP 客户端、方法、路径、可选凭证、请求数据与期望码
# 返回：JSON 对象；失败只报告路径和状态，不输出请求或响应正文
def request(client: httpx.Client, method: str, path: str, token: str = "", *,
            expected: tuple[int, int] | None = OK, payload: dict | None = None,
            params: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else None
    try:
        response = client.request(method, path, headers=headers, json=payload, params=params)
    except httpx.RequestError:
        raise CheckFailed(f"{method} {path} 请求失败，请检查本地服务") from None
    try:
        result = response.json()
    except ValueError:
        raise CheckFailed(f"{method} {path} HTTP {response.status_code}，响应不是 JSON") from None
    if not isinstance(result, dict):
        raise CheckFailed(f"{method} {path} HTTP {response.status_code}，响应不是 JSON 对象")
    code = result.get("code")
    business_code = code if type(code) is int else "缺失或无效"
    if expected is None and response.status_code == OK[0]:
        return result
    if (response.status_code, business_code) != expected:
        raise CheckFailed(f"{method} {path} 返回 {response.status_code}/{business_code}")
    body = result.get("data")
    if not isinstance(body, dict) and expected == OK:
        raise CheckFailed(f"{method} {path} 返回 200/0，但缺少结果对象")
    return body if isinstance(body, dict) else {}

# 做什么：调用真实登录接口并取出令牌
# 入参：HTTP 客户端、用户名和交互输入的密码
# 返回：可用于请求的令牌；登录或响应不符时抛检查异常
def login(client: httpx.Client, username: str, password: str) -> str:
    result = request(client, "POST", "/auth/login", payload={"username": username, "password": password})
    token = result.get("access_token")
    if not isinstance(token, str) or not token or not token.isascii() or any(map(str.isspace, token)):
        raise CheckFailed("POST /auth/login 返回 200/0，但没有有效令牌")
    return token

# 做什么：在真实用户列表中逐页查找指定账号
# 入参：HTTP 客户端、A 的令牌和所需用户名集合
# 返回：按用户名索引的条目；列表不完整或分页字段出错时停止
def find_users(client: httpx.Client, token: str, names: set[str]) -> dict[str, dict]:
    page = 1
    matched = {}
    while True:
        result = request(client, "GET", "/users", token, params={"page": page, "page_size": PAGE_SIZE})
        items, total = result.get("items"), result.get("total")
        if type(total) is not int or total < 0 or not isinstance(items, list):
            raise CheckFailed("GET /users 缺少有效的 total 或 items")
        remaining = max(0, total - (page - 1) * PAGE_SIZE)
        if result.get("page") != page or result.get("page_size") != PAGE_SIZE or len(items) != min(PAGE_SIZE, remaining):
            raise CheckFailed("GET /users 分页结果不完整，未判定任何账号缺失")
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("username"), str):
                raise CheckFailed("GET /users 返回无效用户条目")
            if item["username"] in names:
                matched[item["username"]] = item
        if matched.keys() == names or remaining <= PAGE_SIZE:
            return matched
        page += 1

# 做什么：从身份或列表条目提取有效用户 ID
# 入参：单个接口返回的用户条目
# 返回：正整数 ID；缺失或无效时抛检查异常
def user_id(user: dict) -> int:
    identifier = user.get("id")
    if type(identifier) is not int or identifier <= 0:
        raise CheckFailed("用户响应缺少有效 ID")
    return identifier

# 做什么：检查角色变更的布尔结果
# 入参：HTTP 客户端、方法、路径、令牌、结果字段与期望值，以及可选请求体
# 返回：真实布尔值；类型或期望不符时抛检查异常
def role_change(client: httpx.Client, method: str, path: str, token: str, field: str,
                expected: bool | None, payload: dict | None = None) -> bool:
    value = request(client, method, path, token, payload=payload).get(field)
    if type(value) is not bool:
        raise CheckFailed(f"{method} {path} 返回 200/0，但 {field} 不是布尔值")
    if expected is not None and value is not expected:
        raise CheckFailed(f"{method} {path} 返回 200/0，{field}={str(value).lower()}")
    return value

# 做什么：切换指定用户状态并检查响应
# 入参：HTTP 客户端、令牌、目标 ID、状态，以及是否必须实际变更
# 返回：无；状态、变更标志或响应码不符时抛检查异常
def set_status(client: httpx.Client, token: str, identifier: int, status: str, *, changed: bool | None) -> None:
    result = request(client, "PATCH", f"/users/{identifier}/status", token, payload={"status": status})
    change_flag = result.get("changed")
    if type(change_flag) is not bool or result.get("status") != status:
        raise CheckFailed(f"用户 {identifier} 状态响应与 {status} 不符")
    if changed is not None and change_flag is not changed:
        raise CheckFailed(f"用户 {identifier} 状态切换返回 changed={str(change_flag).lower()}")

# 做什么：输出一行阶段结果，失败时保留已取得的证据并停止依赖步骤
# 入参：编号和结果字典，前置步骤另传操作与期望
# 返回：供步骤追加安全证据的列表；失败原样抛回调用方
@contextmanager
def row(number: int | str, results: dict[str, bool], operation: str = "", expected: str = "") -> Iterator[list[str]]:
    if isinstance(number, int):
        operation, expected = STEPS[number - 1]
    key = str(number)
    evidence: list[str] = []
    results[key] = False
    try:
        yield evidence
        results[key] = True
    except CheckFailed as error:
        evidence.append(str(error))
        raise
    except KeyboardInterrupt:
        evidence.append("用户中断")
        raise
    finally:
        conclusion = "通过" if results[key] else "不通过"
        LOGGER.info("%s | %s | %s | %s | %s", number, operation, expected, "；".join(evidence), conclusion)

# 做什么：依次验证数据库、路由和 A 的角色分配权限
# 入参：HTTP 客户端、A 的用户名和结果字典
# 返回：A 的令牌、本人信息及权限集合；任何前置失败即抛异常
def preflight(client: httpx.Client, username: str, results: dict) -> tuple[str, dict, set[str]]:
    with row("P1", results, "GET /health", "database=ok") as evidence:
        if request(client, "GET", "/health").get("database") != "ok":
            raise CheckFailed("数据库状态不是 ok")
        evidence.append("200/0，database=ok")
    with row("P2", results, "GET /openapi.json", "四条路由和方法均存在") as evidence:
        paths = request(client, "GET", "/openapi.json", expected=None).get("paths")
        if not isinstance(paths, dict):
            raise CheckFailed("OpenAPI 缺少 paths")
        missing = []
        for path, method in ROUTES.items():
            methods = paths.get(path)
            if not isinstance(methods, dict) or method not in methods:
                missing.append(f"{method.upper()} {path}")
        if missing:
            raise CheckFailed("缺少 " + "、".join(missing))
        evidence.append("四条路由和方法均存在")
    with row("P3", results, "A 登录并查权限", ASSIGN_PERMISSION) as evidence:
        token = login(client, username, read_password("A 的密码："))
        identity = request(client, "GET", "/auth/me", token)
        permissions = request(client, "GET", "/auth/me/permissions", token).get("permissions")
        if not isinstance(permissions, list) or ASSIGN_PERMISSION not in permissions:
            raise CheckFailed("先执行 uv run python scripts/seed_rbac.py，并让账号重新登录")
        evidence.append(f"200/0，含 {ASSIGN_PERMISSION}")
    return token, identity, {permission for permission in permissions if isinstance(permission, str)}

# 做什么：复用或创建 B、C，必要时启用并核对来源和属性
# 入参：HTTP 客户端、A 令牌、已验证的 A 身份、待填的账号 ID 和步骤证据
# 返回：两份交互输入的账号密码；任何账号不符合预期即抛异常
def prepare_users(client: httpx.Client, token: str, identity: dict, ids: dict, evidence: list[str]) -> dict[str, str]:
    names = set(TEST_USERS)
    users = find_users(client, token, names | {identity["username"]})
    operator = users.get(identity["username"], {})
    roles = operator.get("roles")
    if user_id(operator) != user_id(identity) or not isinstance(roles, list) or not any(
        isinstance(role, dict) and role.get("code") == ADMIN_ROLE for role in roles
    ):
        raise CheckFailed("A 的列表身份或 admin 角色未确认，停止写入")
    passwords = {}
    for name in TEST_USERS:
        account_kind = "已有账号" if name in users else "新账号"
        passwords[name] = read_password(f"{name} 密码（{account_kind}）：")
    for name in TEST_USERS:
        if name not in users:
            created = request(client, "POST", "/users", token, payload={"username": name, "password": passwords[name]})
            users.update(find_users(client, token, {name}))
            ids[name] = user_id(users.get(name, {}))
            if created.get("is_admin") is not False or users[name].get("roles") != []:
                raise CheckFailed(f"{name} 新建后 is_admin=false 或 roles=[] 未满足")
            evidence.append(f"{name} ID={ids[name]} 新建，is_admin=false、roles=[]")
        else:
            ids[name] = user_id(users[name])
            if users[name].get("status") == DISABLED:
                set_status(client, token, ids[name], ACTIVE, changed=True)
            evidence.append(f"{name} ID={ids[name]} 复用")
    return passwords

# 做什么：验证普通权限、委派追加与撤销
# 入参：客户端、A 令牌、账号 ID、交互密码和步骤结果
# 返回：B 停用前的令牌；验收失败时停止后续检查
def verify_roles(client: httpx.Client, token: str, ids: dict, passwords: dict, results: dict) -> str:
    b_name, c_name = TEST_USERS
    b_roles, c_roles = f"/users/{ids[b_name]}/roles", f"/users/{ids[c_name]}/roles"
    with row(3, results) as evidence:
        b_token = login(client, b_name, passwords[b_name])
        evidence.append("B 登录 200/0")
        request(client, "GET", "/users", b_token, expected=FORBIDDEN)
        evidence.append("GET /users 403/2001")
        # A 已持有 admin，即使权限检查回归，追加也不会新增 A 的角色。
        request(client, "POST", f"/users/{ids['a']}/roles", b_token, expected=FORBIDDEN,
                payload={"role_code": ADMIN_ROLE})
        evidence.append("给 A 追加 admin 403/2001")
    with row(4, results) as evidence:
        created = role_change(client, "POST", c_roles, token, "created", None, {"role_code": ADMIN_ROLE})
        evidence.append(f"200/0，created={str(created).lower()}")
    with row(5, results) as evidence:
        c_token = login(client, c_name, passwords[c_name])
        request(client, "GET", "/users", c_token)
        evidence.append("C 登录、GET /users 均为 200/0")
        role_change(client, "POST", b_roles, c_token, "created", True, {"role_code": USER_ROLE})
        evidence.append("200/0，created=true")
    with row(6, results) as evidence:
        role_change(client, "POST", b_roles, c_token, "created", False, {"role_code": USER_ROLE})
        evidence.append("200/0，created=false")
    with row(7, results) as evidence:
        for expected in (True, False):
            role_change(client, "DELETE", f"{b_roles}/{USER_ROLE}", c_token, "removed", expected)
            evidence.append(f"200/0，removed={str(expected).lower()}")
    return b_token

# 做什么：验证停用使旧凭证失效、启用后能登录以及状态错误码
# 入参：HTTP 客户端、A 和 B 的令牌、账号 ID、B 的密码及结果
# 返回：无；任何状态或错误码不符时停止后续检查
def verify_status(client: httpx.Client, token: str, b_token: str, ids: dict, password: str, results: dict) -> None:
    b_name = TEST_USERS[0]
    b_id = ids[b_name]
    with row(8, results) as evidence:
        set_status(client, token, b_id, DISABLED, changed=True)
        evidence.append("停用 200/0")
        request(client, "GET", "/auth/me", b_token, expected=TOKEN_INVALID)
        evidence.append("旧令牌 401/1002")
        request(client, "POST", "/auth/login", expected=ACCOUNT_DISABLED,
                payload={"username": b_name, "password": password})
        evidence.append("重新登录 403/1003")
    with row(9, results) as evidence:
        set_status(client, token, b_id, ACTIVE, changed=True)
        evidence.append("启用 200/0")
        login(client, b_name, password)
        evidence.append("重新登录 200/0")
    with row(10, results) as evidence:
        request(client, "PATCH", f"/users/{ids['a']}/status", token, expected=SELF_DENIED, payload={"status": DISABLED})
        evidence.append("403/2003")
    with row(11, results) as evidence:
        request(client, "PATCH", f"/users/{b_id}/status", token, expected=INVALID_STATUS, payload={"status": "pending"})
        evidence.append("422/1000")

# 做什么：尽量停用已确认 ID 的测试账号，并回查真实最终状态
# 入参：HTTP 客户端、A 的令牌、已取得的 ID 和步骤结果
# 返回：无；任一账号信息缺失、停用失败或最终状态异常则记录失败
def cleanup(client: httpx.Client, token: str, ids: dict, results: dict) -> None:
    try:
        with row(13, results) as evidence:
            known_names = set(TEST_USERS) & ids.keys()
            all_disabled = len(known_names) == len(TEST_USERS)
            for name in sorted(known_names):
                try:
                    set_status(client, token, ids[name], DISABLED, changed=None)
                except CheckFailed as error:
                    evidence.append(str(error))
                    all_disabled = False
            users = find_users(client, token, known_names) if known_names else {}
            for name in sorted(known_names):
                entry = users.get(name, {})
                status = entry.get("status") if entry.get("status") in (ACTIVE, DISABLED) else "未确认"
                evidence.append(f"{name} ID={ids[name]} status={status}")
                all_disabled &= entry.get("id") == ids[name] and status == DISABLED
            if not all_disabled:
                raise CheckFailed("未确认两名测试账号均已停用")
    except CheckFailed:
        return  # row 已报告，汇总保留非通过结论。

# 做什么：执行依赖顺序，失败后只做允许的收尾
# 入参：HTTP 客户端、A 的令牌与身份、许可集合和结果字典
# 返回：无；失败和未执行步骤留给汇总处理
def run_checks(client: httpx.Client, token: str, identity: dict, permissions: set[str], results: dict) -> None:
    ids = {}
    try:
        with row(1, results) as evidence:
            ids["a"] = user_id(identity)
            valid_identity = identity.get("is_admin") is True and identity.get("status") == ACTIVE
            operator_name = identity.get("username")
            if not valid_identity or not isinstance(operator_name, str) or operator_name.casefold() in TEST_USERS:
                raise CheckFailed(f"A ID={ids['a']} 未确认是有效的启用管理员")
            evidence.append(f"A ID={ids['a']}，is_admin=true、status=active")
            evidence.append("已确认权限 " + "、".join(sorted(PERMISSIONS & permissions)))
            if not PERMISSIONS <= permissions:
                raise CheckFailed("未取得全部四个权限码")
        with row(2, results) as evidence:
            passwords = prepare_users(client, token, identity, ids, evidence)
        b_token = verify_roles(client, token, ids, passwords, results)
        verify_status(client, token, b_token, ids, passwords[TEST_USERS[0]], results)
        with row(12, results) as evidence:
            route = f"/users/{ids[TEST_USERS[1]]}/roles/{ADMIN_ROLE}"
            role_change(client, "DELETE", route, token, "removed", True)
            evidence.append("200/0，removed=true")
    except (CheckFailed, KeyboardInterrupt):
        for number, (operation, expected) in enumerate(STEPS[:-1], 1):
            if str(number) not in results:
                LOGGER.info("%s | %s | %s | 前序步骤未通过，未执行 | 不通过", number, operation, expected)
    finally:
        cleanup(client, token, ids, results)

# 做什么：列出人工待办、自动化覆盖和汇总
# 入参：按步骤编号记录的结果
# 返回：所有必过步骤通过为 0，否则为 1
def summarize(results: dict) -> int:
    passed = sum(results.get(str(number), False) for number in range(1, len(STEPS) + 1))
    for operation in UI_CHECKS:
        LOGGER.info("人工 | %s | 页面点击核对 | 未手验（需人工在页面确认） | 未手验（需人工在页面确认）", operation)
    LOGGER.info("自动化 | 最后管理员保护 | 409/2004 | 由自动化用例覆盖 | 由自动化用例覆盖")
    failed = len(STEPS) - passed
    LOGGER.info("14 | 汇总 | 必过项全部通过 | 通过 %s / 不通过 %s / 未手验 %s | %s",
                passed, failed, len(UI_CHECKS), "不通过" if failed else "通过")
    return int(failed > 0)

# 做什么：读取启动参数，执行前置和真实 HTTP 自检
# 入参：可选 --username，密码只从本机终端读取
# 返回：全部通过 0，必过项失败 1，前置不满足 2
def main() -> int:
    parser = argparse.ArgumentParser(description="阶段二真实 HTTP 自检")
    parser.add_argument("--username", default="admin", help="账号 A 的用户名，默认 admin")
    username = parser.parse_args().username
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if username.casefold() in TEST_USERS:
        LOGGER.error("前置停止：A 不能使用预留测试用户名")
        return 2
    results: dict[str, bool] = {}
    LOGGER.info("序号 | 操作 | 期望 | 实际 | 结论")
    with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT_SECONDS, trust_env=False) as client:
        try:
            token, identity, permissions = preflight(client, username, results)
        except (CheckFailed, KeyboardInterrupt):
            LOGGER.info("前置自检未通过，退出码 2")
            return 2
        run_checks(client, token, identity, permissions, results)
    return summarize(results)


if __name__ == "__main__":
    raise SystemExit(main())
