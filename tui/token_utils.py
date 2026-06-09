"""JWT Token 工具模块。"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone


def is_jwt(value: str) -> bool:
    """判断字符串是否是 JWT token（含2个点号的 base64 编码）。"""
    return value.count(".") == 2


def decode_jwt_expiry(token: str) -> datetime | None:
    """解析 JWT 中的 exp 字段，返回过期时间。"""
    if not is_jwt(token):
        return None
    try:
        payload = token.split(".")[1]
        # 补齐 base64 padding
        pad = 4 - len(payload) % 4
        if pad < 4:
            payload += "=" * pad
        # 处理 URL-safe base64
        payload = payload.replace("-", "+").replace("_", "/")
        decoded = base64.b64decode(payload)
        data = json.loads(decoded)
        exp = data.get("exp")
        if exp:
            return datetime.fromtimestamp(exp, tz=timezone.utc)
    except (ValueError, json.JSONDecodeError, KeyError):
        pass
    return None


def jwt_remaining_seconds(token: str) -> int | None:
    """返回 JWT 剩余有效秒数。None 表示无法解析。"""
    expiry = decode_jwt_expiry(token)
    if expiry is None:
        return None
    now = datetime.now(tz=timezone.utc)
    return int((expiry - now).total_seconds())


def jwt_remaining_str(token: str) -> str:
    """返回人类可读的 JWT 剩余时间。"""
    remaining = jwt_remaining_seconds(token)
    if remaining is None:
        return "未知"
    if remaining <= 0:
        mins = abs(remaining) // 60
        return f"已过期 {mins} 分钟前"
    elif remaining < 3600:
        return f"剩余 {remaining // 60} 分钟"
    else:
        return f"剩余 {remaining // 3600} 小时"
