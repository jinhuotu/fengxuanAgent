"""任务的增删改查；任务完成时回填完成时间。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import PlanDateRangeError
from app.core.plan import TaskStatus
from app.models.plan import IS_DELETED, PlanProject, PlanTask
from app.schemas.plan import TaskCreate, TaskItem, TaskListResponse, TaskUpdate
from app.services.plan_common import owned_filter, owned_project, owned_task, plan_now


# 做什么：校验任务的计划与截止日期
# 入参：开始日期（计划做）与截止日期
# 返回：无；截止早于计划时抛 400/6006
def check_task_dates(planned_date, due_date) -> None:
    if planned_date is not None and due_date is not None and due_date < planned_date:
        raise PlanDateRangeError()


# 做什么：把任务行算成公开字段
# 入参：任务模型和所属计划标题（可空）
# 返回：含计划标题的任务条目
def build_task_item(task: PlanTask, project_title: str | None) -> TaskItem:
    return TaskItem(
        id=task.id,
        project_id=task.project_id,
        project_title=project_title,
        title=task.title,
        note=task.note,
        status=task.status,
        priority=task.priority,
        planned_date=task.planned_date,
        due_date=task.due_date,
        done_at=task.done_at,
        actual_minutes=task.actual_minutes,
    )


# 做什么：按计划编号取标题，避免逐条查询
# 入参：数据库会话、当前用户编号、任务列表
# 返回：计划编号到标题的字典
def load_titles_for_tasks(db_session: Session, owner_id: int, tasks: list[PlanTask]) -> dict[int, str]:
    project_ids = {task.project_id for task in tasks if task.project_id is not None}
    if not project_ids:
        return {}
    rows = db_session.execute(
        select(PlanProject.id, PlanProject.title).where(
            PlanProject.id.in_(project_ids), owned_filter(PlanProject, owner_id)
        )
    ).all()
    return {project_id: title for project_id, title in rows}


# 做什么：列出本人的任务，可按计划或状态筛选
# 入参：请求级数据库会话、当前用户编号、计划编号（可空）、状态（可空）
# 返回：按状态、日期与编号排序的任务列表
def list_tasks(
    db_session: Session, owner_id: int, project_id: int | None, status: int | None
) -> TaskListResponse:
    statement = select(PlanTask).where(owned_filter(PlanTask, owner_id))
    if project_id is not None:
        statement = statement.where(PlanTask.project_id == project_id)
    if status is not None:
        statement = statement.where(PlanTask.status == status)
    task_rows = db_session.scalars(
        statement.order_by(PlanTask.status, PlanTask.planned_date, PlanTask.id.desc())
    ).all()
    titles = load_titles_for_tasks(db_session, owner_id, task_rows)
    return TaskListResponse(items=[build_task_item(row, titles.get(row.project_id)) for row in task_rows])


# 做什么：新建任务
# 入参：独立写入会话、当前用户编号和已校验的请求
# 返回：新任务条目；计划不可见抛 404/6003，日期倒置抛 400/6006
def create_task(db_session: Session, owner_id: int, payload: TaskCreate) -> TaskItem:
    with db_session.begin():
        check_task_dates(payload.planned_date, payload.due_date)
        project_title = None
        if payload.project_id is not None:
            project_title = owned_project(db_session, owner_id, payload.project_id).title
        task = PlanTask(
            owner_id=owner_id,
            project_id=payload.project_id,
            title=payload.title,
            note=payload.note,
            priority=payload.priority,
            planned_date=payload.planned_date,
            due_date=payload.due_date,
            estimate_minutes=payload.estimate_minutes,
        )
        db_session.add(task)
        db_session.flush()
    return build_task_item(task, project_title)


# 做什么：修改任务上明确提交的字段，完成时回填完成时间
# 入参：独立写入会话、当前用户编号、任务编号和请求
# 返回：修改后的任务条目；任务不可见抛 404/6004
def update_task(db_session: Session, owner_id: int, task_id: int, payload: TaskUpdate) -> TaskItem:
    changes = payload.model_dump(exclude_unset=True)
    with db_session.begin():
        task = owned_task(db_session, owner_id, task_id)
        planned_date = changes.get("planned_date", task.planned_date)
        due_date = changes.get("due_date", task.due_date)
        check_task_dates(planned_date, due_date)
        project_title = resolve_project_title(
            db_session, owner_id, changes.get("project_id", task.project_id)
        )
        for field_name, field_value in changes.items():
            setattr(task, field_name, field_value)
        apply_done_time(task)
        db_session.flush()
    return build_task_item(task, project_title)


# 做什么：按计划编号取标题，空编号表示任务不挂计划
# 入参：数据库会话、当前用户编号、计划编号（可空）
# 返回：计划标题；编号为空返回空，不可见抛 404/6003
def resolve_project_title(db_session: Session, owner_id: int, project_id: int | None) -> str | None:
    if project_id is None:
        return None
    return owned_project(db_session, owner_id, project_id).title


# 做什么：按当前状态维护完成时间
# 入参：任务模型（状态已就位）
# 返回：无；改成已完成且没记录时间时补当前时间，改回未完成时清空
def apply_done_time(task: PlanTask) -> None:
    if task.status == TaskStatus.DONE:
        if task.done_at is None:
            task.done_at = plan_now()
    else:
        task.done_at = None


# 做什么：软删除一条任务
# 入参：独立写入会话、当前用户编号、任务编号
# 返回：无；任务不可见抛 404/6004
def delete_task(db_session: Session, owner_id: int, task_id: int) -> None:
    with db_session.begin():
        task = owned_task(db_session, owner_id, task_id)
        task.is_deleted = IS_DELETED
