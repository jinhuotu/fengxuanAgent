"""每日记录：今天的填写、本周打卡与连续天数。"""
import json
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import PlanProjectNotFoundError
from app.core.plan import PlanKind, TaskStatus
from app.models.plan import PlanDailyLog, PlanTask
from app.schemas.plan import (
    DailyLogItem,
    DailyLogListResponse,
    DailyLogUpsert,
    DailyTodayResponse,
    TaskItem,
    WeekMark,
)
from app.services.plan_common import owned_filter, plan_today, WEEK_LENGTH_DAYS
from app.services.plan_projects import load_project_titles
from app.services.plan_tasks import build_task_item, load_titles_for_tasks

# 连续天数的回溯上限，避免异常数据把查询拖长
STREAK_MAX_DAYS = 400
TODAY_TASK_LIMIT = 12


# 做完什么：把每日记录行转成公开字段
# 入参：记录模型、学习计划标题字典
# 返回：含学习计划编号与标题的每日记录条目
def build_daily_item(row: PlanDailyLog, titles: dict[int, str]) -> DailyLogItem:
    project_ids = parse_project_ids(row.study_project_ids)
    return DailyLogItem(
        log_date=row.log_date,
        note=row.note or "",
        study_minutes=row.study_minutes,
        study_gain=row.study_gain,
        study_project_ids=project_ids,
        study_project_titles=[titles.get(project_id, "") for project_id in project_ids],
    )


# 做什么：把库里存的学习计划编号 JSON 读回列表
# 入参：存文本，可能为空或格式损坏
# 返回：整数编号列表；解析失败时返回空列表，不让坏数据打断页面
def parse_project_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    try:
        values = json.loads(raw)
    except ValueError:
        return []
    return [int(value) for value in values if isinstance(value, int)]


# 做什么：取本周一到今天所在周的七天日期
# 入参：今天
# 返回：周一为第一项的日期列表
def current_week_days(today: date) -> list[date]:
    monday = today - timedelta(days=today.weekday())
    return [monday + timedelta(days=offset) for offset in range(WEEK_LENGTH_DAYS)]


# 做什么：算从今天（今天没记录就从昨天）往前的连续打卡天数
# 入参：有记录的日期集合和今天
# 返回：连续天数，最多回溯 STREAK_MAX_DAYS 天
def calc_streak(logged_days: set[date], today: date) -> int:
    cursor = today if today in logged_days else today - timedelta(days=1)
    count = 0
    while count < STREAK_MAX_DAYS and cursor in logged_days:
        count += 1
        cursor -= timedelta(days=1)
    return count


# 做什么：查最近若干天的记录日期，供打卡与连续天数使用
# 入参：数据库会话、当前用户编号、最早日期
# 返回：有记录的日期集合
def load_logged_days(db_session: Session, owner_id: int, since: date) -> set[date]:
    rows = db_session.scalars(
        select(PlanDailyLog.log_date).where(
            owned_filter(PlanDailyLog, owner_id), PlanDailyLog.log_date >= since
        )
    ).all()
    return set(rows)


# 做什么：取今天该做的任务：今天计划做的、今天到期、或已逾期未完成
# 入参：数据库会话、当前用户编号、今天
# 返回：最多 TODAY_TASK_LIMIT 条任务条目
def load_today_tasks(db_session: Session, owner_id: int, today: date) -> list[TaskItem]:
    open_status = (TaskStatus.TODO, TaskStatus.DOING)
    task_rows = db_session.scalars(
        select(PlanTask)
        .where(
            owned_filter(PlanTask, owner_id),
            PlanTask.status.in_(open_status),
            (PlanTask.planned_date == today)
            | (PlanTask.due_date == today)
            | (PlanTask.due_date < today),
        )
        .order_by(PlanTask.priority.desc(), PlanTask.id)
        .limit(TODAY_TASK_LIMIT)
    ).all()
    titles = load_titles_for_tasks(db_session, owner_id, task_rows)
    return [build_task_item(row, titles.get(row.project_id)) for row in task_rows]


# 做什么：组装今天的填写页数据
# 入参：请求级数据库会话和当前用户编号
# 返回：今天的记录（可空）、今日任务、本周打卡、连续天数
def get_today(db_session: Session, owner_id: int) -> DailyTodayResponse:
    today = plan_today()
    week_days = current_week_days(today)
    logged_days = load_logged_days(db_session, owner_id, week_days[0] - timedelta(days=STREAK_MAX_DAYS))
    titles = load_project_titles(db_session, owner_id, PlanKind.STUDY)
    today_row = db_session.scalar(
        select(PlanDailyLog).where(owned_filter(PlanDailyLog, owner_id), PlanDailyLog.log_date == today)
    )
    return DailyTodayResponse(
        today=build_daily_item(today_row, titles) if today_row is not None else None,
        tasks=load_today_tasks(db_session, owner_id, today),
        week=[WeekMark(log_date=day, has_log=day in logged_days) for day in week_days],
        streak=calc_streak(logged_days, today),
    )


# 做什么：校验提交的学习计划编号确实属于本人的学习主题
# 入参：数据库会话、当前用户编号、计划编号列表
# 返回：编号到标题的字典；有不认识的编号时抛 404/6003
def check_study_projects(db_session: Session, owner_id: int, project_ids: list[int]) -> dict[int, str]:
    titles = load_project_titles(db_session, owner_id, PlanKind.STUDY)
    for project_id in project_ids:
        if project_id not in titles:
            raise PlanProjectNotFoundError("学习主题不存在")
    return titles


# 做什么：取最近几天的每日记录，供页面时间线展示
# 入参：数据库会话、当前用户编号、条数上限
# 返回：按日期倒序的记录列表
def list_recent_logs(db_session: Session, owner_id: int, limit: int) -> DailyLogListResponse:
    titles = load_project_titles(db_session, owner_id, PlanKind.STUDY)
    rows = db_session.scalars(
        select(PlanDailyLog)
        .where(owned_filter(PlanDailyLog, owner_id))
        .order_by(PlanDailyLog.log_date.desc())
        .limit(limit)
    ).all()
    return DailyLogListResponse(items=[build_daily_item(row, titles) for row in rows])


# 做什么：按日期写入或覆盖每日记录（同一天只保留一条）
# 入参：独立写入会话、当前用户编号、日期和已校验的请求
# 返回：写入后的每日记录条目
def upsert_daily(
    db_session: Session, owner_id: int, log_date: date, payload: DailyLogUpsert
) -> DailyLogItem:
    titles = {}
    with db_session.begin():
        titles = check_study_projects(db_session, owner_id, payload.study_project_ids)
        row = db_session.scalar(
            select(PlanDailyLog).where(
                owned_filter(PlanDailyLog, owner_id), PlanDailyLog.log_date == log_date
            )
        )
        if row is None:
            row = PlanDailyLog(owner_id=owner_id, log_date=log_date)
            db_session.add(row)
        row.note = payload.note
        row.study_minutes = payload.study_minutes
        row.study_gain = payload.study_gain
        row.study_project_ids = json.dumps(payload.study_project_ids, ensure_ascii=False)
        db_session.flush()
    return build_daily_item(row, titles)
