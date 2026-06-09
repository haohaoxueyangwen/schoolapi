"""Profile 与配置管理模块。

复用 ~/.claude/profiles/*.json 格式，与 switch-model.sh 完全兼容。
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


# ======================== 路径常量 ========================

CLAUDE_DIR = Path.home() / ".claude"
PROFILES_DIR = CLAUDE_DIR / "profiles"
ACTIVE_PROFILE_FILE = CLAUDE_DIR / "active-profile"
SETTINGS_FILE = CLAUDE_DIR / "settings.json"
GENAI_TOKEN_FILE = CLAUDE_DIR / "genai-token.txt"
GENAI_MODEL_FILE = CLAUDE_DIR / "genai-model.txt"
SHTU_CONFIG_DIR = Path.home() / ".config" / "SHTUClaudeProxy"
SHTU_CONFIG_FILE = SHTU_CONFIG_DIR / "config.json"
LOG_DIR = CLAUDE_DIR / "genai-stack" / "logs"

# 代理项目目录（相对于包根目录）
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
GENAI2API_DIR = _PACKAGE_ROOT / "proxies" / "genai2api"
SHTU_PROXY_DIR = _PACKAGE_ROOT / "proxies" / "SHTUClaudeProxy"

# 端口
SHTU_PROXY_PORT = 8082
GENAI2API_PORT = 5000
PROXY_AUTH_TOKEN = "local-proxy"

# Anthropic 官方 (sssaicode)
DEFAULT_ANTHROPIC_URL = "https://node-hk.sssaicode.com/api"


# ======================== 数据模型 ========================

@dataclass
class Profile:
    """一个 Profile 的配置。"""
    name: str
    type: str  # anthropic | genai | genai-api | openai
    url: str = ""
    key: str = ""
    model: str = ""  # anthropic 专用
    big_model: str = ""
    middle_model: str = ""
    small_model: str = ""

    @property
    def display_model(self) -> str:
        """显示用的模型名。"""
        if self.type == "anthropic":
            return self.model or "opus"
        return self.big_model or "未设置"

    @property
    def display_url(self) -> str:
        """显示用的 URL。"""
        if self.type == "genai":
            return "(HTK JWT)"
        return self.url or ""

    @property
    def masked_key(self) -> str:
        """脱敏后的 key。"""
        if not self.key:
            return ""
        if len(self.key) <= 16:
            return self.key
        return self.key[:12] + "..."

    def to_json(self) -> dict[str, Any]:
        """转为 profile JSON（不含 name 字段）。"""
        d: dict[str, Any] = {"type": self.type}
        if self.type == "anthropic":
            d["url"] = self.url
            d["key"] = self.key
            if self.model:
                d["model"] = self.model
            if self.small_model:
                d["small_model"] = self.small_model
        elif self.type == "genai":
            d["key"] = self.key
            d["big_model"] = self.big_model or "GPT-5.5"
            d["middle_model"] = self.middle_model or d["big_model"]
            d["small_model"] = self.small_model or d["big_model"]
        elif self.type in ("genai-api", "openai"):
            d["url"] = self.url
            d["key"] = self.key
            d["big_model"] = self.big_model
            d["middle_model"] = self.middle_model or self.big_model
            d["small_model"] = self.small_model or self.big_model
        return d


# ======================== Profile CRUD ========================

def _ensure_dirs() -> None:
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        PROFILES_DIR.chmod(0o700)


def profile_exists(name: str) -> bool:
    """检查 profile 是否存在。"""
    return (PROFILES_DIR / f"{name}.json").exists()


def list_profiles() -> list[Profile]:
    """列出所有 profiles。"""
    _ensure_dirs()
    profiles = []
    for f in sorted(PROFILES_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            name = f.stem
            profiles.append(_dict_to_profile(name, data))
        except (json.JSONDecodeError, KeyError):
            continue
    return profiles


def load_profile(name: str) -> Profile | None:
    """加载指定 profile。"""
    path = PROFILES_DIR / f"{name}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return _dict_to_profile(name, data)
    except (json.JSONDecodeError, KeyError):
        return None


def save_profile(profile: Profile) -> None:
    """保存 profile（原子写入）。"""
    _ensure_dirs()
    path = PROFILES_DIR / f"{profile.name}.json"
    data = profile.to_json()
    _atomic_write_json(path, data)
    if os.name != "nt":
        path.chmod(0o600)


def delete_profile(name: str) -> bool:
    """删除 profile。"""
    path = PROFILES_DIR / f"{name}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def get_active_profile() -> str | None:
    """获取当前激活的 profile 名。"""
    if ACTIVE_PROFILE_FILE.exists():
        name = ACTIVE_PROFILE_FILE.read_text(encoding="utf-8").strip()
        return name if name else None
    return None


def set_active_profile(name: str) -> None:
    """设置当前激活的 profile 名。"""
    ACTIVE_PROFILE_FILE.write_text(name + "\n", encoding="utf-8")


# ======================== 配置写入 ========================

def write_claude_settings(
    base_url: str,
    auth_token: str,
    model: str | None = None,
    small_model: str | None = None,
) -> None:
    """写入 ~/.claude/settings.json 的 env 块。

    保留已有的其他设置，仅更新/删除相关环境变量。
    model 和 small_model 用于非 Anthropic 兼容后端（如 DeepSeek），
    写入 ANTHROPIC_MODEL / ANTHROPIC_DEFAULT_*_MODEL / CLAUDE_CODE_SUBAGENT_MODEL。
    """
    settings: dict[str, Any] = {}
    if SETTINGS_FILE.exists():
        try:
            settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            settings = {}

    env = settings.get("env", {})
    env["ANTHROPIC_BASE_URL"] = base_url
    env["ANTHROPIC_AUTH_TOKEN"] = auth_token
    # 删除可能冲突的 API_KEY
    env.pop("ANTHROPIC_API_KEY", None)

    if model:
        env["ANTHROPIC_MODEL"] = model
        env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = model
        env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = model
        haiku_model = small_model or model
        env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = haiku_model
        env["CLAUDE_CODE_SUBAGENT_MODEL"] = haiku_model
    else:
        # 清除旧记录，避免切换到官方后残留 DeepSeek 设置
        env.pop("ANTHROPIC_MODEL", None)
        env.pop("ANTHROPIC_DEFAULT_OPUS_MODEL", None)
        env.pop("ANTHROPIC_DEFAULT_SONNET_MODEL", None)
        env.pop("ANTHROPIC_DEFAULT_HAIKU_MODEL", None)
        env.pop("CLAUDE_CODE_SUBAGENT_MODEL", None)

    # 确保 API_TIMEOUT_MS 是字符串
    if "API_TIMEOUT_MS" in env and isinstance(env["API_TIMEOUT_MS"], int):
        env["API_TIMEOUT_MS"] = str(env["API_TIMEOUT_MS"])
    settings["env"] = env

    _atomic_write_json(SETTINGS_FILE, settings)
    if os.name != "nt":
        SETTINGS_FILE.chmod(0o600)


def write_shtu_proxy_config(profile: Profile, port: int = SHTU_PROXY_PORT) -> None:
    """写入 ~/.config/SHTUClaudeProxy/config.json。"""
    SHTU_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    big = profile.big_model or "GPT-5.5"
    mid = profile.middle_model or big
    small = profile.small_model or big

    # 构建 base_url: 在 url 后追加 /response
    base_url = profile.url.rstrip("/")
    if not base_url.endswith("/response"):
        base_url += "/response"

    config = {
        "host": "127.0.0.1",
        "port": port,
        "default_model_id": big,
        "models": [
            {
                "name": big,
                "model_id": big,
                "base_url": base_url,
                "api_key": profile.key,
                "upstream_model": big,
                "api_format": "responses",
            }
        ],
    }

    _atomic_write_json(SHTU_CONFIG_FILE, config)
    if os.name != "nt":
        SHTU_CONFIG_FILE.chmod(0o600)


def write_genai_token(token: str) -> None:
    """写入 ~/.claude/genai-token.txt。"""
    GENAI_TOKEN_FILE.write_text(token, encoding="utf-8")
    if os.name != "nt":
        GENAI_TOKEN_FILE.chmod(0o600)


def write_genai_model(model: str) -> None:
    """写入 ~/.claude/genai-model.txt。"""
    GENAI_MODEL_FILE.write_text(model, encoding="utf-8")


def read_genai_model() -> str:
    """读取当前 GenAI 模型名。"""
    if GENAI_MODEL_FILE.exists():
        return GENAI_MODEL_FILE.read_text(encoding="utf-8").strip()
    return ""


def read_genai_token() -> str:
    """读取当前 GenAI JWT token。"""
    if GENAI_TOKEN_FILE.exists():
        return GENAI_TOKEN_FILE.read_text(encoding="utf-8").strip()
    return ""


# ======================== 切换逻辑 ========================

def apply_profile(profile: Profile) -> None:
    """应用 profile 到 Claude Code 配置（不涉及代理启停）。

    只负责写入配置文件，代理启停由 ProxyManager 负责。
    """
    set_active_profile(profile.name)

    if profile.type == "anthropic":
        write_claude_settings(
            profile.url, profile.key,
            model=profile.model,
            small_model=profile.small_model,
        )

    elif profile.type in ("genai-api", "openai"):
        write_shtu_proxy_config(profile)
        write_claude_settings(f"http://localhost:{SHTU_PROXY_PORT}", PROXY_AUTH_TOKEN)

    elif profile.type == "genai":
        write_genai_token(profile.key)
        write_genai_model(profile.big_model or "GPT-5.5")
        write_claude_settings(f"http://localhost:{GENAI2API_PORT}", PROXY_AUTH_TOKEN)


# ======================== 内部工具 ========================

def _dict_to_profile(name: str, data: dict[str, Any]) -> Profile:
    """从 JSON dict 构建 Profile 对象。"""
    return Profile(
        name=name,
        type=data.get("type", ""),
        url=data.get("url", ""),
        key=data.get("key", ""),
        model=data.get("model", ""),
        big_model=data.get("big_model", ""),
        middle_model=data.get("middle_model", ""),
        small_model=data.get("small_model", ""),
    )


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """原子写入 JSON 文件（写 tmp → 校验 → mv）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        # 验证写入的 JSON 有效
        json.loads(Path(tmp_path).read_text(encoding="utf-8"))
        os.replace(tmp_path, path)
    except Exception:
        os.unlink(tmp_path)
        raise
