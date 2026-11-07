# -*- coding: utf-8 -*-
"""会话持久化 + 登录态判定.

平台路由知识(微信 H5):
  首页: /pages/home/homePage —— 登录有效时能正常渲染; 白屏 = 登录失效
  登录: /pages/login/index   —— 登录失效时到这里重新登录

流程: 打开首页路由 -> 白屏? -> 去 /pages/login/index 重新登录并保存会话
会话存储: output/storage_state.json, 下次启动直接注入, 免重复登录.
"""
from __future__ import annotations

import time
from pathlib import Path

from contracts.browser_port import LoginDriver
from core.config import Settings
from core.errors import LoginError
from core.notify import notify_image

from sdyxy.login import do_login

SESSION_FILENAME = "storage_state.json"

# 平台路由(业务知识)
HOME_PATH = "/pages/home/homePage"
LOGIN_PATH = "/pages/login/index"


def session_file(settings: Settings) -> Path:
    """会话文件路径: output/storage_state.json."""
    return settings.output_dir / SESSION_FILENAME


def has_session(settings: Settings) -> bool:
    """是否已有可复用的会话文件."""
    return session_file(settings).exists()


def home_url(settings: Settings) -> str:
    return settings.base_url.rstrip("/") + HOME_PATH


def login_url(settings: Settings) -> str:
    return settings.base_url.rstrip("/") + LOGIN_PATH


def _wait_rendered(driver: LoginDriver, tries: int = 12) -> bool:
    """轮询等待首页出现登录后特征文本.

    注意: 本站未登录时首页不是空白, 而是永远停在 "loading...",
    所以不能用 "非白屏 = 已登录", 必须等登录后的欢迎文本出现.
    """
    for _ in range(tries):
        if driver.has_text("欢迎你"):
            return True
        time.sleep(1)
    return False


def ensure_login(driver: LoginDriver, settings: Settings) -> bool:
    """打开首页路由判断登录态: 白屏则去登录页重新登录. 返回是否确认已登录.

    Raises:
        LoginError: 重新登录仍失败.
    """
    print(f"[会话] 打开首页路由 {home_url(settings)} 检查登录态…")
    driver.open(home_url(settings))

    if _wait_rendered(driver):
        print(f"[会话] 首页渲染正常, 登录有效, 免登录. 当前 URL: {driver.url}")
        driver.screenshot(settings.output_dir / "session_reused.png")
        return True

    # 白屏: 登录失效(或首次运行), 去登录页重新登录
    print("[会话] 首页白屏 -> 登录失效, 跳转登录页重新登录…")
    ok = do_login(driver, settings, url=login_url(settings))
    if not ok:
        raise LoginError("重新登录后首页仍白屏, 请检查账号密码(见 output/after_login.png)")
    # 登录成功后回首页路由, 给后续步骤(我的教务入口)一个确定的起点
    driver.open(home_url(settings))
    if not _wait_rendered(driver):
        driver.screenshot(settings.output_dir / "home_blank_after_login.png")
        raise LoginError("登录后打开首页仍白屏, 已截图 output/home_blank_after_login.png")
    print(f"[会话] 登录成功, 会话已保存到 {session_file(settings)}, 下次运行免登录")
    # 群通知: 登录成功(带登录后首页截图, 能看到欢迎栏确认身份)
    notify_image(
        settings.output_dir / "after_login.png",
        "✅ 登录成功",
    )
    return True
