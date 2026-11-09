# Unit tests in plain Python with GEGL (no GIMP): the graph module, the
# operation catalog and the nodes:graph operation, including what it
# refuses. Run by tests/run.sh, which puts nodes:graph on GEGL_PATH.
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import struct
import sys
import tempfile

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(here), "plug-ins", "gegl-nodes"))

import gi  # noqa: E402
gi.require_version("Gegl", "0.4")
from gi.repository import Gegl  # noqa: E402

Gegl.init(None)

import gn_graph as G  # noqa: E402
import gn_ops  # noqa: E402

W = H = 32
FMT = "R'G'B'A float"
failures = 0


def check(name, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       (": " + str(detail)) if detail and not ok else ""), flush=True)
    return ok


def source(parent):
    grad = parent.create_child("gegl:linear-gradient")
    grad.set_property("start-x", 0.0)
    grad.set_property("end-x", float(W))
    grad.set_property("start-color", Gegl.Color.new("#ff2000"))
    grad.set_property("end-color", Gegl.Color.new("#2040ff"))
    noise = parent.create_child("gegl:noise-hsv")
    noise.set_property("seed", 7)
    crop = parent.create_child("gegl:crop")
    crop.set_property("width", float(W))
    crop.set_property("height", float(H))
    grad.link(noise)
    noise.link(crop)
    return crop


def pixels(node):
    """the pixels of node in the test rectangle (cropped: a generator is
    endless)"""
    buf = Gegl.Buffer.new(FMT, 0, 0, W, H)
    crop = node.get_parent().create_child("gegl:crop")
    crop.set_property("width", float(W))
    crop.set_property("height", float(H))
    write = node.get_parent().create_child("gegl:write-buffer")
    write.set_property("buffer", buf)
    node.link(crop)
    crop.link(write)
    write.process()
    return buf.get(Gegl.Rectangle.new(0, 0, W, H), 1.0, FMT, Gegl.AbyssPolicy.NONE)


def maxdiff(a, b):
    fa = struct.unpack("%df" % (len(a) // 4), a)
    fb = struct.unpack("%df" % (len(b) // 4), b)
    return max(abs(x - y) for x, y in zip(fa, fb))


def through(op, string, **props):
    """The source through op (nodes:graph or gegl:gegl) with the string;
    the pixels and the node"""
    parent = Gegl.Node()
    node = parent.create_child(op)
    node.set_property("string", string)
    for k, v in props.items():
        node.set_property(k, v)
    source(parent).link(node)
    return pixels(node), node


def direct(graph):
    parent = Gegl.Node()
    end = G.build(graph, parent, source(parent))
    return pixels(end)


def plain():
    parent = Gegl.Node()
    return pixels(source(parent))


def graph(nodes, links):
    g = G.new_graph()
    g["links"] = []
    for nid, (op, props) in nodes.items():
        g["nodes"][nid] = {"op": op, "x": 0, "y": 0, "props": props}
    for a, b, pad in links:
        g["links"].append({"from": a, "to": b, "pad": pad})
    return g


# a graph that uses the layer twice: blur * invert(layer)
branchy = graph({"blur": ("gegl:gaussian-blur", {"std-dev-x": 3.0, "std-dev-y": 3.0,
                                                 "abyss-policy": "black"}),
                 "inv": ("gegl:invert-gamma", {}),
                 "mul": ("gegl:multiply", {})},
                [("layer", "blur", "input"), ("blur", "mul", "input"),
                 ("layer", "inv", "input"), ("inv", "mul", "aux"),
                 ("mul", "output", "input")])

# --- the graph module
s = G.serialize(branchy)
check("serialize: a string with the branch named", "ref=r1" in s and "id=r1" in s, s)
check("serialize: the same graph gives the same string", s == G.serialize(branchy))
check("serialize: JSON round trip", G.serialize(G.from_json(G.to_json(branchy))) == s)
ref = direct(branchy)
check("serialize: the source is changed", ref != plain())
check("gegl:gegl with the string = the graph itself", through("gegl:gegl", s)[0] == ref)
out, node = through(G.GRAPH_OP, s)
check("nodes:graph with the string = the graph itself", out == ref)
check("nodes:graph: no error for a good graph", node.get_property("error") == "",
      node.get_property("error"))

# enum by nick: black, not the default clamp, makes a difference at the edge
clamp = graph({"blur": ("gegl:gaussian-blur", {"std-dev-x": 3.0, "std-dev-y": 3.0})},
              [("layer", "blur", "input"), ("blur", "output", "input")])
black = graph({"blur": ("gegl:gaussian-blur", {"std-dev-x": 3.0, "std-dev-y": 3.0,
                                               "abyss-policy": "black"})},
              [("layer", "blur", "input"), ("blur", "output", "input")])
check("enum by nick reaches GEGL", direct(clamp) != direct(black))
check("enum by nick in the string", through(G.GRAPH_OP, G.serialize(black))[0] == direct(black))

# color, int and bool properties
colorful = graph({"ov": ("gegl:color-overlay", {"value": "#20c040"}),
                  "px": ("gegl:pixelize", {"size-x": 4, "size-y": 4}),
                  "blur": ("gegl:gaussian-blur", {"std-dev-x": 2.0, "clip-extent": False})},
                 [("layer", "px", "input"), ("px", "ov", "input"), ("ov", "blur", "input"),
                  ("blur", "output", "input")])
check("color, int and bool in the string",
      through(G.GRAPH_OP, G.serialize(colorful))[0] == direct(colorful), G.serialize(colorful))
check("the color exactly, not GEGL's three decimals", "#20c040" in G.serialize(colorful),
      G.serialize(colorful))
transparent = graph({"ov": ("gegl:color-overlay", {"value": "#20c04080"})},
                    [("layer", "ov", "input"), ("ov", "output", "input")])
check("a color with alpha",
      through(G.GRAPH_OP, G.serialize(transparent))[0] == direct(transparent),
      G.serialize(transparent))

# a generator, no use of the layer
gen = graph({"cb": ("gegl:checkerboard", {"x": 4, "y": 4})}, [("cb", "output", "input")])
check("a generator alone", through(G.GRAPH_OP, G.serialize(gen))[0] == direct(gen),
      G.serialize(gen))

check("output linked to the layer: empty string", G.serialize(G.new_graph()) == "")
nothing = graph({}, [])
check("output not linked: empty string", G.serialize(nothing) == "")
check("empty string: the layer as it is", through(G.GRAPH_OP, "")[0] == plain())

loop = graph({"a": ("gegl:invert-gamma", {}), "b": ("gegl:invert-gamma", {})},
             [("a", "b", "input"), ("b", "a", "input"), ("b", "output", "input")])
try:
    G.serialize(loop)
    check("a loop is refused", False)
except G.GraphError as e:
    check("a loop is refused", "loop" in str(e))

missing = graph({"x": ("nosuch:op", {})}, [("x", "output", "input")])
try:
    G.serialize(missing)
    check("an operation that is not installed is refused", False)
except G.GraphError as e:
    check("an operation that is not installed is refused", "not installed" in str(e))

# --- what nodes:graph refuses
tmp = tempfile.mkdtemp()
target = os.path.join(tmp, "written.png")
with open(os.path.join(tmp, "in.png"), "wb") as f:
    f.write(b"")
refused = {
    "gegl:save": 'gegl:save path="%s"' % target,
    "gegl:png-save": 'gegl:png-save path="%s"' % target,
    "gegl:save inside aux": 'gegl:over aux=[ gegl:save path="%s" ]' % target,
    "gegl:load": 'gegl:over aux=[ gegl:load path="%s" ]' % os.path.join(tmp, "in.png"),
    "gegl:gegl inside": 'gegl:gegl string="gegl:invert-gamma"',
    "nodes:graph inside": 'nodes:graph string="gegl:invert-gamma"',
    "gegl:introspect": "gegl:introspect",
}
for what, string in refused.items():
    out, node = through(G.GRAPH_OP, string)
    err = node.get_property("error")
    check("refused: %s (says why)" % what, err != "" and out == plain(), err)
check("refused: nothing was written", not os.path.exists(target))

# a bad graph after a good one: back to the layer, not the old graph
parent = Gegl.Node()
node = parent.create_child(G.GRAPH_OP)
node.set_property("string", s)
source(parent).link(node)
first = pixels(node)
node.set_property("string", "gegl:png-save path=\"%s\"" % target)
check("good then refused: the layer as it is", pixels(node) == plain() and first == ref)
node.set_property("string", "gegl:no-such-op")
check("unknown operation: an error", node.get_property("error") != "")

# the layout property is kept, and does not change the image
lay = G.to_json(branchy)
out, node = through(G.GRAPH_OP, s, layout=lay)
check("layout is kept and not used", node.get_property("layout") == lay and out == ref)

# --- the catalog: what is offered is accepted by nodes:graph
cat = gn_ops.catalog()
for name in ("gegl:gaussian-blur", "gegl:multiply", "svg:src-over", "gegl:checkerboard"):
    check("offered: %s" % name, name in cat)
for name in ("gegl:save", "gegl:load", "gegl:gegl", "nodes:graph", "gegl:png-save",
             "gegl:introspect", "gegl:write-buffer"):
    check("not offered: %s" % name, name not in cat)
refused_offers = []
for name in sorted(cat):
    if os.environ.get("GN_VERBOSE"):
        print("  trying", name, flush=True)
    out, node = through(G.GRAPH_OP, name)
    if node.get_property("error"):
        refused_offers.append((name, node.get_property("error")))
check("every one of the %d offered operations is accepted by nodes:graph" % len(cat),
      not refused_offers, refused_offers[:5])
blur = cat["gegl:gaussian-blur"]
check("gaussian-blur: one input, editable std-dev-x and abyss-policy",
      blur.inputs == ["input"] and {"std-dev-x", "abyss-policy"} <= {p.name for p in blur.props})
check("multiply: input and aux", cat["gegl:multiply"].inputs == ["input", "aux"])

print("unit failures: %d" % failures)
sys.exit(1 if failures else 0)
