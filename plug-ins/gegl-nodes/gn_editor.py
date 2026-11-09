# The node editor widget: GtkNodes nodes for GEGL operations, made from
# what GEGL says about each operation (gn_ops), read back into a graph
# (gn_graph) whenever something changes.
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Needs GtkNodes (github.com/aluntzer/gtknodes, LGPL-2.1) and its
# GObject introspection data: gegl-nodes.py finds it before this is
# imported.

import math

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Gegl", "0.4")
gi.require_version("GtkNodes", "0.1")
from gi.repository import Gdk, Gegl, GLib, GObject, Gtk, GtkNodes  # noqa: E402

import gn_graph as G  # noqa: E402
import gn_ops  # noqa: E402

IMAGE_KEY = 1  # all sockets carry images: any output fits any input
COLORS = {"input": "#5aa0d8", "aux": "#d8a05a", "aux2": "#a05ad8", "output": "#6cc06c"}


# GIMP's theme gives every vertical box a background (".vertical" in
# themes/Default/common.css). A GtkNodes node is a vertical box, and that
# background, painted after GtkNodes draws the node's frame, hid parts of
# the frame (and left parts of it behind when a node moved).
_CSS = b"""
.gegl-nodes-node { background-color: transparent; background-image: none; }
/* the Layer and Output nodes: a bold title and a badge in the color of
   their socket */
.gegl-nodes-end expander title label { font-weight: bold; }
.gegl-nodes-badge {
    font-size: smaller; font-weight: bold; border-radius: 3px;
    padding: 1px 6px; margin: 2px 0; color: #202020;
}
.gegl-nodes-layer .gegl-nodes-badge { background-color: #6cc06c; }
.gegl-nodes-output .gegl-nodes-badge { background-color: #5aa0d8; }
.gegl-nodes-layer expander title label { color: #8ad88a; }
.gegl-nodes-output expander title label { color: #84bfec; }
/* the row of categories: menu labels, not buttons */
.gegl-nodes-categories flowboxchild { padding: 0; }
.gegl-nodes-categories button {
    background: none; border: none; box-shadow: none;
    padding: 2px 7px; min-height: 0; min-width: 0;
}
.gegl-nodes-categories button:hover,
.gegl-nodes-categories button:checked { background-color: alpha(currentColor, 0.15); }
"""
_css_screens = set()


def _style(widget):
    screen = widget.get_screen()
    if screen not in _css_screens:
        provider = Gtk.CssProvider()
        provider.load_from_data(_CSS)
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        _css_screens.add(screen)
    widget.get_style_context().add_class("gegl-nodes-node")


def _rgba(spec):
    c = Gdk.RGBA()
    c.parse(spec)
    return c


def _to_srgb(v):
    v = min(max(v, 0.0), 1.0)
    return 12.92 * v if v <= 0.0031308 else 1.055 * math.pow(v, 1 / 2.4) - 0.055


def _hex(rgba):
    return "#%02x%02x%02x%02x" % tuple(round(min(max(c, 0.0), 1.0) * 255) for c in rgba)


def _default_hex(pspec):
    color = pspec.get_default_value()
    if color is None:
        return "#000000ff"
    r, g, b, a = color.get_rgba()
    return _hex((_to_srgb(r), _to_srgb(g), _to_srgb(b), a))


def _no_icon(node):
    """No delete button on the node. Needs GtkNodes with
    sandbranch/gtknodes fix/icon-nullable (its introspection data refused
    None before); without it, the button stays."""
    try:
        node.set_icon_name(None)
    except TypeError:
        pass


class _Node(GtkNodes.Node):
    """A node of the editor: gn_id is its id in the graph, gn_sinks its
    input sockets by pad."""
    __gtype_name__ = "GeglNodesNode"

    def __init__(self, editor, node_id, label):
        super().__init__()
        self.gn_editor = editor
        self.gn_id = node_id
        self.gn_sinks = {}
        self.gn_source = None
        self.set_label(label)
        _style(self)
        for signal in ("node-socket-connect", "node-socket-disconnect"):
            self.connect(signal, lambda *a: editor.changed())

    def mark_end(self, kind):
        """The Layer and Output nodes: tinted, with a bold title, and no
        delete button (they are always there)."""
        _no_icon(self)
        context = self.get_style_context()
        context.add_class("gegl-nodes-end")
        context.add_class("gegl-nodes-" + kind)
        badge = Gtk.Label(label="IMAGE IN" if kind == "layer" else "SHOWN", xalign=0.5)
        badge.get_style_context().add_class("gegl-nodes-badge")
        self.item_add(badge, GtkNodes.NodeSocketIO.DISABLE)
        self.set_tooltip_text("The layer as it is: where the graph starts" if kind == "layer"
                              else "What the layer shows: the end of the graph")

    def add_sink(self, pad):
        label = Gtk.Label(label=pad.capitalize() if pad != "input" else "Input", xalign=0.0)
        socket = self.item_add(label, GtkNodes.NodeSocketIO.SINK)
        socket.set_key(IMAGE_KEY)
        socket.set_rgba(_rgba(COLORS.get(pad, COLORS["aux2"])))
        socket.gn_node = self.gn_id
        self.gn_sinks[pad] = socket

    def add_source(self):
        label = Gtk.Label(label="Output", xalign=1.0)
        socket = self.item_add(label, GtkNodes.NodeSocketIO.SOURCE)
        socket.set_key(IMAGE_KEY)
        socket.set_rgba(_rgba(COLORS["output"]))
        socket.gn_node = self.gn_id
        self.gn_source = socket


class _OpNode(_Node):
    __gtype_name__ = "GeglNodesOpNode"

    def __init__(self, editor, node_id, info, props):
        super().__init__(editor, node_id, info.title)
        self.gn_op = info.name
        self.gn_props = dict(props)
        self.gn_widgets = {}
        self.set_tooltip_text("%s\n%s" % (info.name, info.description))
        self.connect("node-func-clicked", lambda node: editor.remove(node.gn_id))
        for pad in info.inputs:
            self.add_sink(pad)
        if info.props:
            grid = Gtk.Grid(column_spacing=6, row_spacing=2, margin_top=4)
            for row, pspec in enumerate(info.props):
                widget = self._widget(pspec)
                if widget is None:
                    continue
                name = Gtk.Label(label=pspec.get_nick() or pspec.name, xalign=0.0)
                name.set_tooltip_text(pspec.get_blurb() or pspec.name)
                grid.attach(name, 0, row, 1, 1)
                grid.attach(widget, 1, row, 1, 1)
                self.gn_widgets[pspec.name] = widget
            self.item_add(grid, GtkNodes.NodeSocketIO.DISABLE)
        self.add_source()

    def _set(self, name, value):
        self.gn_props[name] = value
        self.gn_editor.changed()

    def _widget(self, pspec):
        kind = pspec.__gtype__.name
        name = pspec.name
        value = self.gn_props.get(name)
        if kind in ("GeglParamDouble", "GParamDouble", "GeglParamInt", "GParamInt"):
            integer = "Int" in kind
            lower, upper = pspec.minimum, pspec.maximum
            if value is None:
                value = pspec.get_default_value()
            # the full range of some operations is huge (+-G_MAXDOUBLE)
            lower, upper = max(lower, -1e7), min(upper, 1e7)
            step = 1.0 if integer or upper - lower > 20 else 0.01
            adj = Gtk.Adjustment(value=value, lower=lower, upper=upper,
                                 step_increment=step, page_increment=step * 10)
            w = Gtk.SpinButton(adjustment=adj, digits=0 if integer else 3, width_chars=7)
            w.connect("value-changed", lambda s: self._set(
                name, s.get_value_as_int() if integer else round(s.get_value(), 6)))
            return w
        if kind == "GParamBoolean":
            w = Gtk.CheckButton()
            w.set_active(bool(pspec.get_default_value() if value is None else value))
            w.connect("toggled", lambda b: self._set(name, b.get_active()))
            return w
        if kind in ("GeglParamEnum", "GParamEnum"):
            w = Gtk.ComboBoxText()
            values = pspec.enum_class.__enum_values__
            for number in sorted(values):
                w.append(values[number].value_nick, values[number].value_name)
            if value is None:
                value = values[int(pspec.get_default_value())].value_nick
            w.set_active_id(str(value))
            w.connect("changed", lambda c: self._set(name, c.get_active_id()))
            return w
        if kind == "GeglParamColor" or pspec.value_type.name == "GeglColor":
            w = Gtk.ColorButton(use_alpha=True)
            w.set_rgba(_rgba((value or _default_hex(pspec))[:7]))
            if value and len(value) == 9:
                c = w.get_rgba()
                c.alpha = int(value[7:9], 16) / 255
                w.set_rgba(c)
            w.connect("color-set", lambda b: self._set(name, _hex(
                (b.get_rgba().red, b.get_rgba().green, b.get_rgba().blue, b.get_rgba().alpha))))
            return w
        if kind in ("GeglParamString", "GParamString"):
            if pspec.get_default_value() is None and value is None:
                value = ""
            w = Gtk.Entry(text=value if value is not None else pspec.get_default_value(),
                          width_chars=12)
            w.connect("changed", lambda e: self._set(name, e.get_text()))
            return w
        return None

    def set_value(self, name, value):
        """Sets a property through its widget, as a user would."""
        w = self.gn_widgets[name]
        if isinstance(w, Gtk.SpinButton):
            w.set_value(value)
        elif isinstance(w, Gtk.CheckButton):
            w.set_active(value)
        elif isinstance(w, Gtk.ComboBoxText):
            w.set_active_id(value)
        elif isinstance(w, Gtk.ColorButton):
            w.set_rgba(_rgba(value[:7]))
            self._set(name, value)
        elif isinstance(w, Gtk.Entry):
            w.set_text(value)


class GraphEditor(Gtk.Box):
    """The editor: a bar with a search field to add operations, and the
    nodes. Emits "changed" when the graph changes (a link, a node, a
    value) and "moved" when only the places of the nodes change."""
    __gtype_name__ = "GeglNodesEditor"
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
                    "moved": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, graph=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._loading = False
        self.nodes = {}

        bar = Gtk.Box(spacing=6, margin=4)
        self.search = Gtk.SearchEntry(placeholder_text="Search filters to add",
                                      width_chars=36)
        # the matches, in a popover under the field (GtkEntryCompletion's
        # list came out empty or a few pixels wide under GIMP's theme)
        self._ops = sorted(gn_ops.catalog().values(), key=lambda i: i.title.lower())
        self.matches = Gtk.ListBox(activate_on_single_click=True)
        self.matches.connect("row-activated", lambda box, row: self._take(row))
        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      propagate_natural_height=True, max_content_height=360,
                                      min_content_width=380)
        scrolled.add(self.matches)
        self.found = Gtk.Popover(relative_to=self.search, modal=False,
                                 position=Gtk.PositionType.BOTTOM)
        self.found.add(scrolled)
        scrolled.show_all()
        self.search.connect("search-changed", self._find)
        self.search.connect("activate", lambda e: self._take(
            self.matches.get_selected_row() or self.matches.get_row_at_index(0)))
        self.search.connect("key-press-event", self._search_key)
        self.search.connect("stop-search", lambda e: self._end_search())
        add = Gtk.MenuButton(label="Add", tooltip_text="Add a node (Shift+A, or right-click "
                             "on the canvas)")
        self._add_at = None
        add.set_popup(self._menu())
        bar.pack_start(add, False, False, 0)
        arrange = Gtk.Button(label="Arrange", tooltip_text="Lay the nodes out from the layer "
                             "to the output, in columns")
        arrange.connect("clicked", lambda b: self.arrange())
        bar.pack_start(arrange, False, False, 0)
        bar.pack_start(self.search, False, False, 0)
        self.status = Gtk.Label(xalign=0.0, ellipsize=3)
        bar.pack_start(self.status, True, True, 0)
        self.pack_start(self._menubar(), False, False, 0)
        self.pack_start(bar, False, False, 0)

        self.view = GtkNodes.NodeView()
        self.view.connect("node-drag-end", lambda *a: self.emit("moved"))
        self.view.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.view.connect("button-press-event", self._pressed)
        self.connect("hierarchy-changed", self._toplevel)
        scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroller.add(self.view)
        self.pack_start(scroller, True, True, 0)
        self.load(graph or G.new_graph())

    def _find(self, entry):
        for row in self.matches.get_children():
            row.destroy()
        words = entry.get_text().lower().split()
        if not words:
            self.found.popdown()
            return
        for info in self._ops:
            text = "%s %s %s" % (info.title, info.name, " ".join(info.categories))
            if all(word in text.lower() for word in words):
                label = Gtk.Label(xalign=0.0, margin=4)
                label.set_markup("%s  <small>%s</small>" % (
                    GLib.markup_escape_text(info.title), GLib.markup_escape_text(info.name)))
                label.set_tooltip_text(info.description)
                row = Gtk.ListBoxRow()
                row.add(label)
                row.gn_op = info.name
                self.matches.add(row)
        rows = self.matches.get_children()
        if rows:
            self.matches.select_row(rows[0])
            self.matches.show_all()
            self.found.popup()
        else:
            self.found.popdown()
        self.say("" if rows else "No filter matches: %s" % entry.get_text())

    def _search_key(self, entry, event):
        rows = self.matches.get_children()
        if event.keyval not in (Gdk.KEY_Down, Gdk.KEY_Up) or not rows:
            return False
        row = self.matches.get_selected_row()
        i = row.get_index() if row else -1
        i = min(i + 1, len(rows) - 1) if event.keyval == Gdk.KEY_Down else max(i - 1, 0)
        self.matches.select_row(rows[i])
        rows[i].grab_focus()
        entry.grab_focus_without_selecting()
        return True

    def _take(self, row):
        if row is None:
            return
        self._end_search()
        self._add_picked(row.gn_op)

    def _end_search(self):
        self.found.popdown()
        GLib.idle_add(lambda: self.search.set_text("") or False)

    def _add_picked(self, name):
        if self._add_at is not None:
            x, y = self._add_at
            self._add_at = None
        else:
            step = 40 + 30 * (len(self.nodes) % 8)
            x, y = 240 + step, 20 + step
        self.add_op(name, x, y)

    @staticmethod
    def groups():
        """The operations offered, by their first category."""
        groups = {}
        for info in gn_ops.catalog().values():
            category = next((c for c in info.categories if c), "other").lower()
            groups.setdefault(category, []).append(info)
        return {c: sorted(ops, key=lambda i: i.title.lower()) for c, ops in sorted(groups.items())}

    def _ops_menu(self, ops):
        sub = Gtk.Menu()
        for info in ops:
            op = Gtk.MenuItem(label=info.title)
            op.set_tooltip_text("%s\n%s" % (info.name, info.description))
            op.connect("activate", lambda w, name=info.name: self._add_picked(name))
            sub.append(op)
        return sub

    def _menu(self):
        """All operations offered, by category, as in Blender's Add menu."""
        menu = Gtk.Menu()
        for category, ops in self.groups().items():
            item = Gtk.MenuItem(label=category.replace("-", " ").capitalize())
            item.set_submenu(self._ops_menu(ops))
            menu.append(item)
        menu.connect("deactivate", lambda m: GLib.idle_add(self._forget_place))
        menu.show_all()
        self._popup = menu
        return menu

    def _menubar(self):
        """The same categories as a row of menus across the top, which
        wraps onto more rows when the window is narrower than the row."""
        self.menubar = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=False,
                                   max_children_per_line=100, column_spacing=0, row_spacing=0,
                                   margin_start=2, margin_end=2)
        self.menubar.get_style_context().add_class("gegl-nodes-categories")
        self.category_buttons = []
        for category, ops in self.groups().items():
            menu = self._ops_menu(ops)
            menu.show_all()
            button = Gtk.MenuButton(label=category.replace("-", " ").capitalize(),
                                    relief=Gtk.ReliefStyle.NONE, focus_on_click=False)
            button.set_popup(menu)
            self.menubar.add(button)
            self.category_buttons.append(button)
        return self.menubar

    def _forget_place(self):
        # after an item's "activate", which comes after "deactivate"
        self._add_at = None
        return False

    def _pointer(self):
        window = self.view.get_window()
        seat = window.get_display().get_default_seat()
        _, x, y, _ = window.get_device_position(seat.get_pointer())
        a = self.view.get_allocation()
        return x - a.x, y - a.y

    def _on_node(self, x, y):
        for node in self.nodes.values():
            nx = self.view.child_get_property(node, "x")
            ny = self.view.child_get_property(node, "y")
            if nx <= x < nx + node.get_allocated_width() and \
               ny <= y < ny + node.get_allocated_height():
                return True
        return False

    def popup_add(self, event=None):
        """The Add menu at the pointer; the node goes where the pointer is."""
        x, y = self._pointer()
        self._add_at = (max(0, x - 20), max(0, y - 10))
        self._popup.popup_at_pointer(event)

    def _pressed(self, view, event):
        x, y = self._pointer()
        if self._on_node(x, y):
            return False
        # a click on the canvas takes the keys from the search field (for
        # Shift+A), as in Blender
        top = self.get_toplevel()
        if isinstance(top, Gtk.Window):
            top.set_focus(None)
        if event.button != Gdk.BUTTON_SECONDARY:
            return False
        self.popup_add(event)
        return True

    def _toplevel(self, widget, previous):
        # Shift+A anywhere in the window, also with nothing focused
        top = self.get_toplevel()
        if isinstance(top, Gtk.Window) and not getattr(top, "gn_keys", False):
            top.connect("key-press-event", self._key)
            top.gn_keys = True

    def _key(self, widget, event):
        if isinstance(self.get_toplevel().get_focus(), Gtk.Editable):
            return False
        if event.keyval == Gdk.KEY_A or (event.keyval == Gdk.KEY_a and
                                         event.state & Gdk.ModifierType.SHIFT_MASK):
            self.popup_add(event)
            return True
        return False

    def say(self, text):
        self.status.set_text(text)

    def changed(self):
        if not self._loading:
            self.emit("changed")

    def _place(self, node, x, y):
        self.view.add(node)
        self.view.child_set_property(node, "x", int(x))
        self.view.child_set_property(node, "y", int(y))
        node.show_all()
        self.nodes[node.gn_id] = node

    def _drop(self, node):
        """Unlinks a node from all others, then destroys it. (Destroying a
        linked node makes GtkNodes signal on sockets already freed.)"""
        for socket in node.gn_sinks.values():
            socket.disconnect()
        for other in self.nodes.values():
            for socket in other.gn_sinks.values():
                if node.gn_source is not None and socket.get_input() == node.gn_source:
                    socket.disconnect()
        # the sockets must go with the node: one that outlives it signals
        # into the freed node, in GtkNodes without the fix
        # sandbranch/gtknodes fix/socket-handlers
        node.gn_sinks = {}
        node.gn_source = None
        node.destroy()

    def load(self, graph):
        """Shows the graph, instead of what is shown."""
        self._loading = True
        for node in list(self.nodes.values()):
            self._drop(node)
        self.nodes = {}
        for node_id, spec in graph["nodes"].items():
            if node_id == G.LAYER:
                node = _Node(self, node_id, "Layer")
                node.mark_end("layer")
                node.add_source()
            elif node_id == G.OUTPUT:
                node = _Node(self, node_id, "Output")
                node.mark_end("output")
                node.add_sink("input")
            else:
                info = gn_ops.catalog().get(spec["op"])
                if info is None:
                    self.say("Not installed, left out: %s" % spec["op"])
                    continue
                node = _OpNode(self, node_id, info, spec.get("props", {}))
            self._place(node, spec.get("x", 0), spec.get("y", 0))
        for link in graph["links"]:
            self._connect(link["from"], link["to"], link["pad"])
        self._loading = False

    def _connect(self, src, dst, pad):
        if src in self.nodes and dst in self.nodes and pad in self.nodes[dst].gn_sinks:
            self.nodes[dst].gn_sinks[pad].connect_sockets(self.nodes[src].gn_source)

    def graph(self):
        """The graph as shown now."""
        graph = {"version": G.VERSION, "nodes": {}, "links": []}
        for node_id, node in self.nodes.items():
            spec = {"op": getattr(node, "gn_op", None),
                    "x": self.view.child_get_property(node, "x"),
                    "y": self.view.child_get_property(node, "y"),
                    "props": dict(getattr(node, "gn_props", {}))}
            graph["nodes"][node_id] = spec
            for pad, socket in node.gn_sinks.items():
                source = socket.get_input()
                if source is not None and getattr(source, "gn_node", None) in self.nodes:
                    graph["links"].append({"from": source.gn_node, "to": node_id, "pad": pad})
        return graph

    # automatic layout

    GAP_X, GAP_Y, TOP, LEFT = 60, 24, 20, 20

    def _size(self, node):
        w, h = node.get_allocated_width(), node.get_allocated_height()
        if w <= 1 or h <= 1:
            w, h = node.get_preferred_width()[1], node.get_preferred_height()[1]
        return w, h

    def arrange(self):
        """Places the nodes in columns: each node to the right of all the
        nodes that feed it, the layer first and the output last; within a
        column, each node as near the height of what feeds it as there is
        room for, in the order that crosses the fewest links."""
        graph = self.graph()
        feeds = {k: [] for k in self.nodes}  # node: the nodes feeding it
        for link in graph["links"]:
            if link["from"] in feeds and link["to"] in feeds:
                feeds[link["to"]].append(link["from"])
        column = {}

        def col(k, depth=0):
            if k not in column:
                if depth > len(self.nodes):  # a loop
                    return 0
                column[k] = 1 + max((col(f, depth + 1) for f in feeds[k]), default=-1)
            return column[k]
        for k in self.nodes:
            col(k)
        column[G.LAYER] = 0
        last = max([c for k, c in column.items() if k != G.OUTPUT] + [0]) + 1
        if G.OUTPUT in column:
            column[G.OUTPUT] = max(last, column[G.OUTPUT])
        columns = {}
        for k in sorted(self.nodes, key=lambda k: self.view.child_get_property(self.nodes[k], "y")):
            columns.setdefault(column[k], []).append(k)

        size = {k: self._size(n) for k, n in self.nodes.items()}
        place = {}
        x = self.LEFT
        for c in sorted(columns):
            keys = columns[c]

            def wanted(k):
                ys = [place[f][1] + size[f][1] / 2 for f in feeds[k] if f in place]
                return sum(ys) / len(ys) - size[k][1] / 2 if ys else None
            # nodes fed from above first, unfed ones after, in their order
            keys.sort(key=lambda k: (wanted(k) is None, wanted(k) or 0))
            # a link that passes this column (from a column before it to
            # one after it) keeps its lane: the nodes here go below it
            y = self.TOP
            for k2, fs in feeds.items():
                if column[k2] > c:
                    for f in fs:
                        if f in place and column[f] < c:
                            y = max(y, place[f][1] + size[f][1] + self.GAP_Y)
            for k in keys:
                w = wanted(k)
                y = max(y, int(w) if w is not None else y)
                place[k] = (x, y)
                y += size[k][1] + self.GAP_Y
            x += max(size[k][0] for k in keys) + self.GAP_X

        self._loading = True
        for k, (x, y) in place.items():
            self.view.child_set_property(self.nodes[k], "x", int(x))
            self.view.child_set_property(self.nodes[k], "y", int(y))
        self._loading = False
        self.view.queue_resize()
        self.view.queue_draw()
        self.emit("moved")
        return place

    # what a user does, also for the tests

    def add_op(self, name, x=300, y=40):
        current = self.graph()
        node_id = G.new_id(current)
        self._place(_OpNode(self, node_id, gn_ops.catalog()[name], {}), x, y)
        self.changed()
        return node_id

    def link(self, src, dst, pad="input"):
        self._connect(src, dst, pad)

    def unlink(self, dst, pad="input"):
        self.nodes[dst].gn_sinks[pad].disconnect()

    def set_value(self, node_id, name, value):
        self.nodes[node_id].set_value(name, value)

    def remove(self, node_id):
        node = self.nodes.get(node_id)
        if node is not None:
            self._drop(node)
            del self.nodes[node_id]
            self.changed()
