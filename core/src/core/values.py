# -*- coding: utf-8 -*-
"""共享值: 跨包使用的常量."""
from __future__ import annotations

# 移动端 UA(微信内嵌浏览器风格) —— 站点为 wx.* 移动端 H5, PC 端打不开.
# browser 能力层与 sdyxy 预检共用此值, 保证两边身份一致.
MOBILE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
    "MicroMessenger/8.0.49 NetType/WIFI Language/zh_CN"
)

# 移动端视口(iPhone 14/15 Pro 尺寸)
MOBILE_VIEWPORT = {"width": 390, "height": 844}
MOBILE_DEVICE_SCALE_FACTOR = 3
