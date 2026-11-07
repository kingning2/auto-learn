# -*- coding: utf-8 -*-
"""装配入口(主编排): 浏览器在此开一次, 业务步骤按顺序共用同一条浏览器线.

用法:
    uv run python -m app               # 立即执行一次: 登录 -> 我的教务 -> 直播安排 -> 等房间
    uv run python -m app schedule      # 常驻调度: 每周六四个上课时段自动开播等待+观看
"""
from __future__ import annotations

import sys
from datetime import datetime

from browser.engine import StealthBrowser
from contracts.browser_port import LaunchOptions
from core.config import load_settings
from core.errors import ConfigError, LoginError, PreflightError
from core.notify import notify_error, notify_info, notify_start
from sdyxy.live import goto_live_room
from sdyxy.preflight import preflight
from sdyxy.session import ensure_login, has_session, session_file


def _load_and_validate():
    settings = load_settings()
    if not settings.id_card or not settings.password:
        raise ConfigError(
            "未设置环境变量 SDYXY_ID_CARD / SDYXY_PASSWORD\n"
            "  方式一: 复制 .env.example 为 .env 填入账号后运行\n"
            "  方式二: PowerShell  $env:SDYXY_ID_CARD='...'; $env:SDYXY_PASSWORD='...'\n"
            "  方式二: CMD         set SDYXY_ID_CARD=... && set SDYXY_PASSWORD=..."
        )
    return settings


def run_once(watch_until: datetime | None = None) -> str:
    """执行一次完整流程: 预检 -> 浏览器 -> 登录 -> 直播安排 -> 等房间 -> 进房间驻留观看.

    Args:
        watch_until: 定时模式用 —— 等到房间后驻留观看到该北京时间;
                     None(单次模式)则进入房间后驻留观看, 直到手动 Ctrl+C.

    Returns:
        房间 URL.
    """
    settings = _load_and_validate()
    options = LaunchOptions(headless=settings.headless, timeout_ms=settings.timeout_ms)

    # 1. curl_cffi 预检 + 预热 Cookie
    cookies = preflight(settings)

    # 2. 打开浏览器 —— 全程只此一次, 以下所有业务步骤共用同一条线
    #    已保存过会话(output/storage_state.json)则尝试恢复(实测服务端可能单点失效,
    #    失效时 ensure_login 会自动转重新登录)
    storage = session_file(settings) if has_session(settings) else None
    if storage:
        print(f"[会话] 尝试恢复已保存会话: {storage}")
    browser = StealthBrowser(options).start(storage_state=storage)
    try:
        browser.inject_cookies(cookies)
        driver = browser.driver

        # 3. 业务步骤: 有会话先试复用, 过期/无会话才真正登录
        ensure_login(driver, settings)
        until_epoch = watch_until.timestamp() if watch_until else None
        room_url = goto_live_room(driver, settings, watch_until_epoch=until_epoch)
        return room_url
    finally:
        # 4. 全部步骤结束才关闭
        browser.close()


def main() -> None:
    run_once()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "schedule":
        from app.scheduler import main as schedule_main

        schedule_main()
        sys.exit(0)
    try:
        main()
    except (ConfigError, PreflightError, LoginError) as exc:
        # LoginError 的详细播报(含截图)已在 live.py 里发过, 这里不再重复发
        print(f"[错误] {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:  # noqa: BLE001 —— 未预期异常也要让群里知道
        import traceback

        traceback.print_exc()
        notify_error("直播脚本未预期异常", exc)
        sys.exit(3)
    except KeyboardInterrupt:
        print("[退出] 已手动停止")
