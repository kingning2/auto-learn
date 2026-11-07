# -*- coding: utf-8 -*-
"""错误类型."""
from __future__ import annotations


class ConfigError(Exception):
    """配置缺失或非法."""


class PreflightError(Exception):
    """curl_cffi 预检失败: 站点不可达等."""


class LoginError(Exception):
    """登录流程失败: 找不到表单 / 提交异常等."""
