"""用项目文档验证混合检索的余弦下限，默认不运行。"""
import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.knowledge import KnowledgeChunk
from app.rag import retrieval, vector_index
from app.schemas.knowledge import KnowledgeBaseCreate, KnowledgeDocumentCreate
from app.services.knowledge import create_knowledge_base, create_knowledge_document, delete_knowledge_base
from tests.smoke_support import PROJECT_ROOT, SMOKE_USER_ID, mysql_sessions, throwaway_collection  # noqa: F401

pytestmark = pytest.mark.smoke
logger = get_logger(__name__)
CORPUS_BASE_NAME = "检索余弦下限冒烟语料"
CORPUS_DOCUMENTS = (
    "architecture.md", "chat.md", "commands.md", "rules/db.md", "scripts.md",
    "术语表.md", "新手导航.md", "验收/阶段二.md",
)
IN_SCOPE_QUESTIONS = (
    "上传纯文本知识库文档如何同步切片？",
    "对话请求如何按原 UUID 确认结果？",
    "最后管理员保护由哪条自动化用例覆盖？",
    "怎样用 seed_rbac.py 初始化角色和权限点？",
    "全文检索按相关性和切片编号怎样排序？",
)
OUT_OF_SCOPE_QUESTIONS = (
    "量子纠缠拓扑绝缘体",
    "红烧肉怎么做才不腻",
    "明天郑州天气怎么样",
    "怎么给猫剪指甲",
    "上一届世界杯冠军是谁",
    "股票市盈率怎么算",
    "怎么用 React 写一个下拉菜单",
    "感冒了吃什么药好得快",
    "光合作用的暗反应过程是怎样的",
    "第一次办护照需要什么材料",
    "Kubernetes 调度器是怎么工作的",
    "怎么煮出好吃的米饭",
)
SEARCH_TOP_K = 5
MAX_OUT_OF_SCOPE_HITS = 1


# 做什么：通过正式上传服务为隔离库写入八篇项目文档并核对向量同步
# 入参：隔离库会话工厂、一次性集合和配置替换工具
# 返回：测试知识库编号；上传或索引失败时抛出，结束时软删测试库
@pytest.fixture
def answer_floor_corpus(mysql_sessions, throwaway_collection, monkeypatch):
    monkeypatch.setattr(get_settings(), "vector_retrieval_mode", "hybrid")
    with mysql_sessions() as db_session:
        knowledge_base = create_knowledge_base(db_session, SMOKE_USER_ID, KnowledgeBaseCreate(name=CORPUS_BASE_NAME))
    try:
        populate_corpus(mysql_sessions, knowledge_base.id)
        yield knowledge_base.id
    finally:
        with mysql_sessions() as db_session:
            delete_knowledge_base(db_session, SMOKE_USER_ID, knowledge_base.id)


# 做什么：逐篇上传 Markdown 原件，核对向量点与实际切片数量一致
# 入参：隔离库会话工厂和测试知识库编号
# 返回：无；语料或索引缺失时断言失败
def populate_corpus(sessions, knowledge_base_id):
    for relative_path in CORPUS_DOCUMENTS:
        document_path = PROJECT_ROOT / "docs" / relative_path
        content = document_path.read_text(encoding="utf-8")
        payload = KnowledgeDocumentCreate(title=document_path.stem, content=content)
        with sessions() as db_session:
            create_knowledge_document(db_session, SMOKE_USER_ID, knowledge_base_id, payload)
    statement = select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.knowledge_base_id == knowledge_base_id)
    with sessions() as db_session:
        chunk_count = db_session.scalar(statement)
    point_count = vector_index.count_points()
    logger.info("余弦下限语料 | knowledge_base_id=%s | 文档=%s | 切片=%s | 向量点=%s",
                knowledge_base_id, len(CORPUS_DOCUMENTS), chunk_count, point_count)
    assert chunk_count and point_count == chunk_count, f"切片={chunk_count}，向量点={point_count}"


# 做什么：记录每题原始余弦和混合命中数，收集指定结果的题目
# 入参：隔离库会话、知识库编号、题目列表和需要收集的命中状态
# 返回：题目与最高余弦及前五名分数；检索异常直接抛出
def collect_questions(db_session, knowledge_base_id, questions, collect_hits):
    selected_questions = {}
    for question in questions:
        vector_rows = retrieval._vector_search(db_session, SMOKE_USER_ID, knowledge_base_id, question, SEARCH_TOP_K)
        scores = [round(row["score"], 4) for row in vector_rows]
        highest_score = scores[0] if scores else None
        result = retrieval.search_relevant_chunks(db_session, SMOKE_USER_ID, knowledge_base_id, question, SEARCH_TOP_K)
        logger.info("余弦下限冒烟 | knowledge_base_id=%s | 题目=%s | 最高余弦=%s | 前五=%s | 命中=%s",
                    knowledge_base_id, question, highest_score, scores, len(result.hits))
        assert scores, f"{question} 没有向量候选，前五={scores}"
        if bool(result.hits) == collect_hits:
            selected_questions[question] = scores
    return selected_questions


# 做什么：同时检查库内题的误拒和库外题的误召回
# 入参：隔离库会话工厂和已上传真实项目文档的测试库编号
# 返回：无；有库内题没命中或库外误召回超过一题时列出原始分数
def test_hybrid_answer_floor_on_project_documents(mysql_sessions, answer_floor_corpus):
    with mysql_sessions() as db_session:
        rejected = collect_questions(db_session, answer_floor_corpus, IN_SCOPE_QUESTIONS, collect_hits=False)
        leaked = collect_questions(db_session, answer_floor_corpus, OUT_OF_SCOPE_QUESTIONS, collect_hits=True)
    logger.info("余弦下限统计 | knowledge_base_id=%s | 库内命中=%s/%s | 库外命中=%s/%s",
                answer_floor_corpus, len(IN_SCOPE_QUESTIONS) - len(rejected), len(IN_SCOPE_QUESTIONS),
                len(leaked), len(OUT_OF_SCOPE_QUESTIONS))
    assert not rejected and len(leaked) <= MAX_OUT_OF_SCOPE_HITS, f"库内误拒前五={rejected}，库外命中前五={leaked}"
