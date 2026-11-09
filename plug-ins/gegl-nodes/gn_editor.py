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
import os

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Gegl", "0.4")
gi.require_version("GtkNodes", "0.1")
from gi.repository import Gdk, Gegl, GLib, GObject, Gtk, GtkNodes, Pango  # noqa: E402

import gn_graph as G  # noqa: E402
import gn_ops  # noqa: E402

IMAGE_KEY = 1  # all sockets carry images: any output fits any input
# zoom levels; 1 is the theme's own size
ZOOMS = (0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.15, 1.3, 1.5)
GRID = 20  # the canvas grid at zoom 1, a stronger line every fifth
SOCKET_RADIUS = 8.0  # GtkNodes' own
SELECTED = (0.96, 0.63, 0.20)  # the outline of a selected node, as Blender's
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
/* square menus (see _menu_widget) */
menu.gegl-nodes-menu, .csd menu.gegl-nodes-menu { border-radius: 0; }
menu.gegl-nodes-menu menuitem.gegl-nodes-heading { padding-top: 6px; }
menu.gegl-nodes-menu menuitem.gegl-nodes-heading label { color: alpha(currentColor, 0.75); }
/* the menus and the tool bar above the canvas, set apart from it */
.gegl-nodes-top {
    background-color: alpha(currentColor, 0.06);
    border-bottom: 1px solid alpha(black, 0.45);
}
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


_zoom_provider = None


def _zoom_css(widget, zoom):
    """Text and widgets in the nodes at the zoom: the theme's font size
    times the zoom, and at other zooms than 1 the heights of the entries
    and buttons scaled from the theme's usual 24 px."""
    global _zoom_provider
    screen = widget.get_screen()
    if _zoom_provider is None:
        _zoom_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(
            screen, _zoom_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2)
    font = widget.get_style_context().get_property("font", Gtk.StateFlags.NORMAL)
    size = font.get_size() / Pango.SCALE if font and font.get_size() else 10.0
    if font and font.get_size_is_absolute():
        size *= 0.75  # px to pt
    css = (".gegl-nodes-node, .gegl-nodes-node * { font-size: %.2fpt; }\n"
           ".gegl-nodes-node .gegl-nodes-badge { font-size: %.2fpt; }\n"
           % (size * zoom, size * zoom * 0.83))
    if zoom != 1.0:
        h = round(24 * zoom)
        css += (".gegl-nodes-node entry, .gegl-nodes-node spinbutton,"
                " .gegl-nodes-node button, .gegl-nodes-node combobox box {"
                " min-height: %dpx; padding-top: 0; padding-bottom: 0; }\n"
                ".gegl-nodes-node spinbutton button { min-width: %dpx; padding: 0; }\n"
                ".gegl-nodes-node check { min-width: %dpx; min-height: %dpx; }\n"
                % (max(h, 8), max(round(20 * zoom), 8), max(round(14 * zoom), 6),
                   max(round(14 * zoom), 6)))
    _zoom_provider.load_from_data(css.encode())


def _menu_widget():
    """A menu with square corners: themes such as Yaru round a menu by
    8 px, but its window stays square, and the window's dark fill showed
    at the corners."""
    menu = Gtk.Menu()
    menu.get_style_context().add_class("gegl-nodes-menu")
    return menu


def _icon_button(cls, label, icon, tooltip):
    """A tool bar button: the icon (when the icon theme has it) and the
    label."""
    button = cls(tooltip_text=tooltip)
    box = Gtk.Box(spacing=4)
    if Gtk.IconTheme.get_default().has_icon(icon):
        box.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.SMALL_TOOLBAR),
                       False, False, 0)
    if label:
        box.pack_start(Gtk.Label(label=label), False, False, 0)
    button.add(box)
    button.gn_label = label
    return button


def _theme_bg(widget):
    context = widget.get_style_context()
    for name in ("bg-color", "theme_bg_color"):  # GIMP's theme, GTK's own
        found, color = context.lookup_color(name)
        if found:
            return color
    return _rgba("rgb(60,60,60)")


def _shade(color, factor):
    return (color.red * factor, color.green * factor, color.blue * factor)


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
        self.gn_pos = [0.0, 0.0]  # where the node is at zoom 1
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
            values = G.enum_values(pspec)
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


def default_setups_dir():
    """Where saved setups go when the plug-in does not say (it uses
    GIMP's profile folder): $GEGL_NODES_SETUPS, or the user's config
    folder."""
    return os.environ.get("GEGL_NODES_SETUPS") or os.path.join(
        GLib.get_user_config_dir(), "gegl-nodes", "setups")


class GraphEditor(Gtk.Box):
    """The editor: a bar with a search field to add operations, and the
    nodes. Emits "changed" when the graph changes (a link, a node, a
    value) and "moved" when only the places of the nodes change."""
    __gtype_name__ = "GeglNodesEditor"
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
                    "moved": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, graph=None, setups_dir=None):
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
        add = _icon_button(Gtk.MenuButton, "Add", "list-add",
                           "Add a node (Shift+A, or right-click on the canvas)")
        self._add_at = None
        add.set_popup(self._menu())
        bar.pack_start(add, False, False, 0)
        arrange = _icon_button(Gtk.Button, "Arrange", "gimp-tool-align",
                               "Lay the nodes out from the layer to the output, in columns "
                               "(only the selected ones, when two or more are)")
        arrange.connect("clicked", lambda b: self.arrange(
            self.selection if len(self.selection) > 1 else None))
        bar.pack_start(arrange, False, False, 0)
        self.snap = True
        snap = _icon_button(Gtk.ToggleButton, "Snap", "gimp-grid",
                            "Snap the nodes to the grid when they are moved, added or "
                            "arranged")
        snap.set_active(True)
        snap.connect("toggled", lambda b: setattr(self, "snap", b.get_active()))
        self.snap_button = snap
        bar.pack_start(snap, False, False, 0)
        self.setups_button = _icon_button(Gtk.MenuButton, "Setups", "document-save",
                                          "Save this graph as a setup, or use a saved one")
        self.setups_button.set_popup(Gtk.Menu())
        self.setups_button.connect("toggled", self._setups_opening)
        bar.pack_start(self.setups_button, False, False, 0)
        bar.pack_start(self.search, False, False, 0)
        zoom_box = Gtk.Box()
        zoom_box.get_style_context().add_class("linked")
        out = _icon_button(Gtk.Button, None, "zoom-out", "Zoom out (Ctrl+minus, Ctrl+wheel)")
        if not out.get_child().get_children():
            out.get_child().add(Gtk.Label(label="\u2212"))
        out.connect("clicked", lambda b: self.zoom_step(-1))
        self.zoom_label = Gtk.Button(label="100%", tooltip_text="Zoom to 100% (Ctrl+0)")
        self.zoom_label.connect("clicked", lambda b: self.set_zoom(1.0))
        into = _icon_button(Gtk.Button, None, "zoom-in", "Zoom in (Ctrl+plus, Ctrl+wheel)")
        if not into.get_child().get_children():
            into.get_child().add(Gtk.Label(label="+"))
        into.connect("clicked", lambda b: self.zoom_step(1))
        fit = _icon_button(Gtk.Button, None, "zoom-fit-best", "Show all the nodes")
        if not fit.get_child().get_children():
            fit.get_child().add(Gtk.Label(label="Fit"))
        fit.connect("clicked", lambda b: self.zoom_to_fit())
        for b in (out, self.zoom_label, into, fit):
            zoom_box.pack_start(b, False, False, 0)
        self.status = Gtk.Label(xalign=0.0, ellipsize=3)
        bar.pack_start(self.status, True, True, 0)
        bar.pack_end(zoom_box, False, False, 0)
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        top.get_style_context().add_class("gegl-nodes-top")
        top.pack_start(self._menubar(), False, False, 0)
        top.pack_start(bar, False, False, 0)
        self.pack_start(top, False, False, 0)

        self.zoom = 1.0
        self.setups_dir = setups_dir or default_setups_dir()
        self._pan = None
        self.selection = set()  # ids of the selected nodes
        # where the graph's 0, 0 is on the canvas: the canvas reaches a
        # window's size past the nodes on every side, and grows (see
        # _fit_canvas), so the nodes can go anywhere
        self.origin = [0, 0]
        self._view_set = False  # the view not yet put on the nodes
        self._box = None  # a box being drawn to select: x0, y0, x1, y1, adding
        self._drag = None  # a node dragged: its id, and where all were then
        self._moving = False
        self.view = GtkNodes.NodeView()
        self.view.connect("node-drag-begin", self._drag_begin)
        self.view.connect("node-drag-end", self._drag_end)
        self.view.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                             | Gdk.EventMask.BUTTON1_MOTION_MASK
                             | Gdk.EventMask.BUTTON2_MOTION_MASK)
        self.view.connect("button-press-event", self._pressed)
        self.view.connect("button-release-event", self._released)
        self.view.connect("motion-notify-event", self._motion)
        # (before GtkNodes draws the links and the nodes, and after)
        self.view.connect("draw", self._draw_canvas)
        self.view.connect_after("draw", self._draw_selection)
        self.connect("hierarchy-changed", self._toplevel)
        self.scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        self.scroller.add(self.view)
        self.scroller.connect("scroll-event", self._scrolled)
        self.scroller.connect("size-allocate", lambda *a: self._fit_canvas())
        self.pack_start(self.scroller, True, True, 0)
        _zoom_css(self, self.zoom)
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
        if self.snap:
            x, y = self.snapped(x), self.snapped(y)
        self.add_op(name, x, y)

    @staticmethod
    def groups():
        """The operations offered, by menu: {menu: [infos]}."""
        return {title: [i for ops in headings.values() for i in ops]
                for title, headings in gn_ops.offered_groups().items()}

    def _ops_menu(self, title):
        """A menu's operations, under a heading for each GEGL category in
        it when there is more than one."""
        sub = _menu_widget()
        headings = gn_ops.offered_groups()[title]
        for heading, ops in headings.items():
            if len(headings) > 1:
                label = Gtk.Label(xalign=0.0)
                label.set_markup("<small><b>%s</b></small>" % GLib.markup_escape_text(
                    "More" if heading == "more" else heading.replace("-", " ").capitalize()))
                item = Gtk.MenuItem()
                item.add(label)
                item.set_sensitive(False)
                item.get_style_context().add_class("gegl-nodes-heading")
                sub.append(item)
            for info in ops:
                op = Gtk.MenuItem(label=info.title)
                op.set_tooltip_text("%s\n%s" % (info.name, info.description))
                op.connect("activate", lambda w, name=info.name: self._add_picked(name))
                op.gn_op = info.name
                sub.append(op)
        return sub

    def _menu(self):
        """All operations offered, by category, as in Blender's Add menu."""
        menu = _menu_widget()
        for title in self.groups():
            item = Gtk.MenuItem(label=title)
            item.set_submenu(self._ops_menu(title))
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
        for title in self.groups():
            menu = self._ops_menu(title)
            menu.show_all()
            button = Gtk.MenuButton(label=title,
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

    def _node_at(self, x, y):
        """The id of the node at x, y on the canvas, or None."""
        for node_id, node in self.nodes.items():
            nx = self._pos(node)[0]
            ny = self._pos(node)[1]
            if nx <= x < nx + node.get_allocated_width() and \
               ny <= y < ny + node.get_allocated_height():
                return node_id
        return None

    def _on_node(self, x, y):
        return self._node_at(x, y) is not None

    def popup_add(self, event=None):
        """The Add menu at the pointer; the node goes where the pointer is."""
        x, y = self._pointer()
        self._add_at = ((x - 20 - self.origin[0]) / self.zoom,
                        (y - 10 - self.origin[1]) / self.zoom)
        self._popup.popup_at_pointer(event)

    def _pressed(self, view, event):
        if event.button == Gdk.BUTTON_MIDDLE:
            # middle-drag moves the canvas, as in Blender
            self._pan = (event.x_root, event.y_root, self.scroller.get_hadjustment().get_value(),
                         self.scroller.get_vadjustment().get_value())
            return True
        x, y = self._pointer()
        shift = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
        # a click on the canvas or a node's frame takes the keys from the
        # search field (for Shift+A, Delete, ...), as in Blender
        top = self.get_toplevel()
        if isinstance(top, Gtk.Window):
            top.set_focus(None)
        on = self._node_at(x, y)
        if on is not None:
            if event.button == Gdk.BUTTON_PRIMARY:
                # (GtkNodes has begun dragging the node already; the drag
                # takes the selection as it is after this)
                if shift:
                    self.select([on], toggle=True)
                elif on not in self.selection:
                    self.select([on])
            return False
        if event.button == Gdk.BUTTON_PRIMARY:
            self._box = [x, y, x, y, shift]
            return True
        if event.button != Gdk.BUTTON_SECONDARY:
            return False
        self.popup_add(event)
        return True

    def _released(self, view, event):
        if event.button == Gdk.BUTTON_MIDDLE and self._pan:
            self._pan = None
            return True
        if event.button == Gdk.BUTTON_PRIMARY and self._box:
            x0, y0, x1, y1, adding = self._box
            self._box = None
            self.box_select(x0, y0, x1, y1, adding)
            return True
        return False

    def _motion(self, view, event):
        if self._pan:
            x0, y0, h, v = self._pan
            self.scroller.get_hadjustment().set_value(h - (event.x_root - x0))
            self.scroller.get_vadjustment().set_value(v - (event.y_root - y0))
            return True
        if self._box:
            self._box[2], self._box[3] = self._pointer()
            self.view.queue_draw()
            return True
        return False

    # selection

    def select(self, ids, add=False, toggle=False):
        """Selects the nodes ids: instead of the selection, added to it,
        or each one in or out of it."""
        ids = [k for k in ids if k in self.nodes]
        if toggle:
            self.selection ^= set(ids)
        elif add:
            self.selection |= set(ids)
        else:
            self.selection = set(ids)
        self.view.queue_draw()
        n = len(self.selection)
        self.say("%d nodes selected" % n if n > 1 else "")

    def box_select(self, x0, y0, x1, y1, add=False):
        """Selects the nodes the box (on the canvas, at the zoom) touches.
        A click without a box clears the selection (unless adding)."""
        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))
        hit = []
        for node_id, node in self.nodes.items():
            nx = self._pos(node)[0]
            ny = self._pos(node)[1]
            w, h = self._size(node)
            if nx < right and left < nx + w and ny < bottom and top < ny + h:
                hit.append(node_id)
        self.select(hit, add=add)

    def _drag_begin(self, view, node):
        self._drag = (node.gn_id, {k: (self._pos(n)[0],
                                       self._pos(n)[1])
                                   for k, n in self.nodes.items()})

    def _dragged(self, node, pspec):
        """While a selected node is dragged, the others selected move with
        it (GtkNodes moves only the one dragged). Called when its y is set:
        GtkNodes sets x, then y, at each move."""
        if self._moving or not self._drag or node.gn_id != self._drag[0]:
            return
        x, y = self._pos(node)
        # near the right or bottom end of the canvas: it grows (GtkNodes
        # keeps a node inside the canvas); the left and top grow when the
        # drag ends
        w, h = self.view.get_size_request()
        right, bottom = x + node.get_allocated_width(), y + node.get_allocated_height()
        if right > w - 40 or bottom > h - 40:
            self.view.set_size_request(max(w, right + 400), max(h, bottom + 400))
        if node.gn_id in self.selection:
            self._move_others(node, x, y)

    def _move_others(self, node, x, y):
        start = self._drag[1]
        dx, dy = x - start[node.gn_id][0], y - start[node.gn_id][1]
        self._moving = True
        for k in self.selection:
            if k != node.gn_id and k in self.nodes and k in start:
                other = self.nodes[k]
                self.view.child_set_property(other, "x", start[k][0] + dx)
                self.view.child_set_property(other, "y", start[k][1] + dy)
        self._moving = False

    def _drag_end(self, view, node):
        # snapped when the drag ends: GtkNodes moves a node by the pointer's
        # offset from where its window was drawn last, so a node snapped
        # during the drag drifted further at each move
        if self.snap and self._drag and node.gn_id == self._drag[0]:
            x, y = self._pos(node)
            z, (ox, oy) = self.zoom, self.origin
            wx, wy = self.snapped((x - ox) / z), self.snapped((y - oy) / z)
            sx, sy = int(round(wx * z)) + ox, int(round(wy * z)) + oy
            self._moving = True
            self.view.child_set_property(node, "x", sx)
            self.view.child_set_property(node, "y", sy)
            self._moving = False
            # (kept exactly: read back from the canvas at other zooms than
            # 1 it would come back rounded)
            node.gn_pos = [float(wx), float(wy)]
            if node.gn_id in self.selection:
                self._move_others(node, sx, sy)
        self._drag = None
        self._fit_canvas()
        self.emit("moved")

    def _pos(self, node):
        """Where the node is on the canvas: its own x and y, which GtkNodes
        sets whenever it moves the node. (GtkNodes' child properties x and
        y are not a plain read: they copy the node's x and y back into
        GtkNodes' place for the node, and read halfway through a move,
        between x and y, that undid the move's y.)"""
        return node.get_property("x"), node.get_property("y")

    def _draw_selection(self, view, cr):
        """The outlines of the selected nodes and the box being drawn, over
        the nodes."""
        cr.save()
        cr.set_line_width(2)
        cr.set_source_rgb(*SELECTED)
        r = SOCKET_RADIUS * self.zoom
        for k in self.selection:
            node = self.nodes.get(k)
            if node is None:
                continue
            a = node.get_allocation()  # (as for the fills)
            x, y = a.x + r - 1, a.y + r - 1
            w, h = a.width - 2 * r + 2, a.height - 2 * r + 2
            if w > 0 and h > 0:
                cr.rectangle(x, y, w, h)
        cr.stroke()
        if self._box:
            x0, y0, x1, y1, _ = self._box
            cr.rectangle(min(x0, x1) + 0.5, min(y0, y1) + 0.5, abs(x1 - x0), abs(y1 - y0))
            cr.set_source_rgba(SELECTED[0], SELECTED[1], SELECTED[2], 0.12)
            cr.fill_preserve()
            cr.set_line_width(1)
            cr.set_source_rgba(SELECTED[0], SELECTED[1], SELECTED[2], 0.8)
            cr.stroke()
        cr.restore()
        return False

    def delete_selected(self):
        """Removes the selected nodes (never the layer or the output)."""
        gone = [k for k in self.selection if k not in (G.LAYER, G.OUTPUT)]
        for k in gone:
            node = self.nodes.pop(k, None)
            if node is not None:
                self._drop(node)
        self.selection -= set(gone)
        self.view.queue_draw()
        if gone:
            self.changed()
            self.say("%d removed" % len(gone) if len(gone) > 1 else "")
        return gone

    def duplicate_selected(self):
        """Copies of the selected nodes, offset a little, with their values
        and the links between them; the copies become the selection."""
        ids = [k for k in self.selection if k not in (G.LAYER, G.OUTPUT)]
        if not ids:
            return []
        graph, copies = G.duplicate(self.graph(), ids, 40, 40)
        self.load(graph)
        self.select(copies)
        self.changed()
        return copies

    def save_selection(self, name):
        """Saves only the selected nodes as a setup (see G.subgraph)."""
        ids = [k for k in self.selection if k not in (G.LAYER, G.OUTPUT)]
        if not ids:
            self.say("Not saved: no nodes selected")
            return False
        try:
            G.save_setup(self.setups_dir, name, G.subgraph(self.graph(), ids))
        except (G.GraphError, OSError) as e:
            self.say("Not saved: %s" % e)
            return False
        self.say("Saved %d nodes as setup %s" % (len(ids), name.strip()))
        return True

    def _scrolled(self, scroller, event):
        if not event.state & Gdk.ModifierType.CONTROL_MASK:
            return False
        if event.direction == Gdk.ScrollDirection.SMOOTH:
            dy = event.get_scroll_deltas()[2]
            if dy == 0:
                return True
            step = -1 if dy > 0 else 1
        elif event.direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.DOWN):
            step = 1 if event.direction == Gdk.ScrollDirection.UP else -1
        else:
            return False
        self.zoom_step(step, around=self._pointer())
        return True

    # zoom

    def zoom_step(self, step, around=None):
        i = min(range(len(ZOOMS)), key=lambda i: abs(ZOOMS[i] - self.zoom))
        self.set_zoom(ZOOMS[min(max(i + step, 0), len(ZOOMS) - 1)], around)

    def set_zoom(self, zoom, around=None):
        """Shows the nodes at the zoom: their text and widgets scaled, and
        their places. around is the point of the canvas that stays where
        it is on screen (the pointer, for the wheel); else the middle."""
        if zoom == self.zoom:
            return
        h, v = self.scroller.get_hadjustment(), self.scroller.get_vadjustment()
        if around is None:
            around = (h.get_value() + h.get_page_size() / 2, v.get_value() + v.get_page_size() / 2)
        on_screen = (around[0] - h.get_value(), around[1] - v.get_value())
        # (the point in the graph's coordinates)
        at = ((around[0] - self.origin[0]) / self.zoom, (around[1] - self.origin[1]) / self.zoom)
        for node in self.nodes.values():
            self._world(node)
        self.zoom = zoom
        _zoom_css(self, zoom)
        self._loading = True
        for node in self.nodes.values():
            node.set_socket_radius(SOCKET_RADIUS * zoom)
            self._show_at(node)
        self._loading = False
        self.zoom_label.set_label("%d%%" % round(zoom * 100))
        self._fit_canvas(scroll=False)
        self.view.queue_resize()
        self.view.queue_draw()
        self._scroll_to(at[0] * zoom + self.origin[0] - on_screen[0],
                        at[1] * zoom + self.origin[1] - on_screen[1])

    def zoom_to_fit(self):
        """The largest zoom that shows all the nodes, at most 100%."""
        if not self.nodes:
            return
        left, top, right, bottom = self._extent()
        h, v = self.scroller.get_hadjustment(), self.scroller.get_vadjustment()
        fits = min(h.get_page_size() / (right - left + 40), v.get_page_size() / (bottom - top + 40),
                   1.0)
        zoom = max([z for z in ZOOMS if z <= fits] or [ZOOMS[0]])
        self.set_zoom(zoom)
        self.show_nodes()

    def _extent(self):
        """left, top, right, bottom of the nodes, in the graph's
        coordinates."""
        boxes = []
        for node in self.nodes.values():
            w, h = self._size(node)
            x, y = self._world(node)
            boxes.append((x, y, x + w / self.zoom, y + h / self.zoom))
        if not boxes:
            return 0, 0, 0, 0
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    def _scroll_to(self, x, y):
        h, v = self.scroller.get_hadjustment(), self.scroller.get_vadjustment()

        def go():
            h.set_value(x)
            v.set_value(y)
            return False
        # (after the canvas has its new size)
        GLib.idle_add(go, priority=GLib.PRIORITY_DEFAULT_IDLE + 10)

    def show_nodes(self):
        """Scrolls to the nodes: centred when they fit, else from their
        top left."""
        left, top, right, bottom = self._extent()
        h, v = self.scroller.get_hadjustment(), self.scroller.get_vadjustment()
        z, (ox, oy) = self.zoom, self.origin
        pw, ph = h.get_page_size(), v.get_page_size()
        w, hh = (right - left) * z, (bottom - top) * z
        x = left * z + ox - (pw - w) / 2 if w < pw else left * z + ox - 20
        y = top * z + oy - (ph - hh) / 2 if hh < ph else top * z + oy - 20
        self._scroll_to(x, y)

    def _fit_canvas(self, scroll=True):
        """Makes the canvas reach about a window's size past the nodes on
        every side. The room on the left and at the top is changed only
        when it is less than half a window or more than three (so that
        resizing the window moves nothing); then the nodes and the view
        move with it, so nothing moves on screen."""
        h, v = self.scroller.get_hadjustment(), self.scroller.get_vadjustment()
        pw, ph = max(h.get_page_size(), 300), max(v.get_page_size(), 200)
        left, top, right, bottom = self._extent()
        z = self.zoom
        ox, oy = self.origin
        if not pw / 2 <= ox + left * z <= 3 * pw:
            ox = int(pw - left * z)
        if not ph / 2 <= oy + top * z <= 3 * ph:
            oy = int(ph - top * z)
        dx, dy = ox - self.origin[0], oy - self.origin[1]
        width, height = int(ox + right * z + pw), int(oy + bottom * z + ph)
        if (dx, dy) != (0, 0):
            for node in self.nodes.values():
                self._world(node)
            self.origin = [ox, oy]
            self._loading = True
            for node in self.nodes.values():
                self._show_at(node)
            self._loading = False
            if scroll and self._view_set:
                self._scroll_to(h.get_value() + dx, v.get_value() + dy)
        if self.view.get_size_request() != (width, height):
            self.view.set_size_request(width, height)
        if not self._view_set and h.get_page_size() > 1:
            self._view_set = True
            self.show_nodes()

    @staticmethod
    def snapped(v, up=False):
        """A place in the graph with the node's frame on the grid (the
        frame is a socket radius inside the node)."""
        r = SOCKET_RADIUS
        f = math.ceil if up else round
        return int(f((v + r) / GRID) * GRID - r)

    def _shown(self, node):
        """Where the node goes on the canvas."""
        return (int(round(node.gn_pos[0] * self.zoom)) + self.origin[0],
                int(round(node.gn_pos[1] * self.zoom)) + self.origin[1])

    def _show_at(self, node):
        x, y = self._shown(node)
        self.view.child_set_property(node, "x", x)
        self.view.child_set_property(node, "y", y)

    def _world(self, node):
        """Where the node is in the graph (at zoom 1): kept, unless it was
        dragged since."""
        x = self._pos(node)[0]
        y = self._pos(node)[1]
        sx, sy = self._shown(node)
        if x != sx:
            node.gn_pos[0] = (x - self.origin[0]) / self.zoom
        if y != sy:
            node.gn_pos[1] = (y - self.origin[1]) / self.zoom
        return int(round(node.gn_pos[0])), int(round(node.gn_pos[1]))

    # the canvas

    def _draw_canvas(self, view, cr):
        """A canvas darker than the theme's background, with a grid, and
        each node filled with the theme's background, so that the nodes
        stand out. (GtkNodes draws a node's frame in a button's style,
        which GIMP's theme leaves without a fill.)"""
        cr.save()  # (GtkNodes draws the links with what is left set)
        bg = _theme_bg(self)
        light = (bg.red + bg.green + bg.blue) / 3 > 0.5
        canvas = _shade(bg, 0.86 if light else 0.62)
        width, height = view.get_allocated_width(), view.get_allocated_height()
        cr.set_source_rgb(*canvas)
        cr.paint()
        step = GRID * self.zoom
        cr.set_line_width(1)
        # (lines at the graph's own grid, wherever its 0, 0 is)
        first_x, first_y = -math.floor(self.origin[0] / step), -math.floor(self.origin[1] / step)
        for major in (False, True):
            f = (0.78 if major else 0.88) if light else (0.66 if major else 0.82)
            cr.set_source_rgb(*(c * f for c in canvas))
            i = first_x
            while self.origin[0] + i * step <= width:
                if (i % 5 == 0) == major:
                    x = math.floor(self.origin[0] + i * step) + 0.5
                    cr.move_to(x, 0)
                    cr.line_to(x, height)
                i += 1
            i = first_y
            while self.origin[1] + i * step <= height:
                if (i % 5 == 0) == major:
                    y = math.floor(self.origin[1] + i * step) + 0.5
                    cr.move_to(0, y)
                    cr.line_to(width, y)
                i += 1
            cr.stroke()
        cr.set_source_rgb(bg.red, bg.green, bg.blue)
        r = SOCKET_RADIUS * self.zoom
        corner = 4
        for node in self.nodes.values():
            # (where GTK has the node now, which is where it is drawn; its
            # x and y may be newer until the next layout)
            a = node.get_allocation()
            x, y = a.x + r, a.y + r
            w, h = a.width - 2 * r, a.height - 2 * r
            if w <= 0 or h <= 0:
                continue
            cr.new_sub_path()
            cr.arc(x + w - corner, y + corner, corner, -math.pi / 2, 0)
            cr.arc(x + w - corner, y + h - corner, corner, 0, math.pi / 2)
            cr.arc(x + corner, y + h - corner, corner, math.pi / 2, math.pi)
            cr.arc(x + corner, y + corner, corner, math.pi, 3 * math.pi / 2)
            cr.close_path()
        cr.fill()
        cr.restore()
        return False

    # saved setups

    def _setups_opening(self, button):
        if button.get_active():
            button.set_popup(self._setups_menu())

    def _setups_menu(self):
        menu = _menu_widget()
        save = Gtk.MenuItem(label="Save Setup...")
        save.connect("activate", lambda i: self._ask_save())
        menu.append(save)
        part = Gtk.MenuItem(label="Save Selection as Setup...")
        part.set_tooltip_text("Only the selected nodes; what fed them comes from the layer")
        part.set_sensitive(bool(self.selection - {G.LAYER, G.OUTPUT}))
        part.connect("activate", lambda i: self._ask_save(selection=True))
        menu.append(part)
        names = G.setups(self.setups_dir)
        if names:
            menu.append(Gtk.SeparatorMenuItem())
        for name in names:
            sub = _menu_widget()
            for label, tip, act in (
                    ("Insert", "Add it at the end of this graph, before the output",
                     lambda i, n=name: self.insert_setup(n)),
                    ("Replace", "Show it instead of this graph",
                     lambda i, n=name: self._ask_replace(n)),
                    ("Rename...", None, lambda i, n=name: self._ask_rename(n)),
                    ("Delete...", None, lambda i, n=name: self._ask_delete(n))):
                item = Gtk.MenuItem(label=label)
                if tip:
                    item.set_tooltip_text(tip)
                item.connect("activate", act)
                sub.append(item)
            item = Gtk.MenuItem(label=name)
            item.set_submenu(sub)
            menu.append(item)
        if not names:
            none = Gtk.MenuItem(label="No saved setups yet")
            none.set_sensitive(False)
            menu.append(none)
        menu.append(Gtk.SeparatorMenuItem())
        where = Gtk.MenuItem(label="Open the Setups Folder")
        where.set_tooltip_text(self.setups_dir)
        where.connect("activate", lambda i: self._open_folder())
        menu.append(where)
        menu.show_all()
        return menu

    def save_setup(self, name):
        try:
            G.save_setup(self.setups_dir, name, self.graph())
        except (G.GraphError, OSError) as e:
            self.say("Not saved: %s" % e)
            return False
        self.say("Saved as setup %s" % name.strip())
        return True

    def _read(self, name):
        try:
            return G.read_setup(self.setups_dir, name)
        except G.GraphError as e:
            self.say(str(e))
            return None

    def replace_with_setup(self, name):
        setup = self._read(name)
        if setup is None:
            return False
        self.load(setup)
        self.changed()
        self.say("Setup %s" % name)
        return True

    def insert_setup(self, name):
        setup = self._read(name)
        if setup is None:
            return False
        self.load(G.insert(self.graph(), setup))
        self.changed()
        self.say("Setup %s added at the end" % name)
        return True

    def rename_setup(self, old, new):
        try:
            src, dst = G.setup_path(self.setups_dir, old), G.setup_path(self.setups_dir, new)
            if os.path.exists(dst):
                raise G.GraphError("there is a setup called %s already" % new.strip())
            os.rename(src, dst)
        except (G.GraphError, OSError) as e:
            self.say("Not renamed: %s" % e)
            return False
        return True

    def delete_setup(self, name):
        try:
            os.remove(G.setup_path(self.setups_dir, name))
        except (G.GraphError, OSError) as e:
            self.say("Not deleted: %s" % e)
            return False
        self.say("Setup %s deleted" % name)
        return True

    def _open_folder(self):
        os.makedirs(self.setups_dir, exist_ok=True)
        try:
            Gtk.show_uri_on_window(self.get_toplevel(), GLib.filename_to_uri(self.setups_dir),
                                   Gtk.get_current_event_time())
        except GLib.Error as e:
            self.say("%s (%s)" % (self.setups_dir, e.message))

    def _ask_name(self, title, button, text=""):
        dialog = Gtk.Dialog(title=title, transient_for=self.get_toplevel(), modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, button, Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        entry = Gtk.Entry(text=text, activates_default=True, width_chars=32, margin=12)
        dialog.get_content_area().add(entry)
        dialog.show_all()
        answer = dialog.run()
        name = entry.get_text().strip()
        dialog.destroy()
        return name if answer == Gtk.ResponseType.OK and name else None

    def _confirm(self, text, button):
        dialog = Gtk.MessageDialog(transient_for=self.get_toplevel(), modal=True,
                                   message_type=Gtk.MessageType.QUESTION, text=text)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, button, Gtk.ResponseType.OK)
        answer = dialog.run()
        dialog.destroy()
        return answer == Gtk.ResponseType.OK

    def _ask_save(self, selection=False):
        name = self._ask_name("Save Selection as Setup" if selection else "Save Setup", "Save")
        if not name:
            return
        if name in G.setups(self.setups_dir) and not self._confirm(
                "There is a setup called %s already. Replace it?" % name, "Replace"):
            return
        if selection:
            self.save_selection(name)
        else:
            self.save_setup(name)

    def _ask_replace(self, name):
        if len(self.nodes) > 2 and not self._confirm(
                "Show setup %s instead of this graph? The nodes here go "
                "(save them as a setup first to keep them)." % name, "Replace"):
            return
        self.replace_with_setup(name)

    def _ask_rename(self, name):
        new = self._ask_name("Rename Setup %s" % name, "Rename", name)
        if new and new != name:
            self.rename_setup(name, new)

    def _ask_delete(self, name):
        if self._confirm("Delete setup %s?" % name, "Delete"):
            self.delete_setup(name)

    def _toplevel(self, widget, previous):
        # Shift+A anywhere in the window, also with nothing focused
        top = self.get_toplevel()
        if isinstance(top, Gtk.Window) and not getattr(top, "gn_keys", False):
            top.connect("key-press-event", self._key)
            top.gn_keys = True

    def _key(self, widget, event):
        if event.state & Gdk.ModifierType.CONTROL_MASK:
            if event.keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
                self.zoom_step(1)
                return True
            if event.keyval in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
                self.zoom_step(-1)
                return True
            if event.keyval in (Gdk.KEY_0, Gdk.KEY_KP_0):
                self.set_zoom(1.0)
                return True
        focus = self.get_toplevel().get_focus()
        if isinstance(focus, Gtk.Editable) or (
                focus is not None and focus.get_ancestor(GtkNodes.Node) is not None):
            # (typing in a node's field, or a node's widget has the keys)
            return False
        ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
        key = event.keyval
        if key == Gdk.KEY_A or (key == Gdk.KEY_a and event.state & Gdk.ModifierType.SHIFT_MASK):
            self.popup_add(event)
            return True
        if ctrl and key in (Gdk.KEY_a, Gdk.KEY_A):
            self.select(list(self.nodes))
            return True
        if ctrl and key in (Gdk.KEY_d, Gdk.KEY_D):
            self.duplicate_selected()
            return True
        if not ctrl and key in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete, Gdk.KEY_x, Gdk.KEY_BackSpace):
            self.delete_selected()
            return True
        if key == Gdk.KEY_Escape and self.selection:
            self.select([])
            return True
        return False

    def say(self, text):
        self.status.set_text(text)

    def changed(self):
        if not self._loading:
            self.emit("changed")

    def _place(self, node, x, y):
        """Puts the node on the canvas at x, y (at zoom 1)."""
        node.gn_pos = [float(x), float(y)]
        if self.zoom != 1.0:
            node.set_socket_radius(SOCKET_RADIUS * self.zoom)
        self.view.add(node)
        # the links follow a node that moves or changes size; GTK would
        # otherwise redraw only the node's own area, and leave parts of
        # the old links on the canvas
        node.connect("size-allocate", lambda *a: self.view.queue_draw())
        node.connect("notify::y", self._dragged)
        self._show_at(node)
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
        self.selection &= set(self.nodes)
        self._loading = False
        self._fit_canvas()

    def _connect(self, src, dst, pad):
        if src in self.nodes and dst in self.nodes and pad in self.nodes[dst].gn_sinks:
            self.nodes[dst].gn_sinks[pad].connect_sockets(self.nodes[src].gn_source)

    def graph(self):
        """The graph as shown now."""
        graph = {"version": G.VERSION, "nodes": {}, "links": []}
        for node_id, node in self.nodes.items():
            x, y = self._world(node)
            spec = {"op": getattr(node, "gn_op", None), "x": x, "y": y,
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

    def arrange(self, only=None):
        """Places the nodes in columns: each node to the right of all the
        nodes that feed it, the layer first and the output last; within a
        column, each node as near the height of what feeds it as there is
        room for, in the order that crosses the fewest links. With only
        (ids), just those nodes, from where the top left one of them is;
        the others stay where they are."""
        graph = self.graph()
        keys = [k for k in self.nodes if only is None or k in only]
        if not keys:
            return {}
        feeds = {k: [] for k in keys}  # node: the nodes feeding it
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
        for k in keys:
            col(k)
        if G.LAYER in column:
            column[G.LAYER] = 0
        last = max([c for k, c in column.items() if k != G.OUTPUT] + [0]) + 1
        if G.OUTPUT in column:
            column[G.OUTPUT] = max(last, column[G.OUTPUT])
        columns = {}
        for k in sorted(keys, key=lambda k: self._pos(self.nodes[k])[1]):
            columns.setdefault(column[k], []).append(k)

        size = {k: self._size(self.nodes[k]) for k in keys}
        place = {}
        # (on screen, at the zoom)
        gap_x, gap_y = self.GAP_X * self.zoom, self.GAP_Y * self.zoom
        if only is None:
            # (from where the graph's 0, 0 is)
            left = self.origin[0] + self.LEFT * self.zoom
            top = self.origin[1] + self.TOP * self.zoom
        else:
            left = min(self._pos(self.nodes[k])[0] for k in keys)
            top = min(self._pos(self.nodes[k])[1] for k in keys)
        x = left
        for c in sorted(columns):
            keys = columns[c]

            def wanted(k):
                ys = [place[f][1] + size[f][1] / 2 for f in feeds[k] if f in place]
                return sum(ys) / len(ys) - size[k][1] / 2 if ys else None
            # nodes fed from above first, unfed ones after, in their order
            keys.sort(key=lambda k: (wanted(k) is None, wanted(k) or 0))
            # a link that passes this column (from a column before it to
            # one after it) keeps its lane: the nodes here go below it
            y = top
            for k2, fs in feeds.items():
                if column[k2] > c:
                    for f in fs:
                        if f in place and column[f] < c:
                            y = max(y, place[f][1] + size[f][1] + gap_y)
            for k in keys:
                w = wanted(k)
                y = max(y, int(w) if w is not None else y)
                place[k] = (int(x), int(y))
                y += size[k][1] + gap_y
            x += max(size[k][0] for k in keys) + gap_x

        self._loading = True
        for k, (x, y) in place.items():
            wx, wy = (x - self.origin[0]) / self.zoom, (y - self.origin[1]) / self.zoom
            if self.snap:
                # (up, never onto the gap: the gaps are wider than the grid)
                wx, wy = self.snapped(wx, up=True), self.snapped(wy, up=True)
            self.nodes[k].gn_pos = [wx, wy]
            self._show_at(self.nodes[k])
        self._loading = False
        self._fit_canvas()
        self.view.queue_resize()
        self.view.queue_draw()
        self.emit("moved")
        # (where they are on the canvas now, which may have grown)
        return {k: (self._pos(self.nodes[k])[0],
                    self._pos(self.nodes[k])[1]) for k in place}

    # what a user does, also for the tests

    def add_op(self, name, x=300, y=40):
        info = gn_ops.catalog()[name]
        if not info.usable():
            self.say("%s cannot be used here: it gives no image" % info.title)
            return None
        node_id = G.new_id(self.graph())
        self._place(_OpNode(self, node_id, info, {}), x, y)
        self._fit_canvas()
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
            self.selection.discard(node_id)
            self.changed()
