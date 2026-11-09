# Executed inside the plug-in when GEGL_NODES_TEST names this file
# (tests/gui/gui-test.sh): drives the open editor through the calls its
# widgets use, waits for GIMP to update the layer, and checks the filter
# and its pixels. Writes $GN_OUT/hook.txt, and $GN_OUT/ready when the
# screens are ready for the screenshot; closes the editor when
# $GN_OUT/shot-taken appears.
#
# Given by the plug-in: session, image, drawable, Gimp, Gegl, GLib, Gtk.
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later

import os as _os

import gn_graph as _G

_out = _os.environ["GN_OUT"]
_lines = []
_W, _H = drawable.get_width(), drawable.get_height()  # noqa: F821
_FMT = "R'G'B'A u8"
_rect = Gegl.Rectangle.new(0, 0, _W, _H)  # noqa: F821
_state = {}


def _check(name, ok, detail=""):
    _lines.append("%s %s%s" % ("PASS" if ok else "FAIL", name,
                               (": " + str(detail)) if detail and not ok else ""))
    with open(_os.path.join(_out, "hook.txt"), "w") as f:
        f.write("\n".join(_lines) + "\n")


def _merged():
    dup = image.duplicate()  # noqa: F821
    layer = dup.get_layers()[0]
    layer.merge_filters()
    px = layer.get_buffer().get(_rect, 1.0, _FMT, Gegl.AbyssPolicy.NONE)  # noqa: F821
    dup.delete()
    return px


def _render(string):
    parent = Gegl.Node()  # noqa: F821
    source = parent.create_child("gegl:buffer-source")
    source.set_property("buffer", drawable.get_buffer())  # noqa: F821
    node = parent.create_child("nodes:graph")
    node.set_property("string", string)
    dst = Gegl.Buffer.new(_FMT, 0, 0, _W, _H)  # noqa: F821
    write = parent.create_child("gegl:write-buffer")
    write.set_property("buffer", dst)
    source.link(node)
    node.link(write)
    write.process()
    return dst.get(_rect, 1.0, _FMT, Gegl.AbyssPolicy.NONE)  # noqa: F821


def _maxdiff(a, b):
    return max(abs(x - y) for x, y in zip(a, b))


def _later(ms, fn):
    GLib.timeout_add(ms, lambda: fn() or False)  # noqa: F821


def _step1():
    # the editor docked under GIMP's window, as in tests/gui/gui-test.sh
    session.window.move(0, 470)  # noqa: F821
    session.window.resize(1400, 530)  # noqa: F821
    e = session.editor  # noqa: F821
    _state["original"] = drawable.get_buffer().get(  # noqa: F821
        _rect, 1.0, _FMT, Gegl.AbyssPolicy.NONE)  # noqa: F821
    # a glow: the layer, screened with a blurred copy of itself, then a
    # vignette
    n = e.nodes["output"]
    e._loading = True
    # (on the canvas, the graph's 0, 0 is at e.origin)
    e.view.child_set_property(n, "x", e.origin[0] + 1240)
    e.view.child_set_property(n, "y", e.origin[1] + 20)
    e.view.child_set_property(e.nodes["layer"], "y", e.origin[1] + 20)
    e._loading = False
    blur = e.add_op("gegl:gaussian-blur", 150, 150)
    screen = e.add_op("svg:screen", 560, 20)
    vig = e.add_op("gegl:vignette", 820, 10)
    e.link("layer", blur)
    e.link("layer", screen)
    e.link(blur, screen, "aux")
    e.link(screen, vig)
    e.link(vig, "output")
    e.set_value(blur, "std-dev-x", 12.0)
    e.set_value(blur, "std-dev-y", 12.0)
    e.set_value(vig, "radius", 1.3)
    _state["blur"] = blur
    _state["places"] = {"layer": (20, 20), "output": (1240, 20), blur: (150, 150),
                        screen: (560, 20), vig: (820, 10)}
    _later(2500, _check1)


def _check1():
    e = session.editor  # noqa: F821
    # placed, read back, and drawn there
    ox, oy = e.origin
    places = {k: (e.view.child_get_property(n, "x") - ox, e.view.child_get_property(n, "y") - oy,
                  n.get_allocation().x - ox, n.get_allocation().y - oy)
              for k, n in e.nodes.items()}
    _check("the nodes are where they were put (GtkNodes with fix/child-y)",
           all(places[k] == xy * 2 for k, xy in _state["places"].items()), places)
    want = _G.serialize(e.graph())
    c = session.filter.get_config()  # noqa: F821
    got = c.get_property("string")
    _check("the filter has the editor's graph", got == want, "%r != %r" % (got, want))
    _check("the filter has the editor's layout",
           _G.from_json(c.get_property("layout"))["links"] == e.graph()["links"])
    px = _merged()
    _check("the layer shows the graph", px != _state["original"])
    d = _maxdiff(px, _render(want))
    _check("its pixels are those of the graph (max difference %d)" % d, d <= 1)
    _state["px1"] = px
    # a value changed in the editor, as a user turns a knob
    e.set_value(_state["blur"], "std-dev-x", 30.0)
    e.set_value(_state["blur"], "std-dev-y", 30.0)
    _later(2500, _check2)


def _check2():
    e = session.editor  # noqa: F821
    want = _G.serialize(e.graph())
    got = session.filter.get_config().get_property("string")  # noqa: F821
    _check("a changed value reaches the filter", got == want and "std-dev-x=30" in got, got)
    px = _merged()
    d = _maxdiff(px, _render(want))
    _check("the layer follows (max difference %d)" % d, d <= 1 and px != _state["px1"])
    _check("the layer itself is not changed", drawable.get_buffer().get(  # noqa: F821
        _rect, 1.0, _FMT, Gegl.AbyssPolicy.NONE) == _state["original"])  # noqa: F821
    # a preview of the blur, from the layer's own pixels
    _state["layer_px"] = px
    _state["preview"] = e.preview(_state["blur"])
    _later(3000, _check3)


def _check3():
    e = session.editor  # noqa: F821
    win = _state["preview"]
    _check("preview in GIMP: a window with the blur's image",
           win is not None and win.pixels is not None and max(win.pixels[1], win.pixels[2]) <= 320,
           win and win.pixels and win.pixels[1:])
    _check("preview in GIMP: the layer still shows the whole graph", _merged() == _state["layer_px"])
    # (left open for the screenshot; closing the editor closes it)
    Gimp.displays_flush()  # noqa: F821
    open(_os.path.join(_out, "ready"), "w").close()
    _state["waited"] = 0
    _later(300, _wait_shot)


def _wait_shot():
    if _os.path.exists(_os.path.join(_out, "shot-taken")) or _state["waited"] > 200:
        session.close()  # noqa: F821
        return
    _state["waited"] += 1
    _later(300, _wait_shot)


_step1()
