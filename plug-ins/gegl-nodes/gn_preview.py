# A small window with the image as it comes out of one node of the graph:
# the layer's pixels through the graph up to that node, worked out at full
# size (so that radii and distances are those of the layer) and scaled
# down to fit, in steps between other work, so that the editor stays
# responsive. It is worked out again shortly after each change.
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gegl", "0.4")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gegl, GLib, Gtk  # noqa: E402

import gn_graph as G  # noqa: E402

SIZE = 320  # the longest side of a preview
DELAY_MS = 150  # after a change, before working it out again
FORMAT = "R'G'B'A u8"


def ending_at(graph, node_id):
    """The graph with node_id's result going to the output."""
    graph = dict(graph, links=[link for link in graph["links"] if link["to"] != G.OUTPUT])
    graph["links"].append({"from": node_id, "to": G.OUTPUT, "pad": "input"})
    return graph


class Preview(Gtk.Window):
    """The preview of one node. source() gives the layer's pixels (a
    GeglBuffer), graph() the graph; on_close is called when the window is
    closed."""

    def __init__(self, title, node_id, source, graph, on_close, parent=None):
        super().__init__(title="Preview: %s" % title)
        self.set_type_hint(1)  # Gdk.WindowTypeHint.DIALOG: small, above
        if parent is not None:
            self.set_transient_for(parent)
            self.set_destroy_with_parent(True)
        self.set_resizable(False)
        self.node_id = node_id
        self.source = source
        self.graph = graph
        self.pending = 0
        self.job = 0
        self.processor = None
        self.pixels = None  # the last image shown: (bytes, width, height)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin=6)
        self.image = Gtk.Image()
        self.image.set_size_request(SIZE // 2, SIZE // 3)
        self.note = Gtk.Label(xalign=0.0)
        self.note.get_style_context().add_class("dim-label")
        box.pack_start(self.image, True, True, 0)
        box.pack_start(self.note, False, False, 0)
        self.add(box)
        self.connect("delete-event", lambda *a: on_close() or False)
        self.connect("destroy", lambda *a: self._stop())
        self.show_all()
        self.update(now=True)

    def set_title_of_node(self, title):
        self.set_title("Preview: %s" % title)

    def update(self, now=False):
        """Works the image out again (after DELAY_MS, unless now)."""
        if self.pending:
            GLib.source_remove(self.pending)
            self.pending = 0
        if now:
            self._start()
        else:
            self.pending = GLib.timeout_add(DELAY_MS, self._start)

    def _stop(self):
        if self.pending:
            GLib.source_remove(self.pending)
            self.pending = 0
        if self.job:
            GLib.source_remove(self.job)
            self.job = 0
        self.processor = None

    def _start(self):
        self.pending = 0
        if self.job:
            GLib.source_remove(self.job)
            self.job = 0
        buffer = self.source()
        if buffer is None:
            self.note.set_text("No image to show")
            return False
        parent = Gegl.Node()
        layer = parent.create_child("gegl:buffer-source")
        layer.set_property("buffer", buffer)
        try:
            end = G.build(ending_at(self.graph(), self.node_id), parent, layer)
        except G.GraphError as e:
            self.note.set_text(str(e))
            return False
        extent = buffer.get_extent()
        scale = min(1.0, SIZE / max(extent.width, extent.height, 1))
        w = max(1, int(extent.width * scale))
        h = max(1, int(extent.height * scale))
        # (cropped to the layer: some operations reach further, as a
        # drop shadow or a blur)
        crop = parent.create_child("gegl:crop")
        for prop, value in (("x", extent.x), ("y", extent.y),
                            ("width", extent.width), ("height", extent.height)):
            crop.set_property(prop, float(value))
        shift = parent.create_child("gegl:translate")
        shift.set_property("x", float(-extent.x))
        shift.set_property("y", float(-extent.y))
        resize = parent.create_child("gegl:scale-ratio")
        resize.set_property("x", w / extent.width)
        resize.set_property("y", h / extent.height)
        out = Gegl.Buffer.new(FORMAT, 0, 0, w, h)
        write = parent.create_child("gegl:write-buffer")
        write.set_property("buffer", out)
        end.link(crop)
        crop.link(shift)
        shift.link(resize)
        resize.link(write)
        self._parent = parent  # (the nodes live while it works)
        self._out = (out, w, h)
        self.processor = write.new_processor(Gegl.Rectangle.new(0, 0, w, h))
        self.note.set_text("Working it out...")
        self.job = GLib.idle_add(self._work)
        return False

    def _work(self):
        if self.processor is None:
            self.job = 0
            return False
        more, progress = self.processor.work()
        if more:
            self.note.set_text("Working it out... %d%%" % round(progress * 100))
            return True
        self.job = 0
        self.processor = None
        out, w, h = self._out
        data = out.get(Gegl.Rectangle.new(0, 0, w, h), 1.0, FORMAT, Gegl.AbyssPolicy.NONE)
        self.pixels = (data, w, h)
        pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(data), GdkPixbuf.Colorspace.RGB,
                                                 True, 8, w, h, w * 4)
        self.image.set_from_pixbuf(pixbuf)
        self.note.set_text("")
        self._parent = None
        return False

    def busy(self):
        return bool(self.pending or self.job)
