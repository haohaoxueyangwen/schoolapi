"""GenAI Stack TUI — 主应用入口。"""

from __future__ import annotations

from pathlib import Path
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Static
from textual.worker import Worker, get_current_worker

from . import config as cfg
from .proxy import ProxyManager
from .models import get_models
from .widgets.profile_list import ProfileList, ProfileSelected, ProfileActivated
from .widgets.profile_detail import ProfileDetail
from .widgets.proxy_status import ProxyStatus
from .widgets.model_select import ModelSelect, ModelChosen
from .screens.profile_form import ProfileFormScreen
from .screens.token_input import TokenInputScreen


class GenAIStackApp(App):
    """GenAI Stack 统一控制面板。"""

    TITLE = "GenAI Stack Dashboard"
    CSS_PATH = "styles/app.tcss"

    BINDINGS = [
        Binding("n", "new_profile", "新建Profile"),
        Binding("e", "edit_profile", "编辑Profile"),
        Binding("d", "delete_profile", "删除Profile"),
        Binding("t", "update_token", "更新Token"),
        Binding("m", "show_models", "刷新模型"),
        Binding("s", "start_proxy", "启动代理"),
        Binding("r", "restart_proxy", "重启代理"),
        Binding("x", "stop_proxy", "停止代理"),
        Binding("q", "quit_app", "退出"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.proxy_mgr = ProxyManager()
        self._current_profile: cfg.Profile | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="status-bar")
        with Horizontal(id="main-container"):
            yield ProfileList()
            with Vertical(id="detail-pane"):
                yield ProfileDetail()
                yield ProxyStatus()
                yield ModelSelect()
        yield Footer()

    def on_mount(self) -> None:
        """启动后初始化。"""
        self._update_status_bar()
        self._refresh_proxy_status()
        self._refresh_models()
        # 定时刷新代理状态（每 5 秒）
        self.set_interval(5, self._refresh_proxy_status)

    # ======================== 消息处理 ========================

    def on_profile_selected(self, event: ProfileSelected) -> None:
        """Profile 高亮变更。"""
        self._current_profile = event.profile
        detail = self.query_one(ProfileDetail)
        detail.update_profile(event.profile)

    def on_profile_activated(self, event: ProfileActivated) -> None:
        """Profile 被激活（Enter）。"""
        self._current_profile = event.profile
        self.notify(f"正在切换到 {event.profile.name}...", title="切换中")
        self.run_worker(
            self._do_switch_profile(event.profile),
            name="switch_profile",
            exclusive=True,
        )

    def on_model_chosen(self, event: ModelChosen) -> None:
        """模型被选中。"""
        if self._current_profile is None:
            self.notify("请先选择一个 Profile", severity="warning")
            return
        active_name = cfg.get_active_profile()
        if self._current_profile.name != active_name:
            self.notify("只能切换当前激活 Profile 的模型", severity="warning")
            return
        self.run_worker(
            self._do_switch_model(event.model),
            name="switch_model",
            exclusive=True,
        )

    # ======================== Actions ========================

    def action_new_profile(self) -> None:
        """新建 Profile。"""
        def on_result(profile: cfg.Profile | None) -> None:
            if profile:
                self.query_one(ProfileList).refresh_list()
                self.notify(f"Profile '{profile.name}' 已创建", title="成功")
        self.push_screen(ProfileFormScreen(), on_result)

    def action_edit_profile(self) -> None:
        """编辑当前选中的 Profile。"""
        if self._current_profile is None:
            self.notify("请先选择一个 Profile", severity="warning")
            return
        # 重新加载最新数据
        profile = cfg.load_profile(self._current_profile.name)
        if profile is None:
            self.notify("Profile 不存在", severity="error")
            return

        def on_result(result: cfg.Profile | None) -> None:
            if result:
                self.query_one(ProfileList).refresh_list()
                self.query_one(ProfileDetail).update_profile(result)
                self._current_profile = result
                self.notify(f"Profile '{result.name}' 已更新", title="成功")
        self.push_screen(ProfileFormScreen(edit_profile=profile), on_result)

    def action_delete_profile(self) -> None:
        """删除当前选中的 Profile。"""
        if self._current_profile is None:
            self.notify("请先选择一个 Profile", severity="warning")
            return
        active = cfg.get_active_profile()
        if self._current_profile.name == active:
            self.notify("不能删除当前激活的 Profile", severity="error")
            return
        cfg.delete_profile(self._current_profile.name)
        self.query_one(ProfileList).refresh_list()
        self.query_one(ProfileDetail).update_profile(None)
        self.notify(f"Profile '{self._current_profile.name}' 已删除", title="已删除")
        self._current_profile = None

    def action_update_token(self) -> None:
        """更新 Token。"""
        if self._current_profile is None:
            self.notify("请先选择一个 Profile", severity="warning")
            return
        # 重新加载
        profile = cfg.load_profile(self._current_profile.name)
        if profile is None:
            self.notify("Profile 不存在", severity="error")
            return

        def on_result(token: str | None) -> None:
            if token and profile:
                profile.key = token
                cfg.save_profile(profile)
                # 如果是当前激活的 genai profile，同步写入 token 文件
                active = cfg.get_active_profile()
                if profile.name == active:
                    if profile.type == "genai":
                        cfg.write_genai_token(token)
                        self.notify("Token 已更新，genai2api 将自动热加载", title="成功")
                    elif profile.type == "genai-api":
                        cfg.write_shtu_proxy_config(profile)
                        self.notify("API Key 已更新，需重启代理生效 (按 R)", title="成功")
                    else:
                        self.notify("Token 已保存", title="成功")
                else:
                    self.notify("Token 已保存", title="成功")
                self.query_one(ProfileList).refresh_list()
                self.query_one(ProfileDetail).update_profile(profile)
                self._current_profile = profile

        self.push_screen(
            TokenInputScreen(
                current_token=profile.key,
                profile_name=profile.name,
            ),
            on_result,
        )

    def action_start_proxy(self) -> None:
        """启动代理。"""
        active = cfg.get_active_profile()
        if not active:
            self.notify("无激活 Profile", severity="warning")
            return
        profile = cfg.load_profile(active)
        if not profile:
            self.notify("Profile 加载失败", severity="error")
            return
        self.notify("正在启动代理...", title="启动中")
        self.run_worker(
            self._do_start_proxy(profile),
            name="start_proxy",
            exclusive=True,
        )

    def action_restart_proxy(self) -> None:
        """重启代理。"""
        active = cfg.get_active_profile()
        if not active:
            self.notify("无激活 Profile", severity="warning")
            return
        profile = cfg.load_profile(active)
        if not profile:
            self.notify("Profile 加载失败", severity="error")
            return
        self.notify("正在重启代理...", title="重启中")
        self.run_worker(
            self._do_restart_proxy(profile),
            name="restart_proxy",
            exclusive=True,
        )

    def action_stop_proxy(self) -> None:
        """停止代理。"""
        self.proxy_mgr.stop_all()
        self._refresh_proxy_status()
        self.notify("所有代理已停止", title="已停止")

    def action_show_models(self) -> None:
        """刷新模型列表。"""
        self._refresh_models()

    def action_quit_app(self) -> None:
        """退出应用。"""
        # 退出时不自动停止代理，让代理在后台继续运行
        self.exit()

    # ======================== Worker 任务 ========================

    async def _do_switch_profile(self, profile: cfg.Profile) -> None:
        """在 worker 中切换 Profile。"""
        import asyncio
        loop = asyncio.get_event_loop()
        ok, msg = await loop.run_in_executor(None, self.proxy_mgr.smart_switch, profile)
        self._on_profile_switched(ok, msg, profile)

    def _on_profile_switched(self, ok: bool, msg: str, profile: cfg.Profile) -> None:
        """Profile 切换完成回调。"""
        if ok:
            self.notify(f"{msg}\n重启 Claude Code 生效", title="切换成功")
        else:
            self.notify(msg, severity="error", title="切换失败")

        self._current_profile = profile
        self.query_one(ProfileList).refresh_list()
        self.query_one(ProfileDetail).update_profile(profile)
        self._update_status_bar()
        self._refresh_proxy_status()
        self._refresh_models()

    async def _do_switch_model(self, model: str) -> None:
        """在 worker 中切换模型。"""
        import asyncio
        active = cfg.get_active_profile()
        if not active:
            return
        profile = cfg.load_profile(active)
        if not profile:
            return
        loop = asyncio.get_event_loop()
        ok, msg = await loop.run_in_executor(
            None, self.proxy_mgr.switch_model, profile, model
        )
        self._on_model_switched(ok, msg)

    def _on_model_switched(self, ok: bool, msg: str) -> None:
        """模型切换完成回调。"""
        if ok:
            self.notify(msg, title="模型切换")
        else:
            self.notify(msg, severity="error", title="切换失败")
        self._refresh_models()
        self._update_status_bar()

        # 刷新 Profile 详情
        active = cfg.get_active_profile()
        if active:
            profile = cfg.load_profile(active)
            if profile:
                self._current_profile = profile
                self.query_one(ProfileDetail).update_profile(profile)
                self.query_one(ProfileList).refresh_list()

    async def _do_start_proxy(self, profile: cfg.Profile) -> None:
        """在 worker 中启动代理。"""
        import asyncio
        loop = asyncio.get_event_loop()
        ok = await loop.run_in_executor(None, self.proxy_mgr.smart_start, profile)
        if ok:
            self.notify("代理启动成功", title="成功")
        else:
            self.notify("代理启动失败，查看日志", severity="error", title="失败")
        self._refresh_proxy_status()

    async def _do_restart_proxy(self, profile: cfg.Profile) -> None:
        """在 worker 中重启代理。"""
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.proxy_mgr.stop_all)
        await asyncio.sleep(1)
        ok = await loop.run_in_executor(None, self.proxy_mgr.smart_start, profile)
        if ok:
            self.notify("代理重启成功", title="成功")
        else:
            self.notify("代理重启失败，查看日志", severity="error", title="失败")
        self._refresh_proxy_status()

    # ======================== UI 刷新 ========================

    def _update_status_bar(self) -> None:
        """更新顶部状态栏。"""
        active = cfg.get_active_profile()
        bar = self.query_one("#status-bar", Static)
        if active:
            profile = cfg.load_profile(active)
            if profile:
                model_str = profile.display_model
                bar.update(
                    f"  Profile: [bold]{active}[/] ({profile.type})  |  "
                    f"模型: [bold]{model_str}[/]"
                )
                return
        bar.update("  [dim]无激活 Profile — 按 [N] 新建或先选择一个 Profile 按 Enter 激活[/]")

    def _refresh_proxy_status(self) -> None:
        """刷新代理状态面板。"""
        health = self.proxy_mgr.health_check()
        proxy_widget = self.query_one(ProxyStatus)
        proxy_widget.update_status(
            shtu_running=health["shtu_proxy"],
            genai2api_running=health["genai2api"],
        )

    def _refresh_models(self) -> None:
        """刷新模型列表。"""
        active = cfg.get_active_profile()

        # 确定从哪个端口拉取模型，以及使用哪种静态列表
        proxy_port = None
        profile_type = None
        has_custom_model = False
        current_model = ""
        profile_url = ""
        profile_key = ""
        if active:
            profile = cfg.load_profile(active)
            if profile:
                profile_type = profile.type
                profile_url = profile.url
                profile_key = profile.key
                if profile.type == "genai" and self.proxy_mgr.is_genai2api_running():
                    proxy_port = cfg.GENAI2API_PORT
                elif profile.type in ("genai-api", "openai") and self.proxy_mgr.is_shtu_running():
                    proxy_port = cfg.SHTU_PROXY_PORT
                if profile.type == "anthropic":
                    current_model = profile.model or ""
                    has_custom_model = bool(profile.model)
                else:
                    current_model = cfg.read_genai_model()

        models = get_models(
            proxy_port, profile_type, has_custom_model,
            profile_url=profile_url, profile_key=profile_key,
        )
        model_widget = self.query_one(ModelSelect)
        readonly = profile_type == "anthropic" and not has_custom_model
        model_widget.update_models(models, current_model, readonly=readonly)


def main() -> None:
    """TUI 启动入口。"""
    app = GenAIStackApp()
    app.run()


if __name__ == "__main__":
    main()
