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
                               "Lay the nodes out from the layer to the output, in columns")
        arrange.connect("clicked", lambda b: self.arrange())
        bar.pack_start(arrange, False, False, 0)
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
        self.view = GtkNodes.NodeView()
        self.view.connect("node-drag-end", lambda *a: self.emit("moved"))
        self.view.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                             | Gdk.EventMask.BUTTON2_MOTION_MASK)
        self.view.connect("button-press-event", self._pressed)
        self.view.connect("button-release-event", self._released)
        self.view.connect("motion-notify-event", self._panning)
        # (before GtkNodes draws the links and the nodes)
        self.view.connect("draw", self._draw_canvas)
        self.connect("hierarchy-changed", self._toplevel)
        self.scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        self.scroller.add(self.view)
        self.scroller.connect("scroll-event", self._scrolled)
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
        self._add_at = (max(0, x - 20) / self.zoom, max(0, y - 10) / self.zoom)
        self._popup.popup_at_pointer(event)

    def _pressed(self, view, event):
        if event.button == Gdk.BUTTON_MIDDLE:
            # middle-drag moves the canvas, as in Blender
            self._pan = (event.x_root, event.y_root, self.scroller.get_hadjustment().get_value(),
                         self.scroller.get_vadjustment().get_value())
            return True
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

    def _released(self, view, event):
        if event.button == Gdk.BUTTON_MIDDLE and self._pan:
            self._pan = None
            return True
        return False

    def _panning(self, view, event):
        if not self._pan:
            return False
        x0, y0, h, v = self._pan
        self.scroller.get_hadjustment().set_value(h - (event.x_root - x0))
        self.scroller.get_vadjustment().set_value(v - (event.y_root - y0))
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
        ratio = zoom / self.zoom
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
        self.view.queue_resize()
        self.view.queue_draw()

        def keep():
            h.set_value(around[0] * ratio - on_screen[0])
            v.set_value(around[1] * ratio - on_screen[1])
            return False
        GLib.idle_add(keep)

    def zoom_to_fit(self):
        """The largest zoom that shows all the nodes, at most 100%."""
        if not self.nodes:
            return
        right = bottom = 0
        for node in self.nodes.values():
            w, h = self._size(node)
            x, y = self._world(node)
            right = max(right, x + w / self.zoom)
            bottom = max(bottom, y + h / self.zoom)
        h, v = self.scroller.get_hadjustment(), self.scroller.get_vadjustment()
        fits = min(h.get_page_size() / (right + 20), v.get_page_size() / (bottom + 20), 1.0)
        zoom = max([z for z in ZOOMS if z <= fits] or [ZOOMS[0]])
        self.set_zoom(zoom, around=(0, 0))
        GLib.idle_add(lambda: (h.set_value(0), v.set_value(0)) and False)

    def _show_at(self, node):
        self.view.child_set_property(node, "x", int(round(node.gn_pos[0] * self.zoom)))
        self.view.child_set_property(node, "y", int(round(node.gn_pos[1] * self.zoom)))

    def _world(self, node):
        """Where the node is at zoom 1: kept, unless it was dragged since."""
        x = self.view.child_get_property(node, "x")
        y = self.view.child_get_property(node, "y")
        if x != int(round(node.gn_pos[0] * self.zoom)):
            node.gn_pos[0] = x / self.zoom
        if y != int(round(node.gn_pos[1] * self.zoom)):
            node.gn_pos[1] = y / self.zoom
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
        for major in (False, True):
            f = (0.78 if major else 0.88) if light else (0.66 if major else 0.82)
            cr.set_source_rgb(*(c * f for c in canvas))
            i = 0
            while i * step <= max(width, height):
                if (i % 5 == 0) == major:
                    x = math.floor(i * step) + 0.5
                    if x <= width:
                        cr.move_to(x, 0)
                        cr.line_to(x, height)
                    if x <= height:
                        cr.move_to(0, x)
                        cr.line_to(width, x)
                i += 1
            cr.stroke()
        cr.set_source_rgb(bg.red, bg.green, bg.blue)
        r = SOCKET_RADIUS * self.zoom
        corner = 4
        for node in self.nodes.values():
            x = self.view.child_get_property(node, "x") + r
            y = self.view.child_get_property(node, "y") + r
            w, h = node.get_allocated_width() - 2 * r, node.get_allocated_height() - 2 * r
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

    def _ask_save(self):
        name = self._ask_name("Save Setup", "Save")
        if not name:
            return
        if name in G.setups(self.setups_dir) and not self._confirm(
                "There is a setup called %s already. Replace it?" % name, "Replace"):
            return
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
        """Puts the node on the canvas at x, y (at zoom 1)."""
        node.gn_pos = [float(x), float(y)]
        if self.zoom != 1.0:
            node.set_socket_radius(SOCKET_RADIUS * self.zoom)
        self.view.add(node)
        # the links follow a node that moves or changes size; GTK would
        # otherwise redraw only the node's own area, and leave parts of
        # the old links on the canvas
        node.connect("size-allocate", lambda *a: self.view.queue_draw())
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
        self._loading = False

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
        # (on screen, at the zoom)
        gap_x, gap_y = self.GAP_X * self.zoom, self.GAP_Y * self.zoom
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
                            y = max(y, place[f][1] + size[f][1] + gap_y)
            for k in keys:
                w = wanted(k)
                y = max(y, int(w) if w is not None else y)
                place[k] = (int(x), int(y))
                y += size[k][1] + gap_y
            x += max(size[k][0] for k in keys) + gap_x

        self._loading = True
        for k, (x, y) in place.items():
            self.nodes[k].gn_pos = [x / self.zoom, y / self.zoom]
            self._show_at(self.nodes[k])
        self._loading = False
        self.view.queue_resize()
        self.view.queue_draw()
        self.emit("moved")
        return place

    # what a user does, also for the tests

    def add_op(self, name, x=300, y=40):
        info = gn_ops.catalog()[name]
        if not info.usable():
            self.say("%s cannot be used here: it gives no image" % info.title)
            return None
        node_id = G.new_id(self.graph())
        self._place(_OpNode(self, node_id, info, {}), x, y)
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
