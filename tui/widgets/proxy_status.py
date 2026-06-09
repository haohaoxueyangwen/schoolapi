"""代理状态显示面板。"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from ..config import SHTU_PROXY_PORT, GENAI2API_PORT


class ProxyStatus(Vertical):
    """显示两个代理的运行状态。"""

    DEFAULT_CSS = """
    ProxyStatus {
        height: auto;
        max-height: 8;
        border: solid $primary-background-lighten-2;
        padding: 1 2;
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(
            "[bold $accent]代理状态[/]",
            id="proxy-title",
            markup=True,
        )
        yield Static("", id="proxy-content")

    def update_status(self, shtu_running: bool, genai2api_running: bool) -> None:
        """更新代理状态显示。"""
        content = self.query_one("#proxy-content", Static)

        shtu_icon = "[bold green]● 运行中[/]" if shtu_running else "[bold red]● 停止[/]"
        g2a_icon = "[bold green]● 运行中[/]" if genai2api_running else "[bold red]● 停止[/]"

        text = (
            f"  SHTUClaudeProxy :{SHTU_PROXY_PORT}    {shtu_icon}\n"
            f"  genai2api       :{GENAI2API_PORT}    {g2a_icon}\n"
            f"\n"
            f"  [dim][S]启动 [R]重启 [X]停止[/]"
        )
        content.update(text)
