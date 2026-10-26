# Flow Execution Debugger
# Copyright (C) 2026 samcall22 (https://github.com/samcall22)
# SPDX-License-Identifier: GPL-3.0-or-later
# This program comes with ABSOLUTELY NO WARRANTY. See the LICENSE file for details.

from __future__ import annotations

import calendar
import json
import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from datetime import date, datetime, timedelta
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Dict, List, Optional

from . import __author__, __license__, __url__, __version__, transforms
from .auth import DEFAULT_CLIENT_ID, TokenProvider
from .browser import BrowserInfo, BrowserProfile, detect_browsers, get_profiles, open_url
from .dataverse import load_solution_maps, normalize_guid
from .pa_client import EnvironmentInfo, FlowInfo, ParamMeta, PowerAutomateClient, RunInfo, list_environments
from .settings import Settings

PARAM_FILTER_WARNING_THRESHOLD = 500
GRID_DATETIME_FORMAT = "%d/%m/%Y %H:%M:%S"
PICKER_FORMAT = "%d/%m/%Y %H:%M"

MODE_FULL, MODE_CLEAN, MODE_TREE, MODE_RAW = 0, 1, 2, 3
JSON_MODES = ["Full", "Clean (loops only)", "Tree (nested loops)", "Raw (run data only)"]
ALL_VALUE = "All"

GUIDE_TEXT = (
    "0. Press \"Sign in\" (the browser opens; MFA is supported)\n"
    "    and choose the environment.\n"
    "    Tenant is optional: leave it empty to use your home\n"
    "    tenant, or type its id/domain if you are a guest.\n\n"
    "1. Press \"Load flows\" to fetch your flows.\n\n"
    "2. Filter by name, solution or status and\n"
    "    select a flow from the list.\n\n"
    "3. Adjust the range (date AND TIME) and press\n"
    "    \"Load runs\".\n"
    "    Pick the date with the ▾ calendar button;\n"
    "    the time, by typing it or with ↑/↓ (the arrow keys\n"
    "    change the part under the cursor).\n\n"
    "4. (Optional) Enter values in the\n"
    "    body parameters and press\n"
    "    \"Filter by parameters\".\n\n"
    "5. On each run: Open, view Parameters,\n"
    "    Child flows, Copy link or Download JSON\n"
    "    (toolbar, right-click menu or double-click = Open).\n\n"
    "FLOW LIST SCOPE:\n"
    "- If you are an environment administrator,\n"
    "  ALL flows in the environment are listed.\n"
    "- Otherwise only your own flows and the ones\n"
    "  shared with you are listed (partial list);\n"
    "  the bottom label warns you about it.\n\n"
    "CHILD FLOWS:\n"
    "- Lists the calls to other flows with a\n"
    "  direct link to the invoked run.\n"
    "- Indicates in which loop iteration each\n"
    "  call took place.\n"
    "- It requires downloading the run's actions,\n"
    "  so the first time it takes a while.\n"
    "- If the flow is invoked over HTTP instead\n"
    "  of with \"Run a child flow\",\n"
    "  there may be no link to the child run.\n\n"
    "NOTE: times are shown in your local time,\n"
    "in 24h format.\n\n"
    "JSON MODES:\n"
    "- Full: everything exactly as the API returns it.\n"
    "- Clean: flat list of loops.\n"
    "- Tree (recommended): real hierarchy.\n"
    "  Each iteration of the outer loop carries\n"
    "  its 'item', its 'actions' and, inside,\n"
    "  'loops' with the iterations of the inner\n"
    "  loop and THEIR own 'item'.\n"
    "  Navigation path in 'path', and the\n"
    "  portal number in 'portalIndex'.\n"
    "- Raw: the run data only, without numbering\n"
    "  loops or adding extra blocks.\n\n"
    "IMPORTANT (high-volume flows):\n"
    "- The range is applied on the SERVER: if you\n"
    "  change it, press \"Load runs\" again.\n"
    "- Use short windows (minutes/hours).\n"
    "- Filtering by parameters downloads 1 file\n"
    "  per run: narrow the range first."
)


def _fmt_span(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02}:{(seconds % 3600) // 60:02}:{seconds % 60:02}"


def _ellipsis(text: str, max_len: int) -> str:
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def _add_months(dt: datetime, months: int) -> datetime:
    total = dt.year * 12 + (dt.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


# ===================== small widgets =====================


class ToolTip:
    def __init__(self, widget: tk.Widget, text: str):
        self.widget, self.text, self.tip = widget, text, None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def _show(self, _e=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 2
        self.tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(tw, text=self.text, justify="left", background="#ffffe0",
                 relief="solid", borderwidth=1, padx=4, pady=2).pack()

    def _hide(self, _e=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


class CalendarPopup(tk.Toplevel):
    def __init__(self, anchor: tk.Widget, current: datetime, callback: Callable[[date], None]):
        super().__init__(anchor)
        self.title("Pick a date")
        self.resizable(False, False)
        self.transient(anchor.winfo_toplevel())
        self.callback = callback
        self.year, self.month = current.year, current.month
        self.selected = current.date()

        header = ttk.Frame(self, padding=4)
        header.pack(fill="x")
        ttk.Button(header, text="◀", width=3, command=lambda: self._shift(-1)).pack(side="left")
        self.lbl = ttk.Label(header, anchor="center")
        self.lbl.pack(side="left", expand=True, fill="x")
        ttk.Button(header, text="▶", width=3, command=lambda: self._shift(1)).pack(side="right")

        self.days = ttk.Frame(self, padding=4)
        self.days.pack()
        ttk.Button(self, text="Today", command=lambda: self._pick(date.today())).pack(pady=(0, 6))

        self._render()
        self.geometry(f"+{anchor.winfo_rootx()}+{anchor.winfo_rooty() + anchor.winfo_height()}")
        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()
        self.focus_set()

    def _render(self):
        for w in self.days.winfo_children():
            w.destroy()
        self.lbl.configure(text=f"{calendar.month_name[self.month]} {self.year}")
        for i, wd in enumerate(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")):
            ttk.Label(self.days, text=wd, width=4, anchor="center").grid(row=0, column=i)
        weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(self.year, self.month)
        for r, week in enumerate(weeks, start=1):
            for c, day in enumerate(week):
                if day == 0:
                    continue
                d = date(self.year, self.month, day)
                text = f"[{day}]" if d == self.selected else str(day)
                ttk.Button(self.days, text=text, width=4,
                           command=lambda d=d: self._pick(d)).grid(row=r, column=c, padx=1, pady=1)

    def _shift(self, delta: int):
        total = self.year * 12 + (self.month - 1) + delta
        self.year, m = divmod(total, 12)
        self.month = m + 1
        self._render()

    def _pick(self, d: date):
        self.callback(d)
        self.destroy()


class DateTimeEntry(ttk.Frame):
    """dd/mm/yyyy hh:mm entry. ↑/↓ change the part under the cursor; ▾ opens a calendar."""

    SEGMENTS = ((0, 2, "day"), (3, 5, "month"), (6, 10, "year"), (11, 13, "hour"), (14, 16, "minute"))

    def __init__(self, master: tk.Widget, value: datetime):
        super().__init__(master)
        self.var = tk.StringVar()
        self.entry = ttk.Entry(self, textvariable=self.var, width=17)
        self.entry.pack(side="left")
        ttk.Button(self, text="▾", width=2, command=self._open_calendar).pack(side="left", padx=(2, 0))
        self.entry.bind("<Up>", lambda _e: self._step(1))
        self.entry.bind("<Down>", lambda _e: self._step(-1))
        self.set(value)

    def set(self, value: datetime) -> None:
        self.var.set(value.strftime(PICKER_FORMAT))

    def get(self) -> datetime:
        return datetime.strptime(self.var.get().strip(), PICKER_FORMAT)

    def _step(self, delta: int):
        try:
            current = self.get()
        except ValueError:
            return "break"
        pos = self.entry.index("insert")
        unit = "minute"
        for start, end, u in self.SEGMENTS:
            if start <= pos <= end:
                unit = u
                break
        if unit == "day":
            new = current + timedelta(days=delta)
        elif unit == "month":
            new = _add_months(current, delta)
        elif unit == "year":
            new = _add_months(current, 12 * delta)
        elif unit == "hour":
            new = current + timedelta(hours=delta)
        else:
            new = current + timedelta(minutes=delta)
        self.set(new)
        self.entry.icursor(pos)
        return "break"

    def _open_calendar(self):
        try:
            current = self.get()
        except ValueError:
            current = datetime.now().replace(second=0, microsecond=0)

        def picked(d: date):
            self.set(current.replace(year=d.year, month=d.month, day=d.day))

        CalendarPopup(self.entry, current, picked)


class WorkContext:
    def __init__(self, q: "queue.Queue"):
        self._q = q
        self._cancel = threading.Event()

    def report(self, msg: str) -> None:
        self._q.put(("progress", str(msg)))

    def cancel(self) -> None:
        self._cancel.set()

    def is_cancelled(self) -> bool:
        return self._cancel.is_set()


# ===================== main window =====================


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings = Settings.load()

        self.tokens: Optional[TokenProvider] = None
        self.environments: List[EnvironmentInfo] = []
        self.env: Optional[EnvironmentInfo] = None
        self.client: Optional[PowerAutomateClient] = None

        self.current_runs: List[RunInfo] = []
        self.selected_flow_id: Optional[str] = None
        self.selected_flow_name: Optional[str] = None
        self.limit_reached = False

        self.all_flows: List[FlowInfo] = []
        self.visible_flows: List[FlowInfo] = []
        # Solution display name -> flow GUIDs (root + activated copies), and -> flow names (fallback).
        self.solution_to_flow_ids: Dict[str, set] = {}
        self.solution_to_flow_names: Dict[str, set] = {}
        self.child_flow_cache: Dict[str, list] = {}

        self.param_filters: Dict[str, tk.StringVar] = {}
        self.param_meta: Dict[str, ParamMeta] = {}
        self._grid_runs: Dict[str, RunInfo] = {}

        self.browsers: List[BrowserInfo] = []
        self.profiles: List[BrowserProfile] = []

        self._busy = False
        self._ctx: Optional[WorkContext] = None
        self._queue: "queue.Queue" = queue.Queue()

        self._build_ui()
        self._load_browsers()
        self.root.after(100, self._poll_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- UI ----------

    def _build_ui(self):
        root = self.root
        root.title(f"Flow Execution Debugger v{__version__}  -  by {__author__}")
        root.geometry("1480x900")
        root.minsize(1150, 700)

        main = ttk.Frame(root, padding=8)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(3, weight=1)

        # --- connection ---
        conn = ttk.LabelFrame(main, text="Connection", padding=6)
        conn.grid(row=0, column=0, sticky="ew")
        ttk.Label(conn, text="Tenant (optional):").grid(row=0, column=0, sticky="w")
        self.var_tenant = tk.StringVar(value=self.settings.tenant)
        ent_tenant = ttk.Entry(conn, textvariable=self.var_tenant, width=36)
        ent_tenant.grid(row=0, column=1, padx=(4, 8))
        ToolTip(ent_tenant, "Tenant id or domain (e.g. contoso.onmicrosoft.com).\n"
                            "Leave it empty to use the home tenant of your account.")
        ttk.Button(conn, text="Sign in", command=self._sign_in).grid(row=0, column=2)
        ttk.Label(conn, text="Environment:").grid(row=0, column=3, padx=(16, 4))
        self.cb_env = ttk.Combobox(conn, state="readonly", width=72)
        self.cb_env.grid(row=0, column=4)
        self.cb_env.bind("<<ComboboxSelected>>", lambda _e: self._on_env_selected())
        ttk.Button(conn, text="Sign out", command=self._sign_out).grid(row=0, column=5, padx=(8, 8))
        self.lbl_user = ttk.Label(conn, text="Not signed in", foreground="gray")
        self.lbl_user.grid(row=0, column=6, sticky="w")
        conn.columnconfigure(6, weight=1)
        ttk.Button(conn, text="About", command=self._show_about).grid(row=0, column=7, sticky="e")

        # --- options ---
        opts = ttk.Frame(main)
        opts.grid(row=1, column=0, sticky="ew", pady=6)
        ttk.Label(opts, text="Browser:").pack(side="left")
        self.cb_browser = ttk.Combobox(opts, state="readonly", width=18)
        self.cb_browser.pack(side="left", padx=(4, 14))
        self.cb_browser.bind("<<ComboboxSelected>>", lambda _e: self._on_browser_changed())
        ttk.Label(opts, text="Profile:").pack(side="left")
        self.cb_profile = ttk.Combobox(opts, state="readonly", width=32)
        self.cb_profile.pack(side="left", padx=(4, 14))
        ttk.Label(opts, text="JSON mode:").pack(side="left")
        self.cb_json_mode = ttk.Combobox(opts, state="readonly", width=22, values=JSON_MODES)
        self.cb_json_mode.pack(side="left", padx=(4, 0))
        mode = self.settings.json_mode if 0 <= self.settings.json_mode < len(JSON_MODES) else MODE_TREE
        self.cb_json_mode.current(mode)

        # --- filters ---
        top = ttk.Frame(main)
        top.grid(row=2, column=0, sticky="ew")
        top.columnconfigure(3, weight=1)

        f1 = ttk.LabelFrame(top, text="Flows", padding=6)
        f1.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        ttk.Label(f1, text="Name:").grid(row=0, column=0, sticky="w", pady=2)
        self.var_flow_name = tk.StringVar()
        self.var_flow_name.trace_add("write", lambda *_: self._apply_flow_filters())
        ttk.Entry(f1, textvariable=self.var_flow_name, width=30).grid(row=0, column=1, sticky="ew", pady=2)
        ttk.Label(f1, text="Solution:").grid(row=1, column=0, sticky="w", pady=2)
        self.cb_solution = ttk.Combobox(f1, state="readonly", width=28, values=[ALL_VALUE])
        self.cb_solution.current(0)
        self.cb_solution.grid(row=1, column=1, sticky="ew", pady=2)
        self.cb_solution.bind("<<ComboboxSelected>>", lambda _e: self._apply_flow_filters())
        ttk.Label(f1, text="Status:").grid(row=2, column=0, sticky="w", pady=2)
        self.cb_flow_state = ttk.Combobox(f1, state="readonly", width=28,
                                          values=[ALL_VALUE, "Started", "Stopped", "Suspended"])
        self.cb_flow_state.current(0)
        self.cb_flow_state.grid(row=2, column=1, sticky="ew", pady=2)
        self.cb_flow_state.bind("<<ComboboxSelected>>", lambda _e: self._apply_flow_filters())
        ttk.Button(f1, text="Load flows", command=self._load_flows).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        f2 = ttk.LabelFrame(top, text="Runs", padding=6)
        f2.grid(row=0, column=1, sticky="nsew", padx=(0, 6))
        now = datetime.now().replace(second=0, microsecond=0)
        ttk.Label(f2, text="From:").grid(row=0, column=0, sticky="w", pady=2)
        self.dt_from = DateTimeEntry(f2, now - timedelta(hours=1))
        self.dt_from.grid(row=0, column=1, sticky="w", pady=2)
        ttk.Label(f2, text="Until:").grid(row=1, column=0, sticky="w", pady=2)
        self.dt_to = DateTimeEntry(f2, now + timedelta(minutes=5))
        self.dt_to.grid(row=1, column=1, sticky="w", pady=2)
        ttk.Label(f2, text="Run status:").grid(row=2, column=0, sticky="w", pady=2)
        self.cb_status = ttk.Combobox(f2, state="readonly", width=18,
                                      values=[ALL_VALUE, "Succeeded", "Failed", "Running", "Cancelled"])
        self.cb_status.current(0)
        self.cb_status.grid(row=2, column=1, sticky="w", pady=2)
        ttk.Label(f2, text="Max. runs:").grid(row=3, column=0, sticky="w", pady=2)
        self.var_max_runs = tk.StringVar(value=str(self.settings.max_runs or 2000))
        ttk.Spinbox(f2, from_=100, to=50000, increment=500, textvariable=self.var_max_runs,
                    width=18).grid(row=3, column=1, sticky="w", pady=2)
        btns = ttk.Frame(f2)
        btns.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(btns, text="Load runs", command=self._load_runs).pack(side="left", fill="x", expand=True)
        ttk.Button(btns, text="Last hour", command=self._last_hour).pack(side="left", padx=(6, 0))

        self.lf_params = ttk.LabelFrame(top, text="Filters by body parameter:", padding=6)
        self.lf_params.grid(row=0, column=2, sticky="nsew", padx=(0, 6))
        holder = ttk.Frame(self.lf_params, relief="solid", borderwidth=1)
        holder.grid(row=0, column=0, columnspan=3, sticky="nsew")
        self.param_canvas = tk.Canvas(holder, width=400, height=118, highlightthickness=0)
        sb = ttk.Scrollbar(holder, orient="vertical", command=self.param_canvas.yview)
        self.param_canvas.configure(yscrollcommand=sb.set)
        self.param_canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.param_inner = ttk.Frame(self.param_canvas)
        self.param_canvas.create_window((0, 0), window=self.param_inner, anchor="nw")
        self.param_inner.bind("<Configure>", lambda _e: self.param_canvas.configure(
            scrollregion=self.param_canvas.bbox("all")))
        self.param_canvas.bind("<Enter>", lambda _e: self.param_canvas.bind_all("<MouseWheel>", self._on_param_wheel))
        self.param_canvas.bind("<Leave>", lambda _e: self.param_canvas.unbind_all("<MouseWheel>"))
        ttk.Label(self.lf_params, text="Condition:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.cb_param_logic = ttk.Combobox(self.lf_params, state="readonly", width=12,
                                           values=["And (all)", "Or (any)"])
        self.cb_param_logic.current(0)
        self.cb_param_logic.grid(row=1, column=1, sticky="w", pady=(6, 0))
        ttk.Button(self.lf_params, text="Filter by parameters", command=self._apply_param_filters).grid(
            row=1, column=2, sticky="e", pady=(6, 0))

        f4 = ttk.LabelFrame(top, text="How do I use the tool?", padding=6)
        f4.grid(row=0, column=3, sticky="nsew")
        guide = tk.Text(f4, width=50, height=11, wrap="word", relief="flat", background=root.cget("background"))
        gsb = ttk.Scrollbar(f4, orient="vertical", command=guide.yview)
        guide.configure(yscrollcommand=gsb.set)
        guide.insert("1.0", GUIDE_TEXT)
        guide.configure(state="disabled")
        guide.pack(side="left", fill="both", expand=True)
        gsb.pack(side="right", fill="y")

        # --- lists ---
        paned = ttk.PanedWindow(main, orient="horizontal")
        paned.grid(row=3, column=0, sticky="nsew", pady=6)

        left = ttk.Frame(paned)
        self.lst_flows = tk.Listbox(left, exportselection=False, activestyle="dotbox")
        lsb = ttk.Scrollbar(left, orient="vertical", command=self.lst_flows.yview)
        self.lst_flows.configure(yscrollcommand=lsb.set)
        self.lst_flows.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")
        paned.add(left, weight=1)

        right = ttk.Frame(paned)
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)
        bar = ttk.Frame(right)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        for text, fn in (("Open", self._open_run), ("Parameters", self._show_body_parameters),
                         ("Child flows", self._show_child_flows), ("Copy link", self._copy_link),
                         ("Download JSON", self._download_json)):
            ttk.Button(bar, text=text, command=lambda fn=fn: self._with_selected_run(fn)).pack(side="left", padx=(0, 4))
        ttk.Label(bar, text="(double-click = Open, right-click = menu)", foreground="gray").pack(side="left", padx=8)

        self.tree = ttk.Treeview(right, columns=("start", "end", "status"), show="headings", selectmode="browse")
        for col, text, width in (("start", "Start", 170), ("end", "End", 170), ("status", "Status", 110)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor="w")
        tsb = ttk.Scrollbar(right, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tsb.set)
        self.tree.grid(row=1, column=0, sticky="nsew")
        tsb.grid(row=1, column=1, sticky="ns")
        self.tree.bind("<Double-1>", lambda _e: self._with_selected_run(self._open_run))
        self.tree.bind("<Return>", lambda _e: self._with_selected_run(self._open_run))
        self.tree.bind("<Button-3>", self._on_tree_right_click)
        paned.add(right, weight=3)

        self.run_menu = tk.Menu(root, tearoff=0)
        self.run_menu.add_command(label="Open", command=lambda: self._with_selected_run(self._open_run))
        self.run_menu.add_command(label="Parameters", command=lambda: self._with_selected_run(self._show_body_parameters))
        self.run_menu.add_command(label="Child flows", command=lambda: self._with_selected_run(self._show_child_flows))
        self.run_menu.add_command(label="Copy link", command=lambda: self._with_selected_run(self._copy_link))
        self.run_menu.add_command(label="Download JSON", command=lambda: self._with_selected_run(self._download_json))

        # --- labels + status bar ---
        self.lbl_warning = tk.Label(main, text="", fg="firebrick", font=("Segoe UI", 9, "bold"),
                                    anchor="w", justify="left")
        self.lbl_warning.grid(row=4, column=0, sticky="ew")
        self.lbl_info = ttk.Label(main, text="", anchor="w", justify="left", wraplength=1400)
        self.lbl_info.grid(row=5, column=0, sticky="ew")

        status = ttk.Frame(main)
        status.grid(row=6, column=0, sticky="ew", pady=(6, 0))
        self.lbl_status = ttk.Label(status, text="Ready", anchor="w")
        self.lbl_status.pack(side="left", fill="x", expand=True)
        self.progress = ttk.Progressbar(status, mode="indeterminate", length=180)
        self.progress.pack(side="left", padx=6)
        self.btn_cancel = ttk.Button(status, text="Cancel", state="disabled", command=self._cancel_work)
        self.btn_cancel.pack(side="left")

    def _show_about(self):
        if messagebox.askyesno(
            "About Flow Execution Debugger",
            f"Flow Execution Debugger v{__version__}\n"
            f"Copyright (C) 2026 {__author__}\n"
            f"{__url__}\n\n"
            f"Licensed under the GNU General Public License ({__license__}). "
            "You may redistribute and modify it under the terms of that license, "
            "keeping this copyright notice. See the LICENSE file.\n\n"
            "This program is provided \"AS IS\", WITHOUT ANY WARRANTY. The author is not liable "
            "for any damage or data loss arising from its use. Use it at your own risk.\n\n"
            "Not affiliated with or endorsed by Microsoft. Power Automate and Dataverse are "
            "trademarks of Microsoft Corporation.\n\n"
            "Open the author's GitHub page?",
        ):
            self._open_url(__url__)

    def _on_param_wheel(self, event):
        self.param_canvas.yview_scroll(int(-event.delta / 120), "units")

    def _on_tree_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self.run_menu.tk_popup(event.x_root, event.y_root)

    def _set_info(self, text: str):
        self.lbl_info.configure(text=text)

    def _set_warning(self, text: str):
        self.lbl_warning.configure(text=text)

    # ---------- background work ----------

    def run_async(self, message: str, work: Callable[[WorkContext], Any],
                  on_done: Callable[[Any, Optional[BaseException], bool], None], cancelable: bool = False):
        if self._busy:
            messagebox.showinfo("Busy", "Another operation is in progress. Wait for it to finish or cancel it.")
            return
        self._busy = True
        ctx = WorkContext(self._queue)
        self._ctx = ctx
        self.lbl_status.configure(text=message)
        self.progress.start(12)
        self.btn_cancel.configure(state="normal" if cancelable else "disabled")
        self.root.configure(cursor="watch")
        client = self.client

        def target():
            if client:
                client.log = ctx.report
            result, error = None, None
            try:
                result = work(ctx)
            except Exception as ex:  # reported to the UI thread
                error = ex
            finally:
                if client:
                    client.log = None
            self._queue.put(("done", on_done, result, error, ctx.is_cancelled()))

        threading.Thread(target=target, daemon=True).start()

    def _cancel_work(self):
        if self._ctx:
            self._ctx.cancel()
            self.lbl_status.configure(text="Cancelling...")
            self.btn_cancel.configure(state="disabled")

    def _poll_queue(self):
        try:
            while True:
                msg = self._queue.get_nowait()
                if msg[0] == "progress":
                    self.lbl_status.configure(text=msg[1])
                elif msg[0] == "done":
                    _, on_done, result, error, cancelled = msg
                    self._busy = False
                    self._ctx = None
                    self.progress.stop()
                    self.btn_cancel.configure(state="disabled")
                    self.lbl_status.configure(text="Ready")
                    self.root.configure(cursor="")
                    try:
                        on_done(result, error, cancelled)
                    except Exception as ex:
                        messagebox.showerror("Error", str(ex))
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    # ---------- browsers ----------

    def _load_browsers(self):
        try:
            self.browsers = detect_browsers()
        except Exception:
            self.browsers = []
        names = [b.name for b in self.browsers]
        self.cb_browser["values"] = ["(System default)"] + names
        idx = names.index(self.settings.browser) + 1 if self.settings.browser in names else 0
        self.cb_browser.current(idx)
        self._on_browser_changed(restore_profile=True)

    def _selected_browser(self) -> Optional[BrowserInfo]:
        i = self.cb_browser.current()
        return self.browsers[i - 1] if 0 < i <= len(self.browsers) else None

    def _selected_profile(self) -> Optional[BrowserProfile]:
        i = self.cb_profile.current()
        return self.profiles[i] if 0 <= i < len(self.profiles) else None

    def _on_browser_changed(self, restore_profile: bool = False):
        browser = self._selected_browser()
        if browser is None:
            self.profiles = []
            self.cb_profile.configure(state="readonly", values=["(No profile)"])
            self.cb_profile.current(0)
            self.cb_profile.configure(state="disabled")
            return
        self.cb_profile.configure(state="readonly")
        self.profiles = get_profiles(browser)
        if not self.profiles:
            self.cb_profile["values"] = ["(Default)"]
            self.cb_profile.current(0)
            return
        self.cb_profile["values"] = [p.display_name for p in self.profiles]
        idx = 0
        if restore_profile:
            dirs = [p.directory_name for p in self.profiles]
            if self.settings.profile in dirs:
                idx = dirs.index(self.settings.profile)
        self.cb_profile.current(idx)

    # ---------- connection ----------

    def _sign_in(self):
        if self._busy:
            messagebox.showinfo("Busy", "Wait for the current operation to finish.")
            return
        tenant = self.var_tenant.get().strip()
        client_id = self.settings.client_id or DEFAULT_CLIENT_ID
        if self.tokens is None or self.tokens.tenant != (tenant or "organizations") or self.tokens.client_id != client_id:
            self.tokens = TokenProvider(tenant, client_id)
        tokens = self.tokens

        def work(ctx: WorkContext):
            ctx.report("Signing in (a browser window may open)...")
            tokens.flow_token()
            ctx.report("Loading environments...")
            return list_environments(tokens.flow_token, ctx.report)

        def done(envs, err, _cancelled):
            if err:
                messagebox.showerror("Sign-in error", str(err))
                return
            self.settings.tenant = tenant
            self.environments = envs
            self.cb_env["values"] = [str(e) for e in envs]
            user = tokens.username or "(unknown user)"
            note = "" if tokens.cache_persisted else "  (session not persisted: install msal-extensions)"
            self.lbl_user.configure(text=f"Signed in as {user}{note}", foreground="")
            if not envs:
                self._set_info("No environments found for this account/tenant.")
                return
            ids = [e.id.lower() for e in envs]
            last = (self.settings.last_environment_id or "").lower()
            self.cb_env.current(ids.index(last) if last in ids else 0)
            self._on_env_selected(force=True)

        self.run_async("Signing in...", work, done)

    def _sign_out(self):
        if self._busy:
            messagebox.showinfo("Busy", "Wait for the current operation to finish.")
            return
        if self.tokens:
            self.tokens.sign_out()
        self.tokens = None
        self.env = None
        self.client = None
        self.environments = []
        self.cb_env.set("")
        self.cb_env["values"] = []
        self._reset_environment_state()
        self.lbl_user.configure(text="Not signed in", foreground="gray")
        self._set_info("Signed out.")

    def _on_env_selected(self, force: bool = False):
        i = self.cb_env.current()
        if i < 0 or i >= len(self.environments) or self.tokens is None:
            return
        env = self.environments[i]
        if not force and self.env and self.env.id == env.id:
            return
        if self._busy:
            messagebox.showinfo("Busy", "Wait for the current operation to finish before changing the environment.")
            if self.env in self.environments:
                self.cb_env.current(self.environments.index(self.env))
            return
        self.env = env
        self.client = PowerAutomateClient(self.tokens.flow_token, env.id)
        self._reset_environment_state()
        self.settings.last_environment_id = env.id
        self.settings.last_used_organization_webapp_url = env.instance_url or ""
        self._load_solutions_async()

    def _reset_environment_state(self):
        self.all_flows = []
        self.visible_flows = []
        self.lst_flows.delete(0, "end")
        self.solution_to_flow_ids = {}
        self.solution_to_flow_names = {}
        self._fill_solutions_combo()
        self._fill_states_combo()
        self.current_runs = []
        self._bind_grid([], 0)
        self._clear_param_filters()
        self.lf_params.configure(text="Filters by body parameter:")
        self.param_meta = {}
        self.child_flow_cache.clear()
        self.selected_flow_id = None
        self.selected_flow_name = None
        self._set_warning("")
        self._set_info("")

    def _validate_connection(self) -> bool:
        if self.tokens is None or self.env is None or self.client is None:
            messagebox.showwarning("Not connected", "Press \"Sign in\" and select an environment first.")
            return False
        return True

    def _load_solutions_async(self):
        env, tokens = self.env, self.tokens
        if env is None or tokens is None:
            return
        if not env.instance_url:
            self._set_info("This environment has no Dataverse database: the Solution filter is unavailable. "
                           "Press \"Load flows\".")
            return

        def work(ctx: WorkContext):
            return load_solution_maps(env.instance_url, lambda: tokens.dataverse_token(env.instance_url), ctx.report)

        def done(res, err, _c):
            if self.env is not env:
                return
            if err:
                self._set_info("The solutions could not be loaded: " + str(err).splitlines()[0])
                return
            self.solution_to_flow_ids, self.solution_to_flow_names, _ = res
            self._fill_solutions_combo()
            self._set_info(f"{len(self.solution_to_flow_ids)} solutions loaded. Press \"Load flows\".")

        self.run_async("Loading solutions...", work, done)

    # ---------- flows ----------

    def _fill_solutions_combo(self):
        current = self.cb_solution.get()
        values = [ALL_VALUE] + sorted(self.solution_to_flow_ids.keys(), key=str.casefold)
        self.cb_solution["values"] = values
        self.cb_solution.current(values.index(current) if current in values else 0)

    def _fill_states_combo(self):
        """Uses the states actually returned by the API (Started, Stopped, Suspended, ...)."""
        current = self.cb_flow_state.get()
        states: Dict[str, str] = {}
        for f in self.all_flows:
            if f.state:
                states.setdefault(f.state.lower(), f.state)
        values = [ALL_VALUE] + sorted(states.values(), key=str.casefold)
        self.cb_flow_state["values"] = values
        self.cb_flow_state.current(values.index(current) if current in values else 0)

    def _load_flows(self):
        if not self._validate_connection():
            return
        client, env, tokens = self.client, self.env, self.tokens

        def work(ctx: WorkContext):
            flows = client.list_flows()
            scope = client.last_flow_list_scope
            ctx.report("Mapping flows to solutions...")
            sol_maps, sol_error = None, None
            if env.instance_url:
                try:
                    sol_maps = load_solution_maps(env.instance_url,
                                                  lambda: tokens.dataverse_token(env.instance_url), ctx.report)
                except Exception as ex:
                    sol_error = str(ex).splitlines()[0]
            else:
                sol_error = "the environment has no Dataverse database"

            added = 0
            if sol_maps is not None:
                # Solution flows the API did not return (e.g. not owned/shared and no admin rights).
                known_ids = {normalize_guid(f.id) for f in flows}
                known_names = {f.name.lower() for f in flows if f.name}
                for fid, (name, is_on) in sol_maps[2].items():
                    if fid in known_ids or name.lower() in known_names:
                        continue
                    flows.append(FlowInfo(id=fid, name=name, state="Started" if is_on else "Stopped",
                                          scope="dataverse"))
                    known_names.add(name.lower())
                    added += 1
                if added:
                    ctx.report(f"{added} solution flows added from Dataverse.")
                    flows.sort(key=lambda f: (f.name or "").casefold())
            return flows, sol_maps, sol_error, scope, added

        def done(res, err, _c):
            if err:
                messagebox.showerror("Error", "Error loading flows: " + str(err))
                return
            flows, sol_maps, sol_error, scope, added = res
            self.all_flows = flows
            if sol_maps is not None:
                self.solution_to_flow_ids, self.solution_to_flow_names, _ = sol_maps
            self._fill_solutions_combo()
            self._fill_states_combo()
            self._apply_flow_filters()

            is_admin = (scope or "").lower() == "admin"
            self._set_warning("" if is_admin else
                              "⚠ PARTIAL LIST: no administrator permissions on the environment. "
                              "Only your flows and the ones shared with you are shown.")
            suffix = " (whole environment)" if is_admin else " (your flows + shared)"
            if added:
                suffix += f", {added} only from Dataverse"
            if sol_error:
                self._set_info(f"{len(flows)} flows loaded{suffix}. (Solutions not loaded: {sol_error})")
            else:
                self._set_info(f"{len(flows)} flows loaded{suffix}. {len(self.solution_to_flow_ids)} solutions.")

        self.run_async("Getting token and loading flows...", work, done)

    def _apply_flow_filters(self):
        if not hasattr(self, "lst_flows"):
            return
        query = list(self.all_flows)

        name = self.var_flow_name.get().strip().lower()
        if name:
            query = [f for f in query if name in (f.name or "").lower()]

        state = self.cb_flow_state.get()
        if state and state != ALL_VALUE:
            query = [f for f in query if (f.state or "").lower() == state.lower()]

        used_name_fallback = False
        sol = self.cb_solution.get()
        if sol and sol != ALL_VALUE:
            flow_ids = self.solution_to_flow_ids.get(sol) or set()
            flow_names = self.solution_to_flow_names.get(sol) or set()
            # Some flows have a different id in the API than in Dataverse: match by name too.
            matched = []
            for f in query:
                if f.id and normalize_guid(f.id) in flow_ids:
                    matched.append(f)
                elif f.name and f.name.lower() in flow_names:
                    matched.append(f)
                    used_name_fallback = True
            query = matched

        self.visible_flows = query
        self.lst_flows.delete(0, "end")
        for f in query:
            self.lst_flows.insert("end", str(f))

        total = len(self.all_flows)
        self._set_info(
            f"{len(query)} flows (out of {total} loaded). Some matched by name."
            if used_name_fallback else f"{len(query)} flows (out of {total} loaded)."
        )

    def _selected_flow(self) -> Optional[FlowInfo]:
        sel = self.lst_flows.curselection()
        return self.visible_flows[sel[0]] if sel and sel[0] < len(self.visible_flows) else None

    # ---------- runs ----------

    def _read_range(self, show_errors: bool = True):
        try:
            return self.dt_from.get(), self.dt_to.get()
        except ValueError:
            if show_errors:
                messagebox.showwarning("Invalid date", "Invalid date/time. Use the format dd/mm/yyyy hh:mm.")
            return None

    def _read_max_runs(self) -> Optional[int]:
        raw = self.var_max_runs.get().replace(",", "").replace(".", "").strip()
        try:
            value = int(raw)
        except ValueError:
            messagebox.showwarning("Invalid value", "Max. runs must be a number between 100 and 50,000.")
            return None
        value = max(100, min(50000, value))
        self.var_max_runs.set(str(value))
        return value

    def _last_hour(self):
        now = datetime.now().replace(second=0, microsecond=0)
        self.dt_from.set(now - timedelta(hours=1))
        self.dt_to.set(now + timedelta(minutes=5))
        self._load_runs()

    def _load_runs(self):
        if not self._validate_connection():
            return
        flow = self._selected_flow()
        if flow is None:
            messagebox.showinfo("No flow selected", "Select a flow from the list first.")
            return
        rng = self._read_range()
        if rng is None:
            return
        from_dt, to_dt = rng
        if to_dt <= from_dt:
            messagebox.showwarning("Invalid range", "Invalid range: \"Until\" must be later than \"From\".")
            return
        max_runs = self._read_max_runs()
        if max_runs is None:
            return

        self.selected_flow_id = flow.id
        self.selected_flow_name = flow.name
        # New load: the child flow cache is no longer valid.
        self.child_flow_cache.clear()

        status_sel = self.cb_status.get()
        span_days = (to_dt - from_dt).total_seconds() / 86400
        if span_days > 2:
            if not messagebox.askyesno(
                "Wide range",
                f"You have selected a range of {span_days:.1f} days.\n\n"
                "On flows with many runs this can take a long time or hit the limit.\n"
                "It is recommended to use windows of minutes or hours.\n\n"
                "Continue anyway?",
                icon="warning",
            ):
                return

        self._set_warning("")
        self.limit_reached = False
        client, flow_id = self.client, flow.id

        def work(ctx: WorkContext):
            runs = client.list_runs(flow_id, from_dt, to_dt, max_runs, status_sel, ctx.is_cancelled)
            limit = len(runs) >= max_runs
            if ctx.is_cancelled():
                return None

            ctx.report("Detecting trigger parameters...")
            keys: Dict[str, str] = {}
            for run in runs[:10]:
                if ctx.is_cancelled():
                    break
                try:
                    for k in client.get_trigger_params(run):
                        keys.setdefault(k.lower(), k)
                except Exception:
                    pass
            try:
                meta = client.get_trigger_param_meta(flow_id)
            except Exception:
                meta = {}
            return runs, sorted(keys.values(), key=str.casefold), meta, limit

        def done(res, err, cancelled):
            if cancelled or (res is None and err is None):
                self._set_info("Load cancelled.")
                return
            if err:
                self._set_info("Error loading runs.")
                messagebox.showerror("Error", "Error: " + str(err))
                return
            self.current_runs, keys, self.param_meta, self.limit_reached = res
            self._apply_filters_and_bind(self.current_runs)
            self._build_param_filters(keys)
            self._set_warning(
                f"⚠ LIMIT REACHED: only the first {len(self.current_runs)} runs are shown. "
                "The result is NOT complete. Narrow the date/time range." if self.limit_reached else ""
            )

        self.run_async("Loading runs...", work, done, cancelable=True)

    def _run_passes_filters(self, r: RunInfo, rng, status: str) -> bool:
        if rng and r.start is not None:
            local = r.start.replace(tzinfo=None)
            if not (rng[0] <= local < rng[1]):
                return False
        if status and status != ALL_VALUE and (r.status or "").lower() != status.lower():
            return False
        return True

    def _apply_filters_and_bind(self, source: List[RunInfo]):
        rng = self._read_range(show_errors=False)
        status = self.cb_status.get()
        self._bind_grid([r for r in source if self._run_passes_filters(r, rng, status)], len(source))

    def _bind_grid(self, runs: List[RunInfo], total: int):
        self.tree.delete(*self.tree.get_children())
        self._grid_runs = {}
        for i, r in enumerate(runs):
            iid = str(i)
            self._grid_runs[iid] = r
            self.tree.insert("", "end", iid=iid, values=(
                r.start.strftime(GRID_DATETIME_FORMAT) if r.start else "",
                r.end.strftime(GRID_DATETIME_FORMAT) if r.end else "",
                r.status or "",
            ))
        self._set_info(f"{len(runs)} runs (out of {total} downloaded).")

    # ---------- parameter filters ----------

    def _clear_param_filters(self):
        for w in self.param_inner.winfo_children():
            w.destroy()
        self.param_filters.clear()

    def _build_param_filters(self, keys: List[str]):
        self._clear_param_filters()
        for row, key in enumerate(keys):
            meta = self.param_meta.get(key.lower())
            label = meta.title if meta and meta.title else key
            desc = meta.description if meta else ""

            lbl = ttk.Label(self.param_inner, text=_ellipsis(f"{label}  ({key})", 30), width=30)
            var = tk.StringVar()
            ent = ttk.Entry(self.param_inner, textvariable=var, width=26)
            ent.bind("<Return>", lambda _e: self._apply_param_filters())
            lbl.grid(row=row, column=0, sticky="w", padx=4, pady=2)
            ent.grid(row=row, column=1, sticky="w", padx=4, pady=2)

            tip = f"{label}\nField: {key}" + (f"\n{desc}" if desc else "")
            ToolTip(lbl, tip)
            ToolTip(ent, tip)
            self.param_filters[key] = var

        if not keys:
            ttk.Label(self.param_inner, text="This flow has no parameters in the body.").grid(
                row=0, column=0, sticky="w", padx=4, pady=4)

        self.param_canvas.yview_moveto(0)
        self.lf_params.configure(
            text=f"Filters by body parameter ({len(keys)}):" if keys else "Filters by body parameter:")

    def _apply_param_filters(self):
        if not self._validate_connection():
            return
        active = {k: v.get().strip() for k, v in self.param_filters.items() if v.get().strip()}
        if not active:
            messagebox.showinfo("No filters", "Type a value in at least one parameter to filter.")
            return

        use_and = self.cb_param_logic.current() == 0
        rng = self._read_range()
        if rng is None:
            return
        status = self.cb_status.get()
        candidates = [r for r in self.current_runs if self._run_passes_filters(r, rng, status)]

        if not candidates:
            messagebox.showinfo("No runs", "There are no candidate runs in the current range.")
            return

        total = len(candidates)
        if total > PARAM_FILTER_WARNING_THRESHOLD:
            if not messagebox.askyesno(
                "Too many runs",
                f"You are about to analyze {total:,} runs.\n\n"
                f"One file is downloaded per run, so the operation may take "
                f"approximately {_fmt_span(total * 0.25)} and cause throttling.\n\n"
                f"Recommended: narrow the range to fewer than {PARAM_FILTER_WARNING_THRESHOLD} runs.\n\n"
                "Continue anyway? (you will be able to cancel during the process)",
                icon="warning",
            ):
                return

        client = self.client
        needles = [(k.lower(), v.lower()) for k, v in active.items()]

        def work(ctx: WorkContext):
            matches: List[RunInfo] = []
            start = time.monotonic()
            i = 0
            cancelled = False
            for run in candidates:
                if ctx.is_cancelled():
                    cancelled = True
                    break
                i += 1
                if i % 5 == 0 or i == 1:
                    elapsed = time.monotonic() - start
                    remaining = elapsed / i * (total - i) if i > 1 else 0
                    ctx.report(f"Analyzing {i:,}/{total:,} - found: {len(matches)} "
                               f"- approx. remaining: {_fmt_span(remaining)}")
                try:
                    params = {k.lower(): v for k, v in client.get_trigger_params(run).items()}
                except Exception:
                    continue

                def ok(item):
                    val = params.get(item[0])
                    return val is not None and item[1] in val.lower()

                if (all(map(ok, needles)) if use_and else any(map(ok, needles))):
                    matches.append(run)
            return matches, i, cancelled

        def done(res, err, _c):
            if err:
                messagebox.showerror("Error", "Error: " + str(err))
                return
            if res is None:
                self._set_info("Filtering cancelled.")
                return
            matches, analyzed, cancelled = res
            self._bind_grid(matches, analyzed)
            logic = "And" if use_and else "Or"
            self._set_info(f"{len(matches)} runs match the filters ({logic}) out of {analyzed:,} analyzed.")
            if cancelled:
                self._set_warning(f"⚠ CANCELLED: only {analyzed:,} out of {total:,} were analyzed. Partial result.")

        self.run_async(f"Filtering {total:,} runs by parameters...", work, done, cancelable=True)

    # ---------- run actions ----------

    def _with_selected_run(self, fn: Callable[[RunInfo], None]):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("No run selected", "Select a run in the grid first.")
            return
        run = self._grid_runs.get(sel[0])
        if run:
            fn(run)

    def _run_url(self, run: RunInfo) -> Optional[str]:
        if not self.selected_flow_id or self.client is None:
            messagebox.showinfo("No flow", "The flow has not been identified.")
            return None
        return self.client.build_run_url(self.selected_flow_id, run.name)

    def _open_run(self, run: RunInfo):
        url = self._run_url(run)
        if url:
            self._open_url(url)

    def _open_url(self, url: str, parent: Optional[tk.Misc] = None):
        if not url:
            return
        try:
            open_url(url, self._selected_browser(), self._selected_profile())
        except Exception as ex:
            messagebox.showerror("Error", "The browser could not be opened: " + str(ex), parent=parent)

    def _copy_link(self, run: RunInfo):
        url = self._run_url(run)
        if url:
            self._copy_to_clipboard(url)

    def _copy_to_clipboard(self, text: str, parent: Optional[tk.Misc] = None):
        if not text:
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()
            self._set_info("Copied to clipboard: " + text)
        except Exception as ex:
            messagebox.showerror("Error", "The text could not be copied: " + str(ex), parent=parent)

    # ---------- child flows ----------

    def _show_child_flows(self, run: RunInfo):
        if not self._validate_connection():
            return
        if not self.selected_flow_id:
            messagebox.showinfo("No flow", "The flow has not been identified.")
            return

        cached = self.child_flow_cache.get(run.name)
        if cached is not None:
            self._show_child_flows_dialog(run, cached)
            return

        client, flow_id, run_id = self.client, self.selected_flow_id, run.name
        known_names = {f.id.lower(): f.name for f in self.all_flows if f.id}

        def work(ctx: WorkContext):
            full = client.download_full_run(flow_id, run_id)
            ctx.report("Detecting calls to child flows...")
            calls = transforms.build_child_flow_calls(full)

            name_cache: Dict[str, str] = {}
            for c in calls:
                cf, cr = c.get("childFlowId"), c.get("childRunId")
                if cf and cr:
                    c["url"] = client.build_run_url(cf, cr)
                if not cf:
                    continue
                if cf.lower() not in name_cache:
                    # Look first in the already loaded list, to avoid spending a call.
                    disp = known_names.get(cf.lower()) or client.try_get_flow_display_name(cf)
                    name_cache[cf.lower()] = disp or ""
                if name_cache[cf.lower()]:
                    c["childFlowName"] = name_cache[cf.lower()]
            return calls

        def done(calls, err, _c):
            if err:
                messagebox.showerror("Error", "Error analyzing the run: " + str(err))
                return
            self.child_flow_cache[run_id] = calls
            self._show_child_flows_dialog(run, calls)

        self.run_async("Analyzing calls to child flows...", work, done)

    def _show_child_flows_dialog(self, run: RunInfo, calls: List[Dict[str, Any]]):
        if not calls:
            messagebox.showinfo(
                "No child flows",
                "This run does not contain calls to other flows.\n\n"
                "The \"Run a child flow\" actions are detected and, as a best effort, "
                "the HTTP invocations to the Power Automate API.",
            )
            return

        rows = [{
            "flow": c.get("childFlowName") or c.get("childFlowId") or "(unknown)",
            "action": c.get("action") or "",
            "iteration": c.get("path") or "",
            "status": c.get("status") or "",
            "kind": c.get("kind") or "",
            "url": c.get("url") or "",
        } for c in calls]

        win = tk.Toplevel(self.root)
        win.title(f"Calls to child flows ({len(rows)}) - run {run.name}")
        win.geometry("1200x550")
        win.transient(self.root)

        bar = ttk.Frame(win, padding=(6, 6, 6, 0))
        bar.pack(fill="x")
        frame = ttk.Frame(win, padding=6)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        cols = (("flow", "Child flow", 200), ("action", "Action", 180), ("iteration", "Iteration", 250),
                ("status", "Status", 90), ("kind", "Kind", 80))
        tree = ttk.Treeview(frame, columns=[c[0] for c in cols], show="headings", selectmode="browse")
        font = tkfont.nametofont("TkDefaultFont")
        for key, text, min_width in cols:
            content = max([font.measure(text)] + [font.measure(r[key]) for r in rows])
            tree.heading(key, text=text, anchor="w")
            # stretch=False lets the columns exceed the window so the horizontal scrollbar can reach long paths.
            tree.column(key, width=max(min_width, content + 24), minwidth=60, stretch=False, anchor="w")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree.bind("<Shift-MouseWheel>", lambda e: tree.xview_scroll(int(-e.delta / 120) * 3, "units"))
        for i, r in enumerate(rows):
            tree.insert("", "end", iid=str(i), values=(r["flow"], r["action"], r["iteration"], r["status"], r["kind"]))

        detail = ttk.Frame(win, padding=(6, 0, 6, 0))
        detail.pack(fill="x")
        ttk.Label(detail, text="Iteration (full path):").pack(side="left")
        var_path = tk.StringVar()
        ttk.Entry(detail, textvariable=var_path, state="readonly").pack(side="left", fill="x", expand=True, padx=(6, 0))

        def on_select(_e=None):
            sel = tree.selection()
            var_path.set(rows[int(sel[0])]["iteration"] if sel else "")

        tree.bind("<<TreeviewSelect>>", on_select)

        def selected_url() -> Optional[str]:
            sel = tree.selection()
            if not sel:
                messagebox.showinfo("No call selected", "Select a call first.", parent=win)
                return None
            url = rows[int(sel[0])]["url"]
            if not url:
                messagebox.showinfo(
                    "No link available",
                    "This call does not expose the child run identifier, so a direct link cannot be built.\n\n"
                    "This usually happens when the flow is invoked over HTTP instead of with "
                    "the \"Run a child flow\" action.",
                    parent=win,
                )
                return None
            return url

        def open_child():
            url = selected_url()
            if url:
                self._open_url(url, parent=win)

        def copy_child():
            url = selected_url()
            if url:
                self._copy_to_clipboard(url, parent=win)

        ttk.Button(bar, text="Open", command=open_child).pack(side="left")
        ttk.Button(bar, text="Copy link", command=copy_child).pack(side="left", padx=4)
        tree.bind("<Double-1>", lambda _e: open_child())

        ttk.Label(
            win, padding=6, wraplength=1160, justify="left",
            text="The \"Iteration\" column indicates in which loop pass the call took place "
                 "(scroll horizontally or select a row to see the full path). "
                 "Kind \"Http\" = invocation by URL: it may not have a link to the child run.",
        ).pack(fill="x", side="bottom")

        win.grab_set()
        win.focus_set()

    # ---------- JSON download ----------

    def _download_json(self, run: RunInfo):
        if not self._validate_connection():
            return
        if not self.selected_flow_id:
            messagebox.showinfo("No flow", "The flow has not been identified.")
            return

        mode = self.cb_json_mode.current()
        mode_name = {MODE_CLEAN: "clean", MODE_TREE: "tree", MODE_RAW: "raw"}.get(mode, "full")

        safe_flow = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (self.selected_flow_name or "flow"))[:40]
        safe_start = (run.start_time or "").replace(":", "-").replace("+", "_").replace("/", "").replace(" ", "_")
        suffix = "" if mode == MODE_FULL else "__" + mode_name
        suggested = f"{safe_flow}__{safe_start}__{run.name}{suffix}.json"
        title = {MODE_CLEAN: "Save clean JSON (loops only)",
                 MODE_TREE: "Save tree JSON (nested loops)",
                 MODE_RAW: "Save raw run JSON"}.get(mode, "Save full run JSON")

        path = filedialog.asksaveasfilename(
            title=title, initialfile=suggested, defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return

        client, flow_id, run_id = self.client, self.selected_flow_id, run.name

        def work(ctx: WorkContext):
            full = client.download_full_run(flow_id, run_id)

            if mode == MODE_RAW:
                ctx.report("Writing file...")
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(full, fh, indent=2, ensure_ascii=False)
                raw_actions = [a for a in full.get("actions") or [] if isinstance(a, dict)]
                raw_reps = sum(len(a.get("_repetitions") or []) for a in raw_actions)
                return len(raw_actions), raw_reps, 0, 0, 0, 0

            ctx.report("Numbering Apply to each iterations...")
            iteration_count = transforms.annotate_foreach_iterations(full)

            output = full
            loop_count = 0
            tree_iteration_count = 0
            if mode == MODE_CLEAN:
                ctx.report("Generating clean version (loops only)...")
                output = transforms.build_clean_run(full)
                loop_count = len(output.get("loops") or [])
            elif mode == MODE_TREE:
                ctx.report("Building the nested loops tree...")
                output = transforms.build_tree_run(full)
                loop_count = len(output.get("loops") or [])
                tree_iteration_count = transforms.count_tree_iterations(output)

            # Calls to child flows are added in all three modes.
            ctx.report("Detecting calls to child flows...")
            child_calls = transforms.build_child_flow_calls(full)
            for c in child_calls:
                if c.get("childFlowId") and c.get("childRunId"):
                    c["url"] = client.build_run_url(c["childFlowId"], c["childRunId"])
            if child_calls:
                output["childFlows"] = child_calls

            ctx.report("Writing file...")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(output, fh, indent=2, ensure_ascii=False)

            actions = [a for a in full.get("actions") or [] if isinstance(a, dict)]
            repetition_count = sum(len(a.get("_repetitions") or []) for a in actions)
            return (len(actions), repetition_count, iteration_count, loop_count,
                    tree_iteration_count, len(child_calls))

        def done(res, err, _c):
            if err:
                messagebox.showerror("Error", "Error downloading the JSON: " + str(err))
                return
            actions, reps, iters, loops, tree_iters, children = res
            if mode == MODE_RAW:
                extra = f"Actions: {actions}\nRepetitions (API): {reps}"
            elif mode == MODE_TREE:
                extra = f"Root loops: {loops}\nTotal iterations (all levels): {tree_iters}"
            elif mode == MODE_CLEAN:
                extra = f"Loops: {loops}\nNumbered foreach iterations: {iters}"
            else:
                extra = f"Actions: {actions}\nRepetitions (API): {reps}\nNumbered foreach iterations: {iters}"
            if children > 0:
                extra += f"\nCalls to child flows: {children} ('childFlows' block)"

            self._set_info(f"{mode_name} JSON saved to {path}  |  " + extra.replace("\n", "  "))

            help_text = (
                "\n\nNavigation: loops[] > iterations[] (use 'portalIndex' to locate it in the portal).\n"
                "Each iteration includes its input 'item', its 'actions' and, if there is an inner loop,\n"
                "another nested 'loops' with its own iterations and items. The 'path' field shows\n"
                "the full route, e.g. \"Apply_to_each[2] > Apply_to_each_2[3]\"."
                if mode == MODE_TREE else ""
            )
            msg = (f"{mode_name} JSON saved successfully:\n{path}\n\n{extra}{help_text}"
                   "\n\nDo you want to open the run in the browser?")
            if messagebox.askyesno("Download completed", msg):
                self._open_url(client.build_run_url(flow_id, run_id))

        self.run_async("Downloading the run JSON...", work, done)

    # ---------- body parameters ----------

    def _show_body_parameters(self, run: RunInfo):
        if not self._validate_connection():
            return
        if not run.trigger_outputs_link and run.trigger_inputs is None:
            messagebox.showinfo("No parameters", "This run has no trigger parameters available.")
            return
        client = self.client

        def work(_ctx: WorkContext):
            return client.get_trigger_params(run)

        def done(params, err, _c):
            if err:
                messagebox.showerror("Error", "Error: " + str(err))
                return
            if not params:
                messagebox.showinfo("No parameters", "This run has no parameters in the body.")
                return

            win = tk.Toplevel(self.root)
            win.title("Body parameters")
            win.geometry("700x450")
            win.transient(self.root)
            frame = ttk.Frame(win, padding=6)
            frame.pack(fill="both", expand=True)

            meta = self.param_meta
            tree = ttk.Treeview(frame, columns=("param", "value"), show="headings", selectmode="browse")
            tree.heading("param", text="Parameter")
            tree.heading("value", text="Value")
            tree.column("param", width=260, anchor="w")
            tree.column("value", width=400, anchor="w")
            sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            tree.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")

            items = list(params.items())
            for i, (k, v) in enumerate(items):
                m = meta.get(k.lower())
                label = f"{m.title}  ({k})" if m and m.title and m.title != k else k
                tree.insert("", "end", iid=str(i), values=(label, v))

            def copy_value(_e=None):
                sel = tree.selection()
                if sel:
                    self._copy_to_clipboard(items[int(sel[0])][1], parent=win)

            tree.bind("<Control-c>", copy_value)
            tree.bind("<Double-1>", copy_value)
            ttk.Label(win, text="Double-click or Ctrl+C copies the value.", foreground="gray",
                      padding=(6, 0, 6, 6)).pack(fill="x")
            win.grab_set()
            win.focus_set()

        self.run_async("Downloading body parameters...", work, done)

    # ---------- shutdown ----------

    def _on_close(self):
        browser = self._selected_browser()
        profile = self._selected_profile()
        self.settings.tenant = self.var_tenant.get().strip()
        self.settings.browser = browser.name if browser else ""
        self.settings.profile = profile.directory_name if profile else ""
        self.settings.json_mode = max(0, self.cb_json_mode.current())
        try:
            self.settings.max_runs = int(self.var_max_runs.get().replace(",", "").replace(".", ""))
        except ValueError:
            pass
        self.settings.save()
        self.root.destroy()


def main():
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()
