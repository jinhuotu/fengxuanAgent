"""独立验证全文候选的相关性过滤。"""
import pytest

from app.core.config import Settings
from app.rag.relevance import select_relevant_hits


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
