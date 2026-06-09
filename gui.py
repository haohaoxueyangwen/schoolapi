#!/usr/bin/env python3
"""GenAI Stack GUI — tkinter-based native desktop interface (Windows + Linux).

Zero extra dependencies — uses only Python stdlib + tkinter.
All HTTP operations run in background threads — UI never blocks.

Usage:
    uv run python gui.py
    uv run genai-stack-gui
"""

from __future__ import annotations

import json
import os
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from tui import config as cfg
from tui.models import get_models
from tui.proxy import ProxyManager
from tui.token_utils import jwt_remaining_str

IS_WINDOWS = os.name == "nt"

PAD_X = 8
PAD_Y = 4
REFRESH_INTERVAL_MS = 3000  # 3 seconds (reduced from 5)


class GenAIStackGUI(tk.Tk):
    """GenAI Stack 桌面管理面板。"""

    def __init__(self) -> None:
        super().__init__()

        self.title("GenAI Stack Dashboard")
        self.geometry("960x680")
        self.minsize(800, 520)

        self.proxy_mgr = ProxyManager()
        self._current_profile: cfg.Profile | None = None
        self._refresh_id: str | None = None
        self._health_cache: dict[str, bool] = {"shtu_proxy": False, "genai2api": False}
        self._busy = False  # guard against concurrent refresh

        self._setup_style()
        self._build_menubar()
        self._build_statusbar()
        self._build_main()

        # Async initial load — don't block window appearance
        self.after(100, self._async_refresh_all)
        self._schedule_health_refresh()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ═══════ Style ═══════

    def _setup_style(self) -> None:
        self.style = ttk.Style(self)
        if IS_WINDOWS:
            for theme in ("vista", "xpnative", "clam"):
                if theme in self.style.theme_names():
                    self.style.theme_use(theme)
                    break
        self.configure(bg="#f0f0f0")
        self.style.configure("Status.TLabel", font=("Segoe UI", 10) if IS_WINDOWS else ("sans-serif", 10))
        self.style.configure("Heading.TLabel", font=("Segoe UI", 11, "bold") if IS_WINDOWS else ("sans-serif", 11, "bold"))

    # ═══════ Menu bar ═══════

    def _build_menubar(self) -> None:
        menubar = tk.Menu(self)
        self.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="New Profile...", command=self._new_profile, accelerator="Ctrl+N")
        file_menu.add_command(label="Edit Profile...", command=self._edit_profile, accelerator="Ctrl+E")
        file_menu.add_command(label="Delete Profile", command=self._delete_profile, accelerator="Del")
        file_menu.add_separator()
        file_menu.add_command(label="Refresh (F5)", command=self._async_refresh_all)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close, accelerator="Ctrl+Q")
        menubar.add_cascade(label="File", menu=file_menu)

        proxy_menu = tk.Menu(menubar, tearoff=0)
        proxy_menu.add_command(label="Start Proxy", command=self._start_proxy)
        proxy_menu.add_command(label="Stop Proxy", command=self._stop_proxy)
        proxy_menu.add_command(label="Restart Proxy", command=self._restart_proxy)
        menubar.add_cascade(label="Proxy", menu=proxy_menu)

        tools_menu = tk.Menu(menubar, tearoff=0)
        tools_menu.add_command(label="Backup Config...", command=self._backup_config)
        tools_menu.add_command(label="Restore Config...", command=self._restore_config)
        menubar.add_cascade(label="Tools", menu=tools_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About", command=self._show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.bind_all("<Control-n>", lambda e: self._new_profile())
        self.bind_all("<Control-e>", lambda e: self._edit_profile())
        self.bind_all("<Control-q>", lambda e: self._on_close())
        self.bind_all("<F5>", lambda e: self._async_refresh_all())

    # ═══════ Status bar ═══════

    def _build_statusbar(self) -> None:
        self.status_frame = ttk.Frame(self)
        self.status_frame.pack(side=tk.TOP, fill=tk.X, padx=PAD_X, pady=(PAD_Y, 0))
        self.status_label = ttk.Label(self.status_frame, text="Ready", style="Status.TLabel")
        self.status_label.pack(side=tk.LEFT, fill=tk.X)

    # ═══════ Main layout ═══════

    def _build_main(self) -> None:
        pw = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        pw.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=PAD_X, pady=PAD_Y)
        self._build_left_panel(pw)
        self._build_right_panel(pw)
        self._build_bottom_bar()

    def _build_left_panel(self, parent: ttk.PanedWindow) -> None:
        frame = ttk.LabelFrame(parent, text="Profiles", padding=4)
        parent.add(frame, weight=1)

        list_frame = ttk.Frame(frame)
        list_frame.pack(fill=tk.BOTH, expand=True)

        self.profile_listbox = tk.Listbox(
            list_frame,
            font=("Consolas", 10) if IS_WINDOWS else ("monospace", 10),
            activestyle="none", exportselection=False,
        )
        sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.profile_listbox.yview)
        self.profile_listbox.configure(yscrollcommand=sb.set)
        self.profile_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.profile_listbox.bind("<<ListboxSelect>>", self._on_profile_select)
        self.profile_listbox.bind("<Double-1>", lambda e: self._activate_profile())

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=(PAD_Y, 0))
        for txt, cmd in [("★ Activate", self._activate_profile),
                         ("+ New", self._new_profile),
                         ("✎ Edit", self._edit_profile),
                         ("✕ Delete", self._delete_profile),
                         ("🔑 Token", self._update_token)]:
            ttk.Button(btn_frame, text=txt, command=cmd).pack(side=tk.LEFT, padx=1)

    def _build_right_panel(self, parent: ttk.PanedWindow) -> None:
        rf = ttk.Frame(parent)
        parent.add(rf, weight=2)

        # Profile Detail
        df = ttk.LabelFrame(rf, text="Profile Detail", padding=4)
        df.pack(fill=tk.BOTH, expand=True, pady=(0, PAD_Y))
        self.detail_text = tk.Text(df, height=7, font=("Consolas", 10) if IS_WINDOWS else ("monospace", 10),
                                   state=tk.DISABLED, wrap=tk.WORD)
        self.detail_text.pack(fill=tk.BOTH, expand=True)

        # Proxy Status
        pf = ttk.LabelFrame(rf, text="Proxy Status", padding=4)
        pf.pack(fill=tk.BOTH, pady=(0, PAD_Y))
        self.proxy_status_text = tk.Text(pf, height=5, font=("Consolas", 10) if IS_WINDOWS else ("monospace", 10),
                                         state=tk.DISABLED, wrap=tk.WORD)
        self.proxy_status_text.pack(fill=tk.BOTH)

        # Model Select
        mf = ttk.LabelFrame(rf, text="Model Select", padding=4)
        mf.pack(fill=tk.BOTH)
        row = ttk.Frame(mf)
        row.pack(fill=tk.X, pady=(0, PAD_Y))
        ttk.Label(row, text="Model:").pack(side=tk.LEFT, padx=(0, 4))
        self.model_var = tk.StringVar()
        self.model_combo = ttk.Combobox(row, textvariable=self.model_var, state="readonly", width=30)
        self.model_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(row, text="Switch Model", command=self._switch_model).pack(side=tk.RIGHT)

    def _build_bottom_bar(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(side=tk.BOTTOM, fill=tk.X, padx=PAD_X, pady=(0, PAD_Y))
        for txt, cmd in [("▶ Start", self._start_proxy),
                         ("■ Stop", self._stop_proxy),
                         ("↻ Restart", self._restart_proxy),
                         ("↺ Refresh", self._async_refresh_all)]:
            ttk.Button(bar, text=txt, command=cmd).pack(side=tk.LEFT, padx=2)
        ttk.Label(bar, text="").pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.auto_refresh_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Auto-refresh (3s)", variable=self.auto_refresh_var).pack(side=tk.RIGHT)

    # ═══════ Async operations (NON-BLOCKING) ═══════

    def _async_refresh_all(self) -> None:
        """Kick off all async refreshes without blocking."""
        self._refresh_profiles()       # fast — disk only
        self._update_statusbar()       # fast
        self._update_detail()          # fast
        self._async_health_check()     # background HTTP
        self._async_refresh_models()   # background HTTP

    def _async_health_check(self) -> None:
        """Run health checks in background thread, update UI via after()."""
        def _run() -> dict[str, bool]:
            return self.proxy_mgr.health_check()

        def _done() -> None:
            t = getattr(self, '_hc_thread', None)
            if t and t.is_alive():
                return  # skip if still running
            try:
                health = t.result_cache
            except AttributeError:
                return
            self._health_cache = health
            shtu = "✓ Running" if health["shtu_proxy"] else "✗ Not running"
            genai2 = "✓ Running" if health["genai2api"] else "✗ Not running"
            lines = [
                f"  SHTUClaudeProxy (:8082):  {shtu}",
                f"  genai2api (:5000):        {genai2}",
            ]
            token = cfg.read_genai_token()
            if token:
                try:
                    lines.append(f"  JWT status:              {jwt_remaining_str(token)}")
                except Exception:
                    pass
            self._set_text(self.proxy_status_text, "\n".join(lines))

        self._hc_thread = threading.Thread(target=lambda: (
            setattr(self._hc_thread, 'result_cache', _run()),
            self.after(0, _done),
        ), daemon=True)
        self._hc_thread.start()

    def _async_refresh_models(self) -> None:
        """Fetch models in background thread."""
        def _run() -> tuple[str | None, int | None, str | None, str | None, bool]:
            active = cfg.get_active_profile()
            proxy_port = None
            profile_type = None
            profile_url = ""
            profile_key = ""
            has_custom = False
            if active:
                p = cfg.load_profile(active)
                if p:
                    profile_type = p.type
                    profile_url = p.url
                    profile_key = p.key
                    if p.type == "genai" and self._health_cache.get("genai2api", False):
                        proxy_port = cfg.GENAI2API_PORT
                    elif p.type in ("genai-api", "openai") and self._health_cache.get("shtu_proxy", False):
                        proxy_port = cfg.SHTU_PROXY_PORT
                    if p.type == "anthropic":
                        has_custom = bool(p.model)
            return profile_type, proxy_port, profile_url, profile_key, has_custom

        def _done() -> None:
            profile_type, proxy_port, profile_url, profile_key, has_custom = self._model_fetch_result
            models = get_models(
                proxy_port, profile_type, has_custom,
                profile_url=profile_url, profile_key=profile_key,
            )
            flat = []
            for gm in models.values():
                flat.extend(gm)
            self.model_combo["values"] = flat

            active = cfg.get_active_profile()
            if active:
                p = cfg.load_profile(active)
                if p and p.display_model in flat:
                    self.model_var.set(p.display_model)
                elif flat:
                    self.model_var.set(flat[0])

        def _fetch() -> None:
            self._model_fetch_result = _run()
            self.after(0, _done)

        threading.Thread(target=_fetch, daemon=True).start()

    # ═══════ Auto-refresh (non-blocking) ═══════

    def _schedule_health_refresh(self) -> None:
        """Schedule periodic health refresh — async, never blocks UI."""
        if self.auto_refresh_var.get():
            self._async_health_check()
        self._refresh_id = self.after(REFRESH_INTERVAL_MS, self._schedule_health_refresh)

    # ═══════ Sync UI updates (fast, disk only) ═══════

    def _refresh_profiles(self) -> None:
        selected = self._get_selected_profile_name()
        self.profile_listbox.delete(0, tk.END)
        active = cfg.get_active_profile()
        for p in cfg.list_profiles():
            prefix = "★ " if p.name == active else "  "
            self.profile_listbox.insert(tk.END, f"{prefix}{p.name:<14} [{p.type}]")
        if selected:
            for i in range(self.profile_listbox.size()):
                if selected in self.profile_listbox.get(i):
                    self.profile_listbox.selection_set(i)
                    break
        if not self.profile_listbox.curselection() and self.profile_listbox.size() > 0:
            self.profile_listbox.selection_set(0)
            self._on_profile_select()

    def _update_statusbar(self) -> None:
        active = cfg.get_active_profile()
        if active and cfg.profile_exists(active):
            p = cfg.load_profile(active)
            if p:
                self.status_label.configure(
                    text=f"★ Active: {active}  |  Type: {p.type}  |  Model: {p.display_model}"
                )
                return
        self.status_label.configure(text="No active profile. Select one and click ★ Activate.")

    def _update_detail(self) -> None:
        name = self._get_selected_profile_name()
        if not name:
            self._set_text(self.detail_text, "(no profile selected)")
            return
        p = cfg.load_profile(name)
        if not p:
            return
        data = p.to_json()
        lines = []
        for k, v in data.items():
            if k == "key" and v:
                v = v if len(v) <= 16 else v[:12] + f"...({len(v)} chars)"
            lines.append(f"  {k}: {v}")
        self._set_text(self.detail_text, "\n".join(lines))
        self._current_profile = p

    # ═══════ Profile actions ═══════

    def _get_selected_profile_name(self) -> str | None:
        sel = self.profile_listbox.curselection()
        if not sel:
            return None
        return self.profile_listbox.get(sel[0]).lstrip("★ ").split()[0].strip() or None

    def _on_profile_select(self, event=None) -> None:
        self._update_detail()

    def _activate_profile(self) -> None:
        name = self._get_selected_profile_name()
        if not name:
            messagebox.showwarning("Activate", "Please select a profile first."); return
        if not messagebox.askyesno("Activate", f"Activate '{name}'?\nWill stop current proxies and switch config."):
            return
        p = cfg.load_profile(name)
        if not p:
            return
        self.status_label.configure(text=f"Switching to {name}...")
        self._busy = True

        def _run():
            ok, msg = self.proxy_mgr.smart_switch(p)
            self.after(0, lambda: self._on_activate_done(ok, msg, p))

        threading.Thread(target=_run, daemon=True).start()

    def _on_activate_done(self, ok: bool, msg: str, profile: cfg.Profile) -> None:
        self._busy = False
        if ok:
            self.status_label.configure(text=f"✓ {msg}. Restart Claude Code to take effect.")
        else:
            messagebox.showerror("Activation Failed", msg)
        self._async_refresh_all()

    def _new_profile(self) -> None:
        dlg = ProfileDialog(self, title="New Profile")
        self.wait_window(dlg)
        if dlg.result:
            cfg.save_profile(dlg.result)
            self._async_refresh_all()
            self.status_label.configure(text=f"✓ Profile '{dlg.result.name}' created.")

    def _edit_profile(self) -> None:
        name = self._get_selected_profile_name()
        if not name: messagebox.showwarning("Edit", "Select a profile."); return
        p = cfg.load_profile(name)
        if not p: return
        dlg = ProfileDialog(self, title=f"Edit: {name}", edit_profile=p)
        self.wait_window(dlg)
        if dlg.result:
            cfg.save_profile(dlg.result)
            self._async_refresh_all()
            self.status_label.configure(text=f"✓ Profile '{dlg.result.name}' updated.")

    def _delete_profile(self) -> None:
        name = self._get_selected_profile_name()
        if not name: messagebox.showwarning("Delete", "Select a profile."); return
        if name == cfg.get_active_profile():
            messagebox.showerror("Delete", f"'{name}' is active. Switch first."); return
        if messagebox.askyesno("Delete", f"Delete '{name}'?", icon="warning"):
            cfg.delete_profile(name)
            self._async_refresh_all()
            self.status_label.configure(text=f"✓ Profile '{name}' deleted.")

    def _update_token(self) -> None:
        name = self._get_selected_profile_name()
        if not name: messagebox.showwarning("Token", "Select a profile."); return
        p = cfg.load_profile(name)
        if not p: return
        dlg = TokenDialog(self, title=f"Update Token: {name}", current_token=p.key)
        self.wait_window(dlg)
        if dlg.result:
            p.key = dlg.result
            cfg.save_profile(p)
            active = cfg.get_active_profile()
            if name == active:
                if p.type == "genai": cfg.write_genai_token(dlg.result)
                elif p.type == "genai-api": cfg.write_shtu_proxy_config(p)
            self._async_refresh_all()
            self.status_label.configure(text=f"✓ Token updated for '{name}'.")

    # ═══════ Model ═══════

    def _switch_model(self) -> None:
        model = self.model_var.get()
        if not model: messagebox.showwarning("Model", "Select a model."); return
        active = cfg.get_active_profile()
        if not active: messagebox.showwarning("Model", "No active profile."); return
        p = cfg.load_profile(active)
        if not p: return
        if not messagebox.askyesno("Switch Model", f"Switch '{active}' to '{model}'?\nMay restart proxies."):
            return
        self.status_label.configure(text=f"Switching model to {model}...")

        def _run():
            ok, msg = self.proxy_mgr.switch_model(p, model)
            self.after(0, lambda: self._on_switch_done(ok, msg))

        threading.Thread(target=_run, daemon=True).start()

    def _on_switch_done(self, ok: bool, msg: str) -> None:
        if ok:
            self.status_label.configure(text=f"✓ {msg}")
        else:
            messagebox.showerror("Switch Failed", msg)
        self._async_refresh_all()

    # ═══════ Proxy ═══════

    def _start_proxy(self) -> None:
        active = cfg.get_active_profile()
        if not active: messagebox.showwarning("Start", "No active profile."); return
        p = cfg.load_profile(active)
        if not p: return
        self.status_label.configure(text="Starting proxy...")
        self._busy = True

        def _run():
            ok = self.proxy_mgr.smart_start(p)
            self.after(0, lambda: self._on_proxy_done("✓ Proxy started" if ok else "✗ Start failed", ok))

        threading.Thread(target=_run, daemon=True).start()

    def _stop_proxy(self) -> None:
        self._busy = True

        def _run():
            self.proxy_mgr.stop_all()
            self.after(0, lambda: self._on_proxy_done("All proxies stopped.", True))

        threading.Thread(target=_run, daemon=True).start()

    def _restart_proxy(self) -> None:
        active = cfg.get_active_profile()
        if not active: messagebox.showwarning("Restart", "No active profile."); return
        p = cfg.load_profile(active)
        if not p: return
        self.status_label.configure(text="Restarting proxy...")
        self._busy = True

        def _run():
            self.proxy_mgr.stop_all()
            time.sleep(1)
            ok = self.proxy_mgr.smart_start(p)
            self.after(0, lambda: self._on_proxy_done("✓ Proxy restarted" if ok else "✗ Restart failed", ok))

        threading.Thread(target=_run, daemon=True).start()

    def _on_proxy_done(self, msg: str, ok: bool) -> None:
        self._busy = False
        self.status_label.configure(text=msg)
        self._async_health_check()

    # ═══════ Config backup / restore ═══════

    def _backup_config(self) -> None:
        """Export all profiles to a portable JSON file."""
        path = filedialog.asksaveasfilename(
            title="Backup Config",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialfile="genai-stack-backup.json",
        )
        if not path:
            return
        profiles_data = {}
        for p in cfg.list_profiles():
            profiles_data[p.name] = p.to_json()
        backup = {
            "version": "7.0.0",
            "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "active_profile": cfg.get_active_profile(),
            "profiles": profiles_data,
        }
        try:
            Path(path).write_text(json.dumps(backup, indent=2, ensure_ascii=False), encoding="utf-8")
            messagebox.showinfo("Backup", f"Exported {len(profiles_data)} profiles to:\n{path}")
            self.status_label.configure(text=f"✓ Config backed up to {os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("Backup Failed", str(e))

    def _restore_config(self) -> None:
        """Import profiles from a backup JSON file."""
        path = filedialog.askopenfilename(
            title="Restore Config",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as e:
            messagebox.showerror("Restore Failed", f"Cannot read file:\n{e}")
            return

        profiles_data = data.get("profiles", {})
        if not profiles_data:
            messagebox.showwarning("Restore", "No profiles found in backup file.")
            return

        count = 0
        skipped = 0
        for name, pdata in profiles_data.items():
            if cfg.profile_exists(name):
                if not messagebox.askyesno("Restore", f"Profile '{name}' already exists. Overwrite?"):
                    skipped += 1
                    continue
            cfg.save_profile(cfg.Profile(
                name=name,
                type=pdata.get("type", ""),
                url=pdata.get("url", ""),
                key=pdata.get("key", ""),
                model=pdata.get("model", ""),
                big_model=pdata.get("big_model", ""),
                middle_model=pdata.get("middle_model", ""),
                small_model=pdata.get("small_model", ""),
            ))
            count += 1

        # Optionally restore active profile
        active = data.get("active_profile")
        if active and active in profiles_data:
            if messagebox.askyesno("Restore", f"Set '{active}' as active profile?"):
                cfg.set_active_profile(active)

        self._async_refresh_all()
        messagebox.showinfo("Restore", f"Restored {count} profiles. (Skipped: {skipped})")
        self.status_label.configure(text=f"✓ Restored {count} profiles from {os.path.basename(path)}")

    # ═══════ Helpers ═══════

    @staticmethod
    def _set_text(widget: tk.Text, text: str) -> None:
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert("1.0", text)
        widget.configure(state=tk.DISABLED)

    def _show_about(self) -> None:
        messagebox.showinfo("About", "GenAI Stack v7.0.0\n\n"
                            "Multi-mode AI backend proxy management.\n"
                            "Anthropic | GenAI JWT | GenAI API key")

    def _on_close(self) -> None:
        if self._refresh_id:
            self.after_cancel(self._refresh_id)
        self.destroy()


# ────────────────────────────────────
# Profile Dialog
# ────────────────────────────────────

class ProfileDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk, title: str = "Profile",
                 edit_profile: cfg.Profile | None = None) -> None:
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.result: cfg.Profile | None = None
        self._editing = edit_profile is not None

        f = ttk.Frame(self, padding=16)
        f.pack(fill=tk.BOTH)

        ttk.Label(f, text="Name:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.name_var = tk.StringVar()
        self.name_entry = ttk.Entry(f, textvariable=self.name_var, width=32)
        self.name_entry.grid(row=0, column=1, sticky=tk.EW, pady=2)
        if self._editing:
            self.name_entry.configure(state=tk.DISABLED)

        ttk.Label(f, text="Type:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.type_var = tk.StringVar(value="genai")
        cb = ttk.Combobox(f, textvariable=self.type_var, state="readonly",
                          values=["anthropic", "genai", "genai-api", "openai"])
        cb.grid(row=1, column=1, sticky=tk.EW, pady=2)
        cb.bind("<<ComboboxSelected>>", self._on_type_change)
        if self._editing:
            cb.configure(state=tk.DISABLED)

        ttk.Label(f, text="URL:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(f, textvariable=self.url_var, width=42)
        self.url_entry.grid(row=2, column=1, sticky=tk.EW, pady=2)

        ttk.Label(f, text="Key / Token:").grid(row=3, column=0, sticky=tk.W, pady=2)
        self.key_var = tk.StringVar()
        self.key_entry = ttk.Entry(f, textvariable=self.key_var, width=42)
        self.key_entry.grid(row=3, column=1, sticky=tk.EW, pady=2)

        ttk.Label(f, text="Big Model:").grid(row=4, column=0, sticky=tk.W, pady=2)
        self.big_var = tk.StringVar(value="GPT-5.5")
        self.big_entry = ttk.Entry(f, textvariable=self.big_var, width=32)
        self.big_entry.grid(row=4, column=1, sticky=tk.EW, pady=2)

        ttk.Label(f, text="Middle Model:").grid(row=5, column=0, sticky=tk.W, pady=2)
        self.mid_var = tk.StringVar()
        self.mid_entry = ttk.Entry(f, textvariable=self.mid_var, width=32)
        self.mid_entry.grid(row=5, column=1, sticky=tk.EW, pady=2)

        ttk.Label(f, text="Small Model:").grid(row=6, column=0, sticky=tk.W, pady=2)
        self.small_var = tk.StringVar()
        self.small_entry = ttk.Entry(f, textvariable=self.small_var, width=32)
        self.small_entry.grid(row=6, column=1, sticky=tk.EW, pady=2)

        bf = ttk.Frame(f)
        bf.grid(row=7, column=0, columnspan=2, pady=(12, 0))
        ttk.Button(bf, text="OK", command=self._on_ok).pack(side=tk.LEFT, padx=4)
        ttk.Button(bf, text="Cancel", command=self.destroy).pack(side=tk.LEFT, padx=4)

        if edit_profile:
            self._load(edit_profile)
        self._on_type_change()
        self._center(parent)

    def _on_type_change(self, e=None) -> None:
        needs_url = self.type_var.get() in ("anthropic", "genai-api", "openai")
        self.url_entry.configure(state=tk.NORMAL if needs_url else tk.DISABLED)
        needs_models = self.type_var.get() != "anthropic"
        st = tk.NORMAL if needs_models else tk.DISABLED
        self.big_entry.configure(state=st)
        self.mid_entry.configure(state=st)
        self.small_entry.configure(state=st)

    def _load(self, p: cfg.Profile) -> None:
        self.name_var.set(p.name)
        self.type_var.set(p.type)
        self.url_var.set(p.url)
        self.key_var.set(p.key)
        self.big_var.set(p.big_model)
        self.mid_var.set(p.middle_model)
        self.small_var.set(p.small_model)

    def _on_ok(self) -> None:
        name = self.name_var.get().strip()
        ptype = self.type_var.get().strip()
        if not name:
            messagebox.showwarning("Validation", "Name required.", parent=self); return
        if not self._editing and cfg.profile_exists(name):
            messagebox.showwarning("Validation", f"'{name}' exists.", parent=self); return
        big = self.big_var.get().strip()
        mid = self.mid_var.get().strip() or big
        small = self.small_var.get().strip() or big
        if ptype == "anthropic":
            self.result = cfg.Profile(name=name, type=ptype, url=self.url_var.get().strip(),
                                      key=self.key_var.get().strip(), model=big or "opus")
        else:
            self.result = cfg.Profile(name=name, type=ptype, url=self.url_var.get().strip(),
                                      key=self.key_var.get().strip(),
                                      big_model=big, middle_model=mid, small_model=small)
        self.destroy()

    def _center(self, parent: tk.Tk) -> None:
        self.update_idletasks()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{px+(pw-w)//2}+{py+(ph-h)//2}")


# ────────────────────────────────────
# Token Dialog
# ────────────────────────────────────

class TokenDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk, title: str = "Update Token",
                 current_token: str = "") -> None:
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.result: str | None = None

        f = ttk.Frame(self, padding=16)
        f.pack(fill=tk.BOTH)
        ttk.Label(f, text="New Token:").grid(row=0, column=0, sticky=tk.W, pady=4)
        self.token_var = tk.StringVar(value=current_token)
        self.token_entry = ttk.Entry(f, textvariable=self.token_var, width=50)
        self.token_entry.grid(row=0, column=1, sticky=tk.EW, pady=4, padx=(8, 0))
        ttk.Label(f, text="JWT (eyJ...) or student_id@password",
                  font=("Segoe UI", 8) if IS_WINDOWS else ("sans-serif", 8),
                 ).grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(0, 8))
        bf = ttk.Frame(f)
        bf.grid(row=2, column=0, columnspan=2)
        ttk.Button(bf, text="OK", command=self._on_ok).pack(side=tk.LEFT, padx=4)
        ttk.Button(bf, text="Cancel", command=self.destroy).pack(side=tk.LEFT, padx=4)
        self._center(parent)
        self.token_entry.focus_set()

    def _on_ok(self) -> None:
        token = self.token_var.get().strip()
        if not token: messagebox.showwarning("Validation", "Token empty.", parent=self); return
        self.result = token
        self.destroy()

    def _center(self, parent: tk.Tk) -> None:
        self.update_idletasks()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{px+(pw-w)//2}+{py+(ph-h)//2}")


def main() -> None:
    app = GenAIStackGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
