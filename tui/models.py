"""模型列表获取模块。"""

from __future__ import annotations

from dataclasses import dataclass

import requests


@dataclass
class ModelInfo:
    """模型信息。"""
    id: str
    group: str = ""  # owned_by / 分组


# 各类型默认模型列表（代理未运行时回退）
STATIC_MODELS_GENAI: dict[str, list[str]] = {
    "免费": [
        "deepseek-pro",
        "deepseek-chat",
        "deepseek-r1:671b",
        "chatglm",
        "qwen-instruct",
        "MiniMax-M1",
    ],
    "付费": [
        "GPT-5.5",
        "GPT-5.4",
        "GPT-5.2",
        "GPT-4.1",
        "o3",
    ],
}

# sssaicode 中转支持的模型（实际 API 返回，2026-05-12 验证）
SSSAICODE_MODELS: dict[str, list[str]] = {
    "Claude": [
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    ],
    "GPT": [
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.3-codex",
        "gpt-5.2",
    ],
}

# DeepSeek 官方 API 模型
DEEPSEEK_MODELS: dict[str, list[str]] = {
    "DeepSeek": ["deepseek-v4-pro", "deepseek-v4-flash"],
}

ANTHROPIC_OFFICIAL_MODELS: dict[str, list[str]] = {
    "Claude": ["opus", "sonnet", "haiku"],
}

OPENAI_MODELS: dict[str, list[str]] = {
    "付费": ["GPT-5.5", "GPT-5.4", "GPT-5.2", "GPT-4.1", "o3"],
    "免费": ["deepseek-pro", "deepseek-chat", "deepseek-r1:671b", "chatglm"],
}


def get_static_models(
    profile_type: str | None = None,
    profile_url: str = "",
    has_custom_model: bool = False,
) -> dict[str, list[str]]:
    """根据 profile 类型和后端 URL 返回对应的静态模型列表。

    对 anthropic 类型，按 url 区分不同后端：
    - sssaicode 中转 → Claude + GPT 系列
    - DeepSeek API → DeepSeek 模型
    - 纯官方（无 custom model） → Claude 官方模型
    """
    if profile_type == "anthropic":
        if not has_custom_model:
            return ANTHROPIC_OFFICIAL_MODELS
        url_lower = profile_url.lower()
        if "sssaicode" in url_lower:
            return SSSAICODE_MODELS
        elif "deepseek" in url_lower:
            return DEEPSEEK_MODELS
        # 其他自定义后端，返回官方模型作为安全默认
        return ANTHROPIC_OFFICIAL_MODELS
    elif profile_type == "openai":
        return OPENAI_MODELS
    return STATIC_MODELS_GENAI


def fetch_models_from_proxy(port: int = 5000) -> list[ModelInfo]:
    """从运行中的本地代理获取动态模型列表。"""
    return _fetch_models_from_url(f"http://localhost:{port}/v1/models")


def fetch_models_from_remote(base_url: str, api_key: str = "") -> list[ModelInfo]:
    """从远程 API 拉取动态模型列表（用于 anthropic 类型 profile）。"""
    url = base_url.rstrip("/") + "/v1/models"
    headers: dict[str, str] = {}
    if api_key:
        headers["x-api-key"] = api_key
        headers["Authorization"] = f"Bearer {api_key}"
    return _fetch_models_from_url(url, headers=headers)


def _fetch_models_from_url(
    url: str, headers: dict[str, str] | None = None,
) -> list[ModelInfo]:
    """从指定 URL 拉取模型列表。"""
    try:
        r = requests.get(url, timeout=3, headers=headers or {})
        r.raise_for_status()
        data = r.json()
        models = []
        seen = set()  # 去重（sssaicode 返回同一 id 多次，不同 context length）
        for m in data.get("data", []):
            mid = m.get("id", "")
            if mid and mid not in seen:
                seen.add(mid)
                # 自动推断分组：优先 owned_by，否则按 id 前缀
                group = m.get("owned_by", "")
                if not group or group in ("system",):
                    if mid.startswith("claude-"):
                        group = "Claude"
                    elif mid.startswith("gpt-"):
                        group = "GPT"
                    elif mid.startswith("deepseek"):
                        group = "DeepSeek"
                    else:
                        group = "other"
                # 统一友好名称
                elif group == "openai":
                    group = "GPT"
                models.append(ModelInfo(id=mid, group=group))
        return models
    except (requests.ConnectionError, requests.Timeout, requests.HTTPError,
            ValueError, KeyError):
        return []


def get_models(
    proxy_port: int | None = None,
    profile_type: str | None = None,
    has_custom_model: bool = False,
    profile_url: str = "",
    profile_key: str = "",
) -> dict[str, list[str]]:
    """获取可用模型列表。

    优先从代理/远程 API 拉取，失败则返回对应类型的静态列表。
    """
    # 1. 本地代理模式（genai / genai-api）
    if proxy_port:
        live = fetch_models_from_proxy(proxy_port)
        if live:
            grouped: dict[str, list[str]] = {}
            for m in live:
                group = m.group or "other"
                grouped.setdefault(group, []).append(m.id)
            return grouped

    # 2. 远程 API 模式（anthropic 有 custom model + 有 url）
    if profile_type == "anthropic" and has_custom_model and profile_url:
        live = fetch_models_from_remote(profile_url, api_key=profile_key)
        if live:
            grouped = {}
            for m in live:
                group = m.group or "other"
                grouped.setdefault(group, []).append(m.id)
            return grouped

    # 3. 兜底：静态列表
    return get_static_models(profile_type, profile_url, has_custom_model)
