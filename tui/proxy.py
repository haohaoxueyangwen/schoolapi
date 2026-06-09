"""代理进程管理模块。

管理 SHTUClaudeProxy 和 shanghaitech-genai2api 两个代理的启停和健康检查。
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import requests

from . import config as cfg

IS_WINDOWS = os.name == "nt"


class ProxyManager:
    """统一管理两个代理进程。"""

    def __init__(self) -> None:
        self._shtu_proc: subprocess.Popen | None = None
        self._genai2api_proc: subprocess.Popen | None = None

    # ======================== 健康检查 ========================

    def is_shtu_running(self) -> bool:
        """检查 SHTUClaudeProxy 是否在运行。"""
        return self._check_health(cfg.SHTU_PROXY_PORT)

    def is_genai2api_running(self) -> bool:
        """检查 genai2api 是否在运行。"""
        return self._check_health(cfg.GENAI2API_PORT)

    def health_check(self) -> dict[str, bool]:
        """返回两个代理的健康状态。"""
        return {
            "shtu_proxy": self.is_shtu_running(),
            "genai2api": self.is_genai2api_running(),
        }

    # ======================== SHTUClaudeProxy ========================

    def start_shtu_proxy(self) -> bool:
        """启动 SHTUClaudeProxy。"""
        if self.is_shtu_running():
            return True

        self._ensure_port_free(cfg.SHTU_PROXY_PORT, "SHTUClaudeProxy")

        cfg_path = cfg.SHTU_CONFIG_FILE
        if not cfg_path.exists():
            return False

        cli_path = cfg.SHTU_PROXY_DIR / "cli.py"
        if not cli_path.exists():
            return False

        cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
        if not IS_WINDOWS:
            (cfg.CLAUDE_DIR / "genai-stack").chmod(0o700)
            cfg.LOG_DIR.chmod(0o700)
        log_file = open(cfg.LOG_DIR / "shtu-proxy.log", "w")
        env = os.environ.copy()
        env["CLAUDE_RESPONSES_PROXY_CONFIG"] = str(cfg_path)

        popen_kwargs: dict = {
            "stdout": log_file,
            "stderr": subprocess.STDOUT,
            "env": env,
        }
        if not IS_WINDOWS:
            popen_kwargs["start_new_session"] = True
        # On Windows, CREATE_NO_WINDOW to avoid console popup
        if IS_WINDOWS and hasattr(subprocess, "CREATE_NO_WINDOW"):
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        self._shtu_proc = subprocess.Popen(
            [sys.executable, str(cli_path), "serve"],
            **popen_kwargs,
        )

        # 等待启动（最多 10 秒）
        return self._wait_for_health(cfg.SHTU_PROXY_PORT, timeout=10)

    def stop_shtu_proxy(self) -> None:
        """停止 SHTUClaudeProxy。"""
        self._kill_by_pattern("proxies/SHTUClaudeProxy/cli.py")
        if self._shtu_proc:
            try:
                self._shtu_proc.terminate()
                self._shtu_proc.wait(timeout=3)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass
            self._shtu_proc = None

    # ======================== genai2api ========================

    def start_genai2api(self, token: str) -> bool:
        """启动 shanghaitech-genai2api。"""
        if self.is_genai2api_running():
            return True

        self._ensure_port_free(cfg.GENAI2API_PORT, "genai2api")

        if not token:
            token = cfg.read_genai_token()
        if not token:
            return False

        main_path = cfg.GENAI2API_DIR / "main.py"
        if not main_path.exists():
            return False

        # 确保 .venv 存在
        venv_dir = cfg.GENAI2API_DIR / ".venv"
        if not venv_dir.exists():
            result = subprocess.run(
                ["uv", "sync"],
                cwd=str(cfg.GENAI2API_DIR),
                capture_output=True,
                timeout=60,
            )
            if result.returncode != 0:
                return False

        cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
        if not IS_WINDOWS:
            (cfg.CLAUDE_DIR / "genai-stack").chmod(0o700)
            cfg.LOG_DIR.chmod(0o700)
        log_file = open(cfg.LOG_DIR / "genai2api.log", "w")
        env = os.environ.copy()
        env["GENAI_TOKEN_FILE"] = str(cfg.GENAI_TOKEN_FILE)
        env["API_KEY"] = cfg.PROXY_AUTH_TOKEN
        env.pop("VIRTUAL_ENV", None)  # 防止父 .venv 泄漏
        popen_kwargs: dict = {
            "cwd": str(cfg.GENAI2API_DIR),
            "stdout": log_file,
            "stderr": subprocess.STDOUT,
            "env": env,
        }
        if not IS_WINDOWS:
            popen_kwargs["start_new_session"] = True
        if IS_WINDOWS and hasattr(subprocess, "CREATE_NO_WINDOW"):
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        self._genai2api_proc = subprocess.Popen(
            [
                "uv", "run", "main.py",
                "--host", "127.0.0.1",
                "--port", str(cfg.GENAI2API_PORT),
                "--api-format", "anthropic",
            ],
            **popen_kwargs,
        )

        # 等待启动（最多 15 秒, genai2api 有 CAS 登录可能较慢）
        return self._wait_for_health(cfg.GENAI2API_PORT, timeout=15)

    def stop_genai2api(self) -> None:
        """停止 genai2api。"""
        self._kill_by_pattern("proxies/genai2api/main.py")
        if self._genai2api_proc:
            try:
                self._genai2api_proc.terminate()
                self._genai2api_proc.wait(timeout=3)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass
            self._genai2api_proc = None

    # ======================== 高级操作 ========================

    def stop_all(self) -> None:
        """停止所有代理。"""
        self.stop_shtu_proxy()
        self.stop_genai2api()

    def smart_start(self, profile: cfg.Profile) -> bool:
        """根据 Profile 类型自动启动正确的代理。"""
        if profile.type == "anthropic":
            return True  # 不需要代理
        elif profile.type in ("genai-api", "openai"):
            return self.start_shtu_proxy()
        elif profile.type == "genai":
            return self.start_genai2api(profile.key)
        return False

    def smart_switch(self, profile: cfg.Profile) -> tuple[bool, str]:
        """完整的 Profile 切换：停旧 → 写配置 → 启新。

        Returns:
            (成功?, 消息)
        """
        # 1. 停止所有代理
        self.stop_all()
        time.sleep(0.5)

        # 2. 写配置
        cfg.apply_profile(profile)

        # 3. 启动对应代理
        if profile.type == "anthropic":
            return True, f"已切换到 {profile.name} (Anthropic 直连)"

        elif profile.type in ("genai-api", "openai"):
            ok = self.start_shtu_proxy()
            if ok:
                return True, f"已切换到 {profile.name} (SHTUClaudeProxy :8082)"
            return False, f"SHTUClaudeProxy 启动失败，查看 {cfg.LOG_DIR / 'shtu-proxy.log'}"

        elif profile.type == "genai":
            ok = self.start_genai2api(profile.key)
            if ok:
                return True, f"已切换到 {profile.name} (genai2api :5000)"
            return False, f"genai2api 启动失败，查看 {cfg.LOG_DIR / 'genai2api.log'}"

        return False, f"未知 profile 类型: {profile.type}"

    def switch_model(self, profile: cfg.Profile, model: str) -> tuple[bool, str]:
        """模型热切换。"""
        if profile.type == "genai":
            cfg.write_genai_model(model)
            # 更新 profile JSON
            profile.big_model = model
            profile.middle_model = model
            profile.small_model = model
            cfg.save_profile(profile)
            return True, f"模型已切换: {model} (即时生效)"

        elif profile.type in ("genai-api", "openai"):
            profile.big_model = model
            profile.middle_model = model
            profile.small_model = model
            cfg.save_profile(profile)
            cfg.write_shtu_proxy_config(profile)
            self.stop_shtu_proxy()
            time.sleep(0.5)
            ok = self.start_shtu_proxy()
            if ok:
                return True, f"模型已切换: {model} (SHTUClaudeProxy 已重启)"
            return False, "SHTUClaudeProxy 重启失败"

        elif profile.type == "anthropic":
            if profile.model:
                profile.model = model
                cfg.save_profile(profile)
                cfg.write_claude_settings(
                    profile.url, profile.key,
                    model=profile.model,
                    small_model=profile.small_model,
                )
                return True, f"模型已切换: {model}"
            return True, "Claude 官方模型由 Claude Code 自行管理"

        return False, f"未知类型: {profile.type}"

    # ======================== 内部工具 ========================

    @staticmethod
    def _ensure_port_free(port: int, label: str) -> None:
        """Kill any process occupying the port before starting a new one."""
        if IS_WINDOWS:
            try:
                result = subprocess.run(
                    ["netstat", "-ano"],
                    capture_output=True, text=True, timeout=5,
                )
                for line in result.stdout.splitlines():
                    if f":{port}" in line and "LISTENING" in line:
                        parts = line.split()
                        pid = parts[-1]
                        subprocess.run(
                            ["taskkill", "/F", "/PID", pid],
                            capture_output=True, timeout=5,
                        )
                        time.sleep(0.5)
                        break
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass
        else:
            try:
                result = subprocess.run(
                    ["fuser", f"{port}/tcp"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.stdout.strip():
                    subprocess.run(
                        ["fuser", "-k", f"{port}/tcp"],
                        capture_output=True, timeout=5,
                    )
                    time.sleep(0.5)
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

    @staticmethod
    def _check_health(port: int) -> bool:
        """HTTP 健康检查。"""
        try:
            r = requests.get(f"http://localhost:{port}/health", timeout=2)
            return r.status_code == 200
        except (requests.ConnectionError, requests.Timeout):
            return False

    @staticmethod
    def _wait_for_health(port: int, timeout: int = 10) -> bool:
        """等待代理启动就绪。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if ProxyManager._check_health(port):
                return True
            time.sleep(0.5)
        return False

    @staticmethod
    def _kill_by_pattern(pattern: str) -> None:
        """通过进程名模式 kill。"""
        if IS_WINDOWS:
            # On Windows, filter by command line containing the pattern
            try:
                subprocess.run(
                    ["taskkill", "/F", "/FI", f"IMAGENAME eq python.exe"],
                    capture_output=True, timeout=10,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass
        else:
            try:
                subprocess.run(
                    ["pkill", "-f", pattern],
                    capture_output=True,
                    timeout=5,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass
