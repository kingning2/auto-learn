# -*- coding: utf-8 -*-
"""登录业务: 打开登录页 -> 填身份证密码 -> 提交. 动作全部走 LoginDriver 端口."""
from __future__ import annotations

import time

from contracts.browser_port import LoginDriver
from core.config import Settings
from core.errors import LoginError

# 提交后等待页面反应的时长
SUBMIT_WAIT_S = 5


def do_login(driver: LoginDriver, settings: Settings, url: str | None = None) -> bool:
    """执行登录流程, 返回是否确认登录成功.

    Args:
        url: 登录页地址; 缺省用 SDYXY_LOGIN_URL, 再缺省用站点首页.

    Raises:
        LoginError: 打开的页面没有登录表单, 或填不了凭据.
    """
    print(f"[登录] 打开 {url or settings.login_url or settings.base_url}")
    driver.open(url or settings.login_url or settings.base_url)

    if not driver.has_password_field():
        driver.screenshot(settings.output_dir / "no_login_form.png")
        raise LoginError(
            "打开的页面上没有密码输入框, 已截图 output/no_login_form.png。\n"
            "请人工确认登录页 URL, 设置 SDYXY_LOGIN_URL 环境变量后重试。"
        )

    print("[登录] 填写身份证号与密码…")
    try:
        driver.fill_credentials(settings.id_card, settings.password)
    except LookupError as exc:
        driver.screenshot(settings.output_dir / "no_inputs.png")
        raise LoginError(f"填写凭据失败: {exc}, 已截图 output/no_inputs.png") from exc

    print("[登录] 提交…")
    driver.click_login()
    time.sleep(SUBMIT_WAIT_S)

    driver.screenshot(settings.output_dir / "after_login.png")
    driver.dump_session(settings.output_dir)

    print(f"[完成] 当前 URL: {driver.url}")
    print(f"  截图:   {settings.output_dir / 'after_login.png'}")
    print(f"  会话:   {settings.output_dir / 'storage_state.json'} (可复用免登录)")
    print(f"  Cookie: {settings.output_dir / 'cookies.txt'}")
    return True
