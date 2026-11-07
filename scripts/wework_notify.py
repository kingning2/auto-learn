# -*- coding: utf-8 -*-
"""独立的企业微信通知 CLI(必须用系统 Python 3.10 运行, ntwork 只装在那里).

用法:
    python wework_notify.py --message "文本" [--conversation R:xxxx]

设计要点:
    - 每次运行 = 一次独立 注入 -> 发送 -> 退出, 崩溃不影响调用方
    - 通过文件锁串行化, 避免两个通知进程同时注入同一个企业微信
    - conversation 缺省用「直播脚本通知」群的固定会话 ID, 失效时回退到按群名查找
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# 本机企业微信装在非默认路径, 注册表检测不到, 必须手动指定
WXWORK_EXE = r"D:\WXWork\4.0.8.6027\WXWork.exe"
WXWORK_VERSION = "4.0.8.6027"

DEFAULT_ROOM_NAME = "直播脚本通知"
# 2026-11-06 实测到的「直播脚本通知」会话 ID(外部群)
DEFAULT_CONVERSATION_ID = "R:10873826481385601"

LOCK_FILE = r"C:\Users\Administrator\.ntwork_notify.lock"


def _acquire_lock(timeout_s: float = 30.0) -> None:
    """跨进程文件锁: 独占打开, 拿不到就等(防止并发注入冲突)."""
    import msvcrt

    deadline = time.time() + timeout_s
    while True:
        try:
            f = open(LOCK_FILE, "a+b")
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            f.seek(0)
            f.truncate()
            f.write(str(time.time()).encode())
            f.flush()
            globals()["_lock_fh"] = f
            return
        except OSError:
            if time.time() > deadline:
                raise TimeoutError("获取通知锁超时, 可能已有其他通知进程卡住")
            time.sleep(1)


def _find_wework_pids() -> list[int]:
    import subprocess

    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq WXWork.exe", "/FO", "CSV"],
        capture_output=True, text=True, encoding="gbk", errors="ignore",
    ).stdout
    if not out:
        return []
    pids = []
    for line in out.splitlines()[1:]:
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and parts[0] == "WXWork.exe":
            pids.append(int(parts[1]))
    return pids


def _launch_wework_if_needed() -> None:
    """企业微信没在运行时拉起它(用已保存的登录态自动登录), 最多等 60 秒."""
    if _find_wework_pids():
        return
    import subprocess as sp

    sp.Popen([WXWORK_EXE], cwd=str(Path(WXWORK_EXE).parent))
    for _ in range(30):
        time.sleep(2)
        if _find_wework_pids():
            return


def send(message: str, conversation_id: str | None = None, image: str | None = None) -> bool:
    import ntwork
    import ntwork.conf as conf

    conf.DEFAULT_WEWORK_EXE_PATH = WXWORK_EXE
    conf.DEFAULT_WEWORK_VERSION = WXWORK_VERSION

    wework = ntwork.WeWork()
    target_cid = conversation_id or DEFAULT_CONVERSATION_ID

    # 企业微信没开就先拉起, 再接管已登录的主进程(不重新登录)
    _launch_wework_if_needed()
    attached = False
    for pid in _find_wework_pids():
        try:
            wework.attach(pid)
        except Exception:
            continue
        wework.wait_login(timeout=15)
        if wework.login_status:
            attached = True
            break
        wework.detach()

    if not attached:
        print("[notify] 未找到已登录的企业微信进程", file=sys.stderr)
        return False

    def _deliver(deliver) -> bool:
        """先打固定会话 ID, 失效则回退按群名查找(需等数据同步)."""
        if deliver(target_cid):
            return True
        for _ in range(5):
            time.sleep(2)
            rooms = wework.get_rooms() or {}
            hit = next(
                (r for r in rooms.get("room_list", []) if r.get("nickname") == DEFAULT_ROOM_NAME),
                None,
            )
            if hit:
                return deliver(hit["conversation_id"])
        return False

    if image:
        # 注意: 参数名是 file_path(不是 path), 传错会 TypeError
        ok = _deliver(lambda cid: wework.send_image(conversation_id=cid, file_path=image))
    else:
        ok = _deliver(lambda cid: wework.send_text(conversation_id=cid, content=message))

    time.sleep(1)
    ntwork.exit_()
    return bool(ok)


def main() -> int:
    parser = argparse.ArgumentParser(description="企业微信「直播脚本通知」群消息通知")
    parser.add_argument("--message", "-m", default=None, help="要发送的文本(发图时可作说明, 可省)")
    parser.add_argument("--image", "-i", default=None, help="要发送的本地图片路径")
    parser.add_argument("--conversation", "-c", default=None, help="会话 ID(缺省发到直播脚本通知群)")
    args = parser.parse_args()

    if not args.message and not args.image:
        parser.error("--message 与 --image 至少给一个")

    ok = False
    try:
        _acquire_lock()
        ok = send(args.message, args.conversation, args.image)
    except Exception as exc:
        # 子进程内任何问题(注入失败/版本不匹配/锁超时/段错误前兆)都干净退出,
        # 绝不向上抛 —— 崩溃边界就在这个进程, 调用方只看退出码
        print(f"[notify] 异常: {type(exc).__name__}: {exc}", file=sys.stderr)
        ok = False
    finally:
        print("[notify] 发送成功" if ok else "[notify] 发送失败", file=sys.stderr)
        sys.stdout.flush(); sys.stderr.flush()
        # ntwork 注入的原生线程不会随解释器正常退出而结束(实测挂死进程),
        # 必须硬退出, 退出码就是唯一结果通道
        os._exit(0 if ok else 1)
    return 0  # 不可达, 仅为类型完整


if __name__ == "__main__":
    main()
