"""计划模块的请求、公开字段与视图响应。"""
from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.core.plan import (
    DAILY_GAIN_LENGTH,
    DAILY_NOTE_LENGTH,
    GOAL_DESCRIPTION_LENGTH,
    GOAL_PERIOD_LABEL_LENGTH,
    GOAL_TITLE_LENGTH,
    KEY_RESULT_TITLE_LENGTH,
    KEY_RESULT_UNIT_LENGTH,
    MAX_DAILY_STUDY_MINUTES,
    MAX_PROGRESS_PERCENT,
    MIN_PROGRESS_PERCENT,
    PROJECT_DESCRIPTION_LENGTH,
    PROJECT_TITLE_LENGTH,
    STUDY_ITEM_TITLE_LENGTH,
    TASK_NOTE_LENGTH,
    TASK_TITLE_LENGTH,
)

TITLE_TEXT = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
PERCENT_FIELD = Field(default=0, ge=MIN_PROGRESS_PERCENT, le=MAX_PROGRESS_PERCENT)


class GoalCreate(BaseModel):
    period_type: int = Field(default=2, ge=1, le=3, description="周期类型：一年，二季，三月")
    period_label: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=GOAL_PERIOD_LABEL_LENGTH)
    ] = Field(description="周期标签，例如 2026 Q4")
    title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=GOAL_TITLE_LENGTH)
    ] = Field(description="目标标题")
    description: str | None = Field(default=None, max_length=GOAL_DESCRIPTION_LENGTH, description="目标说明")
    progress_mode: int = Field(default=2, ge=1, le=2, description="进度来源：一手动，二按关键结果")
    manual_progress: int = PERCENT_FIELD


class GoalUpdate(BaseModel):
    title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=GOAL_TITLE_LENGTH)
    ] | None = None
    description: str | None = Field(default=None, max_length=GOAL_DESCRIPTION_LENGTH)
    period_type: int | None = Field(default=None, ge=1, le=3)
    period_label: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=GOAL_PERIOD_LABEL_LENGTH)
    ] | None = None
    status: int | None = Field(default=None, ge=1, le=3)
    progress_mode: int | None = Field(default=None, ge=1, le=2)
    manual_progress: int | None = Field(default=None, ge=MIN_PROGRESS_PERCENT, le=MAX_PROGRESS_PERCENT)


class KeyResultCreate(BaseModel):
    title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=KEY_RESULT_TITLE_LENGTH)
    ] = Field(description="关键结果描述")
    metric_unit: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=KEY_RESULT_UNIT_LENGTH)
    ] = Field(default="项", description="计量单位")
    start_value: float = Field(default=0, description="起始值")
    target_value: float = Field(description="目标值")
    current_value: float = Field(default=0, description="当前值")
    sort_order: int = Field(default=0, description="展示顺序")


class KeyResultUpdate(BaseModel):
    title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=KEY_RESULT_TITLE_LENGTH)
    ] | None = None
    metric_unit: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=KEY_RESULT_UNIT_LENGTH)
    ] | None = None
    start_value: float | None = None
    target_value: float | None = None
    current_value: float | None = None
    sort_order: int | None = None


class KeyResultItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    metric_unit: str
    start_value: float
    target_value: float
    current_value: float
    progress: int


class GoalItem(BaseModel):
    id: int
    period_type: int
    period_label: str
    title: str
    description: str | None
    status: int
    progress_mode: int
    manual_progress: int
    progress: int
    project_count: int
    key_results: list[KeyResultItem]


class GoalListResponse(BaseModel):
    items: list[GoalItem]


class ProjectCreate(BaseModel):
    kind: int = Field(default=1, ge=1, le=2, description="类型：一工作，二学习")
    title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=PROJECT_TITLE_LENGTH)
    ] = Field(description="计划标题")
    description: str | None = Field(default=None, max_length=PROJECT_DESCRIPTION_LENGTH)
    goal_id: int | None = Field(default=None, description="所属目标编号")
    start_date: date | None = None
    end_date: date | None = None
    status: int = Field(default=1, ge=1, le=5)


class ProjectUpdate(BaseModel):
    title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=PROJECT_TITLE_LENGTH)
    ] | None = None
    description: str | None = Field(default=None, max_length=PROJECT_DESCRIPTION_LENGTH)
    goal_id: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    status: int | None = Field(default=None, ge=1, le=5)


class ProjectItem(BaseModel):
    id: int
    kind: int
    title: str
    description: str | None
    goal_id: int | None
    start_date: date | None
    end_date: date | None
    status: int
    progress: int
    task_total: int
    task_done: int


class ProjectListResponse(BaseModel):
    items: list[ProjectItem]


class TaskCreate(BaseModel):
    project_id: int | None = Field(default=None, description="所属计划编号，可为空")
    title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TASK_TITLE_LENGTH)
    ] = Field(description="任务标题")
    note: str | None = Field(default=None, max_length=TASK_NOTE_LENGTH)
    priority: int = Field(default=2, ge=1, le=3)
    planned_date: date | None = Field(default=None, description="计划做的日期")
    due_date: date | None = Field(default=None, description="截止日期")
    estimate_minutes: int | None = Field(default=None, ge=0, le=MAX_DAILY_STUDY_MINUTES)


class TaskUpdate(BaseModel):
    title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TASK_TITLE_LENGTH)
    ] | None = None
    note: str | None = Field(default=None, max_length=TASK_NOTE_LENGTH)
    status: int | None = Field(default=None, ge=1, le=4)
    priority: int | None = Field(default=None, ge=1, le=3)
    project_id: int | None = None
    planned_date: date | None = None
    due_date: date | None = None
    actual_minutes: int | None = Field(default=None, ge=0, le=MAX_DAILY_STUDY_MINUTES)


class TaskItem(BaseModel):
    id: int
    project_id: int | None
    project_title: str | None
    title: str
    note: str | None
    status: int
    priority: int
    planned_date: date | None
    due_date: date | None
    done_at: datetime | None
    actual_minutes: int | None


class TaskListResponse(BaseModel):
    items: list[TaskItem]


class StudyItemCreate(BaseModel):
    project_id: int = Field(description="所属学习主题编号")
    title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=STUDY_ITEM_TITLE_LENGTH)
    ] = Field(description="知识点名称")
    mastery: int = Field(default=0, ge=0, le=5)


class StudyReviewRequest(BaseModel):
    remembered: bool = Field(description="是否掌握了")


class StudyItemRow(BaseModel):
    id: int
    project_id: int
    project_title: str
    title: str
    mastery: int
    review_stage: int
    next_review_date: date | None
    last_reviewed_at: datetime | None


class StudyProjectSummary(BaseModel):
    id: int
    title: str
    learned: int
    total: int
    next_review_date: date | None


class StudyOverview(BaseModel):
    review_today: list[StudyItemRow]
    projects: list[StudyProjectSummary]
    items: list[StudyItemRow]


class DailyLogUpsert(BaseModel):
    note: str = Field(default="", max_length=DAILY_NOTE_LENGTH, description="今天做了什么")
    study_minutes: int = Field(default=0, ge=0, le=MAX_DAILY_STUDY_MINUTES, description="学习时长分钟")
    study_gain: str | None = Field(default=None, max_length=DAILY_GAIN_LENGTH, description="一句话收获")
    study_project_ids: list[int] = Field(default_factory=list, description="今天学习的计划编号")


class DailyLogItem(BaseModel):
    log_date: date
    note: str
    study_minutes: int
    study_gain: str | None
    study_project_ids: list[int]
    study_project_titles: list[str]


class WeekMark(BaseModel):
    log_date: date
    has_log: bool


class DailyTodayResponse(BaseModel):
    today: DailyLogItem | None
    tasks: list[TaskItem]
    week: list[WeekMark]
    streak: int


class DailyLogListResponse(BaseModel):
    items: list[DailyLogItem]
