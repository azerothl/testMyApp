"""Publie les contrôles visibles d’une cible Tk lancée pour les tests."""

from __future__ import annotations

import json
import os
import tkinter as tk
from pathlib import Path
from tkinter import ttk


def publish_visible_ui(root: tk.Tk) -> None:
    location = os.environ.get("TESTMYAPP_DISCOVERY_FILE")
    if not location:
        return
    path = Path(location)
    temporary = path.with_suffix(".tmp")
    previous = ""

    def sample() -> None:
        nonlocal previous
        elements = []
        focus = str(root.focus_get())

        def add(widget, role, name="", box=None, *, token=None, state=None):
            if box is None:
                box = (0, 0, widget.winfo_width(), widget.winfo_height())
            x1, y1, x2, y2 = box
            if x2 <= x1 or y2 <= y1:
                return
            elements.append({"id": token or str(widget), "name": name[:2500], "role": role,
                             "point": [widget.winfo_rootx() + (x1 + x2) // 2,
                                       widget.winfo_rooty() + (y1 + y2) // 2],
                             "focused": str(widget) == focus, "state": state})

        def visit(widget):
            if not widget.winfo_viewable() or len(elements) >= 160:
                return
            if isinstance(widget, ttk.Notebook):
                locations = {}
                for x in range(2, widget.winfo_width(), 4):
                    try:
                        index = widget.index(f"@{x},18")
                    except tk.TclError:
                        continue
                    locations.setdefault(index, []).append(x)
                for index, tab in enumerate(widget.tabs()):
                    if widget.tab(tab, "state") != "normal":
                        continue
                    points = locations.get(index)
                    if points:
                        add(widget, "tab", widget.tab(tab, "text"), (points[0], 4, points[-1], 22),
                            token=f"{widget}:{tab}", state="selected" if widget.select() == tab else None)
            elif isinstance(widget, ttk.Checkbutton) and not widget.instate(["disabled"]):
                add(widget, "checkbox", widget.cget("text"), state="checked" if widget.instate(["selected"]) else "unchecked")
            elif isinstance(widget, ttk.Button) and not widget.instate(["disabled"]):
                add(widget, "button", widget.cget("text"))
            elif isinstance(widget, (tk.Entry, ttk.Entry)):
                add(widget, "entry", "Champ de conversation", state="filled" if widget.get() else None)
            elif isinstance(widget, (tk.Label, ttk.Label)):
                add(widget, "text", widget.cget("text"))
            elif isinstance(widget, tk.Text):
                add(widget, "text", widget.get("@0,0", f"@0,{widget.winfo_height()} lineend"))
            elif isinstance(widget, tk.Listbox):
                for index in range(min(widget.size(), 100)):
                    box = widget.bbox(index)
                    if box is None:
                        continue
                    x, y, width, height = box
                    add(widget, "listitem", widget.get(index), (x, y, x + width, y + height),
                        token=f"{widget}:{index}", state="selected" if index in widget.curselection() else None)
            elif isinstance(widget, tk.Canvas) and widget.bind("<ButtonPress-1>"):
                for item in widget.find_all():
                    if widget.type(item) != "text":
                        continue
                    x1, y1, x2, y2 = widget.bbox(item)
                    if 0 <= x1 < x2 <= widget.winfo_width() and 0 <= y1 < y2 <= widget.winfo_height():
                        add(widget, "node", widget.itemcget(item, "text"), (x1, y1, x2, y2),
                            token=f"{widget}:{item}")
            for child in widget.winfo_children():
                if not isinstance(child, tk.Toplevel):
                    visit(child)

        try:
            visit(root)
            data = json.dumps(elements, ensure_ascii=False)
            if data != previous:
                temporary.write_text(data, encoding="utf-8")
                temporary.replace(path)
                previous = data
        except (OSError, tk.TclError):
            pass
        root.after(180, sample)

    root.after(100, sample)
