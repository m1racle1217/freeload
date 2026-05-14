# -*- coding: utf-8 -*-
"""Shared platform metadata and capability definitions."""

from typing import Any


PLATFORM_DISPLAY_NAMES: dict[str, str] = {
    "jd": "京东",
    "taobao": "淘宝",
    "pdd": "拼多多",
    "miniapp": "品牌小程序",
}


PLATFORM_LOGIN_URLS: dict[str, str] = {
    "jd": "https://passport.jd.com/new/login.aspx",
    "taobao": "https://login.taobao.com/member/login.jhtml",
    "pdd": "https://mobile.yangkeduo.com/login.html",
    "miniapp": "https://open.weixin.qq.com/connect/qrconnect",
}


PLATFORM_WEB_LOGIN_FALLBACKS: dict[str, list[str]] = {
    "taobao": [
        "https://login.taobao.com/",
        "https://login.m.taobao.com/login.htm",
        "https://www.taobao.com/",
    ],
    "pdd": [
        "https://www.pinduoduo.com/",
        "https://mobile.yangkeduo.com/",
    ],
}


STRICT_PAGE_VERIFICATION_PLATFORMS = {"jd", "taobao"}


SESSION_COOKIE_RULES: dict[str, dict[str, list[set[str]]]] = {
    "jd": {
        "web": [
            {"pt_key", "pt_pin"},
            {"thor", "pin"},
            {"thor", "unick"},
        ],
        "mobile_sign": [
            {"pt_key", "pt_pin"},
        ],
    },
    "taobao": {
        "web": [
            {"_tb_token_", "cookie2"},
            {"unb", "cookie2"},
            {"lgc", "tracknick"},
        ],
    },
    "pdd": {
        "web": [
            {"pdd_user_id"},
            {"PDDAccessToken"},
            {"api_uid", "pdd_user_uin"},
        ],
    },
    "miniapp": {
        "web": [
            {"session"},
            {"token"},
            {"sessionid"},
        ],
    },
}


PLATFORM_DEFAULT_CONFIG: dict[str, dict[str, Any]] = {
    "jd": {
        "enabled": True,
        "poll_interval": 90,
        "value_threshold": 1.0,
        "flash_sale_targets": [],
        "verification_timeout": 180,
        "rate_limit": {
            "min_delay_ms": 8000,
            "max_delay_ms": 20000,
            "session_warmup": True,
        },
    },
    "taobao": {"enabled": False, "poll_interval": 60, "value_threshold": 1.0},
    "pdd": {"enabled": False, "poll_interval": 60, "value_threshold": 1.0},
    "miniapp": {"enabled": True, "poll_interval": 300, "value_threshold": 0.5},
}


JD_MANUAL_ACTION_SPECS: dict[str, dict[str, Any]] = {
    "sign_in": {
        "event_type": "sign_in",
        "title": "京东手动签到",
        "value": 2.0,
        "required_capability": "mobile_sign",
        "unavailable_reason": "当前登录态缺少 pt_key/pt_pin，无法执行京东签到",
    },
    "coupon": {
        "event_type": "coupon",
        "title": "京东手动领券",
        "value": 1.0,
        "required_capability": "web",
        "unavailable_reason": "当前登录态未通过验证，无法执行京东领券",
    },
    "flash_sale": {
        "event_type": "flash_sale",
        "title": "京东手动秒杀",
        "value": 10.0,
        "required_capability": "web",
        "unavailable_reason": "当前登录态未通过验证，无法执行京东秒杀",
    },
}
