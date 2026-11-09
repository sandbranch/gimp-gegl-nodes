# The node graph of the editor, without any user interface: what the
# nodes are, how they are linked, and the GEGL graph and graph string
# (for the nodes:graph filter) made from them.
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later
#
# A graph is plain data, kept as JSON in the filter's "layout" property:
#
#   {"version": 1,
#    "nodes": {"n1": {"op": "gegl:gaussian-blur", "x": 200, "y": 40,
#                     "props": {"std-dev-x": 3.0}}, ...},
#    "links": [{"from": "layer", "to": "n1", "pad": "input"},
#              {"from": "n1", "to": "output", "pad": "input"}, ...]}
#
# Two nodes are always there and have no operation: "layer", the layer
# the filter is on (the filter's input), and "output", what the filter
# gives. A link goes from the output of one node to an input pad of
# another; a pad has at most one link.

import json
import re

import gi
gi.require_version("Gegl", "0.4")
from gi.repository import Gegl  # noqa: E402

GRAPH_OP = "nodes:graph"
LAYER = "layer"
OUTPUT = "output"
VERSION = 1


class GraphError(Exception):
    pass


def new_graph():
    return {"version": VERSION,
            "nodes": {LAYER: {"op": None, "x": 20, "y": 60, "props": {}},
                      OUTPUT: {"op": None, "x": 600, "y": 60, "props": {}}},
            "links": [{"from": LAYER, "to": OUTPUT, "pad": "input"}]}


def to_json(graph):
    return json.dumps(graph, sort_keys=True, separators=(",", ":"))


def from_json(text):
    if not text:
        return new_graph()
    graph = json.loads(text)
    if not isinstance(graph, dict) or graph.get("version") != VERSION:
        raise GraphError("not a node graph of version %d" % VERSION)
    for special in (LAYER, OUTPUT):
        graph["nodes"].setdefault(special, new_graph()["nodes"][special])
    return graph


def new_id(graph):
    i = 1
    while "n%d" % i in graph["nodes"]:
        i += 1
    return "n%d" % i


def input_of(graph, node_id, pad):
    for link in graph["links"]:
        if link["to"] == node_id and link["pad"] == pad:
            return link["from"]
    return None


def set_value(node, pspec, value):
    """Sets a property of a GEGL node from its JSON form (enums by
    nick, colors as CSS strings)."""
    kind = pspec.__gtype__.name
    if kind == "GeglParamEnum" or kind == "GParamEnum":
        if isinstance(value, str):
            values = pspec.enum_class.__enum_values__
            for number, ev in values.items():
                if ev.value_nick == value:
                    value = number
                    break
            else:
                raise GraphError("%s: no value %r" % (pspec.name, value))
        node.set_property(pspec.name, int(value))
    elif kind == "GeglParamColor" or pspec.value_type.name == "GeglColor":
        node.set_property(pspec.name, Gegl.Color.new(str(value)))
    elif kind in ("GeglParamDouble", "GParamDouble"):
        node.set_property(pspec.name, float(value))
    elif kind in ("GeglParamInt", "GParamInt"):
        node.set_property(pspec.name, int(value))
    elif kind == "GParamBoolean":
        node.set_property(pspec.name, bool(value))
    else:
        node.set_property(pspec.name, value)


def build(graph, parent, source):
    """Makes the GEGL nodes of the graph as children of parent, with
    source as the layer. Returns the node that gives the output (source
    itself if the output is linked straight to the layer), or None if
    nothing is linked to the output. Only nodes the output depends on
    are made."""
    made = {LAYER: source}
    busy = set()

    def make(node_id):
        if node_id in made:
            return made[node_id]
        if node_id in busy:
            raise GraphError("the graph has a loop through %s" % node_id)
        spec = graph["nodes"].get(node_id)
        if spec is None:
            raise GraphError("a link to a node that is not there: %s" % node_id)
        busy.add(node_id)
        node = parent.create_child(spec["op"])
        if node.get_operation() != spec["op"]:
            raise GraphError("the operation %s is not installed" % spec["op"])
        pspecs = {p.name: p for p in Gegl.Operation.list_properties(spec["op"])}
        for name, value in spec.get("props", {}).items():
            if name in pspecs:
                set_value(node, pspecs[name], value)
        for pad in node.list_input_pads():
            upstream = input_of(graph, node_id, pad)
            if upstream is not None:
                make(upstream).connect_to("output", node, pad)
        busy.discard(node_id)
        made[node_id] = node
        return node

    end = input_of(graph, OUTPUT, "input")
    if end is None:
        return None
    return make(end)


def serialize(graph):
    """The graph string for the nodes:graph filter. Empty when the output
    is the layer as it is."""
    parent = Gegl.Node()
    source = parent.create_child("gegl:nop")
    end = build(graph, parent, source)
    if end is None or end is source:
        return ""
    text = Gegl.serialize(source, end, "", Gegl.SerializeFlag.TRIM_DEFAULTS)
    # GEGL names results used twice by their address (id=0x55d0c8e0);
    # the same graph should give the same string
    names = {}

    def rename(m):
        return names.setdefault(m.group(1), "r%d" % (len(names) + 1))
    text = re.sub(r"\b(0x[0-9a-f]+)\b", rename, text)
    return " ".join(_exact_colors(graph, text).split())


def _gegl_color_text(value):
    """A color as Gegl.serialize writes it: linear, three decimals"""
    r, g, b, a = Gegl.Color.new(str(value)).get_rgba()
    if a == 1.0:
        return "'rgb(%.3f, %.3f, %.3f)'" % (r, g, b)
    return "'rgba(%.3f, %.3f, %.3f, %.3f)'" % (r, g, b, a)


def _exact_colors(graph, text):
    """Puts the colors of the graph back as they are (#rrggbbaa):
    GEGL keeps only three decimals of the linear values, which is up to
    a step of 8 bits off in dark colors."""
    for node_id, spec in graph["nodes"].items():
        if not spec.get("op"):
            continue
        pspecs = {p.name: p for p in Gegl.Operation.list_properties(spec["op"])}
        for name, value in spec.get("props", {}).items():
            p = pspecs.get(name)
            if p is not None and p.value_type.name == "GeglColor" and \
                    isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6,8}", value):
                text = text.replace("%s=%s" % (name, _gegl_color_text(value)),
                                    "%s=%s" % (name, value), 1)
    return text
