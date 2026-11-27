"""计划（工作项目 / 学习主题）的增删改查与进度汇总。"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import PlanDateRangeError
from app.core.plan import TaskStatus
from app.models.plan import IS_DELETED, PlanProject, PlanStudyItem, PlanTask
from app.schemas.plan import ProjectCreate, ProjectItem, ProjectListResponse, ProjectUpdate
from app.services.plan_common import owned_filter, owned_goal, owned_project
from app.services.plan_progress import project_percent


# 做什么：校验计划日期区间，并确认所属目标归属本人
# 入参：数据库会话、用户编号、目标编号（可空）、开始与结束日期（可空）
# 返回：无；区间倒置抛 400/6006，目标不可见抛 404/6001
def check_project_refs(
    db_session: Session, owner_id: int, goal_id: int | None, start_date, end_date
) -> None:
    if start_date is not None and end_date is not None and end_date < start_date:
        raise PlanDateRangeError()
    if goal_id is not None:
        owned_goal(db_session, owner_id, goal_id)


# 做什么：把计划行和任务统计算成公开字段
# 入参：计划模型、计入统计的任务总数、已完成任务数
# 返回：含进度的计划条目
def build_project_item(project: PlanProject, task_total: int, task_done: int) -> ProjectItem:
    return ProjectItem(
        id=project.id,
        kind=project.kind,
        title=project.title,
        description=project.description,
        goal_id=project.goal_id,
        start_date=project.start_date,
        end_date=project.end_date,
        status=project.status,
        progress=project_percent(task_total, task_done),
        task_total=task_total,
        task_done=task_done,
    )


# 做什么：统计每个计划的任务总数与已完成数，放弃的任务不计入分母
# 入参：请求级数据库会话和当前用户编号
# 返回：计划编号到（总数，完成数）的字典
def load_task_counts(db_session: Session, owner_id: int) -> dict[int, tuple[int, int]]:
    rows = db_session.execute(
        select(PlanTask.project_id, PlanTask.status, func.count())
        .where(owned_filter(PlanTask, owner_id), PlanTask.project_id.is_not(None))
        .group_by(PlanTask.project_id, PlanTask.status)
    ).all()
    counts: dict[int, tuple[int, int]] = {}
    for project_id, status, amount in rows:
        total, done = counts.get(project_id, (0, 0))
        if status == TaskStatus.DROPPED:
            counts[project_id] = (total, done)
        elif status == TaskStatus.DONE:
            counts[project_id] = (total + amount, done + amount)
        else:
            counts[project_id] = (total + amount, done)
    return counts


# 做什么：列出本人的计划，可按类型筛选
# 入参：请求级数据库会话、当前用户编号、计划类型（可空表示全部）
# 返回：按状态与编号排序的计划列表
def list_projects(db_session: Session, owner_id: int, kind: int | None) -> ProjectListResponse:
    statement = select(PlanProject).where(owned_filter(PlanProject, owner_id))
    if kind is not None:
        statement = statement.where(PlanProject.kind == kind)
    project_rows = db_session.scalars(statement.order_by(PlanProject.status, PlanProject.id.desc())).all()
    counts = load_task_counts(db_session, owner_id)
    items = [build_project_item(row, *counts.get(row.id, (0, 0))) for row in project_rows]
    return ProjectListResponse(items=items)


# 做什么：新建计划
# 入参：独立写入会话、当前用户编号和已校验的请求
# 返回：新计划条目；引用不合法时抛业务异常
def create_project(db_session: Session, owner_id: int, payload: ProjectCreate) -> ProjectItem:
    with db_session.begin():
        check_project_refs(db_session, owner_id, payload.goal_id, payload.start_date, payload.end_date)
        project = PlanProject(
            owner_id=owner_id,
            kind=payload.kind,
            title=payload.title,
            description=payload.description,
            goal_id=payload.goal_id,
            start_date=payload.start_date,
            end_date=payload.end_date,
            status=payload.status,
        )
        db_session.add(project)
        db_session.flush()
    return build_project_item(project, 0, 0)


# 做什么：修改计划上明确提交的字段
# 入参：独立写入会话、当前用户编号、计划编号和请求
# 返回：修改后的计划条目；计划不可见抛 404/6003
def update_project(
    db_session: Session, owner_id: int, project_id: int, payload: ProjectUpdate
) -> ProjectItem:
    changes = payload.model_dump(exclude_unset=True)
    with db_session.begin():
        project = owned_project(db_session, owner_id, project_id)
        goal_id = changes.get("goal_id", project.goal_id)
        start_date = changes.get("start_date", project.start_date)
        end_date = changes.get("end_date", project.end_date)
        check_project_refs(db_session, owner_id, goal_id, start_date, end_date)
        for field_name, field_value in changes.items():
            setattr(project, field_name, field_value)
        db_session.flush()
        counts = load_task_counts(db_session, owner_id)
    return build_project_item(project, *counts.get(project_id, (0, 0)))


# 做什么：软删除计划，并一并软删除它的任务与学习单元
# 入参：独立写入会话、当前用户编号、计划编号
# 返回：无；计划不可见抛 404/6003
def delete_project(db_session: Session, owner_id: int, project_id: int) -> None:
    with db_session.begin():
        project = owned_project(db_session, owner_id, project_id)
        project.is_deleted = IS_DELETED
        child_rows = db_session.scalars(
            select(PlanTask).where(PlanTask.project_id == project_id, owned_filter(PlanTask, owner_id))
        ).all()
        for row in child_rows:
            row.is_deleted = IS_DELETED
        study_rows = db_session.scalars(
            select(PlanStudyItem).where(
                PlanStudyItem.project_id == project_id, owned_filter(PlanStudyItem, owner_id)
            )
        ).all()
        for row in study_rows:
            row.is_deleted = IS_DELETED


# 做什么：取出某类型的计划编号集合，供学习与日报校验使用
# 入参：数据库会话、当前用户编号、计划类型
# 返回：该类型下所有未删除计划的编号到标题的字典
def load_project_titles(db_session: Session, owner_id: int, kind: int) -> dict[int, str]:
    rows = db_session.execute(
        select(PlanProject.id, PlanProject.title).where(
            owned_filter(PlanProject, owner_id), PlanProject.kind == kind
        )
    ).all()
    return {project_id: title for project_id, title in rows}
