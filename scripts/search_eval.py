"""用固定问题评测知识库检索，并追加 Markdown 报告。"""
import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
import logging
from pathlib import Path
from statistics import median
import sys
from time import perf_counter

from knowledge_http import DEFAULT_BASE_URL, KNOWLEDGE_BASES_PATH, KnowledgeClient, KnowledgeRequestError, documents_path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_QUESTIONS = PROJECT_ROOT / "scripts" / "eval_questions.txt"
DEFAULT_REPORT = PROJECT_ROOT / "docs" / "评测" / "检索质量.md"
DEFAULT_THRESHOLD_NOTE = "ratio=0.5 / abs=0"
BLIND_SLOT_PREFIX = "# 第 "
BLIND_QUESTION_PLACEHOLDER = "问题写这里"
BLIND_TITLE_PLACEHOLDER = "期望命中的文档标题"
# 与 app/core/knowledge.py 中的检索约束一致。
MIN_QUESTION_LENGTH, MAX_QUESTION_LENGTH = 2, 200
DEFAULT_TOP_K, MAX_TOP_K, EXPECTED_TOP_K = 5, 20, 3
MILLISECONDS_PER_SECOND = 1000
CHINA_TIME = timezone(timedelta(hours=8))
NOT_APPLICABLE = "—"
TABLE_HEADER = "| 问题 | 候选 | 命中 | 丢弃 | top1 分数 | top1 文档 | top1 区间 | 期望文档在 top3 | 耗时ms | 人工判读 |"
TABLE_DIVIDER = "| --- | ---: | ---: | ---: | ---: | --- | --- | --- | ---: | --- |"
LOGGER = logging.getLogger("search_eval")


@dataclass(frozen=True)
class EvaluationQuestion:
    text: str
    expected_title: str


@dataclass(frozen=True)
class EvaluationResult:
    question: EvaluationQuestion
    candidate_count: int
    hit_count: int
    dropped_count: int
    top_score: float | None
    top_span: str
    top_titles: list[str]
    elapsed_ms: float


# 做什么：定义评测参数并校验必填项与接口上限
# 入参：命令行参数，由 argparse 读取
# 返回：评测配置；输入不符时退出
def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="在同一知识库运行一组检索问题并追加报告")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="后端根地址，默认 %(default)s")
    parser.add_argument("--username", required=True, help="登录用户名")
    parser.add_argument("--password", required=True, help="登录密码")
    parser.add_argument("--base-id", type=int, required=True, help="本人知识库编号")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS, help="UTF-8 问题文件")
    parser.add_argument("--top-k", type=int, choices=range(1, MAX_TOP_K + 1), default=DEFAULT_TOP_K)
    parser.add_argument("--threshold-note", default=DEFAULT_THRESHOLD_NOTE, help="本轮后端阈值说明，写入报告标题")
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORT, help="Markdown 报告路径")
    arguments = parser.parse_args()
    if arguments.base_id < 1:
        parser.error("--base-id 必须是正整数")
    if not arguments.username.strip() or not arguments.password:
        parser.error("--username 和 --password 不能为空")
    arguments.threshold_note = " ".join(arguments.threshold_note.splitlines()).strip()
    if not arguments.threshold_note:
        parser.error("--threshold-note 不能为空")
    return arguments


# 做什么：识别仍需填写的盲测占位行
# 入参：问题文件中的一行，可带模板编号和注释前缀
# 返回：问题或期望标题仍是占位时为真，其他注释和题目为假
def is_blind_placeholder(line: str) -> bool:
    line = line.strip()
    if line.startswith(BLIND_SLOT_PREFIX):
        line = line.partition("：")[2]
    if line.startswith("#"):
        return False
    question, _, expected_title = line.partition("|")
    return question.strip() == BLIND_QUESTION_PLACEHOLDER or expected_title.strip() == BLIND_TITLE_PLACEHOLDER


# 做什么：读取问题与可选的期望标题，并忽略空行和注释
# 入参：UTF-8 问题文件路径
# 返回：按原顺序排列的问题；文件不可读、存在占位或题目无效时抛错
def read_questions(source: Path) -> list[EvaluationQuestion]:
    questions: list[EvaluationQuestion] = []
    lines = source.read_text(encoding="utf-8-sig").splitlines()
    placeholder_count = sum(map(is_blind_placeholder, lines))
    if placeholder_count:
        raise ValueError(f"{source} 有 {placeholder_count} 条盲测占位待填，请由出题人填完再运行")
    for line_number, line in enumerate(lines, start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        question_text, _, expected_title = line.partition("|")
        question_text = question_text.strip()
        if not MIN_QUESTION_LENGTH <= len(question_text) <= MAX_QUESTION_LENGTH:
            raise ValueError(f"{source}:{line_number} 问题需有 {MIN_QUESTION_LENGTH}–{MAX_QUESTION_LENGTH} 字")
        questions.append(EvaluationQuestion(question_text, expected_title.strip()))
    if not questions:
        raise ValueError(f"{source} 没有可评测的问题")
    return questions


# 做什么：分页读取标题，并通过各文档切片页的 total 计算精确总数
# 入参：已登录客户端和库编号
# 返回：文档编号到标题的映射、切片总数；任一请求失败时停止
def read_corpus(client: KnowledgeClient, base_id: int) -> tuple[dict[int, str], int]:
    path = documents_path(base_id)
    document_titles = {document["id"]: document["title"] for document in client.iter_items(path)}
    chunk_count = 0
    for document_id in document_titles:
        chunks = client.get_page(f"{path}/{document_id}/chunks", page=1, page_size=1)
        chunk_count += chunks["total"]
    return document_titles, chunk_count


# 做什么：从检索响应读取候选和丢弃条数，并核对两者与返回条数一致
# 入参：接口结果对象及实际返回的切片条数
# 返回：候选数和丢弃数；旧版响应缺字段或计数无效时抛错
def read_search_counts(response: dict, hit_count: int) -> tuple[int, int]:
    if "candidates" not in response or "dropped" not in response:
        raise KnowledgeRequestError("检索响应缺少 candidates / dropped，请更新并重启后端再评测")
    candidate_count = response["candidates"]
    dropped_count = response["dropped"]
    if type(candidate_count) is not int or type(dropped_count) is not int or dropped_count < 0:
        raise KnowledgeRequestError("检索响应的 candidates / dropped 必须是非负整数")
    if candidate_count != hit_count + dropped_count:
        raise KnowledgeRequestError("检索响应的候选数与返回数、丢弃数不一致")
    return candidate_count, dropped_count


# 做什么：为同一个问题执行一次全文检索并记录返回信息和耗时
# 入参：已登录客户端、检索路径、问题、返回上限及文档标题映射
# 返回：该问题的命中与耗时；接口失败时指出出错的问题
def search_once(client: KnowledgeClient, path: str, question: EvaluationQuestion, top_k: int,
                document_titles: dict[int, str]) -> EvaluationResult:
    started = perf_counter()
    try:
        response = client.request("POST", path, {"query": question.text, "top_k": top_k})
    except KnowledgeRequestError as error:
        raise KnowledgeRequestError(f"问题「{question.text}」检索失败 | {error}") from None
    elapsed_ms = (perf_counter() - started) * MILLISECONDS_PER_SECOND
    hits = response.get("items")
    if not isinstance(hits, list):
        raise KnowledgeRequestError(f"问题「{question.text}」的检索响应缺少 items")
    candidate_count, dropped_count = read_search_counts(response, len(hits))
    if not hits:
        return EvaluationResult(question, candidate_count, 0, dropped_count, None, NOT_APPLICABLE, [], elapsed_ms)
    # 第一个命中就是服务端按相关性排序后的 top1。
    top_hit = hits[0]
    top_titles: list[str] = []
    for hit in hits[:EXPECTED_TOP_K]:
        document_id = hit["document_id"]
        top_titles.append(document_titles.get(document_id, f"文档 #{document_id}"))
    span = f"{top_hit['char_start']}–{top_hit['char_end']}"
    return EvaluationResult(question, candidate_count, len(hits), dropped_count,
                            top_hit["score"], span, top_titles, elapsed_ms)


# 做什么：把单个问题和命中字段转成 Markdown 表格行
# 入参：该问题的搜索记录
# 返回：十列表格行；缺失字段显示破折号，人工判读留待填写
def format_result_row(result: EvaluationResult) -> str:
    score = format(result.top_score, ".3g") if result.top_score is not None else NOT_APPLICABLE
    document_title = result.top_titles[0] if result.top_titles else NOT_APPLICABLE
    expected_title = result.question.expected_title
    expected_match = NOT_APPLICABLE
    if expected_title:
        expected_match = "是" if expected_title in result.top_titles else "否"
    cells = (result.question.text, str(result.candidate_count), str(result.hit_count), str(result.dropped_count),
             score, document_title, result.top_span, expected_match, f"{result.elapsed_ms:.2f}", "待填")
    safe_cells = [escape(" ".join(cell.splitlines()), quote=False).replace("|", "&#124;") for cell in cells]
    return "| " + " | ".join(safe_cells) + " |"


# 做什么：汇总命中、候选与丢弃数量，以及耗时中位数
# 入参：本轮所有问题结果和实际请求的 top_k
# 返回：可同时写入终端和 Markdown 的汇总行
def summarize(results: list[EvaluationResult], top_k: int) -> str:
    matched = sum(result.hit_count > 0 for result in results)
    expected = [result for result in results if result.question.expected_title]
    expected_summary = f"期望题=0 | 期望 top1={NOT_APPLICABLE} | 期望 top3={NOT_APPLICABLE}"
    if expected:
        top_one_flags = (result.question.expected_title in result.top_titles[:1] for result in expected)
        top_three_flags = (result.question.expected_title in result.top_titles for result in expected)
        top_one = sum(top_one_flags)
        top_three = sum(top_three_flags)
        expected_summary = (f"期望题={len(expected)} | 期望 top1={top_one}/{len(expected)}（{top_one / len(expected):.1%}）"
                            f" | 期望 top3={top_three}/{len(expected)}（{top_three / len(expected):.1%}）")
    durations = [result.elapsed_ms for result in results]
    candidate_counts = (result.candidate_count for result in results)
    dropped_counts = (result.dropped_count for result in results)
    return (f"问题数={len(results)} | 有命中={matched}（{matched / len(results):.1%}） | {expected_summary}"
            f" | 候选合计={sum(candidate_counts)} | 丢弃合计={sum(dropped_counts)}"
            f" | 耗时 p50={median(durations):.2f} ms | 请求 top_k={top_k}")


# 做什么：追加一轮评测，不覆盖既有说明和历史数据
# 入参：报告路径、问题结果、文档及切片总数、统计行和阈值说明
# 返回：无；报告路径无法写入时抛出文件异常
def append_report(destination: Path, results: list[EvaluationResult], document_count: int, chunk_count: int,
                  summary: str, threshold_note: str) -> None:
    timestamp = datetime.now(CHINA_TIME).strftime("%Y-%m-%d %H:%M")
    heading = f"## {timestamp}（语料 {document_count} 篇 / 切片 {chunk_count} 块 / 阈值 {threshold_note}）"
    rows = [format_result_row(result) for result in results]
    section = "\n".join(["", "", heading, "", TABLE_HEADER, TABLE_DIVIDER, *rows, "", summary, ""])
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf-8", newline="\n") as report:
        report.write(section)


# 做什么：读取问题、验证语料规模并执行一轮评测
# 入参：命令行，由 argparse 解析
# 返回：已追加完整报告时为 0，输入、请求或写入失败时为 1
def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    arguments = parse_arguments()
    try:
        questions = read_questions(arguments.questions)
        client = KnowledgeClient(arguments.base_url)
        client.login(arguments.username, arguments.password)
        document_titles, chunk_count = read_corpus(client, arguments.base_id)
        path = f"{KNOWLEDGE_BASES_PATH}/{arguments.base_id}/search"
        results = [search_once(client, path, question, arguments.top_k, document_titles) for question in questions]
        summary = summarize(results, arguments.top_k)
        append_report(arguments.out, results, len(document_titles), chunk_count, summary, arguments.threshold_note)
    except UnicodeError:
        LOGGER.error("问题文件无法按 UTF-8 解码 | %s", arguments.questions)
        return 1
    except (OSError, ValueError, KnowledgeRequestError) as error:
        LOGGER.error("评测失败 | %s", error)
        return 1
    LOGGER.info("库编号=%s | 语料 %s 篇 / 切片 %s 块", arguments.base_id, len(document_titles), chunk_count)
    LOGGER.info("评测汇总 | %s", summary)
    LOGGER.info("报告已追加 | %s", arguments.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
