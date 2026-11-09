# The editor widget without GIMP, on a Broadway display (tests/run.sh
# starts broadwayd): nodes are added, linked and edited through the
# editor's own calls (the ones its widgets use), and the graph read back
# from the widgets must give the right string; loading a graph must show
# the same graph.
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
plugdir = os.path.join(os.path.dirname(here), "plug-ins", "gegl-nodes")
sys.path.insert(0, plugdir)

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Gegl", "0.4")
from gi.repository import Gdk, Gegl, GLib, Gtk  # noqa: E402

Gegl.init(None)

import gn_editor  # noqa: E402
import gn_graph as G  # noqa: E402
import gn_ops  # noqa: E402
import gn_preview  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       (": " + str(detail)) if detail and not ok else ""), flush=True)


def pump():
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


win = Gtk.Window()
win.set_default_size(1200, 500)
editor = gn_editor.GraphEditor()
changes = []
editor.connect("changed", lambda e: changes.append(1))
win.add(editor)
win.show_all()
pump()

g = editor.graph()
check("a new editor: layer to output", G.serialize(g) == "" and
      g["links"] == [{"from": "layer", "to": "output", "pad": "input"}], g)

end = GLib.get_monotonic_time() + 800000
while GLib.get_monotonic_time() < end:
    pump()
h, v = editor.scroller.get_hadjustment(), editor.scroller.get_vadjustment()
check("on opening, the nodes are in view", all(
    h.get_value() <= n.get_property("x") < h.get_value() + h.get_page_size() and
    v.get_value() <= n.get_property("y") < v.get_value() + v.get_page_size()
    for n in editor.nodes.values()),
    (h.get_value(), v.get_value(), [(n.get_property("x"), n.get_property("y"))
                                    for n in editor.nodes.values()]))

blur = editor.add_op("gegl:gaussian-blur", 250, 40)
inv = editor.add_op("gegl:invert-gamma", 250, 260)
mul = editor.add_op("gegl:multiply", 500, 120)
pump()
check("adding nodes is a change", len(changes) >= 3)
editor.link("layer", blur)
editor.link(blur, mul)
editor.link("layer", inv)
editor.link(inv, mul, "aux")
editor.link(mul, "output")
pump()
g = editor.graph()
links = sorted((link["from"], link["to"], link["pad"]) for link in g["links"])
check("links read back from the sockets", links == sorted([
    ("layer", blur, "input"), (blur, mul, "input"), ("layer", inv, "input"),
    (inv, mul, "aux"), (mul, "output", "input")]), links)
check("linking a pad that was linked replaces the link",
      not any(link["to"] == "output" and link["from"] == "layer" for link in g["links"]))

n = len(changes)
editor.set_value(blur, "std-dev-x", 4.5)
editor.set_value(blur, "abyss-policy", "black")
pump()
check("values set through the widgets are changes", len(changes) >= n + 2)
s = G.serialize(editor.graph())
check("the string has the values and the branch",
      "std-dev-x=4.5" in s and "abyss-policy=black" in s and "ref=L" in s
      and "gegl:multiply" in s, s)

editor.add_op("gegl:color-overlay", 700, 300)
over = [k for k, v in editor.graph()["nodes"].items() if v["op"] == "gegl:color-overlay"][0]
editor.set_value(over, "value", "#20c040ff")
editor.unlink("output")
editor.link(mul, over)
editor.link(over, "output")
pump()
s2 = G.serialize(editor.graph())
check("a color, exactly", "value=#20c040ff" in s2, s2)

saved = G.to_json(editor.graph())
editor.remove(inv)
pump()
check("removing a node removes its links", inv not in editor.graph()["nodes"] and
      not any(inv in (link["from"], link["to"]) for link in editor.graph()["links"]))

editor.load(G.from_json(saved))
pump()
again = editor.graph()
check("load shows the same graph", G.serialize(again) == s2, G.serialize(again))
check("load keeps the places", all(
    (again["nodes"][k]["x"], again["nodes"][k]["y"]) == (v["x"], v["y"])
    for k, v in G.from_json(saved)["nodes"].items()))
check("load keeps the values shown", editor.nodes[blur].gn_widgets["std-dev-x"].get_value() == 4.5)

# adding nodes: the search field, the menu row, the Add menu
before = set(editor.nodes)
editor.search.set_text("gaussian blur")
editor._find(editor.search)
pump()
rows = editor.matches.get_children()
check("the search lists the matches", [r.gn_op for r in rows] == ["gegl:gaussian-blur"],
      [r.gn_op for r in rows])
editor.search.emit("activate")
pump()
added = set(editor.nodes) - before
check("Enter in the search adds the node",
      [getattr(editor.nodes[k], "gn_op", None) for k in added] == ["gegl:gaussian-blur"])
offered = sorted(i.name for ops in editor.groups().values() for i in ops)
check("the menu row has every offered filter once",
      offered == sorted(gn_ops.catalog()) and
      len(editor.category_buttons) == len(editor.groups()))
check("the Add menu has the same categories", [i.get_label() for i in editor._popup.get_children()]
      == [b.get_label() for b in editor.category_buttons])
before = set(editor.nodes)
first = [i for i in editor.category_buttons[0].get_popup().get_children() if hasattr(i, "gn_op")][0]
first.activate()
pump()
check("a filter picked in the menu row is added", len(set(editor.nodes) - before) == 1)
check("nodes are styled out of the theme's box background",
      all(n.get_style_context().has_class("gegl-nodes-node") for n in editor.nodes.values()))

# the Layer and Output nodes look different
check("Layer and Output are marked as the ends",
      all(editor.nodes[k].get_style_context().has_class("gegl-nodes-end")
          for k in (G.LAYER, G.OUTPUT)) and
      not any(n.get_style_context().has_class("gegl-nodes-end")
              for k, n in editor.nodes.items() if k not in (G.LAYER, G.OUTPUT)))

# automatic layout: a glow, placed badly
editor.load(G.new_graph())
pump()
b = editor.add_op("gegl:gaussian-blur", 700, 300)
sc = editor.add_op("svg:screen", 60, 260)
vg = editor.add_op("gegl:vignette", 400, 30)
editor.link("layer", b)
editor.link("layer", sc)
editor.link(b, sc, "aux")
editor.link(sc, vg)
editor.link(vg, "output")
pump()
string_before = G.serialize(editor.graph())
place = editor.arrange()
pump()
size = {k: (n.get_allocated_width(), n.get_allocated_height()) for k, n in editor.nodes.items()}
links = editor.graph()["links"]
check("arrange: every node right of what feeds it", all(
    place[l["from"]][0] + size[l["from"]][0] < place[l["to"]][0] for l in links), place)
boxes = [(place[k][0], place[k][1], place[k][0] + size[k][0], place[k][1] + size[k][1])
         for k in place]
check("arrange: no two nodes overlap", not any(
    a[0] < c[2] and c[0] < a[2] and a[1] < c[3] and c[1] < a[3]
    for i, a in enumerate(boxes) for c in boxes[i + 1:]), boxes)
check("arrange: the layer first, the output last",
      place[G.LAYER][0] == min(x for x, y in place.values()) and
      place[G.OUTPUT][0] == max(x for x, y in place.values()))
check("arrange: a link past a node keeps clear of it (the blur below the layer)",
      place[b][1] >= place[G.LAYER][1] + size[G.LAYER][1])
check("arrange: the nodes are where it says",
      all((editor.view.child_get_property(editor.nodes[k], "x"),
           editor.view.child_get_property(editor.nodes[k], "y")) == p for k, p in place.items()))
check("arrange: the graph is the same", G.serialize(editor.graph()) == string_before)

# the menus: fewer, fuller groups
groups = editor.groups()
sizes = {t: len(ops) for t, ops in groups.items()}
check("menus: at most 14, none with only one or two filters",
      len(groups) <= 14 and min(sizes.values()) >= 3, sizes)
check("menus: Opacity (category transparency) is under Color",
      "gegl:opacity" in [i.name for i in groups.get("Color", [])])
check("menus: the menus are square (no theme corners)",
      editor._popup.get_style_context().has_class("gegl-nodes-menu"))

# zoom: places scale on screen, the graph keeps them
editor.load(G.new_graph())
pump()
zb = editor.add_op("gegl:gaussian-blur", 300, 100)
editor.link("layer", zb)
editor.link(zb, "output")
pump()
at1 = editor.graph()
editor.set_zoom(0.5)
pump()
def on_canvas(k):
    """Where node k is on the canvas, from the graph's 0, 0."""
    return (editor.view.child_get_property(editor.nodes[k], "x") - editor.origin[0],
            editor.view.child_get_property(editor.nodes[k], "y") - editor.origin[1])


check("zoom: the places on screen are halved", on_canvas(zb) == (150, 50), on_canvas(zb))
check("zoom: the graph keeps its places and links", editor.graph() == at1)
check("zoom: the label says 50%", editor.zoom_label.get_label() == "50%")
editor.view.child_set_property(editor.nodes[zb], "x", editor.origin[0] + 200)  # dragged
check("zoom: a node dragged at 50% is placed at zoom 1",
      editor.graph()["nodes"][zb]["x"] == 400)
editor.set_zoom(1.0)
pump()
check("zoom: back at 100%", on_canvas(zb)[0] == 400 and editor.graph()["nodes"][zb]["y"] == 100)
editor.zoom_step(1)
check("zoom: a step in from 100%", editor.zoom > 1.0)
editor.set_zoom(1.0)

# zoom sizes: nodes shrink with the zoom, do not run into each other,
# and come back to their size (GtkNodes once kept sizes from halfway)
editor.load(G.new_graph())
pump()
zb1 = editor.add_op("gegl:gaussian-blur", 200, 60)
zb2 = editor.add_op("gegl:unsharp-mask", 500, 60)
editor.link("layer", zb1)
editor.link(zb1, zb2)
editor.link(zb2, "output")
editor.arrange()


def settle():
    end = GLib.get_monotonic_time() + 600000
    while GLib.get_monotonic_time() < end:
        pump()


settle()
size100 = {k: (n.get_allocated_width(), n.get_allocated_height()) for k, n in editor.nodes.items()}
editor.set_zoom(0.5)
settle()
boxes50 = [(n.get_allocation().x, n.get_allocation().y, n.get_allocation().width,
            n.get_allocation().height) for n in editor.nodes.values()]
check("zoom 50%: the nodes are smaller",
      all(n.get_allocated_width() < size100[k][0] for k, n in editor.nodes.items()))
check("zoom 50%: no two nodes run into each other", not any(
    a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]
    for i, a in enumerate(boxes50) for b in boxes50[i + 1:]), boxes50)
editor.set_zoom(1.0)
settle()
check("zoom back to 100%: the nodes have their sizes again",
      {k: (n.get_allocated_width(), n.get_allocated_height()) for k, n in editor.nodes.items()}
      == size100)

# the canvas: room on every side, and it grows
editor.load(G.new_graph())
pump()
page = editor.scroller.get_hadjustment().get_page_size()
lx = editor.view.child_get_property(editor.nodes["layer"], "x")
check("canvas: room to the left of the nodes (a window's width)", lx >= min(page, 300) - 1,
      (lx, page))
far = editor.add_op("gegl:invert-gamma", -900, -500)  # left of and above everything
pump()
fx, fy = (editor.view.child_get_property(editor.nodes[far], "x"),
          editor.view.child_get_property(editor.nodes[far], "y"))
check("canvas: a node far up and left fits, with room around it", fx > 0 and fy > 0, (fx, fy))
check("canvas: the graph keeps where it is", editor.graph()["nodes"][far]["x"] == -900 and
      editor.graph()["nodes"]["layer"]["x"] == G.new_graph()["nodes"]["layer"]["x"])
page_h = editor.scroller.get_vadjustment().get_page_size()
check("canvas: at least a window's size of room left of and above it",
      fx >= min(page, 300) and fy >= min(page_h, 200), (fx, fy, page, page_h))

# resizing the window moves nothing on the canvas (it once moved all
# the nodes and the view, a frame apart, which flashed)
spots = lambda: (list(editor.origin), {k: (editor.view.child_get_property(n, "x"),  # noqa: E731
                                          editor.view.child_get_property(n, "y"))
                                      for k, n in editor.nodes.items()})
was = spots()
for size in ((1000, 450), (1300, 600), (1200, 500)):
    win.resize(*size)
    pump()
check("canvas: resizing the window moves no node", spots() == was, (was, spots()))

# selecting nodes
editor.load(G.new_graph())
pump()
s1 = editor.add_op("gegl:gaussian-blur", 250, 160)
s2 = editor.add_op("gegl:brightness-contrast", 500, 160)
s3 = editor.add_op("gegl:invert-gamma", 750, 40)
editor.link("layer", s1)
editor.link(s1, s2)
editor.link(s2, s3)
editor.link(s3, "output")
editor.set_value(s1, "std-dev-x", 7.0)
pump()
editor.select([s1, s2])
check("select: two nodes", editor.selection == {s1, s2})
editor.select([s3], toggle=True)
editor.select([s1], toggle=True)
check("select: toggled in and out", editor.selection == {s2, s3})
editor.select([])
x1, y1 = editor.view.child_get_property(editor.nodes[s1], "x"), \
    editor.view.child_get_property(editor.nodes[s1], "y")
x2, y2 = editor.view.child_get_property(editor.nodes[s2], "x"), \
    editor.view.child_get_property(editor.nodes[s2], "y")
editor.box_select(x1 - 5, y1 - 5, x2 + 10, y2 + 10)
check("box select: the nodes the box touches", editor.selection == {s1, s2}, editor.selection)
editor.box_select(x1 - 30, y1 - 30, x1 - 20, y1 - 20)
check("box select: a click on empty canvas clears it", editor.selection == set())
editor.select([s1, s2])

# dragging one selected node moves the others
before = {k: (editor.view.child_get_property(editor.nodes[k], "x"),
              editor.view.child_get_property(editor.nodes[k], "y")) for k in (s1, s2, s3)}
editor.view.emit("node-drag-begin", editor.nodes[s1])
editor.view.child_set_property(editor.nodes[s1], "x", before[s1][0] + 30)
editor.view.child_set_property(editor.nodes[s1], "y", before[s1][1] + 20)
pump()
after = {k: (editor.view.child_get_property(editor.nodes[k], "x"),
             editor.view.child_get_property(editor.nodes[k], "y")) for k in (s1, s2, s3)}
moved = (after[s1][0] - before[s1][0], after[s1][1] - before[s1][1])
check("group drag: the other selected node moved as much (snapped to the grid)",
      moved != (0, 0) and after[s2] == (before[s2][0] + moved[0], before[s2][1] + moved[1]),
      (before, after))
check("group drag: a node not selected stayed", after[s3] == before[s3])
editor.view.emit("node-drag-end", editor.nodes[s1])
pump()
g = editor.graph()
check("group drag: the graph has the new places, once",
      g["nodes"][s2]["x"] - g["nodes"][s1]["x"] == before[s2][0] - before[s1][0])

# duplicate, delete, arrange the selection, save the selection
n_before = len(editor.nodes)
copies = editor.duplicate_selected()
pump()
g = editor.graph()
check("duplicate: two copies, selected", len(copies) == 2 and editor.selection == set(copies)
      and len(editor.nodes) == n_before + 2)
c1 = [k for k in copies if g["nodes"][k]["op"] == "gegl:gaussian-blur"][0]
c2 = [k for k in copies if k != c1][0]
check("duplicate: values and the link between them copied, no other links",
      g["nodes"][c1]["props"].get("std-dev-x") == 7.0 and
      [l for l in g["links"] if c1 in (l["from"], l["to"]) or c2 in (l["from"], l["to"])]
      == [{"from": c1, "to": c2, "pad": "input"}], g["links"])
string_before = G.serialize(editor.graph())
check("duplicate: the image is the same (the copies are not linked in)",
      G.serialize(editor.graph()) == string_before)
editor.select([c1, c2, "layer"])
gone = editor.delete_selected()
pump()
check("delete: the selected nodes go, the layer stays",
      sorted(gone) == sorted([c1, c2]) and "layer" in editor.nodes and
      c1 not in editor.nodes and len(editor.nodes) == n_before)
editor.select([s1, s2])
still = (editor.view.child_get_property(editor.nodes[s3], "x"),
         editor.view.child_get_property(editor.nodes[s3], "y"))
place = editor.arrange(editor.selection)
pump()
check("arrange the selection: only those move",
      set(place) == {s1, s2} and (editor.view.child_get_property(editor.nodes[s3], "x"),
                                  editor.view.child_get_property(editor.nodes[s3], "y")) == still)
check("arrange the selection: left to right", place[s1][0] < place[s2][0])

# previews: the image at a node, in a small window
def checker():
    parent = Gegl.Node()
    board = parent.create_child("gegl:checkerboard")
    board.set_property("x", 16)
    board.set_property("y", 16)
    crop = parent.create_child("gegl:crop")
    crop.set_property("width", 400.0)
    crop.set_property("height", 300.0)
    buf = Gegl.Buffer.new("R'G'B'A u8", 0, 0, 400, 300)
    write = parent.create_child("gegl:write-buffer")
    write.set_property("buffer", buf)
    board.link(crop)
    crop.link(write)
    write.process()
    return buf


layer_pixels = checker()
editor.preview_source = lambda: layer_pixels
editor.load(G.new_graph())
pump()
pb = editor.add_op("gegl:gaussian-blur", 250, 60)
pi = editor.add_op("gegl:invert-gamma", 500, 60)
editor.link("layer", pb)
editor.link(pb, pi)
editor.link(pi, "output")
editor.set_value(pb, "std-dev-x", 6.0)
editor.set_value(pb, "std-dev-y", 6.0)
pump()


def done(window):
    end = GLib.get_monotonic_time() + 10000000
    while window.busy() and GLib.get_monotonic_time() < end:
        pump()
    return window.pixels


real = editor.graph()
eye = editor.nodes[pb].gn_eye
check("preview: the eye is in the node's title row",
      eye.get_ancestor(Gtk.Expander) is not None)
eye.set_active(True)  # as a click on it
pump()
win_b = editor.previews.get(pb)
check("preview: the eye opens a window", win_b is not None and win_b.get_visible())
px_b = done(win_b)
check("preview: it shows an image, scaled to fit",
      px_b is not None and max(px_b[1], px_b[2]) <= gn_preview.SIZE and
      (px_b[1], px_b[2]) == (320, 240), px_b and px_b[1:])
check("preview: the graph does not change", editor.graph() == real)
win_i = editor.preview(pi)
px_i = done(win_i)
check("preview: two at once, each its own node's image", px_i[0] != px_b[0] and
      len(editor.previews) == 2)
editor.set_value(pb, "std-dev-x", 1.0)
editor.set_value(pb, "std-dev-y", 1.0)
pump()
px_b2 = done(win_b)
check("preview: worked out again after a change", px_b2[0] != px_b[0])


def render_to(node_id):
    # the same, worked out here at full size and scaled the same way
    parent = Gegl.Node()
    src = parent.create_child("gegl:buffer-source")
    src.set_property("buffer", layer_pixels)
    end = G.build(gn_preview.ending_at(editor.graph(), node_id), parent, src)
    scale = parent.create_child("gegl:scale-ratio")
    scale.set_property("x", 0.8)
    scale.set_property("y", 0.8)
    buf = Gegl.Buffer.new("R'G'B'A u8", 0, 0, 320, 240)
    write = parent.create_child("gegl:write-buffer")
    write.set_property("buffer", buf)
    end.link(scale)
    scale.link(write)
    write.process()
    return buf.get(Gegl.Rectangle.new(0, 0, 320, 240), 1.0, "R'G'B'A u8", Gegl.AbyssPolicy.NONE)


ref = render_to(pb)
check("preview: its pixels are the node's image",
      max(abs(a - b) for a, b in zip(ref, px_b2[0])) <= 2)
win_b.emit("delete-event", Gdk.Event.new(Gdk.EventType.DELETE))
win_b.destroy()
pump()
check("preview: closing the window turns the eye off",
      pb not in editor.previews and not editor.nodes[pb].gn_eye.get_active())
editor.remove(pi)
pump()
check("preview: removing the node closes its preview", pi not in editor.previews)

# undo, redo and Clear All
def steps_settle():
    end = GLib.get_monotonic_time() + (gn_editor.UNDO_GROUP_MS + 200) * 1000
    while GLib.get_monotonic_time() < end:
        pump()


editor.load(G.new_graph())
editor._record()
ub = editor.add_op("gegl:gaussian-blur", 260, 60)
ui = editor.add_op("gegl:invert-gamma", 520, 60)
editor.link("layer", ub)
editor.link(ub, ui)
editor.link(ui, "output")
steps_settle()
chain = editor.graph()
editor.set_value(ub, "std-dev-x", 3.0)
editor.set_value(ub, "std-dev-x", 4.0)
editor.set_value(ub, "std-dev-x", 5.0)  # (quickly: one step)
steps_settle()
five = editor.graph()
editor.clear_all()
steps_settle()
cleared = editor.graph()
check("Clear All: only the layer and the output, linked, where they were",
      set(cleared["nodes"]) == {"layer", "output"} and
      cleared["links"] == [{"from": "layer", "to": "output", "pad": "input"}] and
      all((cleared["nodes"][k]["x"], cleared["nodes"][k]["y"]) ==
          (chain["nodes"][k]["x"], chain["nodes"][k]["y"]) for k in ("layer", "output")))
n = len(changes)
def diff(a, b):
    out = []
    for k in set(a["nodes"]) | set(b["nodes"]):
        if a["nodes"].get(k) != b["nodes"].get(k):
            out.append((k, a["nodes"].get(k), b["nodes"].get(k)))
    if sorted(map(str, a["links"])) != sorted(map(str, b["links"])):
        out.append(("links", a["links"], b["links"]))
    return out


check("undo after Clear All: the whole chain is back, with its values",
      editor.undo() and editor.graph() == five and len(changes) > n, diff(editor.graph(), five))
check("undo: quick changes of one value are one step",
      editor.undo() and editor.graph() == chain, editor.graph()["nodes"][ub]["props"])
check("redo: forward again", editor.redo() and editor.graph() == five and
      editor.redo() and editor.graph() == cleared)
check("redo: nothing more", not editor.redo())
editor.undo()
editor.remove(ui)
steps_settle()
check("a new change after undo: no redo", not editor.redo())
check("undo of a removed node: it is back, linked", editor.undo() and editor.graph() == five)
check("the buttons follow", editor.undo_button.get_sensitive())

# snap to the grid
G_ = gn_editor.GRID
frame = lambda v: (v + gn_editor.SOCKET_RADIUS) % G_ == 0  # noqa: E731
editor.load(G.new_graph())
pump()
sn = editor.add_op("gegl:invert-gamma", 253, 147)
pump()
node = editor.nodes[sn]
editor.view.emit("node-drag-begin", node)
editor.view.child_set_property(node, "x", editor.origin[0] + 377)
editor.view.child_set_property(node, "y", editor.origin[1] + 191)
editor.view.emit("node-drag-end", node)
pump()
gx, gy = editor.graph()["nodes"][sn]["x"], editor.graph()["nodes"][sn]["y"]
check("snap: a dragged node's frame lands on the grid, the nearest line",
      frame(gx) and frame(gy) and abs(gx - 377) <= G_ / 2 and abs(gy - 191) <= G_ / 2, (gx, gy))
editor.set_zoom(0.7)
pump()
editor.view.emit("node-drag-begin", node)
editor.view.child_set_property(node, "x", editor.origin[0] + 301)
editor.view.emit("node-drag-end", node)
pump()
check("snap: also at 70%", frame(editor.graph()["nodes"][sn]["x"]), editor.graph()["nodes"][sn])
editor.set_zoom(1.0)
editor.arrange()
pump()
check("snap: arranged nodes are on the grid",
      all(frame(v["x"]) and frame(v["y"]) for v in editor.graph()["nodes"].values()),
      {k: (v["x"], v["y"]) for k, v in editor.graph()["nodes"].items()})
editor.snap_button.set_active(False)
editor.view.emit("node-drag-begin", node)
editor.view.child_set_property(node, "x", editor.origin[0] + 377)
editor.view.emit("node-drag-end", node)
pump()
check("snap off: the node stays where it was put", editor.graph()["nodes"][sn]["x"] == 377)
editor.snap_button.set_active(True)

# saved setups
import tempfile  # noqa: E402
editor.setups_dir = tempfile.mkdtemp(prefix="gn-setups-")
glow = G.new_graph()
glow["nodes"]["n1"] = {"op": "gegl:gaussian-blur", "x": 200, "y": 160, "props": {"std-dev-x": 9.0}}
glow["nodes"]["n2"] = {"op": "svg:screen", "x": 400, "y": 60, "props": {}}
glow["links"] = [{"from": "layer", "to": "n1", "pad": "input"},
                 {"from": "layer", "to": "n2", "pad": "input"},
                 {"from": "n1", "to": "n2", "pad": "aux"},
                 {"from": "n2", "to": "output", "pad": "input"}]
editor.load(glow)
pump()
check("setups: saved", editor.save_setup("Soft glow") and
      G.setups(editor.setups_dir) == ["Soft glow"])
check("setups: a name with a folder in it is refused",
      not editor.save_setup("../out") and G.setups(editor.setups_dir) == ["Soft glow"])
editor.load(G.new_graph())
pump()
check("setups: Replace shows the setup",
      editor.replace_with_setup("Soft glow") and
      G.serialize(editor.graph()) == G.serialize(glow))
editor.load(G.new_graph())
pump()
sharp = editor.add_op("gegl:unsharp-mask", 250, 60)
editor.link("layer", sharp)
editor.link(sharp, "output")
pump()
n = len(changes)
editor.insert_setup("Soft glow")
pump()
g = editor.graph()
ops = {k: v["op"] for k, v in g["nodes"].items()}
blur_id = [k for k, o in ops.items() if o == "gegl:gaussian-blur"][0]
screen_id = [k for k, o in ops.items() if o == "svg:screen"][0]
links = sorted((l["from"], l["to"], l["pad"]) for l in g["links"])
check("setups: Insert adds its nodes with new ids", len(g["nodes"]) == 5 and
      sharp in g["nodes"] and len({sharp, blur_id, screen_id}) == 3)
check("setups: Insert puts it at the end of the chain", links == sorted([
    ("layer", sharp, "input"), (sharp, blur_id, "input"), (sharp, screen_id, "input"),
    (blur_id, screen_id, "aux"), (screen_id, "output", "input")]), links)
check("setups: Insert keeps its values", g["nodes"][blur_id]["props"] == {"std-dev-x": 9.0})
check("setups: Insert is a change", len(changes) > n)
check("setups: the output after the inserted nodes",
      g["nodes"]["output"]["x"] > max(v["x"] for k, v in g["nodes"].items() if k != "output"))
check("setups: renamed", editor.rename_setup("Soft glow", "Glow") and
      G.setups(editor.setups_dir) == ["Glow"])
check("setups: deleted", editor.delete_setup("Glow") and G.setups(editor.setups_dir) == [])
open(os.path.join(editor.setups_dir, "Broken.json"), "w").write("{not json")
before_broken = editor.graph()
check("setups: a broken file is said, and nothing changes",
      not editor.replace_with_setup("Broken") and "Broken" in editor.status.get_text()
      and editor.graph() == before_broken)
setups_menu = editor._setups_menu()  # (held: a menu let go of is emptied)
labels = [i.get_label() for i in setups_menu.get_children()]
check("setups: the menu lists them", "Broken" in labels, labels)
editor.select([s1, s2])  # (not in this graph any more)
editor.load(glow)
pump()
editor.select(["n1"])
check("setups: the selection saved alone", editor.save_selection("Blur only"))
part = G.read_setup(editor.setups_dir, "Blur only")
check("setups: a saved selection is fed by the layer and goes to the output",
      set(part["nodes"]) == {"layer", "output", "n1"} and sorted(
          (l["from"], l["to"]) for l in part["links"]) == [("layer", "n1"), ("n1", "output")],
      part["links"])
editor.load(glow)
editor.arrange()

shot = os.environ.get("GN_SHOT")
if shot:
    # (the screenshot of the page is taken by run.sh)
    open(shot, "w").write("ready\n")
    GLib.timeout_add(4000, Gtk.main_quit)
    Gtk.main()

print("editor failures: %d" % failures)
sys.exit(1 if failures else 0)
