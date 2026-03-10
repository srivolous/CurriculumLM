"""
NBA CIS Generator — CO-PO Mapping
Dear PyGui UI  (macOS-safe: all DPG calls on main thread via a queue)

Interaction model:
  1. Table generates automatically.
  2. User clicks a cell → that row + column are selected/highlighted.
  3. User types an edit instruction in the box below and presses Enter.
  4. AI receives the FULL ROW for context but rewrites ONLY the selected column.
  5. Table re-renders with the update in place.
  6. Type "yes" + Enter to export LaTeX.
"""

import json
import ollama
import pandas as pd
import subprocess
import threading
import queue
from datetime import datetime

import dearpygui.dearpygui as dpg

PO_FILE       = 'Environment/PO.json'
SYLLABUS_FILE = 'Environment/syllabus.txt'
MODEL_NAME    = 'mannix/llama3.1-8b-abliterated:latest'
TEX_OUTPUT    = 'CO_PO_Mapping.tex'

_msg_q: queue.Queue = queue.Queue()

_df:             pd.DataFrame | None = None
_co_list:        list = []
_generation_ok:  bool = False
_edit_busy:      bool = False

# selected cell state
_sel_row:  int | None = None   # 0-based row index in _df
_sel_col:  str | None = None   # column name

# ── palette ──────────────────────────────────────────────────
C_BG       = (13,  17,  23,  255)
C_PANEL    = (22,  27,  34,  255)
C_BORDER   = (48,  54,  61,  255)
C_ACCENT   = (88,  166, 255, 255)
C_GREEN    = (63,  185, 80,  255)
C_AMBER    = (240, 136, 62,  255)
C_RED      = (248, 81,  73,  255)
C_TEXT     = (230, 237, 243, 255)
C_MUTED    = (139, 148, 158, 255)
C_ROW_EVEN = (28,  33,  40,  255)
C_ROW_ODD  = (16,  21,  28,  255)
C_HDR_BG   = (22,  33,  50,  255)
C_SEL_ROW  = (31,  60,  100, 180)
C_SEL_CELL = (31, 111,  235, 220)

COLS  = ["CO #", "Course Outcome", "PO Mapping", "Strength", "PIs", "WK", "Justification"]
COL_W = [55, 220, 105, 75, 95, 75, 480]


# ════════════════════════════════════════════════════════════
#  THREAD-SAFE HELPERS
# ════════════════════════════════════════════════════════════

def _push_log(msg, colour=None):
    _msg_q.put(("log", (datetime.now().strftime("%H:%M:%S"), msg, colour or C_TEXT)))

def _set_status(text, colour=None):
    _msg_q.put(("status", (text, colour or C_AMBER)))

def _signal_render():
    _msg_q.put(("render", None))

def _set_hint(text, colour=None):
    _msg_q.put(("hint", (text, colour or C_MUTED)))

def _set_input_enabled(val):
    _msg_q.put(("input_enable", val))


# ════════════════════════════════════════════════════════════
#  QUEUE DRAIN  (called every frame on main thread)
# ════════════════════════════════════════════════════════════

_log_lines = []

def _drain_queue():
    try:
        while True:
            action, payload = _msg_q.get_nowait()
            if action == "log":
                ts, msg, col = payload
                _log_lines.append((ts, msg, col))
                txt = "\n".join(f"[{t}]  {m}" for t, m, _ in _log_lines[-120:])
                if dpg.does_item_exist("log_text"):
                    dpg.set_value("log_text", txt)
                    dpg.set_y_scroll("log_scroll", dpg.get_y_scroll_max("log_scroll") + 99999)
            elif action == "status":
                text, col = payload
                if dpg.does_item_exist("status_text"):
                    dpg.set_value("status_text", f"●  {text}")
                    dpg.configure_item("status_text", color=list(col))
            elif action == "render":
                if _df is not None:
                    _render_table(_df)
            elif action == "hint":
                text, col = payload
                if dpg.does_item_exist("edit_hint"):
                    dpg.set_value("edit_hint", text)
                    dpg.configure_item("edit_hint", color=list(col))
            elif action == "input_enable":
                if dpg.does_item_exist("edit_input"):
                    dpg.configure_item("edit_input", enabled=payload)
            elif action == "sel_label":
                text, col = payload
                if dpg.does_item_exist("sel_label"):
                    dpg.set_value("sel_label", text)
                    dpg.configure_item("sel_label", color=list(col))
    except queue.Empty:
        pass

def _set_sel_label(text, colour=None):
    _msg_q.put(("sel_label", (text, colour or C_MUTED)))


# ════════════════════════════════════════════════════════════
#  LATEX
# ════════════════════════════════════════════════════════════

def generate_latex_table(df):
    latex = r"""
\documentclass[10pt,landscape]{article}
\usepackage[utf8]{inputenc}
\usepackage[margin=0.5in]{geometry}
\usepackage{longtable}
\usepackage{booktabs}
\usepackage{array}
\title{NBA Course Information Sheet: CO-PO Mapping}
\date{}
\begin{document}
\maketitle
\begin{longtable}{|p{1cm}|p{4cm}|p{2cm}|p{1.5cm}|p{1.5cm}|p{2cm}|p{7cm}|}
\hline
\textbf{CO \#} & \textbf{Course Outcome} & \textbf{PO Mapping} & \textbf{Strength} & \textbf{PIs} & \textbf{WK} & \textbf{Justification} \\ \hline
\endhead
"""
    for _, row in df.iterrows():
        just = str(row.get('Justification', '')).replace('&', r'\&').replace('%', r'\%')
        co   = str(row.get('Course Outcome', '')).replace('&', r'\&')
        latex += (f"{row.get('CO #','')} & {co} & {row.get('PO Mapping','')} & "
                  f"{row.get('Strength','')} & {row.get('PIs','')} & "
                  f"{row.get('WK','')} & {just} \\\\ \\hline\n")
    latex += "\n    \\end{longtable}\n    \\end{document}\n"
    return latex


# ════════════════════════════════════════════════════════════
#  CORE: run_automation  (original logic, unchanged)
# ════════════════════════════════════════════════════════════

def run_automation(c: list, change: str = "nil") -> pd.DataFrame | None:
    with open(PO_FILE, 'r') as f:
        pos_data = json.load(f)
    system_msg = "You are an NBA expert. Output ONLY a markdown table. No conversational text."
    base = f"""
        Using these POs: {json.dumps(pos_data)}
        Use these COs: {" | ".join(c)}
        Generate a mapping table.
        1. Strength: Use 1, 2, or 3.
        2. PIs: Use specific codes like 1.1.1 or 2.1.1.
        3. WK: Use profiles like WK1, WK2.
        4. Justification: Technical explanation.
        Headers: | CO # | Course Outcome | PO Mapping | Strength | PIs | WK | Justification |
    """
    prompt = base if change == "nil" else base + f"\n        {change}"
    response = ollama.generate(model=MODEL_NAME, system=system_msg, prompt=prompt)
    lines = [l.strip() for l in response['response'].split('\n') if '|' in l and '---' not in l]
    rows  = [[cell.strip() for cell in l.split('|') if cell.strip()] for l in lines]
    if len(rows) > 1:
        return pd.DataFrame(rows[1:], columns=rows[0])
    return None


# ════════════════════════════════════════════════════════════
#  CELL EDIT WORKER
#  Passes full row as context; AI rewrites only target column.
# ════════════════════════════════════════════════════════════

def _cell_edit_worker(row_idx: int, col_name: str, instruction: str):
    global _df, _edit_busy
    try:
        _edit_busy = True
        _set_input_enabled(False)
        _set_status(f"Rewriting {col_name} for row {row_idx + 1}…", C_AMBER)
        _push_log(f"Edit → row {row_idx+1}, col '{col_name}': {instruction}", C_ACCENT)

        row = _df.iloc[row_idx]
        row_json = row.to_dict()

        system_msg = (
            "You are an NBA accreditation expert editing a single cell of a CO-PO mapping table. "
            "You will be given the full row as context and told which column to rewrite. "
            "Respond with ONLY the new value for that column — no explanation, no labels, no quotes."
        )
        prompt = (
            f"Full row context:\n{json.dumps(row_json, indent=2)}\n\n"
            f"Column to rewrite: {col_name}\n"
            f"Edit instruction: {instruction}\n\n"
            f"Respond with only the new value for '{col_name}'."
        )

        response = ollama.generate(model=MODEL_NAME, system=system_msg, prompt=prompt)
        new_value = response['response'].strip().strip('"').strip("'")

        _df.at[row_idx, col_name] = new_value
        _push_log(f"  → '{col_name}' updated to: {new_value[:80]}{'…' if len(new_value) > 80 else ''}", C_GREEN)
        _set_status("Done — click another cell or type \"yes\" to export", C_GREEN)
        _set_hint("Click a cell to select it, then type your instruction and press Enter.", C_MUTED)
        _signal_render()

    except Exception as e:
        _push_log(f"Cell edit ERROR: {e}", C_RED)
        _set_status(f"Error: {e}", C_RED)
    finally:
        _edit_busy = False
        _set_input_enabled(True)


# ════════════════════════════════════════════════════════════
#  INITIAL GENERATION WORKER
# ════════════════════════════════════════════════════════════

def _generation_worker():
    global _df, _co_list, _generation_ok
    try:
        _set_status("Reading syllabus…")
        _push_log("Reading syllabus…", C_ACCENT)
        with open(SYLLABUS_FILE, 'r') as f:
            syllabus_text = f.read()

        _set_status("Generating Course Outcomes…")
        _push_log("Calling LLM for COs…", C_ACCENT)
        sys_p = (
            "you are a machine created by an NBA Accreditation expert to take in a syllabus "
            "and generate its corresponding Course Outcomes as a list without anything else."
        )
        usr_p = (
            "Create a set of Course Outcomes as defined in National Board of Accreditation.\n"
            "Only generate the course outcomes as a list.\n"
            f"Syllabus Content:\n{syllabus_text}"
        )
        silly_bus = ollama.generate(model=MODEL_NAME, system=sys_p, prompt=usr_p)['response']

        from nltk import sent_tokenize
        a = sent_tokenize(silly_bus)
        b = [x for x in a[1:len(a)-1] if x not in [f"{k}." for k in range(0, 20)]]
        c = [f"CO{i+1}: {b[i]}" for i in range(len(b))]
        _co_list = c
        _push_log(f"Generated {len(c)} COs.", C_GREEN)
        for co in c:
            _push_log(f"  {co}", C_MUTED)

        _set_status("Building CO-PO Mapping…")
        _push_log("Calling LLM for full table…", C_ACCENT)
        new_df = run_automation(c)
        if new_df is not None:
            _df = new_df
            _generation_ok = True
            _push_log("Table ready.", C_GREEN)
            _set_status("Click a cell to select it, then describe your change", C_GREEN)
            _set_hint("Click any cell to select it, then type your instruction and press Enter.", C_MUTED)
            _set_input_enabled(True)
            _signal_render()
        else:
            _push_log("Failed to parse model output.", C_RED)
            _set_status("Parse error — check log", C_RED)
    except Exception as e:
        _push_log(f"ERROR: {e}", C_RED)
        _set_status(f"Error: {e}", C_RED)


# ════════════════════════════════════════════════════════════
#  TABLE RENDER
# ════════════════════════════════════════════════════════════

def _render_table(df):
    global _sel_row, _sel_col
    if not dpg.does_item_exist("table_container"):
        return
    dpg.delete_item("table_container", children_only=True)

    # re-clamp selection to valid range after re-render
    if _sel_row is not None and _sel_row >= len(df):
        _sel_row = None
        _sel_col = None

    with dpg.table(
        parent="table_container",
        tag="copo_table",
        header_row=True,
        borders_innerH=True, borders_outerH=True,
        borders_innerV=True, borders_outerV=True,
        row_background=True,
        scrollX=True, scrollY=True,
        freeze_rows=1,
        policy=dpg.mvTable_SizingFixedFit,
        height=-1,
    ):
        for col, w in zip(COLS, COL_W):
            dpg.add_table_column(label=col, width=w, width_fixed=True)

        for i, (_, row) in enumerate(df.iterrows()):
            with dpg.table_row():
                # row highlight: selected row = blue tint, else alternate
                if i == _sel_row:
                    dpg.highlight_table_row("copo_table", i, list(C_SEL_ROW))
                else:
                    bg = C_ROW_EVEN if i % 2 == 0 else C_ROW_ODD
                    dpg.highlight_table_row("copo_table", i, list(bg))

                for j, col in enumerate(COLS):
                    val = str(row.get(col, ""))
                    is_sel_cell = (i == _sel_row and col == _sel_col)
                    colour = list(C_ACCENT) if is_sel_cell else list(C_TEXT)

                    # each cell is a selectable button-like text
                    cell_tag = f"cell_{i}_{j}"
                    dpg.add_selectable(
                        label=val,
                        tag=cell_tag,
                        span_columns=False,
                        callback=_on_cell_click,
                        user_data=(i, col),
                    )
                    # colour the selected cell
                    if is_sel_cell:
                        with dpg.theme() as cell_theme:
                            with dpg.theme_component(dpg.mvSelectable):
                                dpg.add_theme_color(dpg.mvThemeCol_Header,        list(C_SEL_CELL))
                                dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, list(C_SEL_CELL))
                                dpg.add_theme_color(dpg.mvThemeCol_Text,          (13, 17, 23, 255))
                        dpg.bind_item_theme(cell_tag, cell_theme)


def _on_cell_click(sender, app_data, user_data):
    global _sel_row, _sel_col
    row_idx, col_name = user_data
    _sel_row = row_idx
    _sel_col = col_name

    co_id = _df.iloc[row_idx].get("CO #", f"row {row_idx+1}")
    dpg.set_value("sel_label",
                  f"Selected  →  {co_id}  ·  column: {col_name}")
    dpg.configure_item("sel_label", color=list(C_ACCENT))

    # re-render to update highlights without re-querying the model
    _render_table(_df)

    # focus the input
    if dpg.does_item_exist("edit_input"):
        dpg.focus_item("edit_input")


# ════════════════════════════════════════════════════════════
#  INPUT CALLBACK
# ════════════════════════════════════════════════════════════

def _on_submit(sender, app_data, user_data):
    if not _generation_ok:
        return
    text = dpg.get_value("edit_input").strip()
    if not text:
        return
    dpg.set_value("edit_input", "")

    # export confirmation
    if text.lower() == "yes":
        _push_log("Exporting…", C_GREEN)
        threading.Thread(target=_export_worker, daemon=True).start()
        return

    if _edit_busy:
        _push_log("Still processing — please wait.", C_AMBER)
        return

    if _sel_row is None or _sel_col is None:
        _set_hint("⚠  Click a cell first to select which field to edit.", C_AMBER)
        return

    _set_hint(f'Rewriting "{_sel_col}" for {_df.iloc[_sel_row].get("CO #", "")}…', C_AMBER)
    threading.Thread(
        target=_cell_edit_worker,
        args=(_sel_row, _sel_col, text),
        daemon=True
    ).start()


# ════════════════════════════════════════════════════════════
#  EXPORT WORKER
# ════════════════════════════════════════════════════════════

def _export_worker():
    try:
        _set_status("Exporting…", C_AMBER)
        latex = generate_latex_table(_df)
        with open(TEX_OUTPUT, 'w') as f:
            f.write(latex)
        _push_log(f"LaTeX saved → {TEX_OUTPUT}", C_GREEN)
        try:
            subprocess.run(['pdflatex', TEX_OUTPUT], check=True, capture_output=True)
            _push_log("PDF generated.", C_GREEN)
            _set_status("Export complete ✓", C_GREEN)
        except FileNotFoundError:
            _push_log("pdflatex not found — LaTeX saved only.", C_AMBER)
            _set_status("LaTeX saved ✓  (install pdflatex for PDF)", C_AMBER)
        except subprocess.CalledProcessError as e:
            _push_log(f"pdflatex error: {e}", C_RED)
            _set_status("pdflatex error — LaTeX still saved", C_AMBER)
    except Exception as e:
        _push_log(f"Export error: {e}", C_RED)


# ════════════════════════════════════════════════════════════
#  UI BUILD
# ════════════════════════════════════════════════════════════

def build_ui():
    dpg.create_context()

    with dpg.theme() as global_theme:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg,         C_BG)
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg,          C_PANEL)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg,          (30, 37, 46, 255))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered,   (38, 47, 58, 255))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive,    (50, 62, 78, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBg,          C_PANEL)
            dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive,    C_PANEL)
            dpg.add_theme_color(dpg.mvThemeCol_Border,           C_BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_Text,             C_TEXT)
            dpg.add_theme_color(dpg.mvThemeCol_Button,           (31, 111, 235, 200))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,    (31, 111, 235, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,     (20,  80, 180, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Header,           (31, 111, 235, 40))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered,    (31, 111, 235, 80))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderActive,     (31, 111, 235, 120))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarBg,      C_BG)
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrab,    C_BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_TableHeaderBg,    C_HDR_BG)
            dpg.add_theme_color(dpg.mvThemeCol_TableRowBg,       C_ROW_ODD)
            dpg.add_theme_color(dpg.mvThemeCol_TableRowBgAlt,    C_ROW_EVEN)
            dpg.add_theme_color(dpg.mvThemeCol_TableBorderLight, C_BORDER)
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding,   6)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding,    6)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding,    4)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing,      8, 6)
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding,    14, 14)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding,     8, 6)
            dpg.add_theme_style(dpg.mvStyleVar_CellPadding,      6, 5)

    dpg.bind_theme(global_theme)
    dpg.create_viewport(
        title="NBA CIS Generator — CO-PO Mapping",
        width=1440, height=900, min_width=1100, min_height=700,
        clear_color=list(C_BG),
    )
    dpg.setup_dearpygui()
    dpg.show_viewport()

    with dpg.window(tag="main_win", no_title_bar=True, no_move=True,
                    no_resize=True, no_scrollbar=True):

        with dpg.group(horizontal=True):
            dpg.add_text("NBA  ·  CO-PO Mapping", color=list(C_ACCENT))
            dpg.add_spacer(width=16)
            dpg.add_text("●  Initialising…", tag="status_text", color=list(C_AMBER))
        dpg.add_separator()
        dpg.add_spacer(height=6)

        with dpg.group(horizontal=True):

            # log pane
            with dpg.child_window(tag="log_panel", width=280, border=True, no_scrollbar=True):
                dpg.add_text("LOG", color=list(C_MUTED))
                dpg.add_separator()
                dpg.add_spacer(height=4)
                with dpg.child_window(tag="log_scroll", border=False):
                    dpg.add_text("", tag="log_text", wrap=258, color=list(C_MUTED))

            dpg.add_spacer(width=8)

            with dpg.group(tag="right_group"):

                # table
                with dpg.child_window(tag="table_panel", border=True, no_scrollbar=True):
                    dpg.add_text("CO-PO MAPPING TABLE", color=list(C_MUTED))
                    dpg.add_separator()
                    dpg.add_spacer(height=4)
                    with dpg.group(tag="table_container"):
                        dpg.add_text("Generating table…", color=list(C_MUTED))

                dpg.add_spacer(height=8)

                # edit panel
                with dpg.child_window(tag="edit_panel", border=True,
                                       height=115, no_scrollbar=True):
                    with dpg.group(horizontal=True):
                        dpg.add_text("EDIT CELL", color=list(C_MUTED))
                        dpg.add_spacer(width=12)
                        dpg.add_text("No cell selected", tag="sel_label", color=list(C_MUTED))
                    dpg.add_separator()
                    dpg.add_spacer(height=4)
                    dpg.add_input_text(
                        tag="edit_input",
                        hint='Click a cell, then describe the change and press Enter  ·  "yes" to export',
                        callback=_on_submit,
                        on_enter=True,
                        width=-1,
                        enabled=False,
                    )
                    dpg.add_spacer(height=5)
                    dpg.add_text(
                        "Waiting for generation to complete…",
                        tag="edit_hint", wrap=900, color=list(C_MUTED),
                    )

    def _resize():
        vw = dpg.get_viewport_client_width()
        vh = dpg.get_viewport_client_height()
        dpg.set_item_width("main_win",    vw)
        dpg.set_item_height("main_win",   vh)
        rw = vw - 280 - 8 - 28 - 14
        dpg.set_item_width("table_panel",  rw)
        dpg.set_item_width("edit_panel",   rw)
        dpg.set_item_height("table_panel", max(200, vh - 115 - 80 - 44))
        dpg.set_item_height("log_panel",   vh - 60)

    dpg.set_viewport_resize_callback(_resize)
    _resize()

    while dpg.is_dearpygui_running():
        _drain_queue()
        dpg.render_dearpygui_frame()

    dpg.destroy_context()


if __name__ == "__main__":
    threading.Thread(target=_generation_worker, daemon=True).start()
    build_ui()
