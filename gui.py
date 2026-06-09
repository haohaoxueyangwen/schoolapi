#!/usr/bin/env python3
"""GenAI Stack Windows GUI — tkinter-based native desktop interface.

Replaces the Textual TUI on Windows where terminal rendering is unreliable.
Zero extra dependencies — uses only Python stdlib + tkinter.

Usage:
    uv run python gui.py
    uv run genai-stack-gui
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import requests

from tui import config as cfg
from tui.models import get_models, STATIC_MODELS_GENAI
from tui.proxy import ProxyManager
from tui.token_utils import jwt_remaining_str

IS_WINDOWS = os.name == "nt"

# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────
PAD_X = 8
PAD_Y = 4
REFRESH_INTERVAL_MS = 5000  # 5 seconds


# ──────────────────────────────────────────────
# Main Application
# ──────────────────────────────────────────────

class GenAIStackGUI(tk.Tk):
    """GenAI Stack 桌面管理面板。"""

    def __init__(self) -> None:
        super().__init__()

        self.title("GenAI Stack Dashboard")
        self.geometry("960x640")
        self.minsize(800, 500)

        # Set icon / app ID for Windows taskbar
        if IS_WINDOWS:
            try:
                self.iconbitmap(default="")
            except Exception:
                pass

        self.proxy_mgr = ProxyManager()
        self._current_profile: cfg.Profile | None = None
        self._refresh_job: str | None = None

        # ── Style ──
        self._setup_style()

        # ── Menu bar ──
        self._build_menubar()

        # ── Status bar ──
        self._build_statusbar()

        # ── Main paned window ──
        self._build_main()

        # ── Initial data load ──
        self._refresh_all()
        self._schedule_refresh()

        # ── Bind close ──
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ═══════════════════════════════════════════
    # Style
    # ═══════════════════════════════════════════

    def _setup_style(self) -> None:
        self.style = ttk.Style(self)
        # Try a modern theme on Windows
        if IS_WINDOWS:
            available = self.style.theme_names()
            for theme in ("vista", "xpnative", "clam"):
                if theme in available:
                    self.style.theme_use(theme)
                    break
        self.configure(bg="#f0f0f0")

        self.style.configure("Status.TLabel", font=("Segoe UI", 10) if IS_WINDOWS else ("sans-serif", 10))
        self.style.configure("Heading.TLabel", font=("Segoe UI", 11, "bold") if IS_WINDOWS else ("sans-serif", 11, "bold"))
        self.style.configure("ProxyOk.TLabel", foreground="#228B22")
        self.style.configure("ProxyDown.TLabel", foreground="#CC0000")
        self.style.configure("Action.TButton", padding=(12, 4))

    # ═══════════════════════════════════════════
    # Menu bar
    # ═══════════════════════════════════════════

    def _build_menubar(self) -> None:
        menubar = tk.Menu(self)
        self.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="New Profile...", command=self._new_profile, accelerator="Ctrl+N")
        file_menu.add_command(label="Edit Profile...", command=self._edit_profile, accelerator="Ctrl+E")
        file_menu.add_command(label="Delete Profile", command=self._delete_profile, accelerator="Del")
        file_menu.add_separator()
        file_menu.add_command(label="Refresh", command=self._refresh_all, accelerator="F5")
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close, accelerator="Ctrl+Q")
        menubar.add_cascade(label="File", menu=file_menu)

        proxy_menu = tk.Menu(menubar, tearoff=0)
        proxy_menu.add_command(label="Start Proxy", command=self._start_proxy)
        proxy_menu.add_command(label="Stop Proxy", command=self._stop_proxy)
        proxy_menu.add_command(label="Restart Proxy", command=self._restart_proxy)
        menubar.add_cascade(label="Proxy", menu=proxy_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About", command=self._show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        # Keyboard shortcuts
        self.bind_all("<Control-n>", lambda e: self._new_profile())
        self.bind_all("<Control-e>", lambda e: self._edit_profile())
        self.bind_all("<Control-q>", lambda e: self._on_close())
        self.bind_all("<F5>", lambda e: self._refresh_all())

    # ═══════════════════════════════════════════
    # Status bar
    # ═══════════════════════════════════════════

    def _build_statusbar(self) -> None:
        self.status_frame = ttk.Frame(self)
        self.status_frame.pack(side=tk.TOP, fill=tk.X, padx=PAD_X, pady=(PAD_Y, 0))

        self.status_label = ttk.Label(
            self.status_frame, text="Ready", style="Status.TLabel"
        )
        self.status_label.pack(side=tk.LEFT, fill=tk.X)

    # ═══════════════════════════════════════════
    # Main layout
    # ═══════════════════════════════════════════

    def _build_main(self) -> None:
        pw = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        pw.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=PAD_X, pady=PAD_Y)

        # ── Left: Profiles ──
        self._build_left_panel(pw)

        # ── Right: Detail + Proxy + Model ──
        self._build_right_panel(pw)

        # ── Bottom: Global actions ──
        self._build_bottom_bar()

    def _build_left_panel(self, parent: ttk.PanedWindow) -> None:
        frame = ttk.LabelFrame(parent, text="Profiles", padding=4)
        parent.add(frame, weight=1)

        # Listbox + scrollbar
        list_frame = ttk.Frame(frame)
        list_frame.pack(fill=tk.BOTH, expand=True)

        self.profile_listbox = tk.Listbox(
            list_frame,
            font=("Consolas", 10) if IS_WINDOWS else ("monospace", 10),
            activestyle="none",
            exportselection=False,
        )
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.profile_listbox.yview)
        self.profile_listbox.configure(yscrollcommand=scrollbar.set)

        self.profile_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.profile_listbox.bind("<<ListboxSelect>>", self._on_profile_select)
        self.profile_listbox.bind("<Double-1>", lambda e: self._activate_profile())

        # Profile action buttons
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=(PAD_Y, 0))

        ttk.Button(btn_frame, text="★ Activate", command=self._activate_profile,
                   style="Action.TButton").pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="+ New", command=self._new_profile).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="✎ Edit", command=self._edit_profile).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="✕ Delete", command=self._delete_profile).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="🔑 Token", command=self._update_token).pack(side=tk.LEFT, padx=2)

    def _build_right_panel(self, parent: ttk.PanedWindow) -> None:
        right_frame = ttk.Frame(parent)
        parent.add(right_frame, weight=2)

        # ── Profile Detail ──
        detail_frame = ttk.LabelFrame(right_frame, text="Profile Detail", padding=4)
        detail_frame.pack(fill=tk.BOTH, expand=True, pady=(0, PAD_Y))

        self.detail_text = tk.Text(
            detail_frame,
            height=8,
            font=("Consolas", 10) if IS_WINDOWS else ("monospace", 10),
            state=tk.DISABLED,
            wrap=tk.WORD,
        )
        self.detail_text.pack(fill=tk.BOTH, expand=True)

        # ── Proxy Status ──
        proxy_frame = ttk.LabelFrame(right_frame, text="Proxy Status", padding=4)
        proxy_frame.pack(fill=tk.BOTH, pady=(0, PAD_Y))

        self.proxy_status_text = tk.Text(
            proxy_frame,
            height=4,
            font=("Consolas", 10) if IS_WINDOWS else ("monospace", 10),
            state=tk.DISABLED,
            wrap=tk.WORD,
        )
        self.proxy_status_text.pack(fill=tk.BOTH)

        # ── Model Select ──
        model_frame = ttk.LabelFrame(right_frame, text="Model Select", padding=4)
        model_frame.pack(fill=tk.BOTH)

        model_row = ttk.Frame(model_frame)
        model_row.pack(fill=tk.X, pady=(0, PAD_Y))

        ttk.Label(model_row, text="Model:").pack(side=tk.LEFT, padx=(0, 4))
        self.model_var = tk.StringVar()
        self.model_combo = ttk.Combobox(
            model_row, textvariable=self.model_var, state="readonly", width=30
        )
        self.model_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        self.model_combo.bind("<<ComboboxSelected>>", self._on_model_select)

        ttk.Button(model_row, text="Switch Model",
                   command=self._switch_model).pack(side=tk.RIGHT)

    def _build_bottom_bar(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(side=tk.BOTTOM, fill=tk.X, padx=PAD_X, pady=(0, PAD_Y))

        ttk.Button(bar, text="▶ Start Proxy", command=self._start_proxy,
                   style="Action.TButton").pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="■ Stop Proxy", command=self._stop_proxy,
                   style="Action.TButton").pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="↻ Restart Proxy", command=self._restart_proxy,
                   style="Action.TButton").pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="↺ Refresh", command=self._refresh_all,
                   style="Action.TButton").pack(side=tk.LEFT, padx=2)

        # Spacer
        ttk.Label(bar, text="").pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.auto_refresh_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            bar, text="Auto-refresh (5s)", variable=self.auto_refresh_var,
            command=self._toggle_auto_refresh
        ).pack(side=tk.RIGHT)

    # ═══════════════════════════════════════════
    # Data refresh
    # ═══════════════════════════════════════════

    def _refresh_all(self) -> None:
        """Refresh all UI elements from config."""
        self._refresh_profiles()
        self._refresh_proxy_status()
        self._refresh_models()
        self._update_statusbar()
        self._update_detail()

    def _refresh_profiles(self) -> None:
        """Reload the profile listbox."""
        selected = self._get_selected_profile_name()
        self.profile_listbox.delete(0, tk.END)

        profiles = cfg.list_profiles()
        active = cfg.get_active_profile()

        for p in profiles:
            prefix = "★ " if p.name == active else "  "
            display = f"{prefix}{p.name:<14} [{p.type}]"
            self.profile_listbox.insert(tk.END, display)

        # Restore selection
        if selected:
            for i in range(self.profile_listbox.size()):
                item = self.profile_listbox.get(i)
                if selected in item:
                    self.profile_listbox.selection_set(i)
                    break

        # Select active if nothing selected
        if not self.profile_listbox.curselection() and self.profile_listbox.size() > 0:
            self.profile_listbox.selection_set(0)
            self._on_profile_select()

    def _refresh_proxy_status(self) -> None:
        """Update proxy status display."""
        health = self.proxy_mgr.health_check()
        shtu = "✓ Running" if health["shtu_proxy"] else "✗ Not running"
        genai2 = "✓ Running" if health["genai2api"] else "✗ Not running"

        lines = [
            f"  SHTUClaudeProxy (:8082):  {shtu}",
            f"  genai2api (:5000):        {genai2}",
        ]

        token = cfg.read_genai_token()
        if token:
            lines.append(f"  JWT status:              {jwt_remaining_str(token)}")

        self._set_text(self.proxy_status_text, "\n".join(lines))

    def _refresh_models(self) -> None:
        """Reload model dropdown."""
        active = cfg.get_active_profile()
        proxy_port = None
        profile_type = None

        if active:
            profile = cfg.load_profile(active)
            if profile:
                profile_type = profile.type
                if profile.type == "genai" and self.proxy_mgr.is_genai2api_running():
                    proxy_port = cfg.GENAI2API_PORT
                elif profile.type in ("genai-api", "openai") and self.proxy_mgr.is_shtu_running():
                    proxy_port = cfg.SHTU_PROXY_PORT

        models = get_models(proxy_port, profile_type)
        flat_models: list[str] = []
        for group_models in models.values():
            flat_models.extend(group_models)

        self.model_combo["values"] = flat_models

        # Set current model
        current = ""
        if active:
            profile = cfg.load_profile(active)
            if profile:
                current = profile.display_model
        if current and current in flat_models:
            self.model_var.set(current)
        elif flat_models:
            self.model_var.set(flat_models[0])

    def _update_statusbar(self) -> None:
        """Update the top status bar."""
        active = cfg.get_active_profile()
        if active and cfg.profile_exists(active):
            profile = cfg.load_profile(active)
            if profile:
                model_str = profile.display_model
                self.status_label.configure(
                    text=f"★ Active: {active}  |  Type: {profile.type}  |  Model: {model_str}"
                )
                return
        self.status_label.configure(text="No active profile. Select one and click ★ Activate.")

    def _update_detail(self) -> None:
        """Update profile detail pane."""
        name = self._get_selected_profile_name()
        if not name:
            self._set_text(self.detail_text, "(no profile selected)")
            return

        profile = cfg.load_profile(name)
        if not profile:
            return

        data = profile.to_json()
        lines = []
        for k, v in data.items():
            if k == "key" and v:
                v = v if len(v) <= 16 else v[:12] + f"...({len(v)} chars)"
            lines.append(f"  {k}: {v}")

        self._set_text(self.detail_text, "\n".join(lines))
        self._current_profile = profile

    # ═══════════════════════════════════════════
    # Auto-refresh
    # ═══════════════════════════════════════════

    def _schedule_refresh(self) -> None:
        """Schedule periodic proxy status refresh."""
        if self.auto_refresh_var.get():
            self._refresh_proxy_status()
        self._refresh_job = self.after(REFRESH_INTERVAL_MS, self._schedule_refresh)

    def _toggle_auto_refresh(self) -> None:
        """Toggle auto-refresh on/off."""
        if self.auto_refresh_var.get():
            self._schedule_refresh()

    # ═══════════════════════════════════════════
    # Profile actions
    # ═══════════════════════════════════════════

    def _get_selected_profile_name(self) -> str | None:
        """Extract profile name from listbox selection."""
        sel = self.profile_listbox.curselection()
        if not sel:
            return None
        item = self.profile_listbox.get(sel[0])
        # Strip ★ marker
        name_part = item.lstrip("★ ").split()[0].strip()
        return name_part if name_part else None

    def _on_profile_select(self, event=None) -> None:
        """Handle profile selection in listbox."""
        self._update_detail()
        name = self._get_selected_profile_name()
        if name:
            profile = cfg.load_profile(name)
            if profile:
                self._current_profile = profile

    def _activate_profile(self) -> None:
        """Activate the selected profile."""
        name = self._get_selected_profile_name()
        if not name:
            messagebox.showwarning("Activate", "Please select a profile first.")
            return

        # Confirm
        if not messagebox.askyesno(
            "Activate Profile",
            f"Activate profile '{name}'?\n\nThis will stop current proxies and switch configuration."
        ):
            return

        profile = cfg.load_profile(name)
        if not profile:
            return

        # Run in background thread
        self.status_label.configure(text=f"Switching to {name}...")
        threading.Thread(target=self._do_activate, args=(profile,), daemon=True).start()

    def _do_activate(self, profile: cfg.Profile) -> None:
        """Background: activate profile."""
        ok, msg = self.proxy_mgr.smart_switch(profile)
        self.after(0, lambda: self._on_activate_done(ok, msg, profile))

    def _on_activate_done(self, ok: bool, msg: str, profile: cfg.Profile) -> None:
        """UI update after activation."""
        if ok:
            self.status_label.configure(text=f"✓ {msg}. Restart Claude Code to take effect.")
        else:
            messagebox.showerror("Activation Failed", msg)
            self.status_label.configure(text=f"✗ {msg}")
        self._refresh_all()

    def _new_profile(self) -> None:
        """Open dialog to create a new profile."""
        dialog = ProfileDialog(self, title="New Profile")
        self.wait_window(dialog)
        if dialog.result:
            profile = dialog.result
            cfg.save_profile(profile)
            self._refresh_all()
            self.status_label.configure(text=f"✓ Profile '{profile.name}' created.")

    def _edit_profile(self) -> None:
        """Edit selected profile."""
        name = self._get_selected_profile_name()
        if not name:
            messagebox.showwarning("Edit", "Please select a profile first.")
            return
        profile = cfg.load_profile(name)
        if not profile:
            return

        dialog = ProfileDialog(self, title=f"Edit Profile: {name}", edit_profile=profile)
        self.wait_window(dialog)
        if dialog.result:
            cfg.save_profile(dialog.result)
            self._refresh_all()
            self.status_label.configure(text=f"✓ Profile '{dialog.result.name}' updated.")

    def _delete_profile(self) -> None:
        """Delete selected profile."""
        name = self._get_selected_profile_name()
        if not name:
            messagebox.showwarning("Delete", "Please select a profile first.")
            return
        active = cfg.get_active_profile()
        if name == active:
            messagebox.showerror("Delete", f"Cannot delete active profile '{name}'.\nSwitch to another first.")
            return
        if messagebox.askyesno("Delete Profile", f"Are you sure you want to delete profile '{name}'?", icon="warning"):
            cfg.delete_profile(name)
            self._current_profile = None
            self._refresh_all()
            self.status_label.configure(text=f"✓ Profile '{name}' deleted.")

    def _update_token(self) -> None:
        """Update token for selected profile."""
        name = self._get_selected_profile_name()
        if not name:
            messagebox.showwarning("Update Token", "Please select a profile first.")
            return
        profile = cfg.load_profile(name)
        if not profile:
            return

        dialog = TokenDialog(self, title=f"Update Token: {name}", current_token=profile.key)
        self.wait_window(dialog)
        if dialog.result:
            profile.key = dialog.result
            cfg.save_profile(profile)

            active = cfg.get_active_profile()
            if name == active:
                if profile.type == "genai":
                    cfg.write_genai_token(dialog.result)
                elif profile.type == "genai-api":
                    cfg.write_shtu_proxy_config(profile)

            self._refresh_all()
            self.status_label.configure(text=f"✓ Token updated for '{name}'.")

    # ═══════════════════════════════════════════
    # Model actions
    # ═══════════════════════════════════════════

    def _on_model_select(self, event=None) -> None:
        """Model selected in dropdown — just store, don't switch yet."""
        pass

    def _switch_model(self) -> None:
        """Switch to the selected model."""
        model = self.model_var.get()
        if not model:
            messagebox.showwarning("Switch Model", "Please select a model first.")
            return

        active = cfg.get_active_profile()
        if not active:
            messagebox.showwarning("Switch Model", "No active profile.")
            return

        profile = cfg.load_profile(active)
        if not profile:
            return

        if not messagebox.askyesno(
            "Switch Model",
            f"Switch active profile '{active}' to model '{model}'?\n\nThis may restart proxies."
        ):
            return

        self.status_label.configure(text=f"Switching model to {model}...")
        threading.Thread(target=self._do_switch_model, args=(profile, model), daemon=True).start()

    def _do_switch_model(self, profile: cfg.Profile, model: str) -> None:
        """Background: switch model."""
        ok, msg = self.proxy_mgr.switch_model(profile, model)
        self.after(0, lambda: self._on_switch_model_done(ok, msg))

    def _on_switch_model_done(self, ok: bool, msg: str) -> None:
        """UI update after model switch."""
        if ok:
            self.status_label.configure(text=f"✓ {msg}")
        else:
            messagebox.showerror("Switch Failed", msg)
            self.status_label.configure(text=f"✗ {msg}")
        self._refresh_all()

    # ═══════════════════════════════════════════
    # Proxy actions
    # ═══════════════════════════════════════════

    def _start_proxy(self) -> None:
        """Start proxy for active profile."""
        active = cfg.get_active_profile()
        if not active:
            messagebox.showwarning("Start Proxy", "No active profile. Activate one first.")
            return
        profile = cfg.load_profile(active)
        if not profile:
            return

        self.status_label.configure(text="Starting proxy...")
        threading.Thread(target=self._do_start, args=(profile,), daemon=True).start()

    def _do_start(self, profile: cfg.Profile) -> None:
        ok = self.proxy_mgr.smart_start(profile)
        msg = "Proxy started ✓" if ok else "Proxy start failed ✗"
        self.after(0, lambda: self.status_label.configure(text=msg))
        self.after(100, self._refresh_proxy_status)

    def _stop_proxy(self) -> None:
        """Stop all proxies."""
        self.proxy_mgr.stop_all()
        self._refresh_proxy_status()
        self.status_label.configure(text="All proxies stopped.")

    def _restart_proxy(self) -> None:
        """Restart proxy for active profile."""
        active = cfg.get_active_profile()
        if not active:
            messagebox.showwarning("Restart Proxy", "No active profile.")
            return
        profile = cfg.load_profile(active)
        if not profile:
            return

        self.status_label.configure(text="Restarting proxy...")
        threading.Thread(target=self._do_restart, args=(profile,), daemon=True).start()

    def _do_restart(self, profile: cfg.Profile) -> None:
        self.proxy_mgr.stop_all()
        time.sleep(1)
        ok = self.proxy_mgr.smart_start(profile)
        msg = "Proxy restarted ✓" if ok else "Proxy restart failed ✗"
        self.after(0, lambda: self.status_label.configure(text=msg))
        self.after(100, self._refresh_proxy_status)

    # ═══════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════

    @staticmethod
    def _set_text(widget: tk.Text, text: str) -> None:
        """Set text widget content (read-only)."""
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert("1.0", text)
        widget.configure(state=tk.DISABLED)

    def _show_about(self) -> None:
        messagebox.showinfo(
            "About GenAI Stack",
            "GenAI Stack Dashboard\n\n"
            "Multi-mode AI backend proxy management.\n"
            "Supports: Anthropic | GenAI JWT | GenAI API key\n\n"
            "Version: 7.0.0\n"
            "License: MIT"
        )

    def _on_close(self) -> None:
        """Handle window close — don't kill proxies, just quit GUI."""
        if self._refresh_job:
            self.after_cancel(self._refresh_job)
        self.destroy()


# ──────────────────────────────────────────────
# Profile Dialog
# ──────────────────────────────────────────────

class ProfileDialog(tk.Toplevel):
    """Modal dialog for creating/editing a profile."""

    def __init__(self, parent: tk.Tk, title: str = "Profile",
                 edit_profile: cfg.Profile | None = None) -> None:
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.result: cfg.Profile | None = None
        self._edit_profile = edit_profile
        self._editing = edit_profile is not None

        self._build_ui()
        self._center_on(parent)

        if edit_profile:
            self._load_profile(edit_profile)

        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _build_ui(self) -> None:
        frame = ttk.Frame(self, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)

        # Name
        ttk.Label(frame, text="Profile Name:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.name_var = tk.StringVar()
        self.name_entry = ttk.Entry(frame, textvariable=self.name_var, width=30)
        self.name_entry.grid(row=0, column=1, sticky=tk.EW, pady=2)
        if self._editing:
            self.name_entry.configure(state=tk.DISABLED)

        # Type
        ttk.Label(frame, text="Type:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.type_var = tk.StringVar(value="genai")
        self.type_combo = ttk.Combobox(
            frame, textvariable=self.type_var, state="readonly",
            values=["anthropic", "genai", "genai-api", "openai"]
        )
        self.type_combo.grid(row=1, column=1, sticky=tk.EW, pady=2)
        self.type_combo.bind("<<ComboboxSelected>>", self._on_type_change)
        if self._editing:
            self.type_combo.configure(state=tk.DISABLED)

        # URL
        ttk.Label(frame, text="URL:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(frame, textvariable=self.url_var, width=40)
        self.url_entry.grid(row=2, column=1, sticky=tk.EW, pady=2)

        # Key / Token
        ttk.Label(frame, text="Key / Token:").grid(row=3, column=0, sticky=tk.W, pady=2)
        self.key_var = tk.StringVar()
        self.key_entry = ttk.Entry(frame, textvariable=self.key_var, width=40)
        self.key_entry.grid(row=3, column=1, sticky=tk.EW, pady=2)

        # Big model
        ttk.Label(frame, text="Big Model (opus):").grid(row=4, column=0, sticky=tk.W, pady=2)
        self.big_var = tk.StringVar(value="GPT-5.5")
        self.big_entry = ttk.Entry(frame, textvariable=self.big_var, width=30)
        self.big_entry.grid(row=4, column=1, sticky=tk.EW, pady=2)

        # Middle model
        ttk.Label(frame, text="Middle Model (sonnet):").grid(row=5, column=0, sticky=tk.W, pady=2)
        self.mid_var = tk.StringVar()
        self.mid_entry = ttk.Entry(frame, textvariable=self.mid_var, width=30)
        self.mid_entry.grid(row=5, column=1, sticky=tk.EW, pady=2)

        # Small model
        ttk.Label(frame, text="Small Model (haiku):").grid(row=6, column=0, sticky=tk.W, pady=2)
        self.small_var = tk.StringVar()
        self.small_entry = ttk.Entry(frame, textvariable=self.small_var, width=30)
        self.small_entry.grid(row=6, column=1, sticky=tk.EW, pady=2)

        # Buttons
        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=7, column=0, columnspan=2, pady=(12, 0))

        ttk.Button(btn_frame, text="OK", command=self._on_ok).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side=tk.LEFT, padx=4)

        self._on_type_change()

    def _on_type_change(self, event=None) -> None:
        """Enable/disable fields based on type."""
        ptype = self.type_var.get()
        # URL: needed for anthropic, genai-api, openai; not for genai
        needs_url = ptype in ("anthropic", "genai-api", "openai")
        self.url_entry.configure(state=tk.NORMAL if needs_url else tk.DISABLED)
        # Big/Mid/Small models: needed for genai, genai-api, openai
        needs_models = ptype != "anthropic"
        state = tk.NORMAL if needs_models else tk.DISABLED
        self.big_entry.configure(state=state)
        self.mid_entry.configure(state=state)
        self.small_entry.configure(state=state)

    def _load_profile(self, profile: cfg.Profile) -> None:
        self.name_var.set(profile.name)
        self.type_var.set(profile.type)
        self.url_var.set(profile.url)
        self.key_var.set(profile.key)
        self.big_var.set(profile.big_model)
        self.mid_var.set(profile.middle_model)
        self.small_var.set(profile.small_model)

    def _on_ok(self) -> None:
        name = self.name_var.get().strip()
        ptype = self.type_var.get().strip()

        if not name:
            messagebox.showwarning("Validation", "Profile name is required.", parent=self)
            return
        if not self._editing and cfg.profile_exists(name):
            messagebox.showwarning("Validation", f"Profile '{name}' already exists.", parent=self)
            return

        big = self.big_var.get().strip()
        mid = self.mid_var.get().strip() or big
        small = self.small_var.get().strip() or big

        if ptype == "anthropic":
            model = self.big_var.get().strip() or "opus"
            self.result = cfg.Profile(
                name=name, type=ptype,
                url=self.url_var.get().strip(),
                key=self.key_var.get().strip(),
                model=model,
            )
        else:
            self.result = cfg.Profile(
                name=name, type=ptype,
                url=self.url_var.get().strip(),
                key=self.key_var.get().strip(),
                big_model=big, middle_model=mid, small_model=small,
            )

        self.destroy()

    def _center_on(self, parent: tk.Tk) -> None:
        """Center dialog on parent window."""
        self.update_idletasks()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        w, h = self.winfo_width(), self.winfo_height()
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"+{x}+{y}")


# ──────────────────────────────────────────────
# Token Dialog
# ──────────────────────────────────────────────

class TokenDialog(tk.Toplevel):
    """Modal dialog for updating a profile token/key."""

    def __init__(self, parent: tk.Tk, title: str = "Update Token",
                 current_token: str = "") -> None:
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.result: str | None = None

        frame = ttk.Frame(self, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="New Token / Key:").grid(row=0, column=0, sticky=tk.W, pady=4)
        self.token_var = tk.StringVar(value=current_token)
        self.token_entry = ttk.Entry(frame, textvariable=self.token_var, width=50)
        self.token_entry.grid(row=0, column=1, sticky=tk.EW, pady=4, padx=(8, 0))

        ttk.Label(
            frame,
            text="Supports: JWT (eyJ...) or student_id@password",
            font=("Segoe UI", 8) if IS_WINDOWS else ("sans-serif", 8),
        ).grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(0, 8))

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=2, column=0, columnspan=2)

        ttk.Button(btn_frame, text="OK", command=self._on_ok).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side=tk.LEFT, padx=4)

        self._center_on(parent)
        self.token_entry.focus_set()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _on_ok(self) -> None:
        token = self.token_var.get().strip()
        if not token:
            messagebox.showwarning("Validation", "Token cannot be empty.", parent=self)
            return
        self.result = token
        self.destroy()

    def _center_on(self, parent: tk.Tk) -> None:
        self.update_idletasks()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        w, h = self.winfo_width(), self.winfo_height()
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"+{x}+{y}")


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

def main() -> None:
    """Launch the Windows GUI."""
    app = GenAIStackGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
