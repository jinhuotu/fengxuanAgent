"""工作进度平台的表结构：目标、关键结果、计划、任务、学习单元、每日记录。"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.plan import (
    DAILY_GAIN_LENGTH,
    GOAL_DESCRIPTION_LENGTH,
    GOAL_PERIOD_LABEL_LENGTH,
    GOAL_TITLE_LENGTH,
    KEY_RESULT_TITLE_LENGTH,
    KEY_RESULT_UNIT_LENGTH,
    PROJECT_DESCRIPTION_LENGTH,
    PROJECT_TITLE_LENGTH,
    STUDY_ITEM_TITLE_LENGTH,
    TASK_NOTE_LENGTH,
    TASK_TITLE_LENGTH,
)
from app.db.base import Base

# 新手注意：类型变体像两套编号模板，MySQL 用 BIGINT，SQLite 测试用 INTEGER 自增。
IDENTIFIER_TYPE = BigInteger().with_variant(Integer, "sqlite")
FLAG_TYPE = TINYINT().with_variant(Integer, "sqlite")
IS_NOT_DELETED = 0
IS_DELETED = 1
OWNER_COMMENT = "归属用户编号，由服务校验，不建物理外键"


class PlanGoal(Base):
    __tablename__ = "plan_goals"
    __table_args__ = (
        Index("idx_plan_goals_owner", "owner_id", "is_deleted", "period_type", "id"),
        {"comment": "计划目标：一个周期内要达成的结果，进度可手动填或按关键结果算"},
    )

    id: Mapped[int] = mapped_column(IDENTIFIER_TYPE, primary_key=True, autoincrement=True, comment="目标编号，自增")
    owner_id: Mapped[int] = mapped_column(BigInteger, comment=OWNER_COMMENT)
    period_type: Mapped[int] = mapped_column(FLAG_TYPE, comment="周期类型：一按年，二按季，三按月")
    period_label: Mapped[str] = mapped_column(
        String(GOAL_PERIOD_LABEL_LENGTH), comment="周期标签，例如 2026 Q4"
    )
    title: Mapped[str] = mapped_column(String(GOAL_TITLE_LENGTH), comment="目标标题，说清要达成什么")
    description: Mapped[str | None] = mapped_column(
        String(GOAL_DESCRIPTION_LENGTH), comment="目标说明，可为空"
    )
    status: Mapped[int] = mapped_column(FLAG_TYPE, default=1, server_default="1",
                                        comment="状态：一进行中，二已完成，三已放弃")
    progress_mode: Mapped[int] = mapped_column(FLAG_TYPE, default=2, server_default="2",
                                               comment="进度来源：一手动填写，二按关键结果算")
    manual_progress: Mapped[int] = mapped_column(Integer, default=0, server_default="0",
                                                comment="手动进度百分比，仅在手动模式下使用")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="创建时间，东八区")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间，东八区"
    )
    is_deleted: Mapped[int] = mapped_column(FLAG_TYPE, default=IS_NOT_DELETED,
                                            server_default=str(IS_NOT_DELETED), comment="软删除标记，零正常")


class PlanKeyResult(Base):
    __tablename__ = "plan_key_results"
    __table_args__ = (
        Index("idx_plan_key_results_goal", "goal_id", "is_deleted", "sort_order", "id"),
        {"comment": "关键结果：目标下的可量化指标，用数值判断目标是否达成"},
    )

    id: Mapped[int] = mapped_column(IDENTIFIER_TYPE, primary_key=True, autoincrement=True, comment="关键结果编号，自增")
    owner_id: Mapped[int] = mapped_column(BigInteger, comment=OWNER_COMMENT)
    goal_id: Mapped[int] = mapped_column(BigInteger, comment="所属目标编号，由服务校验归属")
    title: Mapped[str] = mapped_column(String(KEY_RESULT_TITLE_LENGTH), comment="关键结果描述")
    metric_unit: Mapped[str] = mapped_column(String(KEY_RESULT_UNIT_LENGTH), comment="计量单位，例如 篇、小时、%")
    start_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0", comment="起始值")
    target_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0", comment="目标值")
    current_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0", comment="当前值，手工或后续自动更新")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0", comment="同一目标内的展示顺序")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="创建时间，东八区")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间，东八区"
    )
    is_deleted: Mapped[int] = mapped_column(FLAG_TYPE, default=IS_NOT_DELETED,
                                            server_default=str(IS_NOT_DELETED), comment="软删除标记，零正常")


class PlanProject(Base):
    __tablename__ = "plan_projects"
    __table_args__ = (
        Index("idx_plan_projects_owner", "owner_id", "is_deleted", "kind", "id"),
        {"comment": "计划：工作项目或学习主题，是目标和任务之间的那一层"},
    )

    id: Mapped[int] = mapped_column(IDENTIFIER_TYPE, primary_key=True, autoincrement=True, comment="计划编号，自增")
    owner_id: Mapped[int] = mapped_column(BigInteger, comment=OWNER_COMMENT)
    goal_id: Mapped[int | None] = mapped_column(BigInteger, comment="所属目标编号，可为空表示独立计划")
    kind: Mapped[int] = mapped_column(FLAG_TYPE, default=1, server_default="1", comment="类型：一工作，二学习")
    title: Mapped[str] = mapped_column(String(PROJECT_TITLE_LENGTH), comment="计划标题")
    description: Mapped[str | None] = mapped_column(
        String(PROJECT_DESCRIPTION_LENGTH), comment="计划说明，可为空"
    )
    start_date: Mapped[date | None] = mapped_column(Date, comment="开始日期，可为空")
    end_date: Mapped[date | None] = mapped_column(Date, comment="截止日期，可为空")
    status: Mapped[int] = mapped_column(FLAG_TYPE, default=1, server_default="1",
                                        comment="状态：一未开始，二进行中，三已完成，四已暂停，五已放弃")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="创建时间，东八区")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间，东八区"
    )
    is_deleted: Mapped[int] = mapped_column(FLAG_TYPE, default=IS_NOT_DELETED,
                                            server_default=str(IS_NOT_DELETED), comment="软删除标记，零正常")


class PlanTask(Base):
    __tablename__ = "plan_tasks"
    __table_args__ = (
        Index("idx_plan_tasks_owner", "owner_id", "is_deleted", "status", "sort_order", "id"),
        Index("idx_plan_tasks_project", "project_id", "is_deleted", "status", "id"),
        Index("idx_plan_tasks_due", "owner_id", "is_deleted", "due_date"),
        {"comment": "任务：可执行的最小事项，归属某计划或独立存在"},
    )

    id: Mapped[int] = mapped_column(IDENTIFIER_TYPE, primary_key=True, autoincrement=True, comment="任务编号，自增")
    owner_id: Mapped[int] = mapped_column(BigInteger, comment=OWNER_COMMENT)
    project_id: Mapped[int | None] = mapped_column(BigInteger, comment="所属计划编号，可为空表示独立待办")
    title: Mapped[str] = mapped_column(String(TASK_TITLE_LENGTH), comment="任务标题")
    note: Mapped[str | None] = mapped_column(String(TASK_NOTE_LENGTH), comment="备注，可为空")
    status: Mapped[int] = mapped_column(FLAG_TYPE, default=1, server_default="1",
                                        comment="状态：一待办，二进行中，三已完成，四已放弃")
    priority: Mapped[int] = mapped_column(FLAG_TYPE, default=2, server_default="2", comment="优先级：一低，二中，三高")
    planned_date: Mapped[date | None] = mapped_column(Date, comment="计划做的日期，可为空")
    due_date: Mapped[date | None] = mapped_column(Date, comment="截止日期，可为空")
    done_at: Mapped[datetime | None] = mapped_column(DateTime, comment="完成时间，未完成为空")
    estimate_minutes: Mapped[int | None] = mapped_column(Integer, comment="预计耗时分钟，可为空")
    actual_minutes: Mapped[int | None] = mapped_column(Integer, comment="实际耗时分钟，可为空")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0", comment="同组内的展示顺序")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="创建时间，东八区")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间，东八区"
    )
    is_deleted: Mapped[int] = mapped_column(FLAG_TYPE, default=IS_NOT_DELETED,
                                            server_default=str(IS_NOT_DELETED), comment="软删除标记，零正常")


class PlanStudyItem(Base):
    __tablename__ = "plan_study_items"
    __table_args__ = (
        Index("idx_plan_study_items_owner", "owner_id", "is_deleted", "next_review_date", "id"),
        Index("idx_plan_study_items_project", "project_id", "is_deleted", "id"),
        {"comment": "学习单元：按间隔重复排期复习的知识点"},
    )

    id: Mapped[int] = mapped_column(IDENTIFIER_TYPE, primary_key=True, autoincrement=True, comment="学习单元编号，自增")
    owner_id: Mapped[int] = mapped_column(BigInteger, comment=OWNER_COMMENT)
    project_id: Mapped[int] = mapped_column(BigInteger, comment="所属学习主题编号，由服务校验归属")
    title: Mapped[str] = mapped_column(String(STUDY_ITEM_TITLE_LENGTH), comment="知识点名称")
    mastery: Mapped[int] = mapped_column(FLAG_TYPE, default=0, server_default="0",
                                         comment="掌握程度：零到五，越大越熟")
    review_stage: Mapped[int] = mapped_column(FLAG_TYPE, default=0, server_default="0",
                                              comment="复习档位：零到四，对应一天到三十天的间隔")
    next_review_date: Mapped[date | None] = mapped_column(Date, comment="下次复习日期，为空表示待首次安排")
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, comment="上次复习时间，未复习为空")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="创建时间，东八区")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间，东八区"
    )
    is_deleted: Mapped[int] = mapped_column(FLAG_TYPE, default=IS_NOT_DELETED,
                                            server_default=str(IS_NOT_DELETED), comment="软删除标记，零正常")


class PlanDailyLog(Base):
    __tablename__ = "plan_daily_logs"
    __table_args__ = (
        UniqueConstraint("owner_id", "log_date", name="uk_plan_daily_logs_owner_date"),
        Index("idx_plan_daily_logs_owner", "owner_id", "is_deleted", "log_date"),
        {"comment": "每日记录：一个人一天一条，记录做了什么、学了什么"},
    )

    id: Mapped[int] = mapped_column(IDENTIFIER_TYPE, primary_key=True, autoincrement=True, comment="记录编号，自增")
    owner_id: Mapped[int] = mapped_column(BigInteger, comment=OWNER_COMMENT)
    log_date: Mapped[date] = mapped_column(Date, comment="记录日期，一个人一天一条")
    note: Mapped[str] = mapped_column(Text, default="", comment="今天做了什么")
    study_minutes: Mapped[int] = mapped_column(Integer, default=0, server_default="0", comment="学习时长分钟")
    study_gain: Mapped[str | None] = mapped_column(
        String(DAILY_GAIN_LENGTH), comment="一句话收获，可为空"
    )
    study_project_ids: Mapped[str | None] = mapped_column(
        Text, comment="今天学习的计划编号 JSON 数组，空表示未选"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="创建时间，东八区")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间，东八区"
    )
    is_deleted: Mapped[int] = mapped_column(FLAG_TYPE, default=IS_NOT_DELETED,
                                            server_default=str(IS_NOT_DELETED), comment="软删除标记，零正常")
