# Drags nodes with real pointer events (pointer.py, XTest) on an X
# display, and checks that a dragged node follows the pointer: no jumps
# back and forth, and it ends where the pointer took it (to the grid with
# snap on). Run by tests/run.sh on Xvfb.
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import subprocess
import sys

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(here)), "plug-ins", "gegl-nodes"))

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gegl", "0.4")
from gi.repository import Gegl, GLib, Gtk  # noqa: E402

Gegl.init(None)
import gn_editor  # noqa: E402
import gn_graph as G  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       (": " + str(detail)) if detail and not ok else ""), flush=True)


def pump(t=0.3):
    end = GLib.get_monotonic_time() + int(t * 1e6)
    while GLib.get_monotonic_time() < end:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)


win = Gtk.Window()
win.set_default_size(1100, 600)
win.move(0, 0)
editor = gn_editor.GraphEditor()
win.add(editor)
win.show_all()
pump(1)
blur = editor.add_op("gegl:gaussian-blur", 300, 200)
inv = editor.add_op("gegl:invert-gamma", 700, 200)
editor.link("layer", blur)
editor.link(blur, inv)
editor.link(inv, "output")
pump(1)
editor.show_nodes()
pump(1)

track = []


def follow(node, pspec):
    # (the node's own x and y: GtkNodes' child properties change what they
    # read, halfway through a move)
    track.append((node.get_property("x"), node.get_property("y")))


def on_screen(k, dx=30, dy=12):
    """A point on node k's title, on the screen."""
    node = editor.nodes[k]
    x, y = node.translate_coordinates(win, dx, dy)
    ox, oy = win.get_window().get_origin()[1:]
    return ox + x, oy + y


def drag(k, dx, dy, steps=40):
    node = editor.nodes[k]
    track.clear()
    handler = node.connect("notify::y", follow)
    x0, y0 = on_screen(k)
    start = tuple(editor.graph()["nodes"][k][c] for c in "xy")
    origin = list(editor.origin)
    p = subprocess.Popen([sys.executable, os.path.join(here, "pointer.py"), "drag",
                          str(x0), str(y0), str(x0 + dx), str(y0 + dy), str(steps)])
    while p.poll() is None:
        pump(0.05)
    pump(0.5)
    node.disconnect(handler)
    if os.environ.get("GN_DRAG_LOG"):
        a = node.get_allocation()
        print("  node alloc", a.x, a.y, a.width, a.height, "pointer from", (x0, y0))
        print("  view alloc", editor.view.get_allocation().width, editor.view.get_allocation().height,
              "track", track)
    end = tuple(editor.graph()["nodes"][k][c] for c in "xy")
    print("  drag %s: graph %s -> %s, origin %s -> %s, canvas %s, track %s" % (
        k, start, end, origin, editor.origin, editor.view.get_size_request(), track[:3] + track[-3:]))
    return start, end, list(track)


def smooth(track, dx):
    """The node never goes back against the drag by more than the grid."""
    xs = [x for x, y in track]
    back = [b - a for a, b in zip(xs, xs[1:]) if (b - a) * dx < 0]
    return not back or max(abs(b) for b in back) <= gn_editor.GRID * editor.zoom, back


for dx, dy in ((0, 90), (0, -60)):
    editor.snap_button.set_active(False)
    start, end, _ = drag(blur, dx, dy, 15)
    check("a node follows the pointer up and down %s" % ((dx, dy),),
          (end[0] - start[0], end[1] - start[1]) == (dx, dy), (start, end))

for snap in (False, True):
    editor.snap_button.set_active(snap)
    pump(0.2)
    start, end, track_ = drag(blur, 150, 60)
    name = "snap %s" % ("on" if snap else "off")
    ok, back = smooth(track_, 150)
    check("%s: the dragged node moves smoothly (moves seen: %d)" % (name, len(track_)),
          ok and len(track_) > 5, back[:10])
    tol = gn_editor.GRID / 2 + 1 if snap else 1
    check("%s: it ends where the pointer took it" % name,
          abs(end[0] - start[0] - 150) <= tol and abs(end[1] - start[1] - 60) <= tol,
          (start, end))
    if snap:
        gx = editor.graph()["nodes"][blur]["x"]
        check("snap on: its frame is on the grid",
              (gx + gn_editor.SOCKET_RADIUS) % gn_editor.GRID == 0, gx)

# grabbed anywhere on the node, and toward the edges of the canvas: the
# canvas does not move under the drag (it once refitted itself halfway
# through one, and the node lost the pointer); the pointer stays on the
# (1200 x 700) screen
editor.snap_button.set_active(False)
editor.select([])
for name, grab, dx, dy in (("the lower part, up and right", (60, 200), 90, -70),
                           ("the body, down", (20, 60), 0, 100),
                           ("toward the left edge", (30, 12), -400, 0),
                           ("toward the top", (30, 12), 0, -230)):
    node = editor.nodes[blur]
    track.clear()
    # (scrolled so that the node is at 450, 250 in the window)
    editor.scroller.get_hadjustment().set_value(node.get_property("x") - 450)
    editor.scroller.get_vadjustment().set_value(node.get_property("y") - 250)
    pump(0.3)
    x0, y0 = on_screen(blur, *grab)
    assert 0 < x0 < 1100 and 0 < y0 < 600 and 0 < x0 + dx < 1100 and 0 < y0 + dy < 600, \
        "the drag would leave the window: %s" % ((x0, y0, dx, dy),)
    a = tuple(editor.graph()["nodes"][blur][c] for c in "xy")
    rest = {k: tuple(editor.graph()["nodes"][k][c] for c in "xy") for k in editor.nodes if k != blur}
    p = subprocess.Popen([sys.executable, os.path.join(here, "pointer.py"), "drag",
                          str(x0), str(y0), str(x0 + dx), str(y0 + dy), "25"])
    while p.poll() is None:
        pump(0.05)
    pump(0.6)
    b = tuple(editor.graph()["nodes"][blur][c] for c in "xy")
    now = {k: tuple(editor.graph()["nodes"][k][c] for c in "xy") for k in rest}
    check("drag by %s: follows the pointer, nothing else moves" % name,
          (b[0] - a[0], b[1] - a[1]) == (dx, dy) and now == rest, (a, b))

# two selected, one dragged: both move as far
editor.snap_button.set_active(False)
editor.select([blur, inv])
other = tuple(editor.graph()["nodes"][inv][c] for c in "xy")
start, end, _ = drag(blur, 80, 50, 20)
after = tuple(editor.graph()["nodes"][inv][c] for c in "xy")
check("group drag: the other selected node moved as far",
      (after[0] - other[0], after[1] - other[1]) == (end[0] - start[0], end[1] - start[1])
      == (80, 50), (start, end, other, after))

shot = os.environ.get("GN_DRAG_SHOT")
if shot:
    subprocess.call(["import", "-window", "root", shot])
print("drag failures: %d" % failures)
sys.exit(1 if failures else 0)
