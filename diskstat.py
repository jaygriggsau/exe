"""DiskStat - a WinDirStat-style disk usage analyzer.

Scans a directory, shows a folder tree with sizes, a file-type
breakdown, and a squarified treemap where each rectangle is a file
sized by bytes and colored by extension. Click a rectangle to select
the file in the tree; click a tree row to highlight its rectangles.

Pure standard library (tkinter). Works on Windows, macOS, and Linux.
"""

from __future__ import annotations

import argparse
import colorsys
import os
import queue
import sys
import threading
import tkinter as tk
from dataclasses import dataclass, field
from tkinter import filedialog, ttk
from typing import Optional


# --------------------------------------------------------------------------- #
# Scanning
# --------------------------------------------------------------------------- #


@dataclass
class Node:
    name: str
    path: str
    is_dir: bool
    size: int = 0
    children: list["Node"] = field(default_factory=list)
    parent: Optional["Node"] = None
    ext: str = ""


def _scan(path: str, parent: Optional[Node], cancel: threading.Event,
          progress: queue.Queue) -> Optional[Node]:
    if cancel.is_set():
        return None
    name = os.path.basename(path) or path
    try:
        st = os.lstat(path)
    except OSError:
        return None

    if os.path.isdir(path) and not os.path.islink(path):
        node = Node(name=name, path=path, is_dir=True, parent=parent)
        try:
            entries = list(os.scandir(path))
        except OSError:
            return node
        for entry in entries:
            child = _scan(entry.path, node, cancel, progress)
            if child is not None:
                node.children.append(child)
                node.size += child.size
        return node

    size = st.st_size
    ext = os.path.splitext(name)[1].lower() or "<no ext>"
    progress.put(size)
    return Node(name=name, path=path, is_dir=False, size=size,
                parent=parent, ext=ext)


# --------------------------------------------------------------------------- #
# Treemap layout (squarified)
# --------------------------------------------------------------------------- #


@dataclass
class Tile:
    node: Node
    x: float
    y: float
    w: float
    h: float


def _flatten_files(node: Node, out: list[Node]) -> None:
    if node.is_dir:
        for child in node.children:
            _flatten_files(child, out)
    elif node.size > 0:
        out.append(node)


def _squarify(items: list[Node], x: float, y: float, w: float, h: float,
              out: list[Tile]) -> None:
    """Squarified treemap by Bruls, Huijbregts & van Wijk."""
    if not items or w <= 0 or h <= 0:
        return
    total = sum(n.size for n in items)
    if total <= 0:
        return

    # Scale node sizes to area = w * h.
    scale = (w * h) / total
    sizes = [n.size * scale for n in items]

    def worst(row: list[float], side: float) -> float:
        s = sum(row)
        rmax = max(row)
        rmin = min(row)
        side2 = side * side
        s2 = s * s
        return max(side2 * rmax / s2, s2 / (side2 * rmin))

    i = 0
    cx, cy, cw, ch = x, y, w, h
    while i < len(sizes):
        side = min(cw, ch)
        row = [sizes[i]]
        j = i + 1
        while j < len(sizes):
            new_row = row + [sizes[j]]
            if worst(new_row, side) <= worst(row, side):
                row = new_row
                j += 1
            else:
                break

        row_sum = sum(row)
        if cw <= ch:
            row_h = row_sum / cw if cw > 0 else 0
            rx = cx
            for k, area in enumerate(row):
                rw = area / row_h if row_h > 0 else 0
                out.append(Tile(items[i + k], rx, cy, rw, row_h))
                rx += rw
            cy += row_h
            ch -= row_h
        else:
            row_w = row_sum / ch if ch > 0 else 0
            ry = cy
            for k, area in enumerate(row):
                rh = area / row_w if row_w > 0 else 0
                out.append(Tile(items[i + k], cx, ry, row_w, rh))
                ry += rh
            cx += row_w
            cw -= row_w

        i = j


# --------------------------------------------------------------------------- #
# Colors
# --------------------------------------------------------------------------- #


_EXT_PALETTE: dict[str, str] = {}
_PALETTE_HUES = [0.00, 0.08, 0.13, 0.30, 0.45, 0.55, 0.62, 0.72, 0.83, 0.93]


def color_for_ext(ext: str) -> str:
    if ext in _EXT_PALETTE:
        return _EXT_PALETTE[ext]
    idx = len(_EXT_PALETTE) % len(_PALETTE_HUES)
    h = _PALETTE_HUES[idx]
    # Vary saturation/value a bit so collisions look different.
    s = 0.55 + 0.05 * ((len(_EXT_PALETTE) // len(_PALETTE_HUES)) % 5)
    v = 0.90 - 0.06 * ((len(_EXT_PALETTE) // len(_PALETTE_HUES)) % 4)
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    hexv = f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"
    _EXT_PALETTE[ext] = hexv
    return hexv


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #


def human_size(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    f = float(n)
    for u in units:
        if f < 1024 or u == units[-1]:
            return f"{f:,.1f} {u}" if u != "B" else f"{int(f):,} B"
        f /= 1024
    return f"{n} B"


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #


class App:
    def __init__(self, root: tk.Tk, initial: Optional[str] = None) -> None:
        self.root = root
        root.title("DiskStat")
        root.geometry("1200x800")

        self.root_node: Optional[Node] = None
        self.tiles: list[Tile] = []
        self.tile_by_path: dict[str, list[int]] = {}
        self.tree_item_by_path: dict[str, str] = {}
        self.cancel_event = threading.Event()
        self.scan_thread: Optional[threading.Thread] = None
        self.scan_queue: queue.Queue = queue.Queue()
        self.bytes_seen = 0
        self.selected_path: Optional[str] = None

        self._build_ui()

        if initial:
            self.start_scan(initial)

    # -- layout --------------------------------------------------------------

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self.root, padding=4)
        toolbar.pack(side=tk.TOP, fill=tk.X)
        ttk.Button(toolbar, text="Open folder…",
                   command=self.choose_folder).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Rescan",
                   command=self.rescan).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(toolbar, text="Cancel",
                   command=self.cancel).pack(side=tk.LEFT, padx=(4, 0))
        self.path_var = tk.StringVar(value="(no folder)")
        ttk.Label(toolbar, textvariable=self.path_var,
                  anchor=tk.W).pack(side=tk.LEFT, fill=tk.X,
                                    expand=True, padx=8)

        paned = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # Left side: tree on top, treemap below.
        left = ttk.Panedwindow(paned, orient=tk.VERTICAL)
        paned.add(left, weight=4)

        tree_frame = ttk.Frame(left)
        left.add(tree_frame, weight=2)

        self.tree = ttk.Treeview(
            tree_frame,
            columns=("size", "pct", "items"),
            displaycolumns=("size", "pct", "items"),
        )
        self.tree.heading("#0", text="Name")
        self.tree.heading("size", text="Size")
        self.tree.heading("pct", text="%")
        self.tree.heading("items", text="Items")
        self.tree.column("#0", width=380, anchor=tk.W)
        self.tree.column("size", width=110, anchor=tk.E)
        self.tree.column("pct", width=60, anchor=tk.E)
        self.tree.column("items", width=80, anchor=tk.E)
        tsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL,
                            command=self.tree.yview)
        self.tree.configure(yscrollcommand=tsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        self.tree.bind("<<TreeviewOpen>>", self.on_tree_open)

        tm_frame = ttk.Frame(left)
        left.add(tm_frame, weight=3)
        self.canvas = tk.Canvas(tm_frame, background="#1e1e1e",
                                highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda _e: self.redraw_treemap())
        self.canvas.bind("<Button-1>", self.on_canvas_click)
        self.canvas.bind("<Motion>", self.on_canvas_motion)

        # Right side: extension breakdown.
        right = ttk.Frame(paned)
        paned.add(right, weight=1)
        ttk.Label(right, text="By extension",
                  padding=(4, 4)).pack(anchor=tk.W)
        self.ext_tree = ttk.Treeview(
            right, columns=("size", "pct", "count"), show="headings",
        )
        self.ext_tree.heading("size", text="Size")
        self.ext_tree.heading("pct", text="%")
        self.ext_tree.heading("count", text="Files")
        self.ext_tree.column("size", width=100, anchor=tk.E)
        self.ext_tree.column("pct", width=60, anchor=tk.E)
        self.ext_tree.column("count", width=70, anchor=tk.E)
        self.ext_tree.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        # Status bar.
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(self.root, textvariable=self.status_var, anchor=tk.W,
                  relief=tk.SUNKEN, padding=(4, 2)).pack(side=tk.BOTTOM,
                                                        fill=tk.X)

        self.tooltip = tk.Label(self.canvas, background="#ffffe0",
                                relief=tk.SOLID, borderwidth=1, padx=4)
        self.tooltip.place_forget()

    # -- scanning ------------------------------------------------------------

    def choose_folder(self) -> None:
        path = filedialog.askdirectory(mustexist=True)
        if path:
            self.start_scan(path)

    def rescan(self) -> None:
        if self.root_node is not None:
            self.start_scan(self.root_node.path)

    def cancel(self) -> None:
        if self.scan_thread and self.scan_thread.is_alive():
            self.cancel_event.set()
            self.status_var.set("Cancelling…")

    def start_scan(self, path: str) -> None:
        self.cancel()
        self.cancel_event = threading.Event()
        self.scan_queue = queue.Queue()
        self.bytes_seen = 0
        self.path_var.set(path)
        self.status_var.set("Scanning…")
        self.tree.delete(*self.tree.get_children())
        self.ext_tree.delete(*self.ext_tree.get_children())
        self.canvas.delete("all")
        self.tiles = []
        self.tile_by_path = {}
        self.tree_item_by_path = {}
        self.root_node = None

        cancel = self.cancel_event
        q = self.scan_queue

        def worker() -> None:
            node = _scan(path, None, cancel, q)
            q.put(("done", node))

        self.scan_thread = threading.Thread(target=worker, daemon=True)
        self.scan_thread.start()
        self.root.after(80, self._poll_scan)

    def _poll_scan(self) -> None:
        try:
            while True:
                item = self.scan_queue.get_nowait()
                if isinstance(item, tuple) and item and item[0] == "done":
                    self._finish_scan(item[1])
                    return
                else:
                    self.bytes_seen += int(item)
        except queue.Empty:
            pass

        if self.scan_thread and self.scan_thread.is_alive():
            self.status_var.set(
                f"Scanning… {human_size(self.bytes_seen)} seen"
            )
            self.root.after(120, self._poll_scan)

    def _finish_scan(self, node: Optional[Node]) -> None:
        if node is None:
            self.status_var.set("Scan cancelled or failed.")
            return
        self.root_node = node
        self._populate_tree(node)
        self._populate_extensions(node)
        self.redraw_treemap()
        self.status_var.set(
            f"Done. {human_size(node.size)} across "
            f"{self._count_files(node):,} files."
        )

    @staticmethod
    def _count_files(node: Node) -> int:
        if not node.is_dir:
            return 1
        return sum(App._count_files(c) for c in node.children)

    # -- tree ----------------------------------------------------------------

    def _populate_tree(self, root: Node) -> None:
        self._insert_tree_node("", root, root.size or 1)

    def _insert_tree_node(self, parent_iid: str, node: Node,
                          root_size: int) -> str:
        pct = (100.0 * node.size / root_size) if root_size else 0.0
        items = (len(node.children) if node.is_dir
                 else 0)
        text = (node.name if parent_iid else node.path)
        iid = self.tree.insert(
            parent_iid, tk.END,
            text=("📁 " if node.is_dir else "📄 ") + text,
            values=(human_size(node.size), f"{pct:.1f}",
                    items if node.is_dir else ""),
            open=False,
        )
        self.tree_item_by_path[node.path] = iid
        # Sort children: dirs and files together by size, descending.
        if node.is_dir:
            sorted_children = sorted(node.children,
                                     key=lambda n: n.size, reverse=True)
            # Insert a lazy placeholder so the expand arrow appears,
            # then on first open replace with real children.
            if sorted_children:
                self.tree.insert(iid, tk.END, text="…")
                self._pending_children[iid] = (sorted_children, root_size)
        return iid

    _pending_children: dict[str, tuple[list[Node], int]] = {}

    def on_tree_open(self, _event: object) -> None:
        iid = self.tree.focus()
        if iid in self._pending_children:
            children, root_size = self._pending_children.pop(iid)
            # Drop placeholder.
            for ch in self.tree.get_children(iid):
                self.tree.delete(ch)
            for child in children:
                self._insert_tree_node(iid, child, root_size)

    def on_tree_select(self, _event: object) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        # Reverse lookup path.
        for p, i in self.tree_item_by_path.items():
            if i == iid:
                self.selected_path = p
                self.redraw_treemap()
                return

    # -- extensions ----------------------------------------------------------

    def _populate_extensions(self, root: Node) -> None:
        agg: dict[str, tuple[int, int]] = {}

        def walk(n: Node) -> None:
            if n.is_dir:
                for c in n.children:
                    walk(c)
            else:
                size, count = agg.get(n.ext, (0, 0))
                agg[n.ext] = (size + n.size, count + 1)

        walk(root)
        total = root.size or 1
        rows = sorted(agg.items(), key=lambda kv: kv[1][0], reverse=True)
        for ext, (size, count) in rows:
            pct = 100.0 * size / total
            color = color_for_ext(ext)
            tag = f"ext_{ext}"
            self.ext_tree.insert(
                "", tk.END, text=ext,
                values=(human_size(size), f"{pct:.1f}", count),
                tags=(tag,),
            )
            try:
                self.ext_tree.tag_configure(tag, background=color)
            except tk.TclError:
                pass
            self.ext_tree.item(self.ext_tree.get_children()[-1], text=ext)
        # Show ext column.
        self.ext_tree["show"] = "tree headings"
        self.ext_tree.heading("#0", text="Extension")
        self.ext_tree.column("#0", width=120, anchor=tk.W)

    # -- treemap -------------------------------------------------------------

    def redraw_treemap(self) -> None:
        self.canvas.delete("all")
        self.tiles = []
        self.tile_by_path = {}
        if self.root_node is None:
            return
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 2 or h < 2:
            return

        files: list[Node] = []
        _flatten_files(self.root_node, files)
        if not files:
            return
        files.sort(key=lambda n: n.size, reverse=True)

        tiles: list[Tile] = []
        _squarify(files, 0, 0, w, h, tiles)
        self.tiles = tiles

        sel = self.selected_path
        sel_node = None
        if sel:
            sel_node = self._find(self.root_node, sel)

        for idx, t in enumerate(tiles):
            if t.w < 1 or t.h < 1:
                continue
            color = color_for_ext(t.node.ext)
            in_sel = (sel_node is not None
                      and self._is_under(t.node, sel_node))
            outline = "#ffffff" if in_sel else "#222222"
            width = 2 if in_sel else 1
            self.canvas.create_rectangle(
                t.x, t.y, t.x + t.w, t.y + t.h,
                fill=color, outline=outline, width=width,
            )
            self.tile_by_path.setdefault(t.node.path, []).append(idx)
            if t.w > 60 and t.h > 16:
                label = t.node.name
                if len(label) > int(t.w / 7):
                    label = label[: max(1, int(t.w / 7) - 1)] + "…"
                self.canvas.create_text(
                    t.x + 4, t.y + 2, anchor=tk.NW,
                    text=label, fill="#000000",
                    font=("TkDefaultFont", 8),
                )

    @staticmethod
    def _find(node: Node, path: str) -> Optional[Node]:
        if node.path == path:
            return node
        if not node.is_dir:
            return None
        for c in node.children:
            r = App._find(c, path)
            if r is not None:
                return r
        return None

    @staticmethod
    def _is_under(node: Node, ancestor: Node) -> bool:
        n: Optional[Node] = node
        while n is not None:
            if n is ancestor:
                return True
            n = n.parent
        return False

    def _tile_at(self, x: int, y: int) -> Optional[Tile]:
        for t in self.tiles:
            if t.x <= x <= t.x + t.w and t.y <= y <= t.y + t.h:
                return t
        return None

    def on_canvas_click(self, event: tk.Event) -> None:
        t = self._tile_at(event.x, event.y)
        if t is None:
            return
        self.selected_path = t.node.path
        # Reveal in tree.
        self._reveal_in_tree(t.node)
        self.redraw_treemap()

    def _reveal_in_tree(self, node: Node) -> None:
        chain: list[Node] = []
        cur: Optional[Node] = node
        while cur is not None:
            chain.append(cur)
            cur = cur.parent
        chain.reverse()
        for n in chain[:-1]:
            iid = self.tree_item_by_path.get(n.path)
            if iid:
                self.tree.item(iid, open=True)
                # Trigger lazy load.
                self.tree.focus(iid)
                self.on_tree_open(None)
        target = self.tree_item_by_path.get(node.path)
        if target:
            self.tree.selection_set(target)
            self.tree.see(target)

    def on_canvas_motion(self, event: tk.Event) -> None:
        t = self._tile_at(event.x, event.y)
        if t is None:
            self.tooltip.place_forget()
            return
        self.tooltip.config(
            text=f"{t.node.path}\n{human_size(t.node.size)}"
        )
        tx = min(event.x + 12, self.canvas.winfo_width() - 220)
        ty = min(event.y + 12, self.canvas.winfo_height() - 40)
        self.tooltip.place(x=tx, y=ty)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(description="DiskStat - disk usage analyzer")
    parser.add_argument("path", nargs="?", help="Folder to scan on startup")
    args = parser.parse_args()

    root = tk.Tk()
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
    except tk.TclError:
        pass

    initial = args.path
    if initial and not os.path.isdir(initial):
        print(f"Not a directory: {initial}", file=sys.stderr)
        return 2

    App(root, initial=initial)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
