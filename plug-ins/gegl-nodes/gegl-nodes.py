#!/usr/bin/env python3
# Node Editor: edit a graph of GEGL operations in a node editor and see
# it on the layer as you go, as one non-destructive filter (nodes:graph)
# that stays editable: Filters > Generic > Node Editor...
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Needs the GEGL operation nodes:graph (operations/node-graph.c in this
# repository) and GtkNodes (github.com/aluntzer/gtknodes). GtkNodes is
# found in $GEGL_NODES_GTKNODES (its install prefix), in gtknodes/ next
# to this file, in ../gtknodes/_inst next to this repository (as
# dev/install.sh leaves it), or where the system has it.
#
# The node layout is kept in the filter's "layout" property, so it is
# saved in the XCF with the filter and the editor opens it again.

import ctypes
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)


def _find_gtknodes():
    """Makes GtkNodes loadable before gi is imported: its typelib on
    GI_TYPELIB_PATH, and the library loaded already (the typelib names
    it by soname only, and LD_LIBRARY_PATH cannot be set in a running
    process)."""
    repo = os.path.dirname(os.path.dirname(HERE))
    prefixes = [os.environ.get("GEGL_NODES_GTKNODES"),
                os.path.join(HERE, "gtknodes"),
                os.path.join(os.path.dirname(repo), "gtknodes", "_inst")]
    for prefix in prefixes:
        if not prefix:
            continue
        for libdir in ("lib", "lib64", "lib/x86_64-linux-gnu"):
            lib = os.path.join(prefix, libdir, "libgtknodes-0.1.so.0")
            typelibs = os.path.join(prefix, libdir, "girepository-1.0")
            if os.path.exists(lib) and os.path.isdir(typelibs):
                ctypes.CDLL(lib, mode=ctypes.RTLD_GLOBAL)
                old = os.environ.get("GI_TYPELIB_PATH")
                os.environ["GI_TYPELIB_PATH"] = typelibs + (os.pathsep + old if old else "")
                return prefix
    return None


_find_gtknodes()

import gi  # noqa: E402
gi.require_version("Gimp", "3.0")
gi.require_version("GimpUi", "3.0")
gi.require_version("Gegl", "0.4")
gi.require_version("Gtk", "3.0")
from gi.repository import Gegl, Gimp, GimpUi, GLib, Gtk  # noqa: E402

PROC = "plug-in-gegl-nodes"
FILTER_NAME = "Node Graph"
DELAY_MS = 120  # how long to wait after a change before updating the layer


def _gtknodes_ok():
    try:
        gi.require_version("GtkNodes", "0.1")
        from gi.repository import GtkNodes  # noqa: F401
        return True
    except (ValueError, ImportError):
        return False


class Session:
    """One open editor on one filter of one drawable."""

    def __init__(self, drawable, filt, created):
        import gn_editor
        import gn_graph as G
        self.G = G
        self.drawable = drawable
        self.filter = filt
        self.created = created
        self.pending = 0
        self.last = None
        config = filt.get_config()
        self.last = config.get_property("string")
        try:
            graph = G.from_json(config.get_property("layout"))
        except (G.GraphError, ValueError) as e:
            graph = G.new_graph()
            first_error = "The saved layout could not be read (%s); a new one" % e
        else:
            first_error = ""

        self.window = Gtk.Window(title="Node Editor: %s" % drawable.get_name())
        self.window.set_default_size(1100, 420)
        self.window.set_role("gegl-nodes")
        # saved setups: in GIMP's profile folder, next to its own presets
        self.editor = gn_editor.GraphEditor(
            graph, setups_dir=os.path.join(Gimp.directory(), "gegl-nodes", "setups"))
        self.editor.say(first_error)
        self.editor.connect("changed", lambda e: self.later())
        self.editor.connect("moved", lambda e: self.later())
        self.window.add(self.editor)
        self.window.connect("delete-event", self.close)
        self.window.show_all()
        # nothing focused, so the search field shows its hint
        self.window.set_focus(None)
        GimpUi.window_set_transient(self.window)

    def later(self):
        if self.pending:
            GLib.source_remove(self.pending)
        self.pending = GLib.timeout_add(DELAY_MS, self.apply)

    def apply(self):
        """Puts the graph shown into the filter and updates the layer."""
        self.pending = 0
        G = self.G
        graph = self.editor.graph()
        try:
            # (the image at a node, while one is viewed; the layout keeps
            # the graph itself)
            string = G.serialize(self.editor.shown_graph())
        except G.GraphError as e:
            self.editor.say(str(e))
            return False
        config = self.filter.get_config()
        config.set_property("layout", G.to_json(graph))
        if string != self.last:
            config.set_property("string", string)
            self.last = string
        self.filter.update()
        Gimp.displays_flush()
        self.editor.say("")
        return False

    def close(self, *args):
        # the layer keeps the graph's result, not a node's viewed
        if self.editor.viewing:
            self.editor.view_node(None)
        if self.pending:
            GLib.source_remove(self.pending)
            self.apply()
        # a new filter that does nothing is not left behind
        if self.created and self.last == "":
            self.filter.delete()
            Gimp.displays_flush()
        Gtk.main_quit()
        return False


def find_or_make_filter(drawable):
    """The topmost nodes:graph filter of the drawable, or a new one;
    and whether it is new."""
    for f in drawable.get_filters() or []:
        if f.get_operation_name() == "nodes:graph":
            return f, False
    f = Gimp.DrawableFilter.new(drawable, "nodes:graph", FILTER_NAME)
    drawable.append_filter(f)
    return f, True


class GeglNodes(Gimp.PlugIn):
    def do_query_procedures(self):
        return [PROC]

    def do_set_i18n(self, name):
        return False

    def do_create_procedure(self, name):
        proc = Gimp.ImageProcedure.new(self, name, Gimp.PDBProcType.PLUGIN, self.run, None)
        proc.set_image_types("*")
        proc.set_sensitivity_mask(Gimp.ProcedureSensitivityMask.DRAWABLE)
        proc.set_menu_label("_Node Editor...")
        proc.add_menu_path("<Image>/Filters/Generic")
        proc.set_documentation(
            "Edit a graph of GEGL filters with nodes",
            "Opens a node editor for the layer: GEGL operations as nodes, linked as you "
            "like, shown on the layer as you edit, as one non-destructive filter (Node "
            "Graph) that stays editable. Runs again on a layer with a Node Graph filter "
            "to edit it.",
            name)
        proc.set_attribution("David", "David", "2026")
        return proc

    def run(self, procedure, run_mode, image, drawables, config, data):
        def fail(text):
            return procedure.new_return_values(
                Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error(text))

        if run_mode == Gimp.RunMode.NONINTERACTIVE:
            return fail("The node editor needs a display")
        if len(drawables) != 1:
            return fail("Choose one layer")
        drawable = drawables[0]
        GimpUi.init("gegl-nodes")
        Gegl.init(None)
        if not _gtknodes_ok():
            return fail("GtkNodes is not installed (see the README of gimp-gegl-nodes)")
        if not Gimp.DrawableFilter.operation_get_available() or \
                "nodes:graph" not in Gimp.DrawableFilter.operation_get_available():
            return fail("The GEGL operation nodes:graph is not installed "
                        "(see the README of gimp-gegl-nodes)")

        image.undo_group_start()
        try:
            filt, created = find_or_make_filter(drawable)
            session = Session(drawable, filt, created)
            test = os.environ.get("GEGL_NODES_TEST")
            if test:
                # the GUI test drives the editor from here (tests/gui)
                scope = {"session": session, "image": image, "drawable": drawable,
                         "Gimp": Gimp, "Gegl": Gegl, "GLib": GLib, "Gtk": Gtk}
                GLib.timeout_add(500, lambda: exec(open(test).read(), scope) or False)
            Gtk.main()
        finally:
            image.undo_group_end()
        return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, None)


Gimp.main(GeglNodes.__gtype__, sys.argv)
