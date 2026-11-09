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
      "std-dev-x=4.5" in s and "abyss-policy=black" in s and "ref=r1" in s
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
first = editor.category_buttons[0].get_popup().get_children()[0]
first.activate()
pump()
check("a filter picked in the menu row is added", len(set(editor.nodes) - before) == 1)
check("nodes are styled out of the theme's box background",
      all(n.get_style_context().has_class("gegl-nodes-node") for n in editor.nodes.values()))

shot = os.environ.get("GN_SHOT")
if shot:
    # (the screenshot of the page is taken by run.sh)
    open(shot, "w").write("ready\n")
    GLib.timeout_add(4000, Gtk.main_quit)
    Gtk.main()

print("editor failures: %d" % failures)
sys.exit(1 if failures else 0)
