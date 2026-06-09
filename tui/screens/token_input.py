"""Token 输入弹窗。"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static, TextArea

from ..token_utils import is_jwt, jwt_remaining_str


class TokenInputScreen(ModalScreen[str | None]):
    """Token/凭证输入弹窗。"""

    DEFAULT_CSS = """
    TokenInputScreen {
        align: center middle;
    }

    #token-container {
        width: 70;
        height: auto;
        max-height: 20;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }

    #token-title {
        text-style: bold;
        text-align: center;
        margin-bottom: 1;
        color: $accent;
    }

    #token-help {
        color: $text-muted;
        margin-bottom: 1;
    }

    #token-input {
        height: 5;
        margin-bottom: 1;
    }

    #token-status {
        height: 1;
        margin-bottom: 1;
    }

    #token-buttons {
        layout: horizontal;
        align: center middle;
        height: 3;
    }

    #token-buttons Button {
        margin: 0 2;
    }
    """

    BINDINGS = [
        ("escape", "cancel", "取消"),
    ]

    def __init__(self, current_token: str = "", profile_name: str = "") -> None:
        super().__init__()
        self._current_token = current_token
        self._profile_name = profile_name

    def compose(self) -> ComposeResult:
        title = f"更新 Token — {self._profile_name}" if self._profile_name else "更新 Token"
        with Vertical(id="token-container"):
            yield Static(f"[bold]{title}[/]", id="token-title", markup=True)
            yield Static(
                "  支持: JWT Token (eyJ...) 或 学号@密码 (如 2024xxx@mypass)",
                id="token-help",
            )
            yield TextArea(
                self._current_token,
                id="token-input",
            )
            yield Static("", id="token-status")
            with Horizontal(id="token-buttons"):
                yield Button("保存", variant="primary", id="btn-save-token")
                yield Button("取消", variant="default", id="btn-cancel-token")

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """实时显示 JWT 状态。"""
        status = self.query_one("#token-status", Static)
        value = event.text_area.text.strip()
        if not value:
            status.update("")
        elif is_jwt(value):
            remaining = jwt_remaining_str(value)
            status.update(f"  JWT 状态: {remaining}")
        else:
            if "@" in value:
                status.update("  [dim]凭证模式: 学号@密码 (CAS 自动登录)[/]")
            else:
                status.update("  [yellow]格式未识别[/]")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel-token":
            self.dismiss(None)
        elif event.button.id == "btn-save-token":
            text_area = self.query_one("#token-input", TextArea)
            value = text_area.text.strip()
            if value:
                self.dismiss(value)
            else:
                status = self.query_one("#token-status", Static)
                status.update("[red]请输入 Token[/]")

    def action_cancel(self) -> None:
        self.dismiss(None)
