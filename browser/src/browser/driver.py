# -*- coding: utf-8 -*-
"""LoginDriver 的 invisible_playwright 实现: 导航 / 填表 / 提交 / 会话导出.

本文件是选择器等 DOM 细节的唯一归属地; 业务层只见语义方法.
"""
from __future__ import annotations

from pathlib import Path

from contracts.browser_port import LaunchOptions, LoginDriver
from playwright.sync_api import BrowserContext, Locator, Page


class LoginDriverError(Exception):
    """驱动层导航等底层失败."""

# ---------- 登录表单选择器(按优先级, 首位是 2026-11 实测该站的精确选择器) ----------

IDCARD_SELECTORS = [
    "input[name='CardNumber']",  # sdyxy 登录页实测
    "input[name*='idcard' i]", "input[name*='id_card' i]",
    "input[name*='sfz' i]", "input[name*='sfzh' i]",
    "input[name*='username' i]", "input[name*='account' i]", "input[name*='user' i]",
    "input[placeholder*='身份证' i]", "input[placeholder*='证件' i]",
    "input[placeholder*='账号' i]", "input[placeholder*='用户名' i]",
    "input[placeholder*='请输入' i]",
    "input[type=tel]", "input[type=text]",
]

LOGIN_BUTTON_SELECTORS = [
    "button:has-text('登录')",  # sdyxy 登录页实测: 按钮文本"登录"
    "a:has-text('登录')",
    "input[type=submit]",
    "button[type=submit]",
    ".login-btn",
    "#login-btn",
    "text=/^\\s*登\\s*录\\s*$/",
]


def _first_visible(page: Page, selectors: list[str], exclude_text: str = "") -> Locator | None:
    """按优先级返回第一个可见且可编辑的元素.

    exclude_text: 排除 placeholder/name/id 含该文本的元素(如 '忘记密码' 里的 password 框).
    """
    for sel in selectors:
        try:
            loc = page.locator(sel)
            for i in range(loc.count()):
                el = loc.nth(i)
                if not el.is_visible() or not el.is_enabled():
                    continue
                if exclude_text:
                    try:
                        ctx = el.evaluate(
                            "e => (e.placeholder||'') + ' ' + (e.name||'') + ' ' + (e.id||'')"
                        )
                        if exclude_text in ctx:
                            continue
                    except Exception:
                        pass
                return el
        except Exception:
            continue
    return None


class InvisibleLoginDriver(LoginDriver):
    """基于 invisible_playwright(反检测 Firefox) 的 LoginDriver 实现."""

    def __init__(self, context: BrowserContext, options: LaunchOptions) -> None:
        self._context = context
        self._options = options
        self._page: Page = context.new_page()

    # ---------- 端口实现 ----------

    @property
    def context(self) -> BrowserContext:
        """浏览器上下文(供会话级操作: Cookie 注入等)."""
        return self._context

    @property
    def url(self) -> str:
        return self._page.url

    def open(self, url: str) -> None:
        self._goto_with_retry(url)
        self._page.wait_for_timeout(self._options.settle_ms)

    def _goto_with_retry(self, url: str, tries: int = 3) -> None:
        """带重试的导航.

        反检测 Firefox 开启 HTTPS-Only 后对 http 站点首次导航可能报
        NS_BINDING_ABORTED(升级 https 失败), 实测重试即可成功.
        """
        last: Exception | None = None
        for _ in range(tries):
            try:
                self._page.goto(
                    url, timeout=self._options.timeout_ms, wait_until="domcontentloaded"
                )
                return
            except Exception as exc:  # noqa: BLE001
                last = exc
                self._page.wait_for_timeout(1_200)
        raise LoginDriverError(f"导航失败(重试 {tries} 次): {url}") from last

    def has_password_field(self) -> bool:
        try:
            return self._page.locator("input[type=password]:visible").count() > 0
        except Exception:
            return False

    def is_blank(self) -> bool:
        """白屏判定: body 可见文本不足 5 字符(SPA 登录失效时渲染不出内容)."""
        try:
            txt = self._page.evaluate("() => document.body ? document.body.innerText : ''")
            return len((txt or "").strip()) < 5
        except Exception:
            return True

    def fill_credentials(self, id_card: str, password: str) -> None:
        page = self._page
        pwd = _first_visible(page, ["input[type=password]"])
        idc = _first_visible(page, IDCARD_SELECTORS, exclude_text="password")
        # 兜底: 取密码框前最后一个可见文本输入框
        if idc is None and pwd is not None:
            texts = [e for e in page.locator("input:visible").all()
                     if e.evaluate("e => e.type") in ("text", "tel", "")]
            idc = texts[-1] if texts else None
        if pwd is None or idc is None:
            raise LookupError("无法定位身份证/密码输入框")

        self._fill_human(idc, id_card)
        self._fill_human(pwd, password)

    def click_login(self) -> None:
        btn = _first_visible(self._page, LOGIN_BUTTON_SELECTORS, exclude_text="忘记")
        if btn:
            btn.click()
        else:
            # 兜底: 密码框内按回车提交
            self._page.locator("input[type=password]").first.press("Enter")

    def click_text(self, text: str) -> bool:
        """点击第一个可见的含指定文本的元素, 点击后等待渲染."""
        try:
            loc = self._page.locator(f"text={text}")
            for i in range(loc.count()):
                el = loc.nth(i)
                if el.is_visible():
                    el.click()
                    self._page.wait_for_load_state(
                        "domcontentloaded", timeout=self._options.timeout_ms
                    )
                    self._page.wait_for_timeout(self._options.settle_ms)
                    return True
        except Exception:
            pass
        return False

    def switch_to_new_tab(self, timeout_ms: int = 10000) -> bool:
        """点击后若有新标签页(window.open)则切换过去并接管, 返回是否切换.

        注意: window.open 可能被浏览器拦截弹窗时降级为当前页导航,
        所以新页未出现时再检查当前页 URL 是否已变化.
        """
        import time as _time

        old_url = self._page.url
        old_page = self._page
        deadline = _time.monotonic() + timeout_ms / 1000
        while _time.monotonic() < deadline:
            pages = self._page.context.pages
            # 新标签页 = 页面列表里除当前页以外的那个
            others = [p for p in pages if p is not old_page]
            if others:
                self._page = others[-1]
                try:
                    self._page.wait_for_load_state(
                        "domcontentloaded", timeout=self._options.timeout_ms
                    )
                except Exception:
                    pass
                return True
            # 没有新页但当前页 URL 变了(降级为同页导航)
            if self._page.url != old_url:
                return False
            _time.sleep(0.2)
        return False

    def reload(self) -> None:
        try:
            self._page.reload(timeout=self._options.timeout_ms, wait_until="domcontentloaded")
        except Exception:  # 首次导航同类问题: NS_BINDING_ABORTED, 重试一次
            self._page.reload(timeout=self._options.timeout_ms, wait_until="domcontentloaded")
        self._page.wait_for_timeout(self._options.settle_ms)

    def has_text(self, text: str) -> bool:
        try:
            return self._page.locator(f"text={text}").count() > 0
        except Exception:
            return False

    def evaluate(self, script: str) -> str:
        try:
            result = self._page.evaluate(script)
            return "" if result is None else str(result)
        except Exception:
            return ""

    def screenshot(self, name: Path) -> Path:
        # 只截可视区域: full_page=True 会把整个滚动页(上万像素)都截下来,
        # 发群里就是一条细长的"面条", 看不清内容
        self._page.screenshot(path=str(name), full_page=False)
        return name

    def dump_session(self, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        self._context.storage_state(path=str(out_dir / "storage_state.json"))
        with open(out_dir / "cookies.txt", "w", encoding="utf-8") as f:
            for c in self._context.cookies():
                f.write(f"{c['name']}={c['value']}\n")

    # ---------- 内部 ----------

    @staticmethod
    def _fill_human(loc: Locator, text: str) -> None:
        """人工化填写: 点击聚焦, 清空后逐字符输入(60ms/字符)."""
        loc.click()
        loc.fill("")
        loc.type(text, delay=60)
