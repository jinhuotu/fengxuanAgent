"""通过真实 HTTP 自检阶段三：建库、上传、切片、检索、原文、删除。运行前确认 8081 连接的是项目 MySQL。"""
import argparse
from datetime import datetime
from getpass import getpass
import logging
import sys

from knowledge_http import DEFAULT_BASE_URL, KnowledgeClient, KnowledgeRequestError, documents_path

PAGE_SIZE = 50
DOCUMENT_NOT_FOUND, KNOWLEDGE_BASE_NOT_FOUND = 4002, 4001
READY_STATUS = 5
PROBE_PHRASE = "紫荆塔探针"
UNRELATED_QUERY = "量子纠缠拓扑绝缘体"
LOGGER = logging.getLogger("acceptance_stage3")

# 验收用原文：五段独立段落，探针短语放在第四段，确保它不是首块，排序与召回都能被验证。
DOCUMENT_PARAGRAPHS = (
    "第一部分：升温曲线设定。窑炉在升温段需要按设定曲线逐步提高炉温，升温速率过快会让炉衬受热不均，"
    "出现裂纹甚至剥落；升温速率过慢则拖长生产周期，燃料单耗随之上升。现场通常把升温段拆成若干区间，"
    "每个区间单独给定目标温度、允许偏差和保温时间，由温控柜按热电偶反馈自动调节加热功率。"
    "保温时间不足时制品内外温度差偏大，进入降温段容易出现开裂；保温时间过长则晶粒粗化，影响成品强度。"
    "降温段同样要限定速率，急冷会让内部应力集中，慢冷又占用炉次。操作员每调一次曲线都要在工艺卡上留痕，"
    "写明调整原因和调整前后的目标温度。",
    "第二部分：燃烧与风量配比。燃气量与助燃风量必须匹配，风量过大会带走热量、拉低炉温，风量过小则燃烧不充分，"
    "烟气中一氧化碳升高，既浪费燃料也污染环境。操作员按经验值设定空燃比后，再依据烟气氧含量读数做小幅修正，"
    "每次修正幅度不超过两个百分点，避免炉温来回震荡。燃烧器负荷提升时要先加风后加气，降低负荷时先减气后减风，"
    "这个顺序能减少冒黑烟和回火。所有调整都要记录调整前后的炉温、氧含量和燃气流量，便于判断配比是否稳定。"
    "长时间低负荷运行时还要检查火焰形状，火焰发黄发长说明配比偏富油，需要适当增加风量。",
    "第三部分：温度采集。炉膛内布置多支热电偶，分别监测升温段、恒温段和降温段，采集模块按固定周期读取温度，"
    "与设定曲线比对后输出偏差值。偏差超过允许范围时触发报警，由操作员确认是测温元件漂移还是实际工况变化，"
    "不能一报警就直接改设定值。热电偶需要定期校准，长期高温会让测量值系统性偏低，校准记录要写入设备台账。"
    "补偿导线极性别接反，接反会让读数朝相反方向偏移。采集周期过短会放大噪声，周期过长又会漏掉瞬时超温，"
    "现场一般取五到十秒，并在报表里保留原始读数，便于事后复核曲线。",
    "第四部分：验收探针。这一段的开头固定写入了本文件独有的短语，即紫荆塔探针，用来确认检索命中的就是这一段，"
    "而不是其他只谈温度参数的段落。如果检索结果的第一条正文里没有这个短语，说明精确命中优先没有生效，"
    "或者切片被切碎到短语已被截断。本段其余内容仍按窑炉工况书写：班组交接时要核对上一班的曲线是否跑完，"
    "报警是否有未闭环项，燃气表读数是否与报表一致。三项都对上之后才签字交接，任何一项对不上都要先查清原因，"
    "避免把异常带进下一班次。探针短语在本文中只出现一次，出现两次以上说明原文被重复拼入。",
    "第五部分：收尾检查。窑炉运行结束后需要记录本次批次的实际温度曲线、能耗与异常处理记录，并按班次归档。"
    "异常处理记录要写明报警时间、处理动作和恢复时间，便于后续复盘；同一类报警连续出现三次以上时，"
    "要单独写一份原因分析，附上当时的曲线截图和操作记录。归档完成后由班长核对记录完整性，再交出下一班次。"
    "以上五段构成本次验收用的原文，总长度足够切成多块，探针短语落在靠后的段落，用来验证检索排序。",
)
DOCUMENT_TITLE = "验收阶段三-切片与检索"


class CheckFailed(Exception):
    """只承载允许进入报告的诊断，不保留凭证与完整响应。"""


# 做什么：统计一条检查的结果，供最后统一打印和退出码判断
# 入参：序号、操作说明、期望、实测与是否通过
# 返回：无；结果追加到模块级列表
RESULTS: list[tuple[str, str, str, str, bool]] = []


def record(step: str, action: str, expected: str, actual: str, passed: bool = True) -> None:
    RESULTS.append((step, action, expected, actual, passed))
    LOGGER.info("%s %s | %s | %s", "通过" if passed else "不通过", step, action, actual)


# 做什么：读取接口失败响应的错误码，供“应当失败”的检查使用
# 入参：客户端、方法、路径、期望业务码与可选请求体
# 返回：失败响应摘要；请求意外成功或错误码不符时抛 CheckFailed
def expect_failure(client: KnowledgeClient, method: str, path: str, code: int, payload: dict | None = None) -> str:
    try:
        client.request(method, path, payload)
    except KnowledgeRequestError as error:
        text = str(error)
        if f"/{code}" in text:
            return text
        raise CheckFailed(f"{method} {path} 期望业务码 {code}，实际：{text}") from None
    raise CheckFailed(f"{method} {path} 期望失败，实际返回成功")


# 做什么：把上传的原文按空行切段，避免段落之间没有分隔导致切片规则变化
# 入参：无
# 返回：用两个换行连接的三段正文
def build_document_text() -> str:
    return "\n\n".join(DOCUMENT_PARAGRAPHS)


# 做什么：逐页读取切片，直到取满总数
# 入参：客户端、知识库编号与文档编号
# 返回：切片条目列表；分页越界时抛 CheckFailed
def read_all_chunks(client: KnowledgeClient, base_id: int, document_id: int) -> list[dict]:
    path = f"{documents_path(base_id)}/{document_id}/chunks"
    page = 1
    chunks: list[dict] = []
    while True:
        result = client.request("GET", f"{path}?page={page}&page_size={PAGE_SIZE}")
        chunks.extend(result["items"])
        if len(chunks) >= result["total"]:
            return chunks
        if not result["items"]:
            raise CheckFailed(f"切片分页在第 {page} 页提前结束")
        page += 1


# 做什么：核对切片正文与原文区间逐字一致，且未覆盖的字符只有空白
# 入参：原文与切片条目
# 返回：无；偏移错位或有正文未覆盖时抛 CheckFailed
def check_chunk_offsets(document_text: str, chunks: list[dict]) -> None:
    for chunk in chunks:
        start, end = chunk["char_start"], chunk["char_end"]
        if not 0 <= start < end <= len(document_text):
            raise CheckFailed(f"切片 {chunk['id']} 偏移越界：{start}-{end}")
        if document_text[start:end] != chunk["content"]:
            raise CheckFailed(f"切片 {chunk['id']} 正文与原文区间不一致")
    if chunks[0]["char_start"] != 0:
        raise CheckFailed(f"首块没有从原文开头开始：{chunks[0]['char_start']}")
    covered = set()
    for chunk in chunks:
        covered.update(range(chunk["char_start"], chunk["char_end"]))
    uncovered = [index for index in range(len(document_text)) if index not in covered]
    if any(not document_text[index].isspace() for index in uncovered):
        raise CheckFailed(f"有 {len(uncovered)} 个字符既不在切片内也不是空白")


# 做什么：按探针短语检索，核对首条正文确实含探针且候选数与丢弃数自洽
# 入参：客户端、知识库编号与检索词
# 返回：检索结果对象；首条不含探针或计数不自洽时抛 CheckFailed
def search_and_check(client: KnowledgeClient, base_id: int, query: str) -> dict:
    result = client.request("POST", f"/knowledge-bases/{base_id}/search", {"query": query, "top_k": 5})
    if result["candidates"] != len(result["items"]) + result["dropped"]:
        raise CheckFailed(f"候选数与保留、丢弃之和不一致：{result['candidates']}")
    return result


# 做什么：收尾时确保临时知识库被删除，避免污染本人知识库列表
# 入参：客户端、库编号与是否保留
# 返回：无；删除失败只记录日志
def cleanup(client: KnowledgeClient, base_id: int | None, keep: bool) -> None:
    if not base_id or keep:
        return
    try:
        client.request("DELETE", f"/knowledge-bases/{base_id}")
    except KnowledgeRequestError as error:
        LOGGER.warning("清理临时知识库失败：%s", error)


# 做什么：按阶段三链路依次核对，任何必过项失败即抛出
# 入参：客户端、库名与是否保留临时库
# 返回：无；各步结果写入 RESULTS
def run_checks(client: KnowledgeClient, base_name: str, keep: bool) -> None:
    health = client.request("GET", "/health")
    record("1", "健康检查", "database=ok", f"database={health.get('database')}", health.get("database") == "ok")

    document_text = build_document_text()
    base = client.request("POST", "/knowledge-bases", {"name": base_name, "description": "阶段三验收临时库"})
    base_id = base["id"]
    record("2", "新建临时知识库", "返回库编号且篇数为 0", f"id={base_id} document_count={base.get('document_count')}",
           base.get("document_count") == 0)

    document = client.request("POST", documents_path(base_id), {"title": DOCUMENT_TITLE, "content": document_text})
    record("3", "上传纯文本文档", f"status={READY_STATUS} 且字数一致",
           f"id={document['id']} status={document['status']} char_count={document['char_count']}",
           document["status"] == READY_STATUS and document["char_count"] == len(document_text))

    bases = client.get_page("/knowledge-bases", 1, PAGE_SIZE)
    matched = [item for item in bases["items"] if item["id"] == base_id]
    record("4", "库列表回读篇数", "document_count=1",
           f"document_count={matched[0]['document_count'] if matched else '未找到'}", bool(matched) and matched[0]["document_count"] == 1)

    chunks = read_all_chunks(client, base_id, document["id"])
    record("5", "切片数量", "至少 2 块（多块才能验证排序与重叠）", f"块数={len(chunks)}", len(chunks) >= 2)

    check_chunk_offsets(document_text, chunks)
    record("6", "切片偏移", "正文与原文区间逐字一致，首块从 0 开始", "全部一致，首块起始 0")

    record("7", "原文覆盖", "未覆盖字符只有空白", "未覆盖字符均为空白或换行")

    first_chunk = chunks[0]
    detail = client.request("GET", f"/knowledge-chunks/{first_chunk['id']}")
    detail_ok = (
        detail["chunk_id"] == first_chunk["id"]
        and detail["document_id"] == document["id"]
        and detail["char_start"] == first_chunk["char_start"]
        and detail["content"] == first_chunk["content"]
        and bool(detail["heading_path"])
    )
    record("8", "引用详情接口", "编号、区间、正文与列表一致，标题非空",
           f"chunk_id={detail['chunk_id']} [{detail['char_start']},{detail['char_end']})", detail_ok)

    probe_result = search_and_check(client, base_id, PROBE_PHRASE)
    top_item = probe_result["items"][0] if probe_result["items"] else None
    probe_indexes = [chunk["chunk_index"] for chunk in chunks if PROBE_PHRASE in chunk["content"]]
    record("9", "探针检索", "至少 1 条且首条正文含探针短语（探针不在首块时才算真正验证排序）",
           f"候选={probe_result['candidates']} 保留={len(probe_result['items'])} 丢弃={probe_result['dropped']} "
           f"首条含探针={bool(top_item) and PROBE_PHRASE in top_item['content']} 探针所在块={probe_indexes}",
           bool(top_item) and PROBE_PHRASE in top_item["content"])

    unrelated = search_and_check(client, base_id, UNRELATED_QUERY)
    record("10", "无关问题检索", "0 条命中", f"候选={unrelated['candidates']} 保留={len(unrelated['items'])}",
           len(unrelated["items"]) == 0)

    deleted_document = client.request("DELETE", f"{documents_path(base_id)}/{document['id']}")
    record("11", "删除文档", f"deleted_chunks={len(chunks)}",
           f"deleted_chunks={deleted_document['deleted_chunks']}", deleted_document["deleted_chunks"] == len(chunks))

    documents_page = client.get_page(documents_path(base_id), 1, PAGE_SIZE)
    bases_after = client.get_page("/knowledge-bases", 1, PAGE_SIZE)
    matched_after = [item for item in bases_after["items"] if item["id"] == base_id]
    zero_hits = search_and_check(client, base_id, PROBE_PHRASE)
    record("12", "删除文档后的回读", "文档列表为 0、篇数为 0、探针检索 0 条",
           f"文档={documents_page['total']} 篇数={matched_after[0]['document_count'] if matched_after else '未找到'} "
           f"检索={len(zero_hits['items'])}",
           documents_page["total"] == 0 and bool(matched_after) and matched_after[0]["document_count"] == 0
           and len(zero_hits["items"]) == 0)

    expect_failure(client, "DELETE", f"{documents_path(base_id)}/{document['id']}", DOCUMENT_NOT_FOUND)
    record("13", "重复删除文档", f"{DOCUMENT_NOT_FOUND} 文档不存在", f"业务码 {DOCUMENT_NOT_FOUND}")

    document = client.request("POST", documents_path(base_id), {"title": DOCUMENT_TITLE, "content": document_text})
    chat_session = client.request("POST", "/chat/sessions", {})
    session_id = chat_session["id"]
    client.request("PUT", f"/chat/sessions/{session_id}/knowledge-base", {"knowledge_base_id": base_id})
    sessions = client.get_page("/chat/sessions", 1, PAGE_SIZE)
    bound = [item for item in sessions["items"] if item["id"] == session_id]
    record("14", "会话选库", f"会话 knowledge_base_id={base_id}",
           f"knowledge_base_id={bound[0]['knowledge_base_id'] if bound else '未找到'}",
           bool(bound) and bound[0]["knowledge_base_id"] == base_id)

    deleted_base = client.request("DELETE", f"/knowledge-bases/{base_id}")
    record("15", "删除知识库", "文档与切片数量与实际一致，至少清理 1 个会话选库",
           f"documents={deleted_base['deleted_documents']} chunks={deleted_base['deleted_chunks']} "
           f"sessions={deleted_base['cleared_sessions']}",
           deleted_base["deleted_documents"] >= 1 and deleted_base["deleted_chunks"] >= 1
           and deleted_base["cleared_sessions"] >= 1)

    sessions_after = client.get_page("/chat/sessions", 1, PAGE_SIZE)
    cleared = [item for item in sessions_after["items"] if item["id"] == session_id]
    expect_failure(client, "POST", f"/knowledge-bases/{base_id}/search", KNOWLEDGE_BASE_NOT_FOUND,
                   {"query": PROBE_PHRASE, "top_k": 5})
    expect_failure(client, "GET", f"/knowledge-bases/{base_id}/documents", KNOWLEDGE_BASE_NOT_FOUND)
    record("16", "删除后的会话与接口", f"会话选库清空，检索与文档列表返回 {KNOWLEDGE_BASE_NOT_FOUND}",
           f"knowledge_base_id={cleared[0]['knowledge_base_id'] if cleared else '未找到'}，两个接口均为 {KNOWLEDGE_BASE_NOT_FOUND}",
           bool(cleared) and cleared[0]["knowledge_base_id"] is None)

    expect_failure(client, "DELETE", f"/knowledge-bases/{base_id}", KNOWLEDGE_BASE_NOT_FOUND)
    record("17", "重复删除知识库", f"{KNOWLEDGE_BASE_NOT_FOUND} 知识库不存在", f"业务码 {KNOWLEDGE_BASE_NOT_FOUND}")


# 做什么：解析命令行参数
# 入参：命令行参数列表
# 返回：解析结果；缺用户名或密码时抛 SystemExit
def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="阶段三个人知识库链路自检")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="后端根地址")
    parser.add_argument("--username", required=True, help="登录账号")
    parser.add_argument("--password", help="登录密码；不填则交互输入")
    parser.add_argument("--keep", action="store_true", help="保留临时知识库，便于接着做页面验收")
    return parser.parse_args(argv)


# 做什么：打印结果表并汇总，决定退出码
# 入参：无
# 返回：退出码；前置失败为 2，任一检查失败为 1
def report() -> int:
    print("\n| 序号 | 操作 | 期望 | 实测 | 结论 |")
    print("|---|---|---|---|---|")
    for step, action, expected, actual, passed in RESULTS:
        print(f"| {step} | {action} | {expected} | {actual} | {'通过' if passed else '不通过'} |")
    failed = [row for row in RESULTS if not row[4]]
    print(f"\n通过 {len(RESULTS) - len(failed)} 项，不通过 {len(failed)} 项。")
    return 1 if failed else 0


# 做什么：脚本入口，负责登录、执行检查与清理
# 入参：命令行参数列表
# 返回：退出码
def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args(sys.argv[1:] if argv is None else argv)
    password = args.password or getpass("登录密码：")
    client = KnowledgeClient(args.base_url)

    try:
        client.login(args.username, password)
    except KnowledgeRequestError as error:
        print(f"登录失败，停止自检：{error}")
        return 2
    health = client.request("GET", "/health")
    if health.get("database") != "ok":
        print(f"数据库不可用，停止自检：database={health.get('database')}")
        return 2

    base_name = f"验收阶段三-{datetime.now():%m%d%H%M%S}"
    base_id: int | None = None
    try:
        run_checks(client, base_name, args.keep)
    except CheckFailed as error:
        record("X", "执行中断", "能继续执行", str(error), passed=False)
    finally:
        bases = client.get_page("/knowledge-bases", 1, PAGE_SIZE)
        matching = [item for item in bases["items"] if item["name"] == base_name]
        base_id = matching[0]["id"] if matching else None
        cleanup(client, base_id, args.keep)
        if args.keep and base_id:
            print(f"\n已保留临时知识库：{base_name}（id={base_id}），页面验收后可手动删除。")
    return report()


if __name__ == "__main__":
    sys.exit(main())
