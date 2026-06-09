"""右侧 Profile 详情面板。"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from ..config import Profile
from ..token_utils import jwt_remaining_str, is_jwt


class ProfileDetail(Vertical):
    """显示当前选中 Profile 的详情。"""

    DEFAULT_CSS = """
    ProfileDetail {
        height: auto;
        max-height: 12;
        border: solid $primary-background-lighten-2;
        padding: 1 2;
        margin: 1 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(
            "[bold $accent]Profile 详情[/]",
            id="detail-title",
            markup=True,
        )
        yield Static("", id="detail-content")

    def update_profile(self, profile: Profile | None) -> None:
        """更新显示的 Profile 信息。"""
        content = self.query_one("#detail-content", Static)
        if profile is None:
            content.update("选择一个 Profile 查看详情")
            return

        type_labels = {
            "anthropic": "[cyan]Anthropic 直连[/]",
            "genai": "[yellow]GenAI JWT (HTK)[/]",
            "genai-api": "[green]GenAI API Key[/]",
            "openai": "[magenta]OpenAI 兼容[/]",
        }

        lines = [
            f"  [dim]类型:[/]  {type_labels.get(profile.type, profile.type)}",
        ]

        if profile.url:
            url_display = profile.url
            if len(url_display) > 45:
                url_display = url_display[:42] + "..."
            lines.append(f"  [dim]URL:[/]   {url_display}")

        if profile.key:
            lines.append(f"  [dim]认证:[/]  {profile.masked_key}")
            if profile.type == "genai" and is_jwt(profile.key):
                remaining = jwt_remaining_str(profile.key)
                lines.append(f"  [dim]JWT:[/]   {remaining}")

        lines.append(f"  [dim]模型:[/]  [bold]{profile.display_model}[/]")

        if profile.type in ("genai", "genai-api", "openai"):
            mid = profile.middle_model or profile.big_model
            small = profile.small_model or profile.big_model
            if mid != profile.big_model or small != profile.big_model:
                lines.append(f"  [dim]mid:[/]   {mid}  [dim]small:[/] {small}")

        content.update("\n".join(lines))
