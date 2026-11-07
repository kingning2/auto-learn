# -*- coding: utf-8 -*-
"""企业微信群通知(子进程隔离封装).

通过独立子进程调用 scripts/wework_notify.py(系统 Python 3.10 + ntwork)发送消息到
「直播脚本通知」群. 设计约束:

    - 通知永不抛异常: 任何失败只打印日志, 绝不影响直播观看主流程
    - 子进程隔离: ntwork 注入偶发崩溃(实测有段错误), 隔离后崩了也不连累常驻进程
    - 最小间隔节流: 60 秒内重复的相同错误只发一次, 避免重试循环刷屏
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# 系统 Python 3.10(ntwork 装在这里), 可用环境变量覆盖
NOTIFY_PYTHON = r"C:\Program Files\Python310\python.exe"
NOTIFY_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "wework_notify.py"

# 子进程超时: 注入 + 等数据同步 + 发送, 实测约 5~10s; 锁等待上限 30s, 60s 足够
SUBPROCESS_TIMEOUT_S = 60
# 相同内容的最小重复间隔
DEDUP_INTERVAL_S = 60
# 单条消息长度上限(超长截断, 防止异常堆潿刷屏)
MAX_MSG_LEN = 500

_last_sent: dict[str, float] = {}

# 控制台流强制 UTF-8: 消息里的 emoji/中文在 GBK 控制台不再炸编码
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 —— 编码设置失败无所谓, 通知照发
        pass


def _python_exe() -> str:
    exe = NOTIFY_PYTHON
    try:
        if not Path(exe).exists():
            exe = shutil.which("python") or sys.executable
    except Exception:
        exe = sys.executable
    return exe


def _notify_enabled() -> bool:
    """通知总开关: 设 SDYXY_NOTIFY=0 临时关闭全部企业微信通知."""
    return os.getenv("SDYXY_NOTIFY", "1") != "0"


def notify(message: str, *, dedup: bool = False) -> bool:
    """发送一条消息到「直播脚本通知」群. 永不抛异常, 返回是否成功.

    本函数是「永不 panic」的边界: 无论 ntwork 子进程段错误/挂起/不存在/超时,
    都只记日志返回 False, 绝不向调用方传任何异常.
    """
    # 双保险: 即使下面的 try 之外有任何意外, 也在这里拦住
    if not _notify_enabled():
        return False
    try:
        return _do_notify(message, dedup=dedup)
    except BaseException as exc:  # noqa: BLE001 —— 故意拦截一切, 守护主流程
        # KeyboardInterrupt/SystemExit 也吞掉: 通知失败不值得中断观看
        print(f"[通知] 发送异常(已忽略): {type(exc).__name__}: {exc}", file=sys.stderr)
        return False


def _do_notify(message: str, *, dedup: bool = False) -> bool:
    from datetime import datetime

    message = message[:MAX_MSG_LEN]
    stamped = f"[{datetime.now().strftime('%m-%d %H:%M')}] {message}"
    if not NOTIFY_SCRIPT.exists():
        print(f"[通知] 通知脚本不存在, 跳过: {NOTIFY_SCRIPT}", file=sys.stderr)
        return False
    if dedup:
        now = time.monotonic()
        if now - _last_sent.get(message, 0) < DEDUP_INTERVAL_S:
            print(f"[通知] 60 秒内已发过相同消息, 跳过: {message}")
            return True
        _last_sent[message] = now

    try:
        cmd = [
            _python_exe(), "-X", "utf8", str(NOTIFY_SCRIPT),
            "--message", stamped,
        ]
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, encoding="utf-8", errors="ignore",
            timeout=SUBPROCESS_TIMEOUT_S,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        ok = result.returncode == 0
        if not ok:
            print(f"[通知] 发送失败(rc={result.returncode}): "
                  f"{(result.stderr or '').strip()[-300:]}", file=sys.stderr)
        else:
            print(f"[通知] 已发送: {stamped}")
        return ok
    except Exception as exc:  # noqa: BLE001 —— 通知失败绝不能影响主流程
        print(f"[通知] 发送异常(已忽略): {exc}", file=sys.stderr)
        return False


def notify_image(image_path: str | Path, caption: str | None = None) -> bool:
    """发送一张本地截图到群里(可选附一条文字说明). 永不抛异常."""
    if not _notify_enabled():
        return False
    try:
        p = Path(image_path)
        if not p.exists():
            print(f"[通知] 图片不存在, 跳过: {p}", file=sys.stderr)
            return False
        if caption:
            notify(caption)
        cmd = [
            _python_exe(), "-X", "utf8", str(NOTIFY_SCRIPT),
            "--image", str(p),
        ]
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, encoding="utf-8", errors="ignore",
            timeout=SUBPROCESS_TIMEOUT_S,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        ok = result.returncode == 0
        if not ok:
            print(f"[通知] 发图失败(rc={result.returncode}): "
                  f"{(result.stderr or '').strip()[-300:]}", file=sys.stderr)
        else:
            print(f"[通知] 已发图: {p.name}")
        return ok
    except Exception as exc:  # noqa: BLE001
        print(f"[通知] 发图异常(已忽略): {exc}", file=sys.stderr)
        return False


def notify_error(context: str, exc: BaseException | None = None) -> bool:
    """发送一条错误通知, 自动带上异常摘要."""
    msg = f"[错误] {context}"  # 不用 emoji: Windows GBK 控制台编码不过
    if exc is not None:
        msg += f": {type(exc).__name__}: {exc}"
    return notify(msg, dedup=True)


def notify_start(context: str) -> bool:
    """发送启动通知."""
    return notify(f"🚀 {context}")


def notify_info(message: str, *, dedup: bool = False) -> bool:
    """发送普通进度通知(可选去重)."""
    return notify(f"ℹ️ {message}", dedup=dedup)
