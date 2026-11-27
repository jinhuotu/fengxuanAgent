"""学习主题下的知识点：掌握度、间隔复习档位与今日复习清单。"""
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import PlanProjectNotFoundError
from app.core.plan import MAX_MASTERY_LEVEL, PlanKind, REVIEW_INTERVAL_DAYS
from app.models.plan import IS_DELETED, PlanProject, PlanStudyItem
from app.schemas.plan import (
    StudyItemCreate,
    StudyItemRow,
    StudyOverview,
    StudyProjectSummary,
)
from app.services.plan_common import MASTERED_LEVEL, owned_filter, owned_project, owned_study_item, plan_now, plan_today

MIN_LEVEL = 0
REVIEW_TODAY_LIMIT = 30
# 学习视图一次返回的知识点上限；个人数据量小，仍保留上限避免异常数据拖慢页面
STUDY_ITEM_LIMIT = 200


# 做什么：把学习单元和所属计划标题转成公开字段
# 入参：学习单元模型、计划标题
# 返回：含复习档位的单元条目
def build_study_row(item: PlanStudyItem, project_title: str) -> StudyItemRow:
    return StudyItemRow(
        id=item.id,
        project_id=item.project_id,
        project_title=project_title,
        title=item.title,
        mastery=item.mastery,
        review_stage=item.review_stage,
        next_review_date=item.next_review_date,
        last_reviewed_at=item.last_reviewed_at,
    )


# 做什么：取本人学习主题下的全部知识点
# 入参：数据库会话、当前用户编号
# 返回：知识点条目列表；只含学习类型计划下的单元
def load_study_rows(db_session: Session, owner_id: int) -> list[StudyItemRow]:
    project_rows = db_session.execute(
        select(PlanProject.id, PlanProject.title).where(
            owned_filter(PlanProject, owner_id), PlanProject.kind == PlanKind.STUDY
        )
    ).all()
    titles = {project_id: title for project_id, title in project_rows}
    if not titles:
        return []
    item_rows = db_session.scalars(
        select(PlanStudyItem)
        .where(
            owned_filter(PlanStudyItem, owner_id), PlanStudyItem.project_id.in_(titles.keys())
        )
        .order_by(PlanStudyItem.next_review_date, PlanStudyItem.id)
    ).all()
    return [build_study_row(row, titles[row.project_id]) for row in item_rows]


# 做什么：按学习主题汇总已学会数量与下一次复习日期
# 入参：数据库会话、当前用户编号、该主题下的单元条目
# 返回：学习主题的进度摘要列表
def summarize_projects(db_session: Session, owner_id: int, rows: list[StudyItemRow]) -> list[StudyProjectSummary]:
    project_rows = db_session.execute(
        select(PlanProject.id, PlanProject.title)
        .where(owned_filter(PlanProject, owner_id), PlanProject.kind == PlanKind.STUDY)
        .order_by(PlanProject.id.desc())
    ).all()
    summaries = []
    for project_id, title in project_rows:
        mine = [row for row in rows if row.project_id == project_id]
        upcoming = [row.next_review_date for row in mine if row.next_review_date is not None]
        summaries.append(
            StudyProjectSummary(
                id=project_id,
                title=title,
                learned=sum(1 for row in mine if row.mastery >= MASTERED_LEVEL),
                total=len(mine),
                next_review_date=min(upcoming) if upcoming else None,
            )
        )
    return summaries


# 做什么：组装学习视图：今天要复习的单元、各主题进度和全部知识点
# 入参：请求级数据库会话和当前用户编号
# 返回：今日复习清单（最多 REVIEW_TODAY_LIMIT 条）、主题摘要与知识点（最多 STUDY_ITEM_LIMIT 条）
def get_study_overview(db_session: Session, owner_id: int) -> StudyOverview:
    today = plan_today()
    rows = load_study_rows(db_session, owner_id)
    due = [row for row in rows if row.next_review_date is not None and row.next_review_date <= today]
    return StudyOverview(
        review_today=due[:REVIEW_TODAY_LIMIT],
        projects=summarize_projects(db_session, owner_id, rows),
        items=rows[:STUDY_ITEM_LIMIT],
    )


# 做什么：在学习主题下新增知识点，首次复习排到明天
# 入参：独立写入会话、当前用户编号和已校验的请求
# 返回：新单元条目；计划不存在或不是学习类型时抛 404/6003
def create_study_item(db_session: Session, owner_id: int, payload: StudyItemCreate) -> StudyItemRow:
    with db_session.begin():
        project = owned_project(db_session, owner_id, payload.project_id)
        if project.kind != PlanKind.STUDY:
            raise PlanProjectNotFoundError("学习主题不存在")
        item = PlanStudyItem(
            owner_id=owner_id,
            project_id=payload.project_id,
            title=payload.title,
            mastery=payload.mastery,
            review_stage=MIN_LEVEL,
            next_review_date=plan_today() + timedelta(days=REVIEW_INTERVAL_DAYS[0]),
        )
        db_session.add(item)
        db_session.flush()
    return build_study_row(item, project.title)


# 做什么：按是否掌握升降复习档位与掌握度，并排下一次复习
# 入参：独立写入会话、当前用户编号、单元编号、是否掌握
# 返回：更新后的单元条目；单元不可见抛 404/6005
def review_study_item(
    db_session: Session, owner_id: int, item_id: int, remembered: bool
) -> StudyItemRow:
    with db_session.begin():
        item = owned_study_item(db_session, owner_id, item_id)
        step = 1 if remembered else -1
        item.review_stage = clamp_level(item.review_stage + step, len(REVIEW_INTERVAL_DAYS) - 1)
        item.mastery = clamp_level(item.mastery + step, MAX_MASTERY_LEVEL)
        item.last_reviewed_at = plan_now()
        item.next_review_date = plan_today() + timedelta(days=REVIEW_INTERVAL_DAYS[item.review_stage])
        db_session.flush()
        project = owned_project(db_session, owner_id, item.project_id)
    return build_study_row(item, project.title)


# 做什么：把档位夹在允许范围内
# 入参：目标档位和上限
# 返回：不小于零且不超过上限的整数
def clamp_level(value: int, upper: int) -> int:
    return max(MIN_LEVEL, min(upper, value))


# 做什么：软删除一个学习单元
# 入参：独立写入会话、当前用户编号、单元编号
# 返回：无；单元不可见抛 404/6005
def delete_study_item(db_session: Session, owner_id: int, item_id: int) -> None:
    with db_session.begin():
        item = owned_study_item(db_session, owner_id, item_id)
        item.is_deleted = IS_DELETED
