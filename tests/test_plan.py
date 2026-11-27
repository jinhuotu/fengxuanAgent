"""隔离验证工作进度平台：进度计算、复习档位、每日记录与归属校验。"""
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import URL, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.security import create_access_token, hash_password
from app.db import session as database_session
from app.db.base import Base
from app.main import app
from app.models.user import User
from app.services.plan_common import plan_today

OWNER_HEADERS = {"Authorization": f"Bearer {create_access_token(1)}"}
OTHER_HEADERS = {"Authorization": f"Bearer {create_access_token(2)}"}


# 做什么：创建每个用例独享的 SQLite 文件库并种两个用户
# 入参：pytest 提供的用例临时目录
# 返回：测试会话；结束或异常时关闭连接
@pytest.fixture
def plan_session(tmp_path: Path) -> Iterator[Session]:
    database_url = URL.create("sqlite", database=str(tmp_path / "plan.sqlite3"))
    test_engine = create_engine(database_url)
    try:
        Base.metadata.create_all(test_engine)
        test_sessions = sessionmaker(bind=test_engine)
        with test_sessions.begin() as seed_session:
            seed_session.add(User(id=1, username="planner", password_hash=hash_password("isolated-test-password")))
            seed_session.add(User(id=2, username="observer", password_hash=hash_password("isolated-test-password")))
        with test_sessions() as test_session:
            yield test_session
    finally:
        test_engine.dispose()


# 做什么：让接口从同一个 SQLite 文件库取独立会话
# 入参：隔离会话和替换会话工厂的 monkeypatch
# 返回：可调用真实接口的测试客户端，请求结束后关闭
@pytest.fixture
def plan_client(plan_session: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(database_session, "SessionLocal", sessionmaker(bind=plan_session.bind))
    with TestClient(app) as client:
        yield client


# 做什么：建一个自动模式目标和一条关键结果
# 入参：测试客户端、目标标题、关键结果的目标值
# 返回：目标编号与关键结果编号
def create_goal_with_result(client: TestClient, title: str, target: float) -> tuple[int, int]:
    goal = client.post(
        "/plan/goals",
        headers=OWNER_HEADERS,
        json={"period_type": 2, "period_label": "2026 Q4", "title": title},
    )
    assert goal.status_code == 200
    goal_id = goal.json()["data"]["id"]
    key_result = client.post(
        f"/plan/goals/{goal_id}/key-results",
        headers=OWNER_HEADERS,
        json={"title": "完成初稿", "metric_unit": "篇", "target_value": target},
    )
    assert key_result.status_code == 200
    return goal_id, key_result.json()["data"]["id"]


# 做什么：建一个计划
# 入参：测试客户端、计划类型、标题、开始与截止日期（可为空）
# 返回：计划编号
def create_project(client: TestClient, kind: int, title: str, start=None, end=None) -> int:
    response = client.post(
        "/plan/projects",
        headers=OWNER_HEADERS,
        json={"kind": kind, "title": title, "start_date": start, "end_date": end},
    )
    assert response.status_code == 200
    return response.json()["data"]["id"]


# 做什么：验证关键结果按数值达成率折算，目标进度取平均
# 入参：测试客户端
# 返回：无；进度百分比不符时断言失败
def test_goal_progress_follows_key_results(plan_client: TestClient) -> None:
    goal_id, key_result_id = create_goal_with_result(plan_client, "把平台跑起来", 10)
    updated = plan_client.patch(
        f"/plan/key-results/{key_result_id}", headers=OWNER_HEADERS, json={"current_value": 3}
    )
    assert updated.json()["data"]["progress"] == 30
    goals = plan_client.get("/plan/goals", headers=OWNER_HEADERS).json()["data"]["items"]
    assert [(item["id"], item["progress"]) for item in goals] == [(goal_id, 30)]


# 做什么：验证手动模式忽略关键结果，直接用手填百分比
# 入参：测试客户端
# 返回：无；目标进度不等于手填值时报错
def test_manual_goal_progress_ignores_key_results(plan_client: TestClient) -> None:
    goal_id, _ = create_goal_with_result(plan_client, "写年度总结", 100)
    response = plan_client.patch(
        f"/plan/goals/{goal_id}", headers=OWNER_HEADERS, json={"progress_mode": 1, "manual_progress": 65}
    )
    assert response.status_code == 200
    assert response.json()["data"]["progress"] == 65


# 做什么：验证计划进度按任务完成数折算，放弃的任务不进分母
# 入参：测试客户端
# 返回：无；进度与计数不符时断言失败
def test_project_progress_counts_done_tasks(plan_client: TestClient) -> None:
    project_id = create_project(plan_client, 1, "上线工作进度平台")
    task_ids = [
        plan_client.post(
            "/plan/tasks", headers=OWNER_HEADERS, json={"project_id": project_id, "title": title}
        ).json()["data"]["id"]
        for title in ("建表", "写接口", "做页面")
    ]
    plan_client.patch(f"/plan/tasks/{task_ids[0]}", headers=OWNER_HEADERS, json={"status": 3})
    plan_client.patch(f"/plan/tasks/{task_ids[1]}", headers=OWNER_HEADERS, json={"status": 4})
    projects = plan_client.get("/plan/projects?kind=1", headers=OWNER_HEADERS).json()["data"]["items"]
    assert [(item["task_total"], item["task_done"], item["progress"]) for item in projects] == [(2, 1, 50)]


# 做什么：验证任务改成完成时补完成时间，改回待办时清空
# 入参：测试客户端
# 返回：无；完成时间为空或残留时报错
def test_task_done_time_follows_status(plan_client: TestClient) -> None:
    task_id = plan_client.post("/plan/tasks", headers=OWNER_HEADERS, json={"title": "写迁移"}).json()["data"]["id"]
    done = plan_client.patch(f"/plan/tasks/{task_id}", headers=OWNER_HEADERS, json={"status": 3})
    assert done.json()["data"]["done_at"] is not None
    reopened = plan_client.patch(f"/plan/tasks/{task_id}", headers=OWNER_HEADERS, json={"status": 1})
    assert reopened.json()["data"]["done_at"] is None


# 做什么：验证截止日期早于开始日期时拒绝写库
# 入参：测试客户端
# 返回：无；状态码或错误码不符时报错
def test_project_date_range_rejected(plan_client: TestClient) -> None:
    response = plan_client.post(
        "/plan/projects",
        headers=OWNER_HEADERS,
        json={"kind": 1, "title": "日期倒置", "start_date": "2026-12-20", "end_date": "2026-12-01"},
    )
    assert (response.status_code, response.json()["code"]) == (400, 6006)


# 做什么：验证复习答对升档、答错退档，并按档位排下次日期
# 入参：测试客户端
# 返回：无；档位或下次日期不符时报失败
def test_study_review_moves_stage(plan_client: TestClient) -> None:
    project_id = create_project(plan_client, 2, "RAG 精读")
    item_id = plan_client.post(
        "/plan/study/items", headers=OWNER_HEADERS, json={"project_id": project_id, "title": "召回与重排"}
    ).json()["data"]["id"]
    first = plan_client.post(
        f"/plan/study/items/{item_id}/review", headers=OWNER_HEADERS, json={"remembered": True}
    ).json()["data"]
    assert (first["review_stage"], first["mastery"]) == (1, 1)
    assert first["next_review_date"] == str(plan_today() + timedelta(days=3))
    wrong = plan_client.post(
        f"/plan/study/items/{item_id}/review", headers=OWNER_HEADERS, json={"remembered": False}
    ).json()["data"]
    assert (wrong["review_stage"], wrong["mastery"]) == (0, 0)


# 做什么：验证每日记录同一天覆盖而不是新增，并点亮本周打卡
# 入参：测试客户端
# 返回：无；记录内容、打卡或连续天数不符时报错
def test_daily_log_upsert_and_week_mark(plan_client: TestClient) -> None:
    project_id = create_project(plan_client, 2, "数据库专题")
    today = plan_today()
    body = {"note": "上午写了迁移", "study_minutes": 60, "study_gain": "先跑通再加功能", "study_project_ids": [project_id]}
    plan_client.put(f"/plan/daily/{today}", headers=OWNER_HEADERS, json=body)
    body["note"] = "下午补了测试"
    plan_client.put(f"/plan/daily/{today}", headers=OWNER_HEADERS, json=body)
    today_data = plan_client.get("/plan/daily/today", headers=OWNER_HEADERS).json()["data"]
    assert today_data["today"]["note"] == "下午补了测试"
    assert today_data["streak"] == 1
    assert [mark["has_log"] for mark in today_data["week"]].count(True) == 1


# 做什么：验证每日记录不接受别人的学习主题
# 入参：测试客户端
# 返回：无；错误码不符时报错
def test_daily_log_rejects_foreign_project(plan_client: TestClient) -> None:
    project_id = create_project(plan_client, 2, "只属于我")
    response = plan_client.put(
        f"/plan/daily/{plan_today()}",
        headers=OTHER_HEADERS,
        json={"note": "借用", "study_minutes": 10, "study_project_ids": [project_id]},
    )
    assert (response.status_code, response.json()["code"]) == (404, 6003)


# 做什么：验证另一个用户改不到我的计划，也看不到我的目标
# 入参：测试客户端
# 返回：无；归属校验失效时报错
def test_other_user_cannot_touch_my_project(plan_client: TestClient) -> None:
    project_id = create_project(plan_client, 1, "私有计划")
    create_goal_with_result(plan_client, "私有目标", 5)
    patch = plan_client.patch(f"/plan/projects/{project_id}", headers=OTHER_HEADERS, json={"title": "改名"})
    assert (patch.status_code, patch.json()["code"]) == (404, 6003)
    assert plan_client.get("/plan/goals", headers=OTHER_HEADERS).json()["data"]["items"] == []
