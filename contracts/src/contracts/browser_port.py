# -*- coding: utf-8 -*-
"""登录驱动插座：业务(sdyxy)只依赖本接口，不碰 invisible_playwright/Playwright。

browser 包是唯一允许 import 浏览器库的包，负责实现本端口。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Cookie:
    """通用 Cookie，不含平台域名特例。"""

    name: str
    value: str
    domain: str = ""
    path: str = "/"


@dataclass(frozen=True)
class LaunchOptions:
    """引擎启动参数；缺省走 adapter 默认配置。"""

    headless: bool = False
    locale: str = "zh-CN"
    timezone: str = "Asia/Shanghai"
    timeout_ms: int = 30_000
    settle_ms: int = 2_000  # 打开页面后等待 JS 渲染的时长


class LoginDriver(ABC):
    """登录页驱动插座：导航、填表、提交、会话导出。

    语义级接口 —— 业务说"填身份证密码""点登录"，不传 CSS 选择器；
    选择器等 DOM 细节全部留在 browser 实现层。
    """

    @property
    @abstractmethod
    def url(self) -> str:
        """当前页 URL。"""

    @abstractmethod
    def open(self, url: str) -> None:
        """打开页面并等待渲染。"""

    @abstractmethod
    def has_password_field(self) -> bool:
        """当前页是否有可见密码输入框(即是否为登录表单)。"""

    @abstractmethod
    def is_blank(self) -> bool:
        """当前页是否白屏(无可见文本, 常见于 SPA 登录失效后渲染不出内容)。"""

    @abstractmethod
    def fill_credentials(self, id_card: str, password: str) -> None:
        """人工化填写身份证号与密码(逐字符输入)。"""

    @abstractmethod
    def click_login(self) -> None:
        """点击登录按钮(找不到则按 Enter 兜底)。"""

    @abstractmethod
    def screenshot(self, name: Path) -> Path:
        """全页截图到指定路径, 返回截图路径。"""

    @abstractmethod
    def click_text(self, text: str) -> bool:
        """点击第一个可见的含指定文本的元素(页面导航入口等), 成功返回 True。"""

    @abstractmethod
    def switch_to_new_tab(self, timeout_ms: int = 10000) -> bool:
        """点击后若有新标签页(window.open)则切换过去并接管, 返回是否发生了切换。"""

    @abstractmethod
    def reload(self) -> None:
        """刷新当前页面并等待渲染。"""

    @abstractmethod
    def has_text(self, text: str) -> bool:
        """当前页面是否出现指定文本。"""

    @abstractmethod
    def evaluate(self, script: str) -> str:
        """在当前页执行 JS 并返回字符串结果(用于同源 fetch 接口/读 localStorage)。"""

    @abstractmethod
    def dump_session(self, out_dir: Path) -> None:
        """导出会话: storage_state.json + cookies.txt。"""
