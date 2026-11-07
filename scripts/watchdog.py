# -*- coding: utf-8 -*-
"""常驻看门狗: 保证周六定时调度器(scheduler.py)一直活着, 电脑不休眠.

由 Windows 计划任务开机/登录启动, 每 5 分钟被拉起一次, 做:
  1. 防休眠: 快到周六上课时段时临时禁止系统睡眠(SetThreadExecutionState),
     窗口结束后恢复; 其余时间不干预系统电源设置.
  2. 进程保活: 检测 scheduler.py 是否在跑, 没跑且在启动窗口内则拉起.

设计为被 pythonw.exe 静默运行(无控制台窗口), 日志写 output/watchdog.log.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import time
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path

# 北京时间 = 固定 UTC+8(无夏令时, 不依赖 tzdata)
BEIJING = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "output" / "watchdog.log"
SCHED_LOG = ROOT / "output" / "scheduler.log"  # 调度器 stdout/stderr 落这里

# 与 scheduler.CLASS_WINDOWS 保持一致: (启动提前2分钟, 结束) 的北京时间
LEAD = timedelta(minutes=2)
WINDOWS = (
    (dtime(8, 30), dtime(10, 0)),
    (dtime(10, 10), dtime(11, 40)),
    (dtime(14, 30), dtime(16, 0)),
    (dtime(16, 10), dtime(17, 30)),
)
SATURDAY = 5

ES_CONTINUOUS = 0x8000_0000
ES_SYSTEM_REQUIRED = 0x0000_0001


def log(msg: str) -> None:
    line = f"{datetime.now(BEIJING):%Y-%m-%d %H:%M:%S} {msg}"
    try:
        LOG.parent.mkdir(exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def current_window(now: datetime) -> tuple[datetime, datetime] | None:
    """若 now 处于某窗口的 [启动-2min, 结束+10min] 内, 返回该窗口的起止."""
    for start, end in WINDOWS:
        s = datetime.combine(now.date(), start, tzinfo=BEIJING) - LEAD
        e = datetime.combine(now.date(), end, tzinfo=BEIJING) + timedelta(minutes=10)
        if s <= now <= e and now.weekday() == SATURDAY:
            return datetime.combine(now.date(), start, tzinfo=BEIJING), e
    return None


VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"


def scheduler_running() -> bool:
    """用 wmic 检测是否有 scheduler.py 的 python 进程在跑."""
    try:
        out = subprocess.run(
            ["wmic", "process", "where", "name like 'python%'", "get", "commandline"],
            capture_output=True, text=True, timeout=20,
        ).stdout.lower()
        return "scheduler.py" in out and "watchdog" not in out.split("scheduler.py")[0][-80:]
    except Exception:
        return False


def start_scheduler() -> None:
    log("调度器未运行, 静默拉起 scheduler.py…")
    # 必须用项目 venv 的 python(调度器依赖 dotenv/invisible_playwright 等项目依赖)
    # 静默(CREATE_NO_WINDOW 不弹命令行), 输出重定向到 output/scheduler.log
    SCHED_LOG.parent.mkdir(exist_ok=True)
    out = SCHED_LOG.open("a", encoding="utf-8")
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"}  # 立即落盘 + UTF-8 控制台(emoji/中文不炸)
    subprocess.Popen(
        [str(VENV_PY), str(ROOT / "app" / "src" / "app" / "scheduler.py")],
        cwd=str(ROOT),
        stdout=out,
        stderr=subprocess.STDOUT,
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def main() -> None:
    now = datetime.now(BEIJING)
    log(f"看门狗触发 (周{'一二三四五六日'[now.weekday()]} {now:%H:%M})")
    win = current_window(now)

    # 1. 防休眠: 上课窗口内禁止睡眠(ES_SYSTEM_REQUIRED), 否则恢复默认
    kernel32 = ctypes.windll.kernel32
    if win:
        kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        log(f"上课窗口内({win[0]:%H:%M}-{win[1]:%H:%M}), 已临时禁止系统睡眠")
    else:
        kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        # 非窗口期不额外干预(电源计划已设为从不睡眠)

    # 2. 保活: 任何时间都确保调度器进程活着(它自己会等到周六窗口再动作)
    if not scheduler_running():
        start_scheduler()
    else:
        log("调度器运行中, 无需处理")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 —— 看门狗自身异常必须记日志便于排查
        log(f"看门狗异常: {exc!r}")
