# -*- coding: utf-8 -*-
"""sdyxy 业务层: 预检 + 登录 + 会话复用 + 直播监控; 只依赖 contracts 端口."""

from sdyxy.live import goto_live_room
from sdyxy.login import do_login
from sdyxy.preflight import preflight
from sdyxy.session import ensure_login, has_session, session_file

__all__ = [
    "do_login",
    "preflight",
    "goto_live_room",
    "ensure_login",
    "has_session",
    "session_file",
]
