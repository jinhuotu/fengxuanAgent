"""独立验证全文候选的相关性过滤。"""
import pytest

from app.core.config import Settings
from app.rag.relevance import extract_probe_terms, select_relevant_hits

LEARNING_RATE_CANDIDATES = [
    {"score": 12.79, "content": "训练参数中可以设置学习率。"},
    {"score": 9.87, "content": "检查训练过程中的学习率。"},
    {"score": 7.62, "content": "机器学习训练可以编排多个任务。"},
    {"score": 6.815, "content": "学习率（Learning Rate）：决定了模型每次更新时权重改变的幅度。"},
]


# 做什么：覆盖单行、二元组噪声、相近分数与两种阈值的边界
# 入参：候选分数、相对比例、绝对下限及预期保留位置
# 返回：无；规则、顺序或入参发生变化时断言失败
@pytest.mark.parametrize(("scores", "ratio", "absolute", "kept_indexes"), [
    ([1.0], 0.5, 0.0, [0]),
    ([2.17, 0.54], 0.5, 0.0, [0]),
    ([1.0, 0.9], 0.5, 0.0, [0, 1]),
    ([1.0, 0.9], 1.0, 0.0, [0]),
    ([1.0, 1.0, 0.9], 1.0, 0.0, [0, 1]),
    ([2.0, 1.0, 0.99], 0.5, 0.0, [0, 1]),
    ([1.32e-08, 1.32e-08], 0.5, 0.05, []),
    ([1.32e-08, 1.32e-08], 0.5, 0.0, [0, 1]),
    ([1.0, 0.9], -1.0, 0.0, [0, 1]),
    ([1.0, 0.9], 2.0, 0.0, [0]),
    ([0.0, 0.0], 0.0, 0.0, []),
    ([-1.0, -2.0], 0.5, 0.0, []),
    ([0.51, 0.01, 1.0, 0.52], 0.5, 0.0, [0, 2, 3]),
])
def test_select_relevant_hits(scores, ratio, absolute, kept_indexes) -> None:
    rows = [{"id": index, "score": score} for index, score in enumerate(scores)]
    original_rows = [row.copy() for row in rows]
    selected = select_relevant_hits(rows, len(scores) + 1, ratio, absolute)
    assert selected == [rows[index] for index in kept_indexes]
    assert rows == original_rows


# 做什么：验证无候选、零条上限和截断的边界
# 入参：候选数量、返回上限及预期数量
# 返回：无；空列表或结果上限不符时断言失败
@pytest.mark.parametrize(("row_count", "top_k", "expected_count"), [(0, 5, 0), (2, 0, 0), (3, 2, 2)])
def test_relevance_top_k_boundaries(row_count, top_k, expected_count) -> None:
    rows = [{"id": index, "score": 1.0} for index in range(row_count)]
    assert select_relevant_hits(rows, top_k, 0.5, 0.0) == rows[:expected_count]


# 做什么：验证四项检索配置能用指定环境变量覆盖
# 入参：pytest 的进程环境替换工具和虚拟配置凭证
# 返回：无；环境变量未被 Settings 正确解析时断言失败
def test_relevance_settings_read_environment(monkeypatch) -> None:
    monkeypatch.setenv("SEARCH_MIN_SCORE_RATIO", "0.75")
    monkeypatch.setenv("SEARCH_MIN_SCORE_ABSOLUTE", "0.01")
    monkeypatch.setenv("SEARCH_CANDIDATE_MULTIPLIER", "4")
    monkeypatch.setenv("SEARCH_CANDIDATE_MAX", "35")
    settings = Settings(_env_file=None, db_password="test-only", jwt_secret="test-only")
    assert settings.search_min_score_ratio == 0.75
    assert settings.search_min_score_absolute == 0.01
    assert settings.search_candidate_multiplier == 4
    assert settings.search_candidate_max == 35


# 做什么：用开发库的四条分数核对探针排序和最终截断
# 入参：带正文的学习率候选及相对阈值 0.5
# 返回：无；条数、顺序或截断结果变化时断言失败
def test_probe_prioritizes_exact_matches_without_dropping_hits() -> None:
    hits = select_relevant_hits(LEARNING_RATE_CANDIDATES, 5, 0.5, 0.0, probe="学习率")
    assert len(hits) == 4
    assert [hit["score"] for hit in hits] == [12.79, 9.87, 6.815, 7.62]
    limited_hits = select_relevant_hits(LEARNING_RATE_CANDIDATES, 3, 0.5, 0.0, probe="学习率")
    assert limited_hits == hits[:3]


# 做什么：核对空探针与省略探针都会原样保留过滤后的顺序
# 入参：相同的一组学习率候选
# 返回：无；两种调用的保留条数、内容或顺序不一致时断言失败
def test_empty_probe_preserves_original_order() -> None:
    without_probe = select_relevant_hits(LEARNING_RATE_CANDIDATES, 5, 0.5, 0.0)
    with_empty_probe = select_relevant_hits(LEARNING_RATE_CANDIDATES, 5, 0.5, 0.0, probe="")
    assert with_empty_probe == without_probe == LEARNING_RATE_CANDIDATES


# 做什么：核对常见问法剥离和空探针保护
# 入参：用户问句与预期探针词
# 返回：无；剥离结果不一致时断言失败
@pytest.mark.parametrize(("query", "expected_probe"), [
    ("什么是学习率", "学习率"),
    ("后端项目地址是什么", "后端项目地址"),
    ("如何", ""),
    ("", ""),
    ("　（解释一下：学习率是什么意思？） ", "学习率"),
])
def test_extract_probe_terms(query: str, expected_probe: str) -> None:
    assert extract_probe_terms(query) == expected_probe
