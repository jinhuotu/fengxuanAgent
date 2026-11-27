"""计划模块的常量与枚举；不写业务逻辑。"""
from enum import IntEnum

GOAL_TITLE_LENGTH = 80
GOAL_DESCRIPTION_LENGTH = 300
GOAL_PERIOD_LABEL_LENGTH = 20
KEY_RESULT_TITLE_LENGTH = 80
KEY_RESULT_UNIT_LENGTH = 20
PROJECT_TITLE_LENGTH = 80
PROJECT_DESCRIPTION_LENGTH = 300
TASK_TITLE_LENGTH = 120
TASK_NOTE_LENGTH = 300
STUDY_ITEM_TITLE_LENGTH = 120
DAILY_NOTE_LENGTH = 5000
DAILY_GAIN_LENGTH = 200
MAX_DAILY_STUDY_MINUTES = 1440
MIN_PROGRESS_PERCENT = 0
MAX_PROGRESS_PERCENT = 100
MAX_MASTERY_LEVEL = 5
# 复习档位的间隔天数：答对升一档，答错退一档
REVIEW_INTERVAL_DAYS = (1, 3, 7, 14, 30)


class GoalPeriod(IntEnum):
    """目标周期：年、季、月。"""

    YEAR = 1
    QUARTER = 2
    MONTH = 3


class GoalStatus(IntEnum):
    """目标状态：进行中、已完成、已放弃。"""

    ACTIVE = 1
    DONE = 2
    DROPPED = 3


class ProgressMode(IntEnum):
    """目标进度来源：手动填写或按关键结果自动计算。"""

    MANUAL = 1
    AUTO = 2


class PlanKind(IntEnum):
    """计划类型：工作项目或学习主题。"""

    WORK = 1
    STUDY = 2


class PlanStatus(IntEnum):
    """计划状态：未开始、进行中、已完成、已暂停、已放弃。"""

    NOT_STARTED = 1
    ACTIVE = 2
    DONE = 3
    PAUSED = 4
    DROPPED = 5


class TaskStatus(IntEnum):
    """任务状态：待办、进行中、已完成、已放弃。"""

    TODO = 1
    DOING = 2
    DONE = 3
    DROPPED = 4


class TaskPriority(IntEnum):
    """任务优先级：低、中、高。"""

    LOW = 1
    MEDIUM = 2
    HIGH = 3
