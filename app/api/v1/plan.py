"""登录用户的工作进度平台接口：目标、计划、任务、学习复习与每日记录。"""
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.schemas.auth import UserInfo
from app.schemas.plan import (
    DailyLogUpsert,
    GoalCreate,
    GoalUpdate,
    KeyResultCreate,
    KeyResultUpdate,
    ProjectCreate,
    ProjectUpdate,
    StudyItemCreate,
    StudyReviewRequest,
    TaskCreate,
    TaskUpdate,
)
from app.services import plan_daily, plan_goals, plan_projects, plan_study, plan_tasks
from app.utils.response import success

router = APIRouter(prefix="/plan", tags=["工作进度"])
DELETED_RESULT = {"deleted": True}


# 做什么：返回本人全部目标及关键结果进度
# 入参：当前用户和请求级数据库会话
# 返回：统一响应；未登录返回 401
@router.get("/goals", summary="目标列表")
def list_goals(
    current_user: UserInfo = Depends(get_current_user), db_session: Session = Depends(get_db)
) -> dict:
    goals = plan_goals.list_goals(db_session, current_user.id)
    return success(goals.model_dump(mode="json"))


# 做什么：新建目标
# 入参：已校验的请求体、当前用户和独立写入会话
# 返回：统一响应；参数错误返回 422
@router.post("/goals", summary="新建目标")
def create_goal(
    payload: GoalCreate,
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db, use_cache=False),
) -> dict:
    goal = plan_goals.create_goal(db_session, current_user.id, payload)
    return success(goal.model_dump(mode="json"))


# 做什么：修改目标
# 入参：目标编号、已校验的请求体、当前用户和独立写入会话
# 返回：统一响应；目标不可见返回 404/6001
@router.patch("/goals/{goal_id}", summary="修改目标")
def update_goal(
    goal_id: int,
    payload: GoalUpdate,
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db, use_cache=False),
) -> dict:
    goal = plan_goals.update_goal(db_session, current_user.id, goal_id, payload)
    return success(goal.model_dump(mode="json"))


# 做什么：删除目标及其关键结果
# 入参：目标编号、当前用户和独立写入会话
# 返回：统一响应；目标不可见返回 404/6001
@router.delete("/goals/{goal_id}", summary="删除目标")
def delete_goal(
    goal_id: int,
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db, use_cache=False),
) -> dict:
    plan_goals.delete_goal(db_session, current_user.id, goal_id)
    return success(DELETED_RESULT)


# 做什么：在目标下新增关键结果
# 入参：目标编号、已校验的请求体、当前用户和独立写入会话
# 返回：统一响应；目标不可见返回 404/6001
@router.post("/goals/{goal_id}/key-results", summary="新增关键结果")
def create_key_result(
    goal_id: int,
    payload: KeyResultCreate,
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db, use_cache=False),
) -> dict:
    key_result = plan_goals.create_key_result(db_session, current_user.id, goal_id, payload)
    return success(key_result.model_dump(mode="json"))


# 做什么：修改关键结果（主要是当前值）
# 入参：关键结果编号、已校验的请求体、当前用户和独立写入会话
# 返回：统一响应；关键结果不可见返回 404/6002
@router.patch("/key-results/{key_result_id}", summary="修改关键结果")
def update_key_result(
    key_result_id: int,
    payload: KeyResultUpdate,
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db, use_cache=False),
) -> dict:
    key_result = plan_goals.update_key_result(db_session, current_user.id, key_result_id, payload)
    return success(key_result.model_dump(mode="json"))


# 做什么：删除关键结果
# 入参：关键结果编号、当前用户和独立写入会话
# 返回：统一响应；关键结果不可见返回 404/6002
@router.delete("/key-results/{key_result_id}", summary="删除关键结果")
def delete_key_result(
    key_result_id: int,
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db, use_cache=False),
) -> dict:
    plan_goals.delete_key_result(db_session, current_user.id, key_result_id)
    return success(DELETED_RESULT)


# 做什么：列出工作计划或学习主题
# 入参：类型筛选（一工作，二学习，空为全部）、当前用户和请求级会话
# 返回：统一响应；未登录返回 401
@router.get("/projects", summary="计划列表")
def list_projects(
    kind: int | None = Query(default=None, ge=1, le=2, description="类型：一工作，二学习"),
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db),
) -> dict:
    projects = plan_projects.list_projects(db_session, current_user.id, kind)
    return success(projects.model_dump(mode="json"))


# 做什么：新建工作计划或学习主题
# 入参：已校验的请求体、当前用户和独立写入会话
# 返回：统一响应；日期区间倒置返回 400/6006
@router.post("/projects", summary="新建计划")
def create_project(
    payload: ProjectCreate,
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db, use_cache=False),
) -> dict:
    project = plan_projects.create_project(db_session, current_user.id, payload)
    return success(project.model_dump(mode="json"))


# 做什么：修改计划
# 入参：计划编号、已校验的请求体、当前用户和独立写入会话
# 返回：统一响应；计划不可见返回 404/6003
@router.patch("/projects/{project_id}", summary="修改计划")
def update_project(
    project_id: int,
    payload: ProjectUpdate,
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db, use_cache=False),
) -> dict:
    project = plan_projects.update_project(db_session, current_user.id, project_id, payload)
    return success(project.model_dump(mode="json"))


# 做什么：删除计划及其任务、学习单元
# 入参：计划编号、当前用户和独立写入会话
# 返回：统一响应；计划不可见返回 404/6003
@router.delete("/projects/{project_id}", summary="删除计划")
def delete_project(
    project_id: int,
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db, use_cache=False),
) -> dict:
    plan_projects.delete_project(db_session, current_user.id, project_id)
    return success(DELETED_RESULT)


# 做什么：列出任务，可按计划或状态筛选
# 入参：计划编号、状态、当前用户和请求级会话
# 返回：统一响应；未登录返回 401
@router.get("/tasks", summary="任务列表")
def list_tasks(
    project_id: int | None = Query(default=None, description="所属计划编号"),
    status: int | None = Query(default=None, ge=1, le=4, description="状态：一待办，二进行中，三已完成，四已放弃"),
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db),
) -> dict:
    tasks = plan_tasks.list_tasks(db_session, current_user.id, project_id, status)
    return success(tasks.model_dump(mode="json"))


# 做什么：新建任务
# 入参：已校验的请求体、当前用户和独立写入会话
# 返回：统一响应；计划不可见返回 404/6003
@router.post("/tasks", summary="新建任务")
def create_task(
    payload: TaskCreate,
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db, use_cache=False),
) -> dict:
    task = plan_tasks.create_task(db_session, current_user.id, payload)
    return success(task.model_dump(mode="json"))


# 做什么：修改任务状态或字段
# 入参：任务编号、已校验的请求体、当前用户和独立写入会话
# 返回：统一响应；任务不可见返回 404/6004
@router.patch("/tasks/{task_id}", summary="修改任务")
def update_task(
    task_id: int,
    payload: TaskUpdate,
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db, use_cache=False),
) -> dict:
    task = plan_tasks.update_task(db_session, current_user.id, task_id, payload)
    return success(task.model_dump(mode="json"))


# 做什么：删除任务
# 入参：任务编号、当前用户和独立写入会话
# 返回：统一响应；任务不可见返回 404/6004
@router.delete("/tasks/{task_id}", summary="删除任务")
def delete_task(
    task_id: int,
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db, use_cache=False),
) -> dict:
    plan_tasks.delete_task(db_session, current_user.id, task_id)
    return success(DELETED_RESULT)


# 做什么：返回今日复习清单与各学习主题进度
# 入参：当前用户和请求级数据库会话
# 返回：统一响应；未登录返回 401
@router.get("/study", summary="学习概览")
def get_study_overview(
    current_user: UserInfo = Depends(get_current_user), db_session: Session = Depends(get_db)
) -> dict:
    overview = plan_study.get_study_overview(db_session, current_user.id)
    return success(overview.model_dump(mode="json"))


# 做什么：在学习主题下新增知识点
# 入参：已校验的请求体、当前用户和独立写入会话
# 返回：统一响应；计划不存在或不是学习类型返回 404/6003
@router.post("/study/items", summary="新增知识点")
def create_study_item(
    payload: StudyItemCreate,
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db, use_cache=False),
) -> dict:
    item = plan_study.create_study_item(db_session, current_user.id, payload)
    return success(item.model_dump(mode="json"))


# 做什么：提交一次复习结果并推进档位
# 入参：单元编号、是否掌握、当前用户和独立写入会话
# 返回：统一响应；单元不可见返回 404/6005
@router.post("/study/items/{item_id}/review", summary="复习一个知识点")
def review_study_item(
    item_id: int,
    payload: StudyReviewRequest,
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db, use_cache=False),
) -> dict:
    item = plan_study.review_study_item(db_session, current_user.id, item_id, payload.remembered)
    return success(item.model_dump(mode="json"))


# 做什么：删除知识点
# 入参：单元编号、当前用户和独立写入会话
# 返回：统一响应；单元不可见返回 404/6005
@router.delete("/study/items/{item_id}", summary="删除知识点")
def delete_study_item(
    item_id: int,
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db, use_cache=False),
) -> dict:
    plan_study.delete_study_item(db_session, current_user.id, item_id)
    return success(DELETED_RESULT)


# 做什么：返回今天的填写状态、今日任务、本周打卡和连续天数
# 入参：当前用户和请求级数据库会话
# 返回：统一响应；未登录返回 401
@router.get("/daily/today", summary="今天的进度")
def get_daily_today(
    current_user: UserInfo = Depends(get_current_user), db_session: Session = Depends(get_db)
) -> dict:
    today = plan_daily.get_today(db_session, current_user.id)
    return success(today.model_dump(mode="json"))


# 做什么：返回最近几天的每日记录
# 入参：条数上限、当前用户和请求级数据库会话
# 返回：统一响应；未登录返回 401
@router.get("/daily/recent", summary="最近的每日记录")
def list_recent_daily(
    limit: int = Query(default=10, ge=1, le=30, description="返回条数"),
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db),
) -> dict:
    recent = plan_daily.list_recent_logs(db_session, current_user.id, limit)
    return success(recent.model_dump(mode="json"))


# 做什么：按日期写入或覆盖每日记录
# 入参：记录日期、已校验的请求体、当前用户和独立写入会话
# 返回：统一响应；学习主题不可见返回 404/6003
@router.put("/daily/{log_date}", summary="保存某天的记录")
def upsert_daily(
    log_date: date,
    payload: DailyLogUpsert,
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db, use_cache=False),
) -> dict:
    record = plan_daily.upsert_daily(db_session, current_user.id, log_date, payload)
    return success(record.model_dump(mode="json"))
