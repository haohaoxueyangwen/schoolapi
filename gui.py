#!/usr/bin/env python3
"""GenAI Stack GUI — modern tkinter desktop interface.

Dark sidebar + card layout + async health checks.
Zero extra deps — Python stdlib only.

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

IS_WIN = os.name == "nt"
FONT = ("Segoe UI", 10) if IS_WIN else ("sans-serif", 10)
FONT_SM = ("Segoe UI", 9) if IS_WIN else ("sans-serif", 9)
FONT_BOLD = ("Segoe UI", 10, "bold") if IS_WIN else ("sans-serif", 10, "bold")
FONT_TITLE = ("Segoe UI", 13, "bold") if IS_WIN else ("sans-serif", 13, "bold")
FONT_MONO = ("Cascadia Code", 10) if IS_WIN else ("monospace", 10)
FONT_MONO_SM = ("Cascadia Code", 9) if IS_WIN else ("monospace", 9)

# ── Color palette ──
C_SIDEBAR = "#1a1b2e"
C_SIDEBAR_HL = "#252640"
C_BG = "#f0f2f5"
C_CARD = "#ffffff"
C_TEXT = "#1f2937"
C_SUBTLE = "#6b7280"
C_PRIMARY = "#4f6ef7"
C_PRIMARY_HOVER = "#3b5de7"
C_SUCCESS = "#10b981"
C_DANGER = "#ef4444"
C_WARNING = "#f59e0b"
C_BORDER = "#e5e7eb"
C_TAG_GENAI = "#8b5cf6"
C_TAG_ANTHROPIC = "#3b82f6"
C_TAG_API = "#06b6d4"
C_TITLEBAR = "#131428"

REFRESH_MS = 3000


# ── Helpers ──────────────────────────────────────

def _tag_color(ptype: str) -> str:
    return {"genai": C_TAG_GENAI, "genai-api": C_TAG_API,
            "openai": C_TAG_API, "anthropic": C_TAG_ANTHROPIC}.get(ptype, C_SUBTLE)


def _status_dot(on: bool) -> str:
    return "●" if on else "○"


def _status_color(on: bool) -> str:
    return C_SUCCESS if on else C_DANGER


# ─────────────────────────────────────────────
# Main window
# ─────────────────────────────────────────────

class GenAIStackGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("GenAI Stack")
        self.geometry("980x680")
        self.minsize(820, 520)
        self.configure(bg=C_BG)

        self.pm = ProxyManager()
        self._cur: cfg.Profile | None = None
        self._rid: str | None = None
        self._hc: dict = {"shtu_proxy": False, "genai2api": False}

        self._build_styles()
        self._build_titlebar()
        self._build_layout()
        self.after(80, self._async_refresh_all)
        self._schedule_health()
        self.protocol("WM_DELETE_WINDOW", self._close)

    # ── Styles ──

    def _build_styles(self):
        s = ttk.Style(self)
        if IS_WIN:
            for t_ in ("vista", "xpnative", "clam"):
                if t_ in s.theme_names(): s.theme_use(t_); break
        s.configure("TFrame", background=C_BG)
        s.configure("Sidebar.TFrame", background=C_SIDEBAR)
        s.configure("Card.TFrame", background=C_CARD)
        s.configure("TitleBar.TFrame", background=C_TITLEBAR)
        s.configure("TLabel", background=C_BG, foreground=C_TEXT, font=FONT)
        s.configure("SidebarLbl.TLabel", background=C_SIDEBAR, foreground="#c8c8d4", font=FONT)
        s.configure("SidebarHd.TLabel", background=C_SIDEBAR, foreground="#ffffff", font=FONT_BOLD)
        s.configure("CardHd.TLabel", background=C_CARD, foreground=C_TEXT, font=FONT_BOLD)
        s.configure("CardBody.TLabel", background=C_CARD, foreground=C_TEXT, font=FONT_MONO_SM)
        s.configure("Subtitle.TLabel", foreground=C_SUBTLE, font=FONT_SM)
        s.configure("Title.TLabel", background=C_TITLEBAR, foreground="#ffffff", font=FONT_TITLE)
        s.configure("Tag.TLabel", font=FONT_SM)

        s.configure("Primary.TButton", font=FONT_BOLD, padding=(14, 6))
        s.configure("Sidebar.TButton", font=FONT_SM, padding=(6, 3))
        s.map("Primary.TButton",
              background=[("active", C_PRIMARY_HOVER), ("!active", C_PRIMARY)],
              foreground=[("active", "#fff"), ("!active", "#fff")])

    # ── Title bar ──

    def _build_titlebar(self):
        bar = ttk.Frame(self, style="TitleBar.TFrame")
        bar.pack(fill=tk.X)
        inner = ttk.Frame(bar, style="TitleBar.TFrame")
        inner.pack(fill=tk.X, padx=20, pady=(10, 8))
        ttk.Label(inner, text="⚡ GenAI Stack", style="Title.TLabel").pack(side=tk.LEFT)
        self._title_status = ttk.Label(inner, foreground="#8b8fa3",
                                       background=C_TITLEBAR, font=FONT_SM)
        self._title_status.pack(side=tk.RIGHT)

    # ── Layout ──

    def _build_layout(self):
        body = ttk.Frame(self)
        body.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        # Sidebar
        sidebar = ttk.Frame(body, style="Sidebar.TFrame", width=220)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)
        self._build_sidebar(sidebar)

        # Content
        content = ttk.Frame(body)
        content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(1, 0))
        self._build_content(content)

        # Bottom bar
        self._build_bottombar()

    # ── Sidebar ──

    def _build_sidebar(self, parent: ttk.Frame):
        # Header
        hdr = ttk.Frame(parent, style="Sidebar.TFrame")
        hdr.pack(fill=tk.X, padx=16, pady=(16, 8))
        ttk.Label(hdr, text="PROFILES", style="SidebarHd.TLabel").pack(side=tk.LEFT)
        ttk.Button(hdr, text="＋", width=3, style="Sidebar.TButton",
                   command=self._new_profile).pack(side=tk.RIGHT)

        # List
        lf = ttk.Frame(parent, style="Sidebar.TFrame")
        lf.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))

        self._plist = tk.Listbox(
            lf,
            bg=C_SIDEBAR, fg="#d0d0dc",
            selectbackground=C_SIDEBAR_HL, selectforeground="#ffffff",
            font=FONT_MONO,
            activestyle="none", highlightthickness=0,
            borderwidth=0, relief=tk.FLAT,
            exportselection=False,
        )
        sb = ttk.Scrollbar(lf, orient=tk.VERTICAL, command=self._plist.yview)
        self._plist.configure(yscrollcommand=sb.set)
        self._plist.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._plist.bind("<<ListboxSelect>>", self._on_select)
        self._plist.bind("<Double-1>", lambda e: self._activate())

        # Buttons
        bf = ttk.Frame(parent, style="Sidebar.TFrame")
        bf.pack(fill=tk.X, padx=10, pady=(0, 16))
        for txt, cmd in [("★ Activate", self._activate),
                         ("✎ Edit", self._edit_profile),
                         ("✕ Delete", self._delete_profile),
                         ("🔑 Token", self._update_token)]:
            ttk.Button(bf, text=txt, style="Sidebar.TButton",
                       command=cmd).pack(fill=tk.X, pady=1)

    # ── Content ──

    def _build_content(self, parent: ttk.Frame):
        cf = ttk.Frame(parent)
        cf.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)

        # Row 1: Profile Detail + Proxy Status
        row1 = ttk.Frame(cf)
        row1.pack(fill=tk.X, pady=(0, 12))
        self._card_detail(row1)
        self._card_proxy(row1)

        # Row 2: Model + quick actions
        row2 = ttk.Frame(cf)
        row2.pack(fill=tk.X)
        self._card_model(row2)

    def _card_frame(self, parent, title: str, side=tk.LEFT, expand=True, padx=(0, 0), w=None):
        """Create a white card with header."""
        outer = ttk.Frame(parent)
        outer.pack(side=side, fill=tk.BOTH if expand else tk.Y,
                   expand=expand, padx=padx)
        if w:
            outer.configure(width=w)
            outer.pack_propagate(False)

        card = tk.Frame(outer, bg=C_CARD, highlightbackground=C_BORDER,
                        highlightthickness=1, bd=0)
        card.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        hdr = tk.Frame(card, bg=C_CARD, bd=0)
        hdr.pack(fill=tk.X, padx=14, pady=(12, 6))
        tk.Label(hdr, text=title, font=FONT_BOLD, bg=C_CARD,
                 fg=C_TEXT).pack(side=tk.LEFT)
        return card

    def _card_detail(self, parent):
        card = self._card_frame(parent, "📋 Profile Detail", side=tk.LEFT,
                                expand=True, padx=(0, 6))

        self._detail_text = tk.Text(
            card, height=9,
            font=FONT_MONO,
            bg=C_CARD, fg=C_TEXT,
            relief=tk.FLAT, borderwidth=0,
            wrap=tk.WORD, state=tk.DISABLED,
            padx=14, pady=4,
        )
        self._detail_text.pack(fill=tk.BOTH, expand=True)
        # Tag colors
        self._detail_text.tag_configure("key", foreground=C_SUBTLE)
        self._detail_text.tag_configure("val", foreground=C_TEXT)

    def _card_proxy(self, parent):
        card = self._card_frame(parent, "🔌 Proxy Status", side=tk.LEFT,
                                expand=False, w=280)

        inner = tk.Frame(card, bg=C_CARD)
        inner.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 10))

        self._proxy_labels: dict[str, tuple[tk.Label, tk.Label]] = {}

        def _row(label, port):
            f = tk.Frame(inner, bg=C_CARD)
            f.pack(fill=tk.X, pady=3)
            dot_lbl = tk.Label(f, text="○", font=("sans-serif", 12), bg=C_CARD, fg=C_DANGER)
            dot_lbl.pack(side=tk.LEFT, padx=(0, 6))
            tk.Label(f, text=f"{label}  ", font=FONT_BOLD, bg=C_CARD, fg=C_TEXT).pack(side=tk.LEFT)
            st_lbl = tk.Label(f, text="Checking...", font=FONT_SM, bg=C_CARD, fg=C_SUBTLE)
            st_lbl.pack(side=tk.RIGHT)
            self._proxy_labels[label] = (dot_lbl, st_lbl)

        _row("genai2api", 5000)
        _row("SHTUProxy", 8082)

        # JWT
        jf = tk.Frame(inner, bg=C_CARD)
        jf.pack(fill=tk.X, pady=(8, 0))
        tk.Label(jf, text="JWT Status", font=FONT_SM, bg=C_CARD, fg=C_SUBTLE).pack(anchor=tk.W)
        self._jwt_label = tk.Label(jf, text="—", font=FONT_BOLD, bg=C_CARD, fg=C_TEXT)
        self._jwt_label.pack(anchor=tk.W)

    def _card_model(self, parent):
        card = self._card_frame(parent, "🧠 Model", side=tk.LEFT, expand=True)

        inner = tk.Frame(card, bg=C_CARD)
        inner.pack(fill=tk.X, padx=14, pady=(0, 12))

        self.model_var = tk.StringVar()
        cb = ttk.Combobox(inner, textvariable=self.model_var, state="readonly",
                          font=FONT, width=36)
        cb.pack(side=tk.LEFT, padx=(0, 8))

        ttk.Button(inner, text="Switch", style="Primary.TButton",
                   command=self._switch_model).pack(side=tk.LEFT)

        # Quick status
        self._model_status = tk.Label(card, text="", font=FONT_SM, bg=C_CARD, fg=C_SUBTLE)
        self._model_status.pack(anchor=tk.W, padx=14, pady=(0, 8))

    # ── Bottom bar ──

    def _build_bottombar(self):
        bar = tk.Frame(self, bg=C_TITLEBAR, height=44)
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        bar.pack_propagate(False)

        inner = tk.Frame(bar, bg=C_TITLEBAR)
        inner.pack(fill=tk.BOTH, padx=16, pady=6)

        for txt, cmd in [("▶  Start", self._start_proxy),
                         ("■  Stop", self._stop_proxy),
                         ("↻  Restart", self._restart_proxy)]:
            btn = tk.Button(inner, text=txt, font=FONT_SM,
                            bg=C_PRIMARY, fg="#fff",
                            activebackground=C_PRIMARY_HOVER, activeforeground="#fff",
                            relief=tk.FLAT, bd=0, padx=14, pady=4,
                            cursor="hand2", command=cmd)
            btn.pack(side=tk.LEFT, padx=3)

        tk.Label(inner, text="", bg=C_TITLEBAR).pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._auto_var = tk.BooleanVar(value=True)
        cb = tk.Checkbutton(inner, text="Auto 3s", variable=self._auto_var,
                            font=FONT_SM, bg=C_TITLEBAR, fg="#8b8fa3",
                            selectcolor=C_SIDEBAR, activebackground=C_TITLEBAR,
                            activeforeground="#fff")
        cb.pack(side=tk.RIGHT)

        tk.Button(inner, text="↺", font=FONT_BOLD, bg=C_TITLEBAR, fg="#8b8fa3",
                  activebackground=C_SIDEBAR_HL, relief=tk.FLAT, bd=0, padx=8,
                  cursor="hand2", command=self._async_refresh_all).pack(side=tk.RIGHT)

    # ═══════ Async ops (NEVER block UI) ═══════

    def _async_refresh_all(self):
        self._refresh_profiles()
        self._update_detail()
        self._update_titlebar()
        self._async_health()
        self._async_models()

    def _async_health(self):
        def _run():
            return self.pm.health_check()

        def _done():
            if hasattr(self, '_hct') and self._hct.is_alive():
                return
            self._hc = self._hct.result_cache if hasattr(self._hct, 'result_cache') else self._hc
            self._update_proxy_display()

        t = threading.Thread(target=lambda: (
            setattr(t, 'result_cache', _run()),
            self.after(0, _done)
        ), daemon=True)
        self._hct = t
        t.start()

    def _async_models(self):
        def _run():
            active = cfg.get_active_profile()
            pt, pp, url, key, hc = None, None, "", "", False
            if active:
                p = cfg.load_profile(active)
                if p:
                    pt = p.type
                    url = p.url
                    key = p.key
                    if p.type == "genai" and self._hc.get("genai2api", False):
                        pp = cfg.GENAI2API_PORT
                    elif p.type in ("genai-api", "openai") and self._hc.get("shtu_proxy", False):
                        pp = cfg.SHTU_PROXY_PORT
                    if p.type == "anthropic":
                        hc = bool(p.model)
            return pt, pp, url, key, hc

        def _done():
            pt, pp, url, key, hc = self._mres
            models = get_models(pp, pt, hc, profile_url=url, profile_key=key)
            flat = [m for gm in models.values() for m in gm]
            self.model_combo["values"] = flat
            active = cfg.get_active_profile()
            cur = ""
            if active:
                p = cfg.load_profile(active)
                if p:
                    cur = p.display_model
                    # Show sub-models info
                    mid = p.middle_model or ""
                    sml = p.small_model or ""
                    parts = [f"Big: {cur}"]
                    if mid and mid != cur:
                        parts.append(f"Mid: {mid}")
                    if sml and sml != cur:
                        parts.append(f"Small: {sml}")
                    self._model_status.configure(text="  |  ".join(parts))
            if cur in flat:
                self.model_var.set(cur)
            elif flat:
                self.model_var.set(flat[0])

        def _fetch():
            self._mres = _run()
            self.after(0, _done)

        threading.Thread(target=_fetch, daemon=True).start()

    # ── Schedule ──

    def _schedule_health(self):
        if self._auto_var.get():
            self._async_health()
        self._rid = self.after(REFRESH_MS, self._schedule_health)

    # ── Update displays ──

    def _refresh_profiles(self):
        sel = self._get_sel()
        self._plist.delete(0, tk.END)
        active = cfg.get_active_profile()
        for p in cfg.list_profiles():
            marker = "★ " if p.name == active else "  "
            self._plist.insert(tk.END, f"{marker}{p.name}")
        if sel:
            for i in range(self._plist.size()):
                if sel in self._plist.get(i):
                    self._plist.selection_set(i); break
        if not self._plist.curselection() and self._plist.size() > 0:
            self._plist.selection_set(0)
            self._on_select()

    def _update_detail(self):
        name = self._get_sel()
        if not name:
            self._detail_text.configure(state=tk.NORMAL)
            self._detail_text.delete("1.0", tk.END)
            self._detail_text.insert("1.0", "\n  (select a profile)")
            self._detail_text.configure(state=tk.DISABLED)
            return
        p = cfg.load_profile(name)
        if not p:
            return
        self._cur = p
        data = p.to_json()
        tag_color = _tag_color(p.type)

        lines = [f"  Profile:  {p.name}"]
        lines.append(f"  Type:     {p.type}")
        for k, v in data.items():
            if k in ("type",):
                continue
            if k == "key" and v:
                v = v if len(v) <= 16 else v[:12] + f"…({len(v)} chars)"
            lines.append(f"  {k}:  {v}")

        self._detail_text.configure(state=tk.NORMAL)
        self._detail_text.delete("1.0", tk.END)
        self._detail_text.insert("1.0", "\n".join(lines))
        self._detail_text.configure(state=tk.DISABLED)

        # Update type tag in status
        self._title_status.configure(text=f"{p.type}  ·  {p.display_model}")

    def _update_proxy_display(self):
        h = self._hc
        for name, key in [("genai2api", "genai2api"), ("SHTUProxy", "shtu_proxy")]:
            pair = self._proxy_labels.get(name)
            if not pair:
                continue
            dot, st = pair
            on = h.get(key, False)
            dot.configure(text=_status_dot(on), fg=_status_color(on))
            st.configure(text="Running" if on else "Stopped",
                         fg=_status_color(on))

        token = cfg.read_genai_token()
        if token:
            try:
                self._jwt_label.configure(text=jwt_remaining_str(token))
            except Exception:
                pass
        else:
            self._jwt_label.configure(text="No token", fg=C_SUBTLE)

    def _update_titlebar(self):
        active = cfg.get_active_profile()
        if active and cfg.profile_exists(active):
            p = cfg.load_profile(active)
            if p:
                self._title_status.configure(text=f"{p.type}  ·  {p.display_model}")

    # ── Profile actions ──

    def _get_sel(self) -> str | None:
        s = self._plist.curselection()
        if not s:
            return None
        return self._plist.get(s[0]).lstrip("★ ").strip() or None

    def _on_select(self, e=None):
        self._update_detail()

    def _activate(self):
        name = self._get_sel()
        if not name:
            messagebox.showwarning("Activate", "Select a profile."); return
        if not messagebox.askyesno("Activate", f"Activate '{name}'?"): return
        p = cfg.load_profile(name)
        if not p: return

        self._title_status.configure(text=f"Switching...")

        def _run():
            ok, msg = self.pm.smart_switch(p)
            self.after(0, lambda: self._on_activate_done(ok, msg, p))

        threading.Thread(target=_run, daemon=True).start()

    def _on_activate_done(self, ok, msg, p):
        if ok:
            self._title_status.configure(text=f"✓ {p.type} · {p.display_model}")
        else:
            messagebox.showerror("Failed", msg)
        self._async_refresh_all()

    def _new_profile(self):
        d = ProfileDialog(self)
        self.wait_window(d)
        if d.result:
            cfg.save_profile(d.result)
            self._async_refresh_all()

    def _edit_profile(self):
        name = self._get_sel()
        if not name: messagebox.showwarning("Edit", "Select a profile."); return
        p = cfg.load_profile(name)
        if not p: return
        d = ProfileDialog(self, edit_profile=p)
        self.wait_window(d)
        if d.result:
            cfg.save_profile(d.result)
            self._async_refresh_all()

    def _delete_profile(self):
        name = self._get_sel()
        if not name: return
        if name == cfg.get_active_profile():
            messagebox.showerror("Delete", f"'{name}' is active."); return
        if messagebox.askyesno("Delete", f"Delete '{name}'?", icon="warning"):
            cfg.delete_profile(name)
            self._async_refresh_all()

    def _update_token(self):
        name = self._get_sel()
        if not name: return
        p = cfg.load_profile(name)
        if not p: return
        d = TokenDialog(self, current=cfg.read_genai_token() if p.type == "genai" else p.key)
        self.wait_window(d)
        if d.result:
            p.key = d.result; cfg.save_profile(p)
            if name == cfg.get_active_profile():
                if p.type == "genai": cfg.write_genai_token(d.result)
                elif p.type == "genai-api": cfg.write_shtu_proxy_config(p)
            self._async_refresh_all()

    # ── Model ──

    def _switch_model(self):
        m = self.model_var.get()
        if not m: return
        active = cfg.get_active_profile()
        if not active: return
        p = cfg.load_profile(active)
        if not p: return
        if not messagebox.askyesno("Switch", f"Switch to '{m}'?"): return
        self._model_status.configure(text="Switching...")

        def _run():
            ok, msg = self.pm.switch_model(p, m)
            self.after(0, lambda: self._on_switch_done(ok, msg))

        threading.Thread(target=_run, daemon=True).start()

    def _on_switch_done(self, ok, msg):
        self._model_status.configure(text=msg if ok else f"✗ {msg}")
        self._async_refresh_all()

    # ── Proxy ──

    def _start_proxy(self):
        p = self._active_p()
        if not p: return

        def _run():
            ok = self.pm.smart_start(p)
            self.after(0, lambda: self._title_status.configure(
                text="✓ Proxy started" if ok else "✗ Start failed"))
            self.after(100, self._async_health)

        threading.Thread(target=_run, daemon=True).start()

    def _stop_proxy(self):
        def _run():
            self.pm.stop_all()
            self.after(0, lambda: self._title_status.configure(text="Stopped"))
            self.after(100, self._async_health)

        threading.Thread(target=_run, daemon=True).start()

    def _restart_proxy(self):
        p = self._active_p()
        if not p: return

        def _run():
            self.pm.stop_all(); time.sleep(1)
            ok = self.pm.smart_start(p)
            self.after(0, lambda: self._title_status.configure(
                text="✓ Restarted" if ok else "✗ Failed"))
            self.after(100, self._async_health)

        threading.Thread(target=_run, daemon=True).start()

    def _active_p(self):
        active = cfg.get_active_profile()
        if not active:
            messagebox.showwarning("Proxy", "No active profile."); return None
        return cfg.load_profile(active)

    # ── Backup / Restore ──

    def backup_config(self):
        p = filedialog.asksaveasfilename(defaultextension=".json",
                                         filetypes=[("JSON", "*.json")],
                                         initialfile="genai-backup.json")
        if not p: return
        data = {"v": "7.0.0", "date": time.strftime("%Y-%m-%d %H:%M"),
                "active": cfg.get_active_profile(),
                "profiles": {x.name: x.to_json() for x in cfg.list_profiles()}}
        Path(p).write_text(json.dumps(data, indent=2, ensure_ascii=False))
        messagebox.showinfo("Backup", f"Saved to:\n{p}")

    def restore_config(self):
        p = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if not p: return
        data = json.loads(Path(p).read_text())
        profiles = data.get("profiles", {})
        count, skip = 0, 0
        for name, pd in profiles.items():
            if cfg.profile_exists(name):
                if not messagebox.askyesno("Restore", f"'{name}' exists. Overwrite?"):
                    skip += 1; continue
            cfg.save_profile(cfg.Profile(
                name=name, type=pd.get("type", ""), url=pd.get("url", ""),
                key=pd.get("key", ""), model=pd.get("model", ""),
                big_model=pd.get("big_model", ""),
                middle_model=pd.get("middle_model", ""),
                small_model=pd.get("small_model", ""),
            )); count += 1
        if data.get("active") in profiles:
            cfg.set_active_profile(data["active"])
        self._async_refresh_all()
        messagebox.showinfo("Restore", f"Restored {count} profiles.")

    # ── Misc ──

    def _close(self):
        if self._rid: self.after_cancel(self._rid)
        self.destroy()


# ─────────────────────────────────────
# Profile Dialog
# ─────────────────────────────────────

class ProfileDialog(tk.Toplevel):
    def __init__(self, parent, title="New Profile", edit_profile=None):
        super().__init__(parent)
        self.title(title); self.resizable(False, False)
        self.transient(parent); self.grab_set()
        self.result = None
        self._editing = edit_profile is not None
        self.configure(bg=C_BG)

        f = ttk.Frame(self)
        f.pack(fill=tk.BOTH, padx=20, pady=20)

        ttk.Label(f, text="Profile Name", font=FONT_BOLD).grid(row=0, column=0, sticky=tk.W, pady=(0, 2))
        self._name = tk.StringVar()
        e = ttk.Entry(f, textvariable=self._name, font=FONT, width=34)
        e.grid(row=1, column=0, columnspan=2, sticky=tk.EW, pady=(0, 10))
        if self._editing:
            e.configure(state=tk.DISABLED)

        ttk.Label(f, text="Type", font=FONT_BOLD).grid(row=2, column=0, sticky=tk.W, pady=(0, 2))
        self._type = tk.StringVar(value="genai")
        cb = ttk.Combobox(f, textvariable=self._type, state="readonly", font=FONT,
                          values=["anthropic", "genai", "genai-api", "openai"])
        cb.grid(row=3, column=0, columnspan=2, sticky=tk.EW, pady=(0, 10))
        cb.bind("<<ComboboxSelected>>", self._type_changed)
        if self._editing: cb.configure(state=tk.DISABLED)

        ttk.Label(f, text="API URL", font=FONT_BOLD).grid(row=4, column=0, sticky=tk.W, pady=(0, 2))
        self._url = tk.StringVar()
        self._url_e = ttk.Entry(f, textvariable=self._url, font=FONT, width=46)
        self._url_e.grid(row=5, column=0, columnspan=2, sticky=tk.EW, pady=(0, 10))

        ttk.Label(f, text="Key / Token", font=FONT_BOLD).grid(row=6, column=0, sticky=tk.W, pady=(0, 2))
        self._key = tk.StringVar()
        self._key_e = ttk.Entry(f, textvariable=self._key, font=FONT, width=46)
        self._key_e.grid(row=7, column=0, columnspan=2, sticky=tk.EW, pady=(0, 10))

        ttk.Label(f, text="Models", font=FONT_BOLD).grid(row=8, column=0, sticky=tk.W, pady=(0, 4))
        mf = ttk.Frame(f)
        mf.grid(row=9, column=0, columnspan=2, sticky=tk.EW, pady=(0, 10))
        self._big = tk.StringVar(value="GPT-5.5")
        self._mid = tk.StringVar()
        self._small = tk.StringVar()
        for i, (label, var) in enumerate([("Big", self._big), ("Mid", self._mid), ("Small", self._small)]):
            ttk.Label(mf, text=label, font=FONT_SM).grid(row=0, column=i, padx=(0 if i == 0 else 8, 0))
            self._be = ttk.Entry(mf, textvariable=var, font=FONT, width=14)
            self._be.grid(row=1, column=i, padx=(0 if i == 0 else 8, 0))
            setattr(self, f"_m{i}", self._be)

        bf = ttk.Frame(f)
        bf.grid(row=10, column=0, columnspan=2, pady=(6, 0))
        ttk.Button(bf, text="OK", style="Primary.TButton",
                   command=self._ok).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(bf, text="Cancel", command=self.destroy).pack(side=tk.LEFT)

        if edit_profile:
            self._load(edit_profile)
        self._type_changed()
        self._center(parent)

    def _type_changed(self, e=None):
        needs_url = self._type.get() in ("anthropic", "genai-api", "openai")
        self._url_e.configure(state=tk.NORMAL if needs_url else tk.DISABLED)
        needs_m = self._type.get() != "anthropic"
        st = tk.NORMAL if needs_m else tk.DISABLED
        for i in range(3):
            getattr(self, f"_m{i}").configure(state=st)

    def _load(self, p):
        self._name.set(p.name); self._type.set(p.type)
        self._url.set(p.url); self._key.set(p.key)
        self._big.set(p.big_model); self._mid.set(p.middle_model)
        self._small.set(p.small_model)

    def _ok(self):
        name = self._name.get().strip()
        if not name: messagebox.showwarning("Validation", "Name required."); return
        if not self._editing and cfg.profile_exists(name):
            messagebox.showwarning("Validation", f"'{name}' exists."); return
        ptype = self._type.get().strip()
        big = self._big.get().strip()
        mid = self._mid.get().strip() or big
        small = self._small.get().strip() or big
        if ptype == "anthropic":
            self.result = cfg.Profile(name=name, type=ptype, url=self._url.get().strip(),
                                      key=self._key.get().strip(), model=big or "opus")
        else:
            self.result = cfg.Profile(name=name, type=ptype, url=self._url.get().strip(),
                                      key=self._key.get().strip(),
                                      big_model=big, middle_model=mid, small_model=small)
        self.destroy()

    def _center(self, p):
        self.update_idletasks()
        pw, ph, px, py = p.winfo_width(), p.winfo_height(), p.winfo_rootx(), p.winfo_rooty()
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{px+(pw-w)//2}+{py+(ph-h)//2}")


# ─────────────────────────────────────
# Token Dialog
# ─────────────────────────────────────

class TokenDialog(tk.Toplevel):
    def __init__(self, parent, title="Update Token", current=""):
        super().__init__(parent)
        self.title(title); self.resizable(False, False)
        self.transient(parent); self.grab_set()
        self.result = None
        self.configure(bg=C_BG)

        f = ttk.Frame(self)
        f.pack(fill=tk.BOTH, padx=20, pady=20)

        ttk.Label(f, text="New Token / Key", font=FONT_BOLD).pack(anchor=tk.W, pady=(0, 4))
        self._v = tk.StringVar(value=current)
        ttk.Entry(f, textvariable=self._v, font=FONT, width=50).pack(fill=tk.X, pady=(0, 4))
        ttk.Label(f, text="JWT (eyJ…) or student_id@password", style="Subtitle.TLabel",
                  font=FONT_SM).pack(anchor=tk.W, pady=(0, 12))

        bf = ttk.Frame(f)
        bf.pack()
        ttk.Button(bf, text="OK", style="Primary.TButton",
                   command=self._ok).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(bf, text="Cancel", command=self.destroy).pack(side=tk.LEFT)

        self._center(parent)

    def _ok(self):
        t = self._v.get().strip()
        if not t: messagebox.showwarning("Validation", "Empty."); return
        self.result = t; self.destroy()

    def _center(self, p):
        self.update_idletasks()
        pw, ph, px, py = p.winfo_width(), p.winfo_height(), p.winfo_rootx(), p.winfo_rooty()
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{px+(pw-w)//2}+{py+(ph-h)//2}")


def main():
    GenAIStackGUI().mainloop()


if __name__ == "__main__":
    main()
