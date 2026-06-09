"""模型选择面板。"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static, ListView, ListItem

from ..config import read_genai_model


class ModelChosen(Message):
    """用户选择了一个模型。"""
    def __init__(self, model: str) -> None:
        self.model = model
        super().__init__()


class ModelListItem(ListItem):
    """模型列表项。"""
    def __init__(self, model_id: str, is_current: bool = False) -> None:
        self.model_id = model_id
        self._is_current = is_current
        super().__init__()

    def compose(self) -> ComposeResult:
        marker = "[bold green]▸[/]" if self._is_current else " "
        yield Static(f" {marker} {self.model_id}", markup=True)


class ModelSelect(Vertical):
    """模型选择面板。"""

    DEFAULT_CSS = """
    ModelSelect {
        height: 1fr;
        min-height: 8;
        border: solid $primary-background-lighten-2;
        padding: 1 2;
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(
            "[bold $accent]模型选择[/]  [dim](Enter 切换)[/]",
            id="model-title",
            markup=True,
        )
        yield Static("", id="current-model")
        yield ListView(id="model-listview")

    def update_models(
        self,
        models: dict[str, list[str]],
        current_model: str = "",
        readonly: bool = False,
    ) -> None:
        """更新模型列表。

        Args:
            models: 分组的模型列表
            current_model: 当前选中的模型
            readonly: 是否只读（官方 Claude，无需选择）
        """
        if not current_model:
            current_model = read_genai_model()

        current_label = self.query_one("#current-model", Static)
        current_label.update(f"  当前: [bold cyan]{current_model or '未设置'}[/]")

        listview = self.query_one("#model-listview", ListView)
        listview.clear()

        if readonly:
            listview.append(ListItem(Static(
                " [dim]无需选择，由 Claude Code 自动管理[/]"
            )))
            return

        for group, model_ids in models.items():
            listview.append(ListItem(Static(f" [dim bold]── {group} ──[/]", markup=True)))
            for mid in model_ids:
                item = ModelListItem(mid, is_current=(mid == current_model))
                listview.append(item)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """选中模型。"""
        if event.item and isinstance(event.item, ModelListItem):
            self.post_message(ModelChosen(event.item.model_id))
