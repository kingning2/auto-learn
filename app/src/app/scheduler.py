# -*- coding: utf-8 -*-
"""周六上课时段调度器(常驻进程).

每周六北京时间四个上课时段:
    08:30 - 10:00
    10:10 - 11:40
    14:30 - 16:00
    16:10 - 17:30

在每个时段开始前 2 分钟启动一次完整流程(登录 -> 我的教务 -> 直播安排),
轮询等待房间出现, 房间出现后驻留观看到该时段结束; 时段结束关闭浏览器,
休眠等待下一个时段.

用法:
    uv run python -m app schedule
测试(不等到周六, 立即触发, 窗口 3 分钟):
    SDYXY_SCHEDULE_TEST=1 uv run python -m app schedule
"""
from __future__ import annotations

import os
import time
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

BEIJING = ZoneInfo("Asia/Shanghai")

# 周六四个上课时段(北京时间, 24 小时制)
CLASS_WINDOWS: tuple[tuple[dtime, dtime], ...] = (
    (dtime(8, 30), dtime(10, 0)),
    (dtime(10, 10), dtime(11, 40)),
    (dtime(14, 30), dtime(16, 0)),
    (dtime(16, 10), dtime(17, 30)),
)

SATURDAY = 5  # datetime.weekday(): 周一=0 … 周六=5 周日=6

# 上课前 2 分钟启动, 等待房间出现
LEAD_MINUTES = 2

# 睡眠上限(秒): 长时间休眠时分片睡, 便于打印状态和及时响应
SLEEP_CAP_S = 600

# 测试模式: 触发提前量与窗口时长
TEST_TRIGGER_DELAY_S = 15
TEST_WINDOW_S = 3 * 60


def next_window(now: datetime, after: datetime | None = None) -> tuple[datetime, datetime]:
    """下一个待执行窗口, 返回 (启动时刻, 观看截止时刻), 均为北京时间 aware dt.

    启动时刻 = 上课开始前 2 分钟; 截止时刻 = 上课结束(该时段 end).

    边界处理: 若触发时刻已过但还没到上课时间(如上一节拖堂吃掉间隔),
    立即返回该窗口 —— 迟到就位也强于跳过(进去看后半节).

    Args:
        after: 已消费窗口的截止时刻 —— 早收工后防止把同一个窗口再抬一遍;
        另外剩余不足 10 分钟的已开课窗口直接跳过(临近结束没意义).
    """
    day = now.date()
    for day_offset in range(0, 8):
        d = day + timedelta(days=day_offset)
        if d.weekday() != SATURDAY:
            continue
        for start, end in CLASS_WINDOWS:
            start_dt = datetime.combine(d, start, tzinfo=BEIJING)
            trigger = start_dt - timedelta(minutes=LEAD_MINUTES)
            deadline = datetime.combine(d, end, tzinfo=BEIJING)
            if after is not None and deadline <= after:
                continue
            if trigger > now:
                return trigger, deadline
            if now < start_dt:  # 触发点已过但课还没开始: 立即启动(迟到就位)
                print(f"[调度] 触发时刻 {trigger:%H:%M} 已过但课未开始, 立即就位!")
                return now, deadline
            # 课已开始但只剩不到 10 分钟: 加入观看没意义, 跳过避免临结束反复重入
            if now < deadline:
                if deadline - now < timedelta(minutes=10):
                    continue
                print(f"[调度] 课已开始({start_dt:%H:%M})尚未结束, 立即加入!")
                return now, deadline
    raise RuntimeError("8 天内找不到周六窗口, 不可能发生")


def _sleep_until(trigger: datetime, now: datetime) -> None:
    """分片睡眠到启动时刻, 期间每小时打印一次倒计时."""
    total = (trigger - now).total_seconds()
    print(f"[调度] 距下次启动还有 {total / 3600:.1f} 小时, 进入休眠…")
    while True:
        remaining = (trigger - datetime.now(BEIJING)).total_seconds()
        if remaining <= 0:
            return
        # 倒计时播报: 剩余 >1h 每小时一次, 1h~10min 每 10 分钟, <10min 每分钟
        if remaining > 3600:
            print(f"[调度] 距启动还有 {remaining / 3600:.1f} 小时…")
            time.sleep(min(remaining - 3600, SLEEP_CAP_S))
        elif remaining > 600:
            print(f"[调度] 距启动还有 {remaining / 60:.0f} 分钟…")
            time.sleep(min(remaining - 600, SLEEP_CAP_S))
        else:
            print(f"[调度] 距启动还有 {remaining:.0f} 秒, 准备启动流程…")
            time.sleep(min(remaining, SLEEP_CAP_S))


def _run_one_window(deadline: datetime) -> bool:
    """执行一个窗口: 登录 + 等房间 + 驻留观看到 deadline. 失败重试一次.

    Returns:
        True=成功看完; False=失败(含重试)."""
    from app.__main__ import run_once
    from core.notify import notify_error, notify_info

    try:
        run_once(watch_until=deadline)
        return True
    except Exception as exc:  # noqa: BLE001 —— 单窗口失败不能拖垮常驻调度
        print(f"[调度] 本窗口第一次执行失败: {exc}", flush=True)
        notify_error("本时段第一次执行失败, 准备重试", exc)
    # 距截止还剩 2 分钟以上才值得重试一次
    remaining = (deadline - datetime.now(BEIJING)).total_seconds()
    if remaining > 120:
        print(f"[调度] 距截止还有 {remaining / 60:.0f} 分钟, 60 秒后重试一次…")
        notify_info(f"本时段执行失败, 距下课还有 {remaining / 60:.0f} 分钟, 60 秒后重试一次")
        time.sleep(60)
        try:
            run_once(watch_until=deadline)
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[调度] 本窗口重试仍失败, 放弃该窗口: {exc}", flush=True)
            notify_error("本时段重试仍失败, 已放弃该窗口", exc)
    else:
        print("[调度] 距截止不足 2 分钟, 不再重试, 等待下一个窗口")
        notify_info("距下课不足 2 分钟, 不再重试, 等待下一个时段")
    return False


def main() -> None:
    print("=" * 60)
    print("[调度] 周六上课时段调度器已启动 (北京时间)")
    for i, (s, e) in enumerate(CLASS_WINDOWS, 1):
        print(f"       时段{i}: {s.strftime('%H:%M')} - {e.strftime('%H:%M')} (提前 {LEAD_MINUTES} 分钟启动)")
    print("=" * 60)

    test = os.getenv("SDYXY_SCHEDULE_TEST") == "1"
    if test:
        now = datetime.now(BEIJING)
        trigger = now + timedelta(seconds=TEST_TRIGGER_DELAY_S)
        deadline = trigger + timedelta(seconds=TEST_WINDOW_S)
        print(f"[调度][测试模式] {TEST_TRIGGER_DELAY_S} 秒后触发, 窗口 {TEST_WINDOW_S} 秒")
        time.sleep(TEST_TRIGGER_DELAY_S)
        _run_one_window(deadline)
        print("[调度][测试模式] 测试窗口执行完毕, 退出")
        return

    last_deadline = None  # 已消费窗口的截止时刻, 防止同一窗口重复加入
    while True:
        now = datetime.now(BEIJING)
        trigger, deadline = next_window(now, after=last_deadline)
        last_deadline = deadline
        print(
            f"[调度] 下一个窗口: {deadline:%Y-%m-%d %A} "
            f"{(deadline.strftime('%H:%M'))} 结束, 启动于 {trigger:%m-%d %H:%M}"
        )
        _sleep_until(trigger, datetime.now(BEIJING))
        print(f"[调度] === 窗口触发 {datetime.now(BEIJING):%m-%d %H:%M:%S}, 观看截止 {deadline:%H:%M} ===")
        ok = _run_one_window(deadline)
        print(f"[调度] === 窗口结束 {datetime.now(BEIJING):%m-%d %H:%M:%S} ===")
        if not ok:
            # 课中就位后若反复失败, 歇 3 分钟再评估, 避免快速重试循环
            print("[调度] 本窗口异常结束, 3 分钟后再评估下一个窗口…")
            time.sleep(180)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[调度] 手动停止")
