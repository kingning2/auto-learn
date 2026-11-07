# -*- coding: utf-8 -*-
"""业务预检: 用 curl_cffi 以真实 Chrome TLS 指纹探测站点 + 预热 Cookie."""
from __future__ import annotations

from curl_cffi import requests as cffi_requests

from contracts.browser_port import Cookie
from core.config import Settings
from core.errors import PreflightError
from core.values import MOBILE_USER_AGENT

# 预置 Cookie 的域(与站点 CNAME 对应的主域)
COOKIE_DOMAIN = ".superchutou.com"


def preflight(settings: Settings) -> list[Cookie]:
    """以 impersonate=chrome 访问站点首页.

    Returns:
        预热得到的 Cookie 列表(端口类型, 供 browser 引擎注入), 失败抛 PreflightError.
    """
    print(f"[预检] curl_cffi 访问 {settings.base_url} (impersonate=chrome)")
    session = cffi_requests.Session(impersonate="chrome")
    common = {
        "timeout": 20,
        "allow_redirects": True,
        # 移动端 UA: 站点为 wx.* 移动端 H5, PC 端打不开
        "headers": {"User-Agent": MOBILE_USER_AGENT},
    }
    try:
        resp = session.get(settings.base_url, **common)
    except Exception as first_exc:
        # 该站点证书与域名不匹配(实测 curl error 60), 微信内嵌也不校验; 关闭校验重试
        if "SSL" not in str(first_exc) and "certificate" not in str(first_exc).lower():
            raise PreflightError(f"站点无法访问: {first_exc}") from first_exc
        print(f"       证书校验失败({first_exc}), 改为不校验证书重试…")
        session = cffi_requests.Session(impersonate="chrome")
        try:
            resp = session.get(settings.base_url, verify=False, **common)
        except Exception as exc:
            raise PreflightError(f"站点无法访问: {exc}") from exc

    print(f"       HTTP {resp.status_code}, 最终 URL: {resp.url}, 长度 {len(resp.text)}")
    cookies = [
        Cookie(name=k, value=v, domain=COOKIE_DOMAIN)
        for k, v in session.cookies.get_dict().items()
    ]
    if cookies:
        print(f"       预热 Cookie: {[c.name for c in cookies]}")
    return cookies
