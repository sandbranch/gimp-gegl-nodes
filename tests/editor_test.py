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
gi.require_version("Gegl", "0.4")
from gi.repository import Gegl, GLib, Gtk  # noqa: E402

Gegl.init(None)

import gn_editor  # noqa: E402
import gn_graph as G  # noqa: E402
import gn_ops  # noqa: E402

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
check("zoom: the places on screen are halved",
      (editor.view.child_get_property(editor.nodes[zb], "x"),
       editor.view.child_get_property(editor.nodes[zb], "y")) == (150, 50))
check("zoom: the graph keeps its places and links", editor.graph() == at1)
check("zoom: the label says 50%", editor.zoom_label.get_label() == "50%")
editor.view.child_set_property(editor.nodes[zb], "x", 200)  # dragged at 50%
check("zoom: a node dragged at 50% is placed at zoom 1",
      editor.graph()["nodes"][zb]["x"] == 400)
editor.set_zoom(1.0)
pump()
check("zoom: back at 100%", editor.view.child_get_property(editor.nodes[zb], "x") == 400
      and editor.graph()["nodes"][zb]["y"] == 100)
editor.zoom_step(1)
check("zoom: a step in from 100%", editor.zoom > 1.0)
editor.set_zoom(1.0)

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
