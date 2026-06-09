"""左侧 Profile 列表控件。"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Label, ListView, ListItem, Static

from ..config import Profile, list_profiles, get_active_profile


class ProfileSelected(Message):
    """Profile 被选中时发送。"""
    def __init__(self, profile: Profile) -> None:
        self.profile = profile
        super().__init__()


class ProfileActivated(Message):
    """Profile 被激活（按 Enter）时发送。"""
    def __init__(self, profile: Profile) -> None:
        self.profile = profile
        super().__init__()


class ProfileListItem(ListItem):
    """Profile 列表中的一项。"""

    def __init__(self, profile: Profile, is_active: bool = False) -> None:
        self.profile = profile
        self._is_active = is_active
        super().__init__()

    def compose(self) -> ComposeResult:
        marker = "[bold green]★[/]" if self._is_active else "  "
        type_colors = {
            "anthropic": "cyan",
            "genai": "yellow",
            "genai-api": "green",
            "openai": "magenta",
        }
        color = type_colors.get(self.profile.type, "white")
        yield Static(
            f" {marker} [bold]{self.profile.name}[/] [{color}]{self.profile.type}[/]",
            markup=True,
        )


class ProfileList(Vertical):
    """左侧 Profile 列表面板。"""

    DEFAULT_CSS = """
    ProfileList {
        width: 30;
        border-right: solid $accent;
        padding: 0;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._profiles: list[Profile] = []

    def compose(self) -> ComposeResult:
        yield Static(
            "[bold $accent]  ═══ Profiles ═══[/]",
            markup=True,
            classes="title",
        )
        yield ListView(id="profile-listview")

    def on_mount(self) -> None:
        self.refresh_list()

    def refresh_list(self) -> None:
        """刷新 Profile 列表。"""
        self._profiles = list_profiles()
        active = get_active_profile()
        listview = self.query_one("#profile-listview", ListView)
        listview.clear()
        for p in self._profiles:
            item = ProfileListItem(p, is_active=(p.name == active))
            listview.append(item)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """高亮变更时，通知详情面板更新。"""
        if event.item and isinstance(event.item, ProfileListItem):
            self.post_message(ProfileSelected(event.item.profile))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """按 Enter 激活 Profile。"""
        if event.item and isinstance(event.item, ProfileListItem):
            self.post_message(ProfileActivated(event.item.profile))
