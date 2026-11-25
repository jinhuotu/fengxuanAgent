"""将本地纯文本目录导入本人知识库。"""
import argparse
from collections import Counter
import logging
from pathlib import Path
import sys

from knowledge_http import (
    DEFAULT_BASE_URL, KNOWLEDGE_BASES_PATH, KnowledgeClient, KnowledgeRequestError, documents_path,
)

SUPPORTED_EXTENSIONS = {".txt", ".md"}
DEFAULT_KNOWLEDGE_BASE_NAME = "语料库"
# 与 app/core/knowledge.py 中的上传约束一致。
MAX_CONTENT_LENGTH = 50000
MAX_TITLE_LENGTH = 100
ADDED, EXISTING, SKIPPED, FAILED, PLANNED = "新增", "已存在", "跳过", "失败", "待上传"
LOGGER = logging.getLogger("corpus_import")


class UnsupportedCorpusError(Exception):
    """目录中存在未获支持的文件，停止整批导入。"""


class SkippedDocument(Exception):
    """正文为空或超过接口上限。"""


# 做什么：定义导入参数并在访问文件和接口前检查必填项
# 入参：命令行参数列表，由 argparse 读取
# 返回：导入配置；缺参数或库编号无效时退出
def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量导入目录里的 UTF-8 纯文本")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="后端根地址，默认 %(default)s")
    parser.add_argument("--username", required=True, help="登录用户名")
    parser.add_argument("--password", required=True, help="登录密码")
    parser.add_argument("--dir", type=Path, required=True, help="语料目录，只扫描这一层")
    parser.add_argument("--kb-name", default=DEFAULT_KNOWLEDGE_BASE_NAME, help="按名称查找或创建，默认 %(default)s")
    parser.add_argument("--base-id", type=int, help="直接使用指定的现有知识库")
    parser.add_argument("--dry-run", action="store_true", help="仅核对本地文件和上传清单")
    arguments = parser.parse_args()
    if arguments.base_id is not None and arguments.base_id < 1:
        parser.error("--base-id 必须是正整数")
    if not arguments.username.strip() or not arguments.password:
        parser.error("--username 和 --password 不能为空")
    return arguments


# 做什么：按文件名排序，并提前拒绝目录内所有不支持的格式
# 入参：待扫描目录路径
# 返回：可处理文件清单；目录无效或存在不支持文件时抛错
def find_sources(directory: Path) -> list[Path]:
    files = [entry for entry in sorted(directory.iterdir()) if entry.is_file()]
    unsupported = [file.name for file in files if file.suffix.lower() not in SUPPORTED_EXTENSIONS]
    if unsupported:
        raise UnsupportedCorpusError("不支持以下文件格式，整批停止：" + "、".join(unsupported))
    return files


# 做什么：按编号复用库，或逐页查找同名库后按需创建
# 入参：已登录客户端、可选库编号和目标名称
# 返回：现有或新建的库编号；接口失败时抛错
def resolve_base_id(client: KnowledgeClient, base_id: int | None, name: str) -> int:
    if base_id is not None:
        return base_id
    name = name.strip()
    for knowledge_base in client.iter_items(KNOWLEDGE_BASES_PATH):
        if knowledge_base["name"] == name:
            return knowledge_base["id"]
    created = client.request("POST", KNOWLEDGE_BASES_PATH, {"name": name})
    return created["id"]


# 做什么：试读一篇 UTF-8 文档并检查正文上下限
# 入参：单个文本文件路径
# 返回：保留换行的原文；过长、空白或读取失败时抛错
def read_content(source: Path) -> str:
    # 多读一个字符就能发现超限，不必把大文件完整读入内存。
    with source.open(encoding="utf-8-sig", newline="") as text_file:
        content = text_file.read(MAX_CONTENT_LENGTH + 1)
    if len(content) > MAX_CONTENT_LENGTH:
        raise SkippedDocument(f"正文超过 {MAX_CONTENT_LENGTH} 字")
    if not content.strip():
        raise SkippedDocument("正文全空白")
    return content


# 做什么：处理一个文件，记录结果并让同名文件保持幂等
# 入参：文件、已知标题、文档接口路径和可为空的客户端
# 返回：新增、已存在、跳过、失败或待上传；已知错误按文件记录
def import_source(source: Path, seen_titles: set[str], path: str, client: KnowledgeClient | None) -> str:
    title = source.stem.strip()
    if title in seen_titles:
        reason = "本地已有同标题待上传文件" if client is None else "库中已有同标题文档"
        LOGGER.info("%s | %s | %s", EXISTING, source.name, reason)
        return EXISTING
    try:
        content = read_content(source)
        if not title or len(title) > MAX_TITLE_LENGTH:
            raise ValueError(f"标题需有 1–{MAX_TITLE_LENGTH} 字")
        if client is not None:
            client.request("POST", path, {"title": title, "content": content})
    except SkippedDocument as error:
        LOGGER.info("%s | %s | %s", SKIPPED, source.name, error)
        return SKIPPED
    except UnicodeError:
        LOGGER.error("%s | %s | 文件无法按 UTF-8 解码", FAILED, source.name)
        return FAILED
    except (OSError, ValueError, KnowledgeRequestError) as error:
        LOGGER.error("%s | %s | %s", FAILED, source.name, error)
        return FAILED
    seen_titles.add(title)
    result = PLANNED if client is None else ADDED
    LOGGER.info("%s | %s | 标题=%s | %s 字", result, source.name, title, len(content))
    return result


# 做什么：逐文件执行导入或预览，并用接口回查库内总数
# 入参：已预检的文件列表、已登录客户端和可选库编号
# 返回：出现文件失败或总数回查失败时为 1，其余为 0
def process_sources(sources: list[Path], client: KnowledgeClient | None, base_id: int | None = None) -> int:
    counts: Counter[str] = Counter()
    seen_titles: set[str] = set()
    path = ""
    if client is not None and base_id is not None:
        path = documents_path(base_id)
        seen_titles = {document["title"] for document in client.iter_items(path)}
    for source in sources:
        outcome = import_source(source, seen_titles, path, client)
        counts[outcome] += 1
    total_count: int | str = "—"
    count_failed = False
    if client is not None:
        try:
            total_count = client.get_page(path, page=1, page_size=1)["total"]
        except KnowledgeRequestError as error:
            count_failed = True
            LOGGER.error("库编号=%s | 无法核对库内文档总数 | %s", base_id, error)
    LOGGER.info("导入汇总 | 文件总数=%s | 新增=%s | 已存在=%s | 跳过=%s | 失败=%s | 库内文档总数=%s%s",
                len(sources), counts[ADDED], counts[EXISTING], counts[SKIPPED], counts[FAILED],
                total_count, f" | 待上传={counts[PLANNED]}" if client is None else "")
    return int(count_failed or counts[FAILED] > 0)


# 做什么：预检目录，按参数选择离线预览或真实导入
# 入参：命令行，由 argparse 解析
# 返回：成功为 0，执行失败为 1，目录或参数无效为 2
def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    arguments = parse_arguments()
    try:
        sources = find_sources(arguments.dir)
    except (OSError, UnsupportedCorpusError) as error:
        LOGGER.error("目录 %s | %s", arguments.dir, error)
        return 2
    if arguments.dry_run:
        return process_sources(sources, None)
    client = KnowledgeClient(arguments.base_url)
    try:
        client.login(arguments.username, arguments.password)
        base_id = resolve_base_id(client, arguments.base_id, arguments.kb_name)
        LOGGER.info("目标知识库编号=%s", base_id)
        return process_sources(sources, client, base_id)
    except KnowledgeRequestError as error:
        LOGGER.error("导入无法开始 | %s", error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
