# Run by GIMP (python-fu-eval) on a Broadway display for
# tests/gui/gui-test.sh: makes an image, shows it, and runs the Node
# Editor on it interactively. The editor is driven from inside the
# plug-in by tests/gui/hook.py (GEGL_NODES_TEST). When the editor is
# closed, writes what the layer has to $GN_OUT/result.txt and quits GIMP.
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later

import math
import os

import gi
gi.require_version("Gimp", "3.0")
gi.require_version("Gegl", "0.4")
from gi.repository import Gegl, Gimp

out = os.environ["GN_OUT"]
W, H = 520, 340


def write(name, lines):
    with open(os.path.join(out, name + ".tmp"), "w") as f:
        f.write("\n".join(lines) + "\n")
    os.rename(os.path.join(out, name + ".tmp"), os.path.join(out, name))


# a picture with something to see: a sky, a sun and hills
data = bytearray()
for y in range(H):
    for x in range(W):
        t = y / H
        r, g, b = 0.25 + 0.5 * t, 0.45 + 0.3 * t, 0.85 - 0.3 * t
        if (x - 360) ** 2 + (y - 110) ** 2 < 55 ** 2:
            r, g, b = 1.0, 0.85, 0.3
        hill = H * 0.68 + 30 * math.sin(x / 47.0) + 14 * math.sin(x / 13.0)
        if y > hill:
            r, g, b = 0.15, 0.45 + 0.2 * math.sin(x / 9.0) ** 2, 0.2
        data += bytes((int(r * 255), int(g * 255), int(b * 255), 255))

image = Gimp.Image.new(W, H, Gimp.ImageBaseType.RGB)
layer = Gimp.Layer.new(image, "landscape", W, H, Gimp.ImageType.RGBA_IMAGE, 100,
                       Gimp.LayerMode.NORMAL)
image.insert_layer(layer, None, 0)
buf = layer.get_buffer()
buf.set(Gegl.Rectangle.new(0, 0, W, H), "R'G'B'A u8", bytes(data))
buf.flush()
image.clean_all()
display = Gimp.Display.new(image)
Gimp.displays_flush()

pdb = Gimp.get_pdb()
proc = pdb.lookup_procedure("plug-in-gegl-nodes")
lines = []
if proc is None:
    lines.append("FAIL the plug-in is not installed")
else:
    config = proc.create_config()
    config.set_property("run-mode", Gimp.RunMode.INTERACTIVE)
    config.set_property("image", image)
    config.set_core_object_array("drawables", [layer])
    result = proc.run(config)
    status = result.index(0)
    lines.append("%s the plug-in ran (%s)" % (
        "PASS" if status == Gimp.PDBStatusType.SUCCESS else "FAIL", status.value_nick))
    filters = [f for f in layer.get_filters() or []
               if f.get_operation_name() == "nodes:graph"]
    lines.append("%s the Node Graph filter stays on the layer after the editor is closed"
                 % ("PASS" if len(filters) == 1 else "FAIL"))
    if filters:
        c = filters[0].get_config()
        lines.append("%s with its graph and layout" % (
            "PASS" if c.get_property("string") and c.get_property("layout") else "FAIL"))
write("result.txt", lines)

quit_proc = pdb.lookup_procedure("gimp-quit")
qc = quit_proc.create_config()
qc.set_property("force", True)
quit_proc.run(qc)
