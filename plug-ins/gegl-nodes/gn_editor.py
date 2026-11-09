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
        for signal in ("node-socket-connect", "node-socket-disconnect"):
            self.connect(signal, lambda *a: editor.changed())

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
        self.search = Gtk.SearchEntry(placeholder_text="Add a node: type a filter name",
                                      width_chars=36)
        store = Gtk.ListStore(str, str)
        for info in sorted(gn_ops.catalog().values(), key=lambda i: i.title.lower()):
            store.append(["%s  (%s)" % (info.title, info.name), info.name])
        completion = Gtk.EntryCompletion(model=store, text_column=0, minimum_key_length=1,
                                         popup_set_width=False)
        completion.set_match_func(self._match, None)
        completion.connect("match-selected", self._picked)
        self.search.set_completion(completion)
        bar.pack_start(self.search, False, False, 0)
        self.status = Gtk.Label(xalign=0.0, ellipsize=3)
        bar.pack_start(self.status, True, True, 0)
        self.pack_start(bar, False, False, 0)

        self.view = GtkNodes.NodeView()
        self.view.connect("node-drag-end", lambda *a: self.emit("moved"))
        scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroller.add(self.view)
        self.pack_start(scroller, True, True, 0)
        self.load(graph or G.new_graph())

    @staticmethod
    def _match(completion, key, it, data):
        text = completion.get_model()[it][0].lower()
        return all(word in text for word in key.lower().split())

    def _picked(self, completion, model, it):
        name = model[it][1]
        GLib.idle_add(lambda: self.search.set_text("") or False)
        x = 40 + 30 * (len(self.nodes) % 8)
        self.add_op(name, 240 + x, 20 + x)
        return True

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
                node.add_source()
            elif node_id == G.OUTPUT:
                node = _Node(self, node_id, "Output")
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
