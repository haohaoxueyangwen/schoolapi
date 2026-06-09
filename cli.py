#!/usr/bin/env python3
"""GenAI Stack CLI — cross-platform replacement for switch-model.sh.

用法:
    uv run python cli.py status
    uv run python cli.py use genai
    uv run python cli.py model GPT-5.5

也可通过 pyproject.toml entry point 调用:
    genai-stack-cli status
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import requests

from tui import config as cfg
from tui.proxy import ProxyManager
from tui.token_utils import jwt_remaining_str


IS_WINDOWS = os.name == "nt"


# ═══════════════════════════════════════════════════════════════
# Color helpers
# ═══════════════════════════════════════════════════════════════

class Ansi:
    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    CYAN = "\033[0;36m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    NC = "\033[0m"

    @staticmethod
    def _enable() -> None:
        if IS_WINDOWS:
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
            except Exception:
                pass


def green(s: str) -> str: return f"{Ansi.GREEN}{s}{Ansi.NC}"
def red(s: str) -> str: return f"{Ansi.RED}{s}{Ansi.NC}"
def yellow(s: str) -> str: return f"{Ansi.YELLOW}{s}{Ansi.NC}"
def cyan(s: str) -> str: return f"{Ansi.CYAN}{s}{Ansi.NC}"
def dim(s: str) -> str: return f"{Ansi.DIM}{s}{Ansi.NC}"
def bold(s: str) -> str: return f"{Ansi.BOLD}{s}{Ansi.NC}"


# ═══════════════════════════════════════════════════════════════
# Health check helper
# ═══════════════════════════════════════════════════════════════

def health_detail(port: int, name: str) -> str:
    """Return a colorized health-check string for a proxy."""
    try:
        resp = requests.get(f"http://localhost:{port}/health", timeout=2)
        if resp.status_code == 200:
            return f"{green('✓')} {name}"
        return f"{red('✗')} {name}"
    except (requests.ConnectionError, requests.Timeout):
        return f"{red('✗')} {name}"


# ═══════════════════════════════════════════════════════════════
# Model listing
# ═══════════════════════════════════════════════════════════════

def fetch_live_models(port: int = cfg.GENAI2API_PORT) -> dict[str, list[str]] | None:
    """Fetch model list from a running proxy, grouped by owned_by."""
    try:
        r = requests.get(f"http://localhost:{port}/v1/models", timeout=3)
        r.raise_for_status()
        data = r.json()
        grouped: dict[str, list[str]] = {}
        for m in data.get("data", []):
            g = m.get("owned_by", "other")
            grouped.setdefault(g, []).append(m.get("id", ""))
        return grouped
    except Exception:
        return None


def show_models() -> None:
    """显示可用模型列表。"""
    print()
    print(f"{cyan('═══ 可用模型列表 ═══')}")

    live = fetch_live_models(cfg.GENAI2API_PORT)
    if live:
        print()
        print(f"{green('▸ GenAI 平台在线模型')} {dim(f'(来自 genai2api :{cfg.GENAI2API_PORT})')}")
        for group, models in live.items():
            print(f"  {dim(f'── {group} ──')}")
            for m in models:
                print(f"    {m}")
    else:
        from tui.models import STATIC_MODELS_GENAI
        print()
        for group, models in STATIC_MODELS_GENAI.items():
            print(f"{green(f'【GenAI {group}模型】')}")
            for m in models:
                print(f"  {m}")
        print()
        print(f"{dim('(genai2api 未运行，显示静态列表)')}")

    print()
    print(f"{green('【Claude 官方模型】')}")
    print("  opus               Claude Opus 4.8")
    print("  sonnet             Claude Sonnet 4.6")
    print("  haiku              Claude Haiku 4.5")
    print()


# ═══════════════════════════════════════════════════════════════
# Status display
# ═══════════════════════════════════════════════════════════════

def show_status() -> None:
    """显示当前配置状态（对应 switch-model.sh show_status）。"""
    print()
    print(f"{cyan('═══ 当前配置状态 ═══')}")
    print()

    active = cfg.get_active_profile()
    if active and cfg.profile_exists(active):
        profile = cfg.load_profile(active)
        if profile:
            print(f"  Active profile: {green(profile.name)} ({cyan(profile.type)})")
    else:
        print(f"  Active profile: {yellow('未设置')} (运行 sm init)")

    # Proxy health
    print()
    print(f"  {health_detail(cfg.SHTU_PROXY_PORT, 'SHTUClaudeProxy :' + str(cfg.SHTU_PROXY_PORT))}")
    print(f"  {health_detail(cfg.GENAI2API_PORT, 'genai2api :' + str(cfg.GENAI2API_PORT))}")

    # JWT status
    token = cfg.read_genai_token()
    if token:
        print(f"  JWT: {jwt_remaining_str(token)}")
    else:
        print(f"  JWT: {dim('未设置')}")

    # settings.json env
    print()
    print(f"{cyan('settings.json env:')}")
    if cfg.SETTINGS_FILE.exists():
        import json
        try:
            settings = json.loads(cfg.SETTINGS_FILE.read_text(encoding="utf-8"))
            env = settings.get("env", {})
            base_url = env.get("ANTHROPIC_BASE_URL", "")
            auth_token = env.get("ANTHROPIC_AUTH_TOKEN", "")
            api_key = env.get("ANTHROPIC_API_KEY", "")
            mode = "GenAI 代理" if ("localhost" in base_url or "127.0.0.1" in base_url) else "Claude 直连"
            print(f"  模式:     {green(mode)}")
            print(f"  BASE_URL: {cyan(base_url)}")
            if auth_token:
                display = auth_token if len(auth_token) <= 12 else auth_token[:12] + "..."
                print(f"  AUTH_TOKEN: {cyan(display)}")
            if api_key:
                display = api_key if len(api_key) <= 12 else api_key[:12] + "..."
                print(f"  API_KEY: {cyan(display)}")
        except Exception:
            print(f"  {dim('(无法读取)')}")
    else:
        print(f"  {dim('(settings.json 不存在)')}")

    # Shell env residuals
    print()
    print(f"{cyan('当前 shell env:')}")
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        val = os.environ.get(var, "")
        if val:
            display = val if len(val) <= 16 else val[:12] + "..."
            print(f"  {var}={cyan(display)}")

    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    has_token = bool(os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    if has_key and has_token:
        print(f"  {red('⚠ shell 残留冲突，运行 sm fix 修复')}")
    print()


# ═══════════════════════════════════════════════════════════════
# Profile commands
# ═══════════════════════════════════════════════════════════════

def cmd_ls() -> None:
    """列出所有 profiles。"""
    profiles = cfg.list_profiles()
    if not profiles:
        print(f"{yellow('无 profile。运行 sm init 迁现有凭证，或 sm add 创建。')}")
        return
    active = cfg.get_active_profile()
    print()
    print(f"{cyan('═══ Profiles ═══')}")
    print()
    for p in profiles:
        marker = f"{green('★')} " if p.name == active else "  "
        key_display = p.masked_key or "(无)"
        url_display = p.display_url
        if p.type == "genai":
            url_display = f"(HTK) {p.display_model}"
        elif p.display_model != "未设置":
            url_display = f"{p.display_url} → {p.display_model}"
        print(f"  {marker}{p.name:<15} {p.type:<12} {url_display:<40} {key_display}")
    print()
    print(f"★ = active profile")
    print()


def cmd_show(name: str | None = None) -> None:
    """显示 profile 详情。"""
    if name is None:
        name = cfg.get_active_profile()
        if not name:
            print(f"{red('用法: sm show <name>  (无 active profile)')}")
            return
    if not cfg.profile_exists(name):
        print(f"{red(f'profile {name} 不存在')}")
        return
    active = cfg.get_active_profile()
    active_mark = f" {green('(active)')}" if name == active else ""
    print()
    print(f"{cyan(f'═══ profile: {name}')}{active_mark}")
    profile = cfg.load_profile(name)
    if profile:
        data = profile.to_json()
        for k, v in data.items():
            if k == "key" and v:
                v = v if len(v) <= 12 else v[:12] + f"...({len(v)} chars)"
            print(f"  {k}: {v}")
    print()


def cmd_use(name: str) -> int:
    """激活 profile。"""
    if not cfg.profile_exists(name):
        print(f"{red(f'profile {cyan(name)}{red} 不存在')}")
        cmd_ls()
        return 1

    profile = cfg.load_profile(name)
    if not profile:
        return 1

    proxy_mgr = ProxyManager()
    ok, msg = proxy_mgr.smart_switch(profile)
    if ok:
        print(f"{green('✓')} {msg}")
        print(f"  {yellow('⚠ 重启 Claude Code 才能生效')}")
    else:
        print(f"{red('✗')} {msg}")
        return 1
    return 0


def cmd_model(model: str) -> int:
    """热切换模型。"""
    active = cfg.get_active_profile()
    if not active:
        print(f"{red('无 active profile')}")
        return 1

    profile = cfg.load_profile(active)
    if not profile:
        return 1

    proxy_mgr = ProxyManager()
    ok, msg = proxy_mgr.switch_model(profile, model)
    if ok:
        print(f"{green('✓')} {msg}")
    else:
        print(f"{red('✗')} {msg}")
        return 1
    return 0


def cmd_add(subtype: str, name: str, **kwargs: str) -> int:
    """新建 profile。"""
    if cfg.profile_exists(name):
        print(f"{red(f'profile {name} 已存在。用 sm edit {name} 修改或 sm rm {name} 删除')}")
        return 1

    cfg.PROFILES_DIR.mkdir(parents=True, exist_ok=True)

    if subtype in ("anthropic", "a"):
        url = kwargs.get("url") or input("Anthropic URL (e.g. https://api.anthropic.com): ")
        key = kwargs.get("key") or input("Auth token (sk-...): ")
        model = kwargs.get("model", "opus")
        profile = cfg.Profile(name=name, type="anthropic", url=url, key=key, model=model)

    elif subtype in ("openai", "o"):
        url = kwargs.get("url") or input("OpenAI 兼容 URL (e.g. https://api.school.edu/v1): ")
        key = kwargs.get("key") or input("API key: ")
        big = kwargs.get("big") or input("Big model (opus 映射，如 gpt-4o): ")
        mid = kwargs.get("middle", big)
        small = kwargs.get("small", big)
        profile = cfg.Profile(name=name, type="openai", url=url, key=key,
                              big_model=big, middle_model=mid, small_model=small)

    elif subtype in ("genai", "g"):
        key = kwargs.get("key") or kwargs.get("token") or input("GenAI JWT token: ")
        big = kwargs.get("big", "GPT-5.5")
        mid = kwargs.get("middle", big)
        small = kwargs.get("small", big)
        profile = cfg.Profile(name=name, type="genai", key=key,
                              big_model=big, middle_model=mid, small_model=small)

    elif subtype in ("genai-api", "ga"):
        url = kwargs.get("url", "https://genaiapi.shanghaitech.edu.cn/api/v1")
        key = kwargs.get("key") or input("GenAI API key: ")
        big = kwargs.get("big") or input("Big model (opus 映射，如 GPT-5.5): ")
        mid = kwargs.get("middle", big)
        small = kwargs.get("small", big)
        profile = cfg.Profile(name=name, type="genai-api", url=url, key=key,
                              big_model=big, middle_model=mid, small_model=small)
    else:
        print(f"{red(f'未知 type: {subtype} (期望 anthropic|openai|genai|genai-api)')}")
        return 1

    cfg.save_profile(profile)
    print(f"{green('✓')} 创建 profile {cyan(name)} ({profile.type})")
    return 0


def cmd_rm(name: str) -> int:
    """删除 profile。"""
    if not cfg.profile_exists(name):
        print(f"{red(f'profile {name} 不存在')}")
        return 1
    active = cfg.get_active_profile()
    if name == active:
        print(f"{red(f'{name} 当前 active，先 sm use <其他> 切走再删')}")
        return 1
    confirm = input(f"确认删除 profile '{name}'? [y/N] ")
    if confirm.lower() in ("y", "yes"):
        cfg.delete_profile(name)
        print(f"{green('✓')} 已删 profile {cyan(name)}")
    else:
        print("取消")
    return 0


def cmd_edit(name: str) -> int:
    """编辑 profile。"""
    if not cfg.profile_exists(name):
        print(f"{red(f'profile {name} 不存在')}")
        return 1
    path = cfg.PROFILES_DIR / f"{name}.json"
    editor = os.environ.get("EDITOR", "notepad" if IS_WINDOWS else "vi")
    import subprocess
    subprocess.run([editor, str(path)])
    # Validate JSON after edit
    import json
    try:
        json.loads(path.read_text(encoding="utf-8"))
        print(f"{green('✓')} 已保存 profile {cyan(name)}")
    except json.JSONDecodeError:
        print(f"{red('警告: 编辑后 JSON 无效。请修复。')}")
        return 1
    return 0


def cmd_init() -> int:
    """从现有 settings/token 文件迁移生成 profiles。"""
    cfg.PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    if not IS_WINDOWS:
        cfg.PROFILES_DIR.chmod(0o700)

    created = 0

    # 1. Anthropic credentials from settings.json
    import json
    cur_url = ""
    cur_token = ""
    if cfg.SETTINGS_FILE.exists():
        try:
            settings = json.loads(cfg.SETTINGS_FILE.read_text(encoding="utf-8"))
            env = settings.get("env", {})
            cur_url = env.get("ANTHROPIC_BASE_URL", "")
            cur_token = env.get("ANTHROPIC_AUTH_TOKEN", "")
        except Exception:
            pass

    if cur_token and cur_url and "localhost" not in cur_url and "127.0.0.1" not in cur_url:
        if not cfg.profile_exists("claude"):
            profile = cfg.Profile(name="claude", type="anthropic",
                                  url=cur_url, key=cur_token, model="opus")
            cfg.save_profile(profile)
            print(f"{green('✓')} 创建 profile {cyan('claude')} (anthropic, {cur_url})")
            created += 1
        else:
            print(f"{yellow('⚠')} profile 'claude' 已存在，跳过")

    # 2. GenAI credentials from token file
    token = cfg.read_genai_token()
    if token:
        if not cfg.profile_exists("genai"):
            big = cfg.read_genai_model() or "GPT-5.5"
            profile = cfg.Profile(name="genai", type="genai", key=token,
                                  big_model=big, middle_model=big, small_model=big)
            cfg.save_profile(profile)
            print(f"{green('✓')} 创建 profile {cyan('genai')} (genai, model={big})")
            created += 1
        else:
            print(f"{yellow('⚠')} profile 'genai' 已存在，跳过")

    # 3. Set active profile
    if not cfg.get_active_profile():
        # Detect current mode from settings
        if "localhost" in cur_url or "127.0.0.1" in cur_url:
            if cfg.profile_exists("genai"):
                cfg.set_active_profile("genai")
                print(f"{green('✓')} active profile = {cyan('genai')}")
        elif cfg.profile_exists("claude"):
            cfg.set_active_profile("claude")
            print(f"{green('✓')} active profile = {cyan('claude')}")
    else:
        print(f"{yellow('⚠')} active-profile 已存在 ({cfg.get_active_profile()})，未变更")

    print()
    print(f"创建 {created} 个 profile")
    print(f"运行 {cyan('sm ls')} 查看所有 profile")
    return 0


def cmd_token(*args: str) -> int:
    """更新 profile token。"""
    import json
    if len(args) == 1:
        target_name = cfg.get_active_profile()
        new_token = args[0]
        if not target_name:
            print(f"{red('无 active profile，请用 sm token <name> <token>')}")
            return 1
    elif len(args) == 2:
        target_name, new_token = args
    else:
        print(f"{red('用法: sm token [<name>] <token>')}")
        return 1

    if not cfg.profile_exists(target_name):
        print(f"{red(f'profile {target_name} 不存在')}")
        return 1

    path = cfg.PROFILES_DIR / f"{target_name}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["key"] = new_token
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    profile = cfg.load_profile(target_name)
    if profile:
        active = cfg.get_active_profile()
        if target_name == active:
            if profile.type == "genai":
                cfg.write_genai_token(new_token)
                print(f"{green('✓')} ~/.claude/genai-token.txt 已同步")
            elif profile.type == "genai-api":
                cfg.write_shtu_proxy_config(profile)
                print(f"{green('✓')} SHTUClaudeProxy config 已更新")

    print(f"{green('✓')} profile {cyan(target_name)} token 已更新")
    return 0


# ═══════════════════════════════════════════════════════════════
# Proxy management commands
# ═══════════════════════════════════════════════════════════════

def _get_active_profile():
    """Helper: get active profile or print error."""
    active = cfg.get_active_profile()
    if not active:
        print(f"{red('无 active profile，先运行 sm use <profile>')}")
        return None
    profile = cfg.load_profile(active)
    if not profile:
        print(f"{red('Profile 加载失败')}")
        return None
    return profile


def cmd_start() -> int:
    """启动当前 profile 对应的代理。"""
    profile = _get_active_profile()
    if not profile:
        return 1
    proxy_mgr = ProxyManager()
    ok = proxy_mgr.smart_start(profile)
    if ok:
        print(f"{green('✓')} 代理启动成功")
    else:
        print(f"{red('✗')} 代理启动失败，查看 {cfg.LOG_DIR}")
        return 1
    return 0


def cmd_stop() -> int:
    """停止所有代理。"""
    proxy_mgr = ProxyManager()
    proxy_mgr.stop_all()
    print(f"{green('✓')} 所有代理已停止")
    return 0


def cmd_restart() -> int:
    """重启当前代理。"""
    profile = _get_active_profile()
    if not profile:
        return 1
    proxy_mgr = ProxyManager()
    proxy_mgr.stop_all()
    time.sleep(1)
    ok = proxy_mgr.smart_start(profile)
    if ok:
        print(f"{green('✓')} 代理重启成功")
    else:
        print(f"{red('✗')} 代理重启失败")
        return 1
    return 0


def cmd_fix() -> int:
    """修复 settings.json 冲突。"""
    if not cfg.SETTINGS_FILE.exists():
        print(f"{green('✓')} settings.json 无冲突")
        return 0

    import json
    try:
        settings = json.loads(cfg.SETTINGS_FILE.read_text(encoding="utf-8"))
        env = settings.get("env", {})
        has_key = "ANTHROPIC_API_KEY" in env
        has_token = "ANTHROPIC_AUTH_TOKEN" in env
        base_url = env.get("ANTHROPIC_BASE_URL", "")
        is_genai = "localhost" in base_url or "127.0.0.1" in base_url

        if has_key and has_token:
            if is_genai:
                del env["ANTHROPIC_AUTH_TOKEN"]
                print(f"{green('✓')} 检测到 GenAI 模式，已删除 AUTH_TOKEN")
            else:
                del env["ANTHROPIC_API_KEY"]
                print(f"{green('✓')} 检测到 Claude 模式，已删除 API_KEY")
            settings["env"] = env
            cfg.SETTINGS_FILE.write_text(
                json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8")
        else:
            print(f"{green('✓')} settings.json 无冲突")
    except Exception as e:
        print(f"{red(f'修复失败: {e}')}")
        return 1
    return 0


def cmd_menu() -> None:
    """启动 TUI 交互界面。"""
    from tui.app import main
    main()


def cmd_gui() -> None:
    """启动 Windows 原生 GUI 控制面板。"""
    from gui import main
    main()


# ═══════════════════════════════════════════════════════════════
# Main entry point
# ═══════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="GenAI Stack CLI — 管理 AI 后端代理和 Profile",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
架构（三模式）:
  anthropic:    Claude Code → sssaicode → Anthropic API
  genai-token:  Claude Code → genai2api(:5000) → GenAI HTK
  genai-api:    Claude Code → SHTUClaudeProxy(:8082) → genaiapi.shanghaitech.edu.cn/api/v1

示例:
  sm use genai           # 切到 genai profile
  sm use claude          # 切回 Anthropic 直连
  sm model GPT-5.5       # 热切换模型
  sm status              # 查看状态
  sm ls                  # 列出所有 profile
  sm add genai mygenai --key JWT_TOKEN --big GPT-5.5
  sm token mygenai NEW_JWT_TOKEN
  sm start               # 启动代理
  sm restart             # 重启代理
  sm menu                # 启动 TUI 控制面板
""",
    )

    sub = parser.add_subparsers(dest="command")

    # use
    p_use = sub.add_parser("use", help="切换到指定 profile")
    p_use.add_argument("name", help="Profile 名称")

    # model
    p_model = sub.add_parser("model", aliases=["m"], help="热切换模型")
    p_model.add_argument("name", help="模型名 (如 GPT-5.5, deepseek-chat)")

    # ls
    sub.add_parser("ls", help="列出所有 profile")

    # show
    p_show = sub.add_parser("show", help="显示 profile 详情")
    p_show.add_argument("name", nargs="?", help="Profile 名称 (默认当前激活)")

    # add
    p_add = sub.add_parser("add", help="新建 profile")
    p_add.add_argument("type", help="Profile 类型: anthropic|openai|genai|genai-api")
    p_add.add_argument("name", help="Profile 名称")
    p_add.add_argument("--url", help="API URL")
    p_add.add_argument("--key", help="API key / JWT token")
    p_add.add_argument("--token", help="(genai) JWT token")
    p_add.add_argument("--big", help="Big model (opus 映射)")
    p_add.add_argument("--middle", help="Middle model (sonnet 映射)")
    p_add.add_argument("--small", help="Small model (haiku 映射)")
    p_add.add_argument("--model", help="(anthropic) 默认模型")

    # rm
    p_rm = sub.add_parser("rm", help="删除 profile")
    p_rm.add_argument("name", help="Profile 名称")

    # edit
    p_edit = sub.add_parser("edit", help="用编辑器编辑 profile JSON")
    p_edit.add_argument("name", help="Profile 名称")

    # init
    sub.add_parser("init", help="从现有凭证迁移生成 profiles")

    # token
    p_token = sub.add_parser("token", aliases=["t"], help="更新 profile token")
    p_token.add_argument("args", nargs="+", help="[name] <token> 或 <token>")

    # start / stop / restart
    sub.add_parser("start", help="启动当前 profile 对应代理")
    sub.add_parser("stop", help="停止所有代理")
    sub.add_parser("restart", aliases=["r"], help="重启代理")

    # status
    sub.add_parser("status", aliases=["s"], help="查看当前配置状态")

    # list / models
    sub.add_parser("list", aliases=["l"], help="显示可用模型列表")
    sub.add_parser("models", help="显示可用模型列表")

    # fix
    sub.add_parser("fix", help="修复 settings.json Auth conflict")

    # menu
    sub.add_parser("menu", help="启动 TUI 交互控制面板")

    # gui
    sub.add_parser("gui", help="启动 Windows 原生 GUI 控制面板")

    return parser


def main(argv: list[str] | None = None) -> int:
    Ansi._enable()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    cmd = args.command

    if cmd == "use":
        return cmd_use(args.name)
    elif cmd in ("model", "m"):
        return cmd_model(args.name)
    elif cmd == "ls":
        cmd_ls()
    elif cmd == "show":
        cmd_show(args.name)
    elif cmd == "add":
        kwargs = {k: v for k, v in vars(args).items()
                  if k not in ("command", "type", "name") and v is not None}
        return cmd_add(args.type, args.name, **kwargs)
    elif cmd == "rm":
        return cmd_rm(args.name)
    elif cmd == "edit":
        return cmd_edit(args.name)
    elif cmd == "init":
        return cmd_init()
    elif cmd in ("token", "t"):
        return cmd_token(*args.args)
    elif cmd == "start":
        return cmd_start()
    elif cmd == "stop":
        return cmd_stop()
    elif cmd in ("restart", "r"):
        return cmd_restart()
    elif cmd in ("status", "s"):
        show_status()
    elif cmd in ("list", "l", "models"):
        show_models()
    elif cmd == "fix":
        return cmd_fix()
    elif cmd == "menu":
        cmd_menu()
    elif cmd == "gui":
        cmd_gui()
    else:
        print(f"{red(f'未知命令: {cmd}')}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
