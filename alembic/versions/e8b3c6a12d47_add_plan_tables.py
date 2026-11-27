"""新增工作进度平台的六张表：目标、关键结果、计划、任务、学习单元、每日记录。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "e8b3c6a12d47"
down_revision = "c1a7e3d5b9f2"
branch_labels = None
depends_on = None

IDENTIFIER_TYPE = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
FLAG_TYPE = mysql.TINYINT().with_variant(sa.Integer(), "sqlite")
OWNER_COMMENT = "归属用户编号，由服务校验，不建物理外键"


# 做什么：按模型定义建出计划模块的六张表与索引
# 入参：无，使用 Alembic 当前迁移连接
# 返回：无；建表失败由 Alembic 报错
def upgrade() -> None:
    op.create_table(
        "plan_goals",
        sa.Column("id", IDENTIFIER_TYPE, primary_key=True, autoincrement=True, comment="目标编号，自增"),
        sa.Column("owner_id", sa.BigInteger(), nullable=False, comment=OWNER_COMMENT),
        sa.Column("period_type", FLAG_TYPE, nullable=False, comment="周期类型：一按年，二按季，三按月"),
        sa.Column("period_label", sa.String(20), nullable=False, comment="周期标签，例如 2026 Q4"),
        sa.Column("title", sa.String(80), nullable=False, comment="目标标题，说清要达成什么"),
        sa.Column("description", sa.String(300), comment="目标说明，可为空"),
        sa.Column("status", FLAG_TYPE, nullable=False, server_default=sa.text("1"), comment="状态：一进行中，二已完成，三已放弃"),
        sa.Column("progress_mode", FLAG_TYPE, nullable=False, server_default=sa.text("2"), comment="进度来源：一手动填写，二按关键结果算"),
        sa.Column("manual_progress", sa.Integer(), nullable=False, server_default=sa.text("0"), comment="手动进度百分比，仅在手动模式下使用"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"), comment="创建时间，东八区"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"), comment="更新时间，东八区"),
        sa.Column("is_deleted", FLAG_TYPE, nullable=False, server_default=sa.text("0"), comment="软删除标记，零正常"),
        comment="计划目标：一个周期内要达成的结果，进度可手动填或按关键结果算",
    )
    op.create_index("idx_plan_goals_owner", "plan_goals", ["owner_id", "is_deleted", "period_type", "id"])

    op.create_table(
        "plan_key_results",
        sa.Column("id", IDENTIFIER_TYPE, primary_key=True, autoincrement=True, comment="关键结果编号，自增"),
        sa.Column("owner_id", sa.BigInteger(), nullable=False, comment=OWNER_COMMENT),
        sa.Column("goal_id", sa.BigInteger(), nullable=False, comment="所属目标编号，由服务校验归属"),
        sa.Column("title", sa.String(80), nullable=False, comment="关键结果描述"),
        sa.Column("metric_unit", sa.String(20), nullable=False, comment="计量单位，例如 篇、小时、%"),
        sa.Column("start_value", sa.Numeric(12, 2), nullable=False, server_default=sa.text("0"), comment="起始值"),
        sa.Column("target_value", sa.Numeric(12, 2), nullable=False, server_default=sa.text("0"), comment="目标值"),
        sa.Column("current_value", sa.Numeric(12, 2), nullable=False, server_default=sa.text("0"), comment="当前值，手工或后续自动更新"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0"), comment="同一目标内的展示顺序"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"), comment="创建时间，东八区"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"), comment="更新时间，东八区"),
        sa.Column("is_deleted", FLAG_TYPE, nullable=False, server_default=sa.text("0"), comment="软删除标记，零正常"),
        comment="关键结果：目标下的可量化指标，用数值判断目标是否达成",
    )
    op.create_index("idx_plan_key_results_goal", "plan_key_results", ["goal_id", "is_deleted", "sort_order", "id"])

    op.create_table(
        "plan_projects",
        sa.Column("id", IDENTIFIER_TYPE, primary_key=True, autoincrement=True, comment="计划编号，自增"),
        sa.Column("owner_id", sa.BigInteger(), nullable=False, comment=OWNER_COMMENT),
        sa.Column("goal_id", sa.BigInteger(), comment="所属目标编号，可为空表示独立计划"),
        sa.Column("kind", FLAG_TYPE, nullable=False, server_default=sa.text("1"), comment="类型：一工作，二学习"),
        sa.Column("title", sa.String(80), nullable=False, comment="计划标题"),
        sa.Column("description", sa.String(300), comment="计划说明，可为空"),
        sa.Column("start_date", sa.Date(), comment="开始日期，可为空"),
        sa.Column("end_date", sa.Date(), comment="截止日期，可为空"),
        sa.Column("status", FLAG_TYPE, nullable=False, server_default=sa.text("1"), comment="状态：一未开始，二进行中，三已完成，四已暂停，五已放弃"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"), comment="创建时间，东八区"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"), comment="更新时间，东八区"),
        sa.Column("is_deleted", FLAG_TYPE, nullable=False, server_default=sa.text("0"), comment="软删除标记，零正常"),
        comment="计划：工作项目或学习主题，是目标和任务之间的那一层",
    )
    op.create_index("idx_plan_projects_owner", "plan_projects", ["owner_id", "is_deleted", "kind", "id"])

    op.create_table(
        "plan_tasks",
        sa.Column("id", IDENTIFIER_TYPE, primary_key=True, autoincrement=True, comment="任务编号，自增"),
        sa.Column("owner_id", sa.BigInteger(), nullable=False, comment=OWNER_COMMENT),
        sa.Column("project_id", sa.BigInteger(), comment="所属计划编号，可为空表示独立待办"),
        sa.Column("title", sa.String(120), nullable=False, comment="任务标题"),
        sa.Column("note", sa.String(300), comment="备注，可为空"),
        sa.Column("status", FLAG_TYPE, nullable=False, server_default=sa.text("1"), comment="状态：一待办，二进行中，三已完成，四已放弃"),
        sa.Column("priority", FLAG_TYPE, nullable=False, server_default=sa.text("2"), comment="优先级：一低，二中，三高"),
        sa.Column("planned_date", sa.Date(), comment="计划做的日期，可为空"),
        sa.Column("due_date", sa.Date(), comment="截止日期，可为空"),
        sa.Column("done_at", sa.DateTime(), comment="完成时间，未完成为空"),
        sa.Column("estimate_minutes", sa.Integer(), comment="预计耗时分钟，可为空"),
        sa.Column("actual_minutes", sa.Integer(), comment="实际耗时分钟，可为空"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0"), comment="同组内的展示顺序"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"), comment="创建时间，东八区"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"), comment="更新时间，东八区"),
        sa.Column("is_deleted", FLAG_TYPE, nullable=False, server_default=sa.text("0"), comment="软删除标记，零正常"),
        comment="任务：可执行的最小事项，归属某计划或独立存在",
    )
    op.create_index("idx_plan_tasks_owner", "plan_tasks", ["owner_id", "is_deleted", "status", "sort_order", "id"])
    op.create_index("idx_plan_tasks_project", "plan_tasks", ["project_id", "is_deleted", "status", "id"])
    op.create_index("idx_plan_tasks_due", "plan_tasks", ["owner_id", "is_deleted", "due_date"])

    op.create_table(
        "plan_study_items",
        sa.Column("id", IDENTIFIER_TYPE, primary_key=True, autoincrement=True, comment="学习单元编号，自增"),
        sa.Column("owner_id", sa.BigInteger(), nullable=False, comment=OWNER_COMMENT),
        sa.Column("project_id", sa.BigInteger(), nullable=False, comment="所属学习主题编号，由服务校验归属"),
        sa.Column("title", sa.String(120), nullable=False, comment="知识点名称"),
        sa.Column("mastery", FLAG_TYPE, nullable=False, server_default=sa.text("0"), comment="掌握程度：零到五，越大越熟"),
        sa.Column("review_stage", FLAG_TYPE, nullable=False, server_default=sa.text("0"), comment="复习档位：零到四，对应一天到三十天的间隔"),
        sa.Column("next_review_date", sa.Date(), comment="下次复习日期，为空表示待首次安排"),
        sa.Column("last_reviewed_at", sa.DateTime(), comment="上次复习时间，未复习为空"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"), comment="创建时间，东八区"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"), comment="更新时间，东八区"),
        sa.Column("is_deleted", FLAG_TYPE, nullable=False, server_default=sa.text("0"), comment="软删除标记，零正常"),
        comment="学习单元：按间隔重复排期复习的知识点",
    )
    op.create_index("idx_plan_study_items_owner", "plan_study_items", ["owner_id", "is_deleted", "next_review_date", "id"])
    op.create_index("idx_plan_study_items_project", "plan_study_items", ["project_id", "is_deleted", "id"])

    op.create_table(
        "plan_daily_logs",
        sa.Column("id", IDENTIFIER_TYPE, primary_key=True, autoincrement=True, comment="记录编号，自增"),
        sa.Column("owner_id", sa.BigInteger(), nullable=False, comment=OWNER_COMMENT),
        sa.Column("log_date", sa.Date(), nullable=False, comment="记录日期，一个人一天一条"),
        sa.Column("note", sa.Text(), nullable=False, comment="今天做了什么"),
        sa.Column("study_minutes", sa.Integer(), nullable=False, server_default=sa.text("0"), comment="学习时长分钟"),
        sa.Column("study_gain", sa.String(200), comment="一句话收获，可为空"),
        sa.Column("study_project_ids", sa.Text(), comment="今天学习的计划编号 JSON 数组，空表示未选"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"), comment="创建时间，东八区"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"), comment="更新时间，东八区"),
        sa.Column("is_deleted", FLAG_TYPE, nullable=False, server_default=sa.text("0"), comment="软删除标记，零正常"),
        sa.UniqueConstraint("owner_id", "log_date", name="uk_plan_daily_logs_owner_date"),
        comment="每日记录：一个人一天一条，记录做了什么、学了什么",
    )
    op.create_index("idx_plan_daily_logs_owner", "plan_daily_logs", ["owner_id", "is_deleted", "log_date"])


# 做什么：按依赖倒序删掉本次新增的六张表
# 入参：无，使用 Alembic 当前迁移连接
# 返回：无；回退失败由 Alembic 报错
def downgrade() -> None:
    op.drop_table("plan_daily_logs")
    op.drop_table("plan_study_items")
    op.drop_table("plan_tasks")
    op.drop_table("plan_projects")
    op.drop_table("plan_key_results")
    op.drop_table("plan_goals")
