# Headless GIMP (gimp-console, python-fu-eval), with nodes:graph
# installed in the throwaway home of tests/run.sh: the filter is
# allowed as a layer filter (gegl:gegl is not), gives the same pixels as
# the graph in plain GEGL, follows changes of its graph, refuses a graph
# that writes a file, and is saved in an XCF with its layout and opened
# again.
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

here = os.environ["TEST_HERE"]
work = os.environ["TEST_WORK"]
sys.path.insert(0, os.path.join(os.path.dirname(here), "plug-ins", "gegl-nodes"))

import gi  # noqa: E402
gi.require_version("Gimp", "3.0")
gi.require_version("Gegl", "0.4")
from gi.repository import Gegl, Gimp, Gio  # noqa: E402

Gegl.init(None)

import gn_graph as G  # noqa: E402

W, H = 48, 32
FMT = "R'G'B'A u8"
failures = 0


def check(name, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       (": " + str(detail)) if detail and not ok else ""), flush=True)
    return ok


def start_pixels():
    out = bytearray()
    for y in range(H):
        for x in range(W):
            out += bytes(((x * 5) % 256, (y * 8) % 256, ((x + y) * 3) % 256, 255))
    return bytes(out)


DATA = start_pixels()


def new_image():
    image = Gimp.Image.new(W, H, Gimp.ImageBaseType.RGB)
    layer = Gimp.Layer.new(image, "bg", W, H, Gimp.ImageType.RGBA_IMAGE, 100,
                           Gimp.LayerMode.NORMAL)
    image.insert_layer(layer, None, 0)
    buf = layer.get_buffer()
    buf.set(Gegl.Rectangle.new(0, 0, W, H), FMT, DATA)
    buf.flush()
    layer.update(0, 0, W, H)
    return image, layer


def merged(image):
    """The pixels of the first layer with its filters applied (on a copy)"""
    dup = image.duplicate()
    layer = dup.get_layers()[0]
    layer.merge_filters()
    out = layer.get_buffer().get(Gegl.Rectangle.new(0, 0, W, H), 1.0, FMT,
                                 Gegl.AbyssPolicy.NONE)
    dup.delete()
    return out


def gegl_render(string):
    """The start pixels through nodes:graph in plain GEGL, here"""
    parent = Gegl.Node()
    src = Gegl.Buffer.new(FMT, 0, 0, W, H)
    src.set(Gegl.Rectangle.new(0, 0, W, H), FMT, DATA)
    source = parent.create_child("gegl:buffer-source")
    source.set_property("buffer", src)
    node = parent.create_child("nodes:graph")
    node.set_property("string", string)
    dst = Gegl.Buffer.new(FMT, 0, 0, W, H)
    write = parent.create_child("gegl:write-buffer")
    write.set_property("buffer", dst)
    source.link(node)
    node.link(write)
    write.process()
    return dst.get(Gegl.Rectangle.new(0, 0, W, H), 1.0, FMT, Gegl.AbyssPolicy.NONE)


def maxdiff(a, b):
    return max(abs(x - y) for x, y in zip(a, b))


def graph(nodes, links):
    g = G.new_graph()
    g["links"] = []
    for nid, (op, props) in nodes.items():
        g["nodes"][nid] = {"op": op, "x": 100, "y": 50, "props": props}
    for a, b, pad in links:
        g["links"].append({"from": a, "to": b, "pad": pad})
    return g


branchy = graph({"blur": ("gegl:gaussian-blur", {"std-dev-x": 3.0, "std-dev-y": 3.0}),
                 "inv": ("gegl:invert-gamma", {}),
                 "mul": ("gegl:multiply", {})},
                [("layer", "blur", "input"), ("blur", "mul", "input"),
                 ("layer", "inv", "input"), ("inv", "mul", "aux"),
                 ("mul", "output", "input")])
second = graph({"px": ("gegl:pixelize", {"size-x": 6, "size-y": 6}),
                "ov": ("gegl:color-overlay", {"value": "#3060c0"})},
               [("layer", "px", "input"), ("px", "ov", "input"), ("ov", "output", "input")])

available = Gimp.DrawableFilter.operation_get_available()
check("GIMP offers nodes:graph as a filter", "nodes:graph" in available)
check("(and not gegl:gegl, which is why nodes:graph exists)", "gegl:gegl" not in available)

image, layer = new_image()
try:
    Gimp.DrawableFilter.new(layer, "gegl:gegl", "x")
    check("GIMP refuses gegl:gegl as a layer filter", False)
except Exception:
    check("GIMP refuses gegl:gegl as a layer filter", True)

filt = Gimp.DrawableFilter.new(layer, "nodes:graph", "Node Graph")
check("a nodes:graph filter is made", filt is not None)
layer.append_filter(filt)
s1 = G.serialize(branchy)
config = filt.get_config()
config.set_property("string", s1)
config.set_property("layout", G.to_json(branchy))
filt.update()
got = merged(image)
want = gegl_render(s1)
check("the layer shows the graph", got != DATA)
check("same pixels as the graph in plain GEGL (max difference %d)" % maxdiff(got, want),
      maxdiff(got, want) <= 1)

# a change of the graph, as the editor makes it
s2 = G.serialize(second)
config = filt.get_config()
config.set_property("string", s2)
filt.update()
got2 = merged(image)
check("a changed graph is shown (max difference %d)" % maxdiff(got2, gegl_render(s2)),
      maxdiff(got2, gegl_render(s2)) <= 1 and got2 != got)
check("the layer itself is not changed", layer.get_buffer().get(
    Gegl.Rectangle.new(0, 0, W, H), 1.0, FMT, Gegl.AbyssPolicy.NONE) == DATA)

# a graph that would write a file
target = os.path.join(work, "written.png")
config = filt.get_config()
config.set_property("string", 'gegl:png-save path="%s"' % target)
filt.update()
check("a graph that writes a file: the layer as it is", merged(image) == DATA)
check("a graph that writes a file: nothing written", not os.path.exists(target))

# back, and into an XCF
config = filt.get_config()
config.set_property("string", s1)
config.set_property("layout", G.to_json(branchy))
filt.update()
xcf = os.path.join(work, "graph.xcf")
Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, image, Gio.File.new_for_path(xcf), None)
check("saved as XCF", os.path.exists(xcf))
image.delete()

loaded = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, Gio.File.new_for_path(xcf))
lay2 = loaded.get_layers()[0]
filters = [f for f in lay2.get_filters() or [] if f.get_operation_name() == "nodes:graph"]
check("the filter is in the XCF", len(filters) == 1, [f.get_operation_name() for f in
                                                      lay2.get_filters() or []])
if filters:
    c = filters[0].get_config()
    check("its graph is in the XCF", c.get_property("string") == s1, c.get_property("string"))
    check("its layout is in the XCF",
          G.from_json(c.get_property("layout")) == G.from_json(G.to_json(branchy)))
    check("it gives the same pixels after opening", maxdiff(merged(loaded), want) <= 1)
loaded.delete()

print("gimp failures: %d" % failures, flush=True)
