"""计划模块共用：时区、归属校验与软删除过滤。"""
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import ColumnElement, and_, select
from sqlalchemy.orm import Session

from app.core.errors import (
    PlanGoalNotFoundError,
    PlanKeyResultNotFoundError,
    PlanProjectNotFoundError,
    PlanStudyItemNotFoundError,
    PlanTaskNotFoundError,
)
from app.models.plan import (
    IS_NOT_DELETED,
    PlanGoal,
    PlanKeyResult,
    PlanProject,
    PlanStudyItem,
    PlanTask,
)

PLAN_TIMEZONE = timezone(timedelta(hours=8))
# 掌握到这一档就算"已学会"，低于它的仍在学习中
MASTERED_LEVEL = 3
WEEK_LENGTH_DAYS = 7


# 做什么：取得与项目数据库时区一致的当前时间
# 入参：无
# 返回：东八区的无时区 datetime，供 MySQL DATETIME 保存
def plan_now() -> datetime:
    return datetime.now(PLAN_TIMEZONE).replace(tzinfo=None)


# 做什么：取得东八区的今天
# 入参：无
# 返回：日期对象，用于每日记录与复习排期
def plan_today() -> date:
    return plan_now().date()


# 做什么：生成"本人的、未删除的"过滤条件
# 入参：模型类与用户编号
# 返回：SQLAlchemy 条件表达式，供各查询拼进 where
def owned_filter(model: type, owner_id: int) -> ColumnElement[bool]:
    return and_(model.owner_id == owner_id, model.is_deleted == IS_NOT_DELETED)


# 做什么：查找本人名下的目标
# 入参：数据库会话、用户编号、目标编号
# 返回：目标模型；不存在或不归本人时抛 404/6001
def owned_goal(db_session: Session, owner_id: int, goal_id: int) -> PlanGoal:
    goal = db_session.scalar(
        select(PlanGoal).where(PlanGoal.id == goal_id, owned_filter(PlanGoal, owner_id))
    )
    if goal is None:
        raise PlanGoalNotFoundError()
    return goal


# 做什么：查找本人名下的关键结果
# 入参：数据库会话、用户编号、关键结果编号
# 返回：关键结果模型；不存在或不归本人时抛 404/6002
def owned_key_result(db_session: Session, owner_id: int, key_result_id: int) -> PlanKeyResult:
    key_result = db_session.scalar(
        select(PlanKeyResult).where(
            PlanKeyResult.id == key_result_id, owned_filter(PlanKeyResult, owner_id)
        )
    )
    if key_result is None:
        raise PlanKeyResultNotFoundError()
    return key_result


# 做什么：查找本人名下的计划
# 入参：数据库会话、用户编号、计划编号
# 返回：计划模型；不存在或不归本人时抛 404/6003
def owned_project(db_session: Session, owner_id: int, project_id: int) -> PlanProject:
    project = db_session.scalar(
        select(PlanProject).where(PlanProject.id == project_id, owned_filter(PlanProject, owner_id))
    )
    if project is None:
        raise PlanProjectNotFoundError()
    return project


# 做什么：查找本人名下的任务
# 入参：数据库会话、用户编号、任务编号
# 返回：任务模型；不存在或不归本人时抛 404/6004
def owned_task(db_session: Session, owner_id: int, task_id: int) -> PlanTask:
    task = db_session.scalar(
        select(PlanTask).where(PlanTask.id == task_id, owned_filter(PlanTask, owner_id))
    )
    if task is None:
        raise PlanTaskNotFoundError()
    return task


# 做什么：查找本人名下的学习单元
# 入参：数据库会话、用户编号、学习单元编号
# 返回：学习单元模型；不存在或不归本人时抛 404/6005
def owned_study_item(db_session: Session, owner_id: int, item_id: int) -> PlanStudyItem:
    item = db_session.scalar(
        select(PlanStudyItem).where(PlanStudyItem.id == item_id, owned_filter(PlanStudyItem, owner_id))
    )
    if item is None:
        raise PlanStudyItemNotFoundError()
    return item
