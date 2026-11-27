"""进度计算：纯函数，不查库、不抛业务异常。"""
from app.core.plan import (
    MAX_PROGRESS_PERCENT,
    MIN_PROGRESS_PERCENT,
    ProgressMode,
)


# 做什么：把任意比例收进零到一百的整数
# 入参：原始比例，可为负数或超过一
# 返回：零到一百之间的整数百分比
def clamp_percent(ratio: float) -> int:
    percent = round(ratio * MAX_PROGRESS_PERCENT)
    return max(MIN_PROGRESS_PERCENT, min(MAX_PROGRESS_PERCENT, percent))


# 做什么：按起始值、目标值和当前值算关键结果的达成度
# 入参：三个数值，数值型均可
# 返回：零到一百的整数；目标与起始相同时，达到即一百，未达到为零
def key_result_percent(start_value: float, target_value: float, current_value: float) -> int:
    span = float(target_value) - float(start_value)
    if span == 0:
        return MAX_PROGRESS_PERCENT if current_value >= target_value else MIN_PROGRESS_PERCENT
    return clamp_percent((float(current_value) - float(start_value)) / span)


# 做什么：按进度来源算目标进度
# 入参：进度来源、手动百分比、各关键结果的百分比
# 返回：零到一百的整数；自动模式下没有关键结果时退回手动值
def goal_percent(progress_mode: int, manual_progress: int, key_result_percents: list[int]) -> int:
    if progress_mode == ProgressMode.MANUAL or not key_result_percents:
        return max(MIN_PROGRESS_PERCENT, min(MAX_PROGRESS_PERCENT, manual_progress))
    average = sum(key_result_percents) / len(key_result_percents)
    return clamp_percent(average / MAX_PROGRESS_PERCENT)


# 做什么：按任务的完成数算计划进度
# 入参：计入统计的任务总数与已完成数
# 返回：零到一百的整数；没有任务时为零
def project_percent(task_total: int, task_done: int) -> int:
    if task_total <= 0:
        return MIN_PROGRESS_PERCENT
    return clamp_percent(task_done / task_total)
