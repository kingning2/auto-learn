# -*- coding: utf-8 -*-
"""Playwright Chrome 反检测引擎: 浏览器实例的启动 / Cookie 注入 / 关闭.

浏览器生命周期只归主编排(app)管理: 开一次, 所有业务步骤共用同一实例.

注: 2026-11 起从 invisible_playwright(Firefox) 换为 Chrome —— 该站对 Chrome
零拦截(人工 Chrome 直接可播), 而反检测 Firefox 缺移动端触摸派发能力且
保利威播放器 SDK 在其下初始化不稳定.
"""
from __future__ import annotations

import json
from pathlib import Path

from contracts.browser_port import Cookie, LaunchOptions, LoginDriver
from core.values import MOBILE_DEVICE_SCALE_FACTOR, MOBILE_USER_AGENT, MOBILE_VIEWPORT
from playwright.async_api import Error as PlaywrightError  # noqa: F401 仅供类型提示
from playwright.sync_api import sync_playwright

from browser.driver import InvisibleLoginDriver  # 驱动与内核无关, 沿用


class StealthBrowser:
    """Chrome 浏览器实例(带基础反自动化处理).

    用法(在主编排里):
        browser = StealthBrowser(options)
        browser.start()            # 打开, 全程只此一次
        ...各业务步骤共用 browser.driver...
        browser.close()            # 全部步骤结束才关闭
    """

    def __init__(self, options: LaunchOptions) -> None:
        self._options = options
        self._pw = None
        self._browser = None
        self._driver: InvisibleLoginDriver | None = None

    def start(self, storage_state: Path | None = None) -> "StealthBrowser":
        """启动引擎并创建页面, 返回自身便于链式使用.

        Args:
            storage_state: 可选的已保存会话文件(output/storage_state.json),
                           传入后恢复 Cookie/登录态, 免重复登录.
        """
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self._options.headless,
            # 去掉 AutomationControlled 特征; full-screen 交给窗口默认尺寸
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--start-maximized",
            ],
        )
        # 移动端打开: 站点为 wx.* 移动端 H5, PC 端打不开 —— UA/视口/触摸全部移动端
        # no_viewport + --start-maximized: 桌面窗口铺满, 页面用移动 UA 渲染
        context = self._browser.new_context(
            user_agent=MOBILE_USER_AGENT,
            viewport=MOBILE_VIEWPORT,
            device_scale_factor=MOBILE_DEVICE_SCALE_FACTOR,
            has_touch=True,
            locale=self._options.locale,
            timezone_id=self._options.timezone,
            ignore_https_errors=True,
        )
        # 基础反自动化: 抹掉 navigator.webdriver
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        # 恢复会话: 不用 new_context(storage_state=...), 之前实测会静默丢弃;
        # 统一改为手动读 JSON 注入, 稳定可靠
        if storage_state and Path(storage_state).exists():
            state = json.loads(Path(storage_state).read_text(encoding="utf-8"))
            cookies = [
                {
                    "name": c["name"],
                    "value": c["value"],
                    "domain": c["domain"],
                    "path": c.get("path", "/"),
                    **({"expires": c["expires"]} if c.get("expires", -1) and c["expires"] > 0 else {}),
                }
                for c in state.get("cookies", [])
            ]
            if cookies:
                context.add_cookies(cookies)
        self._driver = InvisibleLoginDriver(context, self._options)
        return self

    @property
    def driver(self) -> LoginDriver:
        """登录页驱动; 各业务步骤只拿这个, 不接触引擎."""
        if self._driver is None:
            raise RuntimeError("浏览器未启动, 先调用 start()")
        return self._driver

    def inject_cookies(self, cookies: list[Cookie]) -> None:
        """注入预置 Cookie(通常来自 curl_cffi 预检), 保持会话连贯."""
        self.driver.context.add_cookies(
            [
                {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
                for c in cookies
            ]
        )

    def close(self) -> None:
        """关闭浏览器, 全部业务步骤结束后调用一次."""
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            if self._pw is not None:
                try:
                    self._pw.stop()
                finally:
                    self._pw = None
            self._browser = None
            self._driver = None
