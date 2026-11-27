"""目标的创建、列表、修改、删除与关键结果维护。"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.plan import IS_DELETED, PlanGoal, PlanKeyResult, PlanProject
from app.schemas.plan import (
    GoalCreate,
    GoalItem,
    GoalListResponse,
    GoalUpdate,
    KeyResultCreate,
    KeyResultItem,
    KeyResultUpdate,
)
from app.services.plan_common import owned_filter, owned_goal, owned_key_result
from app.services.plan_progress import goal_percent, key_result_percent


# 做什么：把关键结果行算成带进度的公开字段
# 入参：关键结果模型
# 返回：含达成百分比的关键结果条目
def build_key_result_item(row: PlanKeyResult) -> KeyResultItem:
    percent = key_result_percent(row.start_value, row.target_value, row.current_value)
    return KeyResultItem(
        id=row.id,
        title=row.title,
        metric_unit=row.metric_unit,
        start_value=float(row.start_value),
        target_value=float(row.target_value),
        current_value=float(row.current_value),
        progress=percent,
    )


# 做什么：把目标行、关键结果和计划数组装成公开字段
# 入参：目标模型、该目标的关键结果列表、下属计划个数
# 返回：含总进度的目标条目
def build_goal_item(goal: PlanGoal, key_results: list[PlanKeyResult], project_count: int) -> GoalItem:
    key_result_items = [build_key_result_item(row) for row in key_results]
    percent = goal_percent(
        goal.progress_mode, goal.manual_progress, [item.progress for item in key_result_items]
    )
    return GoalItem(
        id=goal.id,
        period_type=goal.period_type,
        period_label=goal.period_label,
        title=goal.title,
        description=goal.description,
        status=goal.status,
        progress_mode=goal.progress_mode,
        manual_progress=goal.manual_progress,
        progress=percent,
        project_count=project_count,
        key_results=key_result_items,
    )


# 做什么：一次性取出本人全部目标及其关键结果和计划数
# 入参：请求级数据库会话和当前用户编号
# 返回：按周期和编号排序的目标列表；数据库异常直接抛出
def list_goals(db_session: Session, owner_id: int) -> GoalListResponse:
    goal_rows = db_session.scalars(
        select(PlanGoal).where(owned_filter(PlanGoal, owner_id)).order_by(PlanGoal.period_type, PlanGoal.id.desc())
    ).all()
    key_result_rows = db_session.scalars(
        select(PlanKeyResult)
        .where(owned_filter(PlanKeyResult, owner_id))
        .order_by(PlanKeyResult.sort_order, PlanKeyResult.id)
    ).all()
    project_rows = db_session.execute(
        select(PlanProject.goal_id, func.count())
        .where(owned_filter(PlanProject, owner_id), PlanProject.goal_id.is_not(None))
        .group_by(PlanProject.goal_id)
    ).all()
    key_results_by_goal = _group_key_results(key_result_rows)
    project_counts = {goal_id: count for goal_id, count in project_rows}
    items = [
        build_goal_item(goal, key_results_by_goal.get(goal.id, []), project_counts.get(goal.id, 0))
        for goal in goal_rows
    ]
    return GoalListResponse(items=items)


# 做什么：按目标编号把关键结果分组，避免逐条查询
# 入参：关键结果模型列表
# 返回：目标编号到关键结果列表的字典
def _group_key_results(rows: list[PlanKeyResult]) -> dict[int, list[PlanKeyResult]]:
    grouped: dict[int, list[PlanKeyResult]] = {}
    for row in rows:
        grouped.setdefault(row.goal_id, []).append(row)
    return grouped


# 做什么：创建目标并返回带进度的公开字段
# 入参：独立写入会话、当前用户编号和已校验的请求
# 返回：新目标条目；写入失败时回滚并抛出异常
def create_goal(db_session: Session, owner_id: int, payload: GoalCreate) -> GoalItem:
    with db_session.begin():
        goal = PlanGoal(
            owner_id=owner_id,
            period_type=payload.period_type,
            period_label=payload.period_label,
            title=payload.title,
            description=payload.description,
            progress_mode=payload.progress_mode,
            manual_progress=payload.manual_progress,
        )
        db_session.add(goal)
        db_session.flush()
    return build_goal_item(goal, [], 0)


# 做什么：修改目标上明确提交的字段
# 入参：独立写入会话、当前用户编号、目标编号和请求
# 返回：修改后的目标条目；目标不可见时抛 404/6001
def update_goal(db_session: Session, owner_id: int, goal_id: int, payload: GoalUpdate) -> GoalItem:
    with db_session.begin():
        goal = owned_goal(db_session, owner_id, goal_id)
        for field_name, field_value in payload.model_dump(exclude_unset=True).items():
            setattr(goal, field_name, field_value)
        db_session.flush()
        key_results = db_session.scalars(
            select(PlanKeyResult).where(
                PlanKeyResult.goal_id == goal_id, owned_filter(PlanKeyResult, owner_id)
            ).order_by(PlanKeyResult.sort_order, PlanKeyResult.id)
        ).all()
        project_count = db_session.scalar(
            select(func.count()).select_from(PlanProject).where(
                PlanProject.goal_id == goal_id, owned_filter(PlanProject, owner_id)
            )
        )
    return build_goal_item(goal, key_results, project_count or 0)


# 做什么：软删除目标，并一并软删除它的关键结果
# 入参：独立写入会话、当前用户编号、目标编号
# 返回：无；目标不可见时抛 404/6001，事务内不落任何改动
def delete_goal(db_session: Session, owner_id: int, goal_id: int) -> None:
    with db_session.begin():
        goal = owned_goal(db_session, owner_id, goal_id)
        goal.is_deleted = IS_DELETED
        key_result_rows = db_session.scalars(
            select(PlanKeyResult).where(
                PlanKeyResult.goal_id == goal_id, owned_filter(PlanKeyResult, owner_id)
            )
        ).all()
        for row in key_result_rows:
            row.is_deleted = IS_DELETED


# 做什么：在指定目标下新增一条关键结果
# 入参：独立写入会话、当前用户编号、目标编号和请求
# 返回：新关键结果条目；目标不可见时抛 404/6001
def create_key_result(
    db_session: Session, owner_id: int, goal_id: int, payload: KeyResultCreate
) -> KeyResultItem:
    with db_session.begin():
        owned_goal(db_session, owner_id, goal_id)
        row = PlanKeyResult(
            owner_id=owner_id,
            goal_id=goal_id,
            title=payload.title,
            metric_unit=payload.metric_unit,
            start_value=payload.start_value,
            target_value=payload.target_value,
            current_value=payload.current_value,
            sort_order=payload.sort_order,
        )
        db_session.add(row)
        db_session.flush()
    return build_key_result_item(row)


# 做什么：修改关键结果上明确提交的字段
# 入参：独立写入会话、当前用户编号、关键结果编号和请求
# 返回：修改后的关键结果条目；不可见时抛 404/6002
def update_key_result(
    db_session: Session, owner_id: int, key_result_id: int, payload: KeyResultUpdate
) -> KeyResultItem:
    with db_session.begin():
        row = owned_key_result(db_session, owner_id, key_result_id)
        for field_name, field_value in payload.model_dump(exclude_unset=True).items():
            setattr(row, field_name, field_value)
        db_session.flush()
    return build_key_result_item(row)


# 做什么：软删除一条关键结果
# 入参：独立写入会话、当前用户编号、关键结果编号
# 返回：无；不可见时抛 404/6002
def delete_key_result(db_session: Session, owner_id: int, key_result_id: int) -> None:
    with db_session.begin():
        row = owned_key_result(db_session, owner_id, key_result_id)
        row.is_deleted = IS_DELETED
