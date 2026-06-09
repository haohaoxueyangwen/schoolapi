"""新建/编辑 Profile 弹窗。"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal, Center
from textual.screen import ModalScreen
from textual.widgets import (
    Button, Input, Label, RadioButton, RadioSet, Static,
)

from ..config import Profile, save_profile, profile_exists


# 默认 URL 预设
DEFAULT_URLS = {
    "anthropic": "https://node-hk.sssaicode.com/api",
    "genai-api": "https://genaiapi.shanghaitech.edu.cn/api/v1",
    "genai": "",
    "openai": "",
}


class ProfileFormScreen(ModalScreen[Profile | None]):
    """新建或编辑 Profile 的弹窗。"""

    DEFAULT_CSS = """
    ProfileFormScreen {
        align: center middle;
    }

    #form-container {
        width: 70;
        max-height: 32;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }

    #form-title {
        text-style: bold;
        text-align: center;
        margin-bottom: 1;
        color: $accent;
    }

    .form-row {
        layout: horizontal;
        height: 3;
        margin: 0 0;
    }

    .form-label {
        width: 14;
        padding-top: 1;
        color: $text-muted;
    }

    .form-input {
        width: 1fr;
    }

    #type-select {
        height: auto;
        margin: 0 0 1 0;
    }

    #form-buttons {
        layout: horizontal;
        align: center middle;
        height: 3;
        margin-top: 1;
    }

    #form-buttons Button {
        margin: 0 2;
    }

    #form-error {
        color: $error;
        text-align: center;
        height: 1;
    }
    """

    BINDINGS = [
        ("escape", "cancel", "取消"),
    ]

    def __init__(
        self,
        edit_profile: Profile | None = None,
        name: str = "",
    ) -> None:
        super().__init__()
        self._edit = edit_profile
        self._initial_name = name

    def compose(self) -> ComposeResult:
        is_edit = self._edit is not None
        title = "编辑 Profile" if is_edit else "新建 Profile"

        with Vertical(id="form-container"):
            yield Static(f"[bold]{title}[/]", id="form-title", markup=True)

            # Profile 名称
            with Horizontal(classes="form-row"):
                yield Label("名称", classes="form-label")
                yield Input(
                    value=self._edit.name if is_edit else self._initial_name,
                    placeholder="例如: myapi",
                    id="input-name",
                    classes="form-input",
                    disabled=is_edit,
                )

            # 类型选择
            yield Static("  [dim]类型:[/]", markup=True)
            default_type = self._edit.type if is_edit else "genai-api"
            with RadioSet(id="type-select"):
                yield RadioButton("anthropic  (Anthropic 直连)", value=default_type == "anthropic", id="type-anthropic")
                yield RadioButton("genai-api  (API Key 模式)", value=default_type == "genai-api", id="type-genai-api")
                yield RadioButton("genai      (JWT Token 模式)", value=default_type == "genai", id="type-genai")
                yield RadioButton("openai     (OpenAI 兼容)", value=default_type == "openai", id="type-openai")

            # URL
            with Horizontal(classes="form-row", id="row-url"):
                yield Label("URL", classes="form-label")
                yield Input(
                    value=self._edit.url if is_edit else DEFAULT_URLS.get(default_type, ""),
                    placeholder="API URL",
                    id="input-url",
                    classes="form-input",
                )

            # Key / Token
            with Horizontal(classes="form-row"):
                yield Label("Key/Token", classes="form-label")
                yield Input(
                    value=self._edit.key if is_edit else "",
                    placeholder="API Key, JWT Token, 或 学号@密码",
                    id="input-key",
                    classes="form-input",
                    password=True,
                )

            # 模型
            with Horizontal(classes="form-row"):
                yield Label("大模型", classes="form-label")
                yield Input(
                    value=self._get_model_value(),
                    placeholder="例如: GPT-5.5, deepseek-pro, opus",
                    id="input-model",
                    classes="form-input",
                )

            # 小模型（anthropic 类型专用，用于 haiku/subagent 层级）
            with Horizontal(classes="form-row", id="row-small-model"):
                yield Label("小模型", classes="form-label")
                yield Input(
                    value=self._get_small_model_value(),
                    placeholder="例如: deepseek-v4-flash（留空则与大模型相同）",
                    id="input-small-model",
                    classes="form-input",
                )

            yield Static("", id="form-error")

            # 按钮
            with Horizontal(id="form-buttons"):
                yield Button("确定", variant="primary", id="btn-save")
                yield Button("取消", variant="default", id="btn-cancel")

    def _get_model_value(self) -> str:
        if self._edit is None:
            return ""
        if self._edit.type == "anthropic":
            return self._edit.model or "opus"
        return self._edit.big_model or ""

    def _get_small_model_value(self) -> str:
        if self._edit is None:
            return ""
        return self._edit.small_model or ""

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        """类型切换时更新 URL 默认值。"""
        type_map = {
            "type-anthropic": "anthropic",
            "type-genai-api": "genai-api",
            "type-genai": "genai",
            "type-openai": "openai",
        }
        pressed_id = event.pressed.id or ""
        profile_type = type_map.get(pressed_id, "genai-api")

        url_input = self.query_one("#input-url", Input)
        # 只在 URL 为空或等于某个默认值时才自动填充
        current_url = url_input.value.strip()
        if not current_url or current_url in DEFAULT_URLS.values():
            url_input.value = DEFAULT_URLS.get(profile_type, "")

        # genai 模式不需要 URL
        row_url = self.query_one("#row-url")
        row_url.display = profile_type != "genai"

        # 小模型行只对 anthropic 类型显示（DeepSeek 等第三方后端）
        row_small = self.query_one("#row-small-model")
        row_small.display = profile_type == "anthropic"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.dismiss(None)
            return
        if event.button.id == "btn-save":
            self._save()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _get_selected_type(self) -> str:
        """获取当前选中的类型。"""
        type_map = {
            "type-anthropic": "anthropic",
            "type-genai-api": "genai-api",
            "type-genai": "genai",
            "type-openai": "openai",
        }
        radio_set = self.query_one("#type-select", RadioSet)
        if radio_set.pressed_button and radio_set.pressed_button.id:
            return type_map.get(radio_set.pressed_button.id, "genai-api")
        return "genai-api"

    def _save(self) -> None:
        """验证并保存 Profile。"""
        error = self.query_one("#form-error", Static)
        name = self.query_one("#input-name", Input).value.strip()
        ptype = self._get_selected_type()
        url = self.query_one("#input-url", Input).value.strip()
        key = self.query_one("#input-key", Input).value.strip()
        model = self.query_one("#input-model", Input).value.strip()

        # 验证
        if not name:
            error.update("[red]请输入 Profile 名称[/]")
            return
        if not self._edit and profile_exists(name):
            error.update("[red]Profile 名称已存在[/]")
            return
        if ptype != "genai" and not url:
            error.update("[red]请输入 URL[/]")
            return
        if not key:
            error.update("[red]请输入 Key/Token[/]")
            return

        # 构建 Profile
        profile = Profile(name=name, type=ptype)
        if ptype == "anthropic":
            profile.url = url
            profile.key = key
            profile.model = model or "opus"
            small_model = self.query_one("#input-small-model", Input).value.strip()
            if small_model:
                profile.small_model = small_model
        elif ptype == "genai":
            profile.key = key
            profile.big_model = model or "GPT-5.5"
            profile.middle_model = profile.big_model
            profile.small_model = profile.big_model
        elif ptype in ("genai-api", "openai"):
            profile.url = url
            profile.key = key
            profile.big_model = model or "GPT-5.5"
            profile.middle_model = profile.big_model
            profile.small_model = profile.big_model

        save_profile(profile)
        self.dismiss(profile)
