# -*- coding: utf-8 -*-
"""配置: 全部从环境变量 / .env 读取, 汇聚成 Settings 值对象."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")
except ImportError:
    pass


@dataclass(frozen=True)
class Settings:
    """运行配置值对象."""

    base_url: str
    login_url: str  # 留空则打开首页后自动找登录入口
    id_card: str
    password: str
    headless: bool  # 默认 False(有头), 反检测效果更好
    timeout_ms: int
    output_dir: Path


def load_settings() -> Settings:
    """从环境变量读配置, 返回不可变 Settings."""
    root = Path(__file__).parent.parent.parent.parent  # core/src/core -> 项目根
    output_dir = root / "output"
    output_dir.mkdir(exist_ok=True)
    return Settings(
        base_url=os.getenv("SDYXY_BASE_URL", "http://sdyxy.wx.superchutou.com"),
        login_url=os.getenv("SDYXY_LOGIN_URL", ""),
        id_card=os.getenv("SDYXY_ID_CARD", ""),
        password=os.getenv("SDYXY_PASSWORD", ""),
        headless=os.getenv("SDYXY_HEADLESS", "0") == "1",
        timeout_ms=int(os.getenv("SDYXY_TIMEOUT_MS", "30000")),
        output_dir=output_dir,
    )
