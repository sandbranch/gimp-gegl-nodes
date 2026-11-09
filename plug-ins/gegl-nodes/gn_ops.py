# The GEGL operations the node editor offers, and what their nodes look
# like: input pads and the properties that can be edited.
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later
#
# The rules match those of the nodes:graph operation
# (operations/node-graph.c), which is what enforces them; here they only
# keep operations out of the list that the filter would refuse.

import re

import gi
gi.require_version("Gegl", "0.4")
from gi.repository import Gegl  # noqa: E402

FORBIDDEN_CATEGORIES = {"input", "output", "programming", "display", "video", "debug"}
FORBIDDEN_NAMES = {"gegl:gegl", "nodes:graph", "gegl:introspect"}
# also not offered: of no use in the editor
HIDDEN_CATEGORIES = {"hidden"}
HIDDEN_NAMES = {"gegl:nop", "gegl:clone", "gegl:convert-format", "gegl:cast-format",
                "gegl:buffer-source", "gegl:introspect"}

# property types the editor has a widget for
EDITABLE = {"GeglParamDouble", "GParamDouble", "GeglParamInt", "GParamInt",
            "GParamBoolean", "GeglParamEnum", "GParamEnum", "GeglParamColor",
            "GeglParamString", "GParamString"}
FILE_PARAMS = {"GeglParamFilePath", "GeglParamUri"}


class OpInfo:
    def __init__(self, name):
        self.name = name
        # (some have no title, such as the svg: blend modes)
        self.title = Gegl.Operation.get_key(name, "title") or \
            name.split(":")[-1].replace("-", " ").capitalize()
        self.categories = (Gegl.Operation.get_key(name, "categories") or "").split(":")
        self.description = Gegl.Operation.get_key(name, "description") or ""
        node = Gegl.Node().create_child(name)
        # input first, then the others in their natural order (aux2 before aux10)
        self.inputs = sorted(node.list_input_pads(), key=lambda p: (p != "input", [
            int(t) if t.isdigit() else t for t in re.split(r"(\d+)", p)]))
        self.outputs = list(node.list_output_pads())
        self.pspecs = list(Gegl.Operation.list_properties(name))
        self.props = [p for p in self.pspecs
                      if p.__gtype__.name in EDITABLE or p.value_type.name == "GeglColor"]

    def why_not(self):
        """Why the filter would refuse this operation, or None."""
        if self.name in FORBIDDEN_NAMES:
            return "not allowed in the graph"
        if "output" not in self.outputs:
            return "no output"
        bad = FORBIDDEN_CATEGORIES.intersection(self.categories)
        if bad:
            return "category " + sorted(bad)[0]
        if any(p.__gtype__.name in FILE_PARAMS for p in self.pspecs):
            return "reads or writes files"
        return None


_catalog = None


def catalog():
    """The operations to offer, by name."""
    global _catalog
    if _catalog is None:
        _catalog = {}
        for name in Gegl.list_operations():
            if name in HIDDEN_NAMES:
                continue
            try:
                info = OpInfo(name)
            except Exception:
                continue
            if info.why_not() or HIDDEN_CATEGORIES.intersection(info.categories):
                continue
            _catalog[name] = info
    return _catalog
