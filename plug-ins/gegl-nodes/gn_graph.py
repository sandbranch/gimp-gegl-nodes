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
    is the layer as it is.

    Written here, not by gegl_serialize: that one writes only the input
    and aux pads, so links into aux2 (gegl:remap), aux1 to aux16
    (gegl:piecewise-blend) or exposure-N (gegl:exp-combine) were lost.
    GEGL's chain parser reads any pad as pad=[ ... ]. A result used more
    than once is named the first time (id=rN) and referred to after that
    (ref=rN); the layer, when used anywhere but at the start, is named L
    at the very start of the string."""
    parent = Gegl.Node()
    source = parent.create_child("gegl:nop")
    end = build(graph, parent, source)  # checks loops and missing operations
    if end is None or end is source:
        return ""
    uses = {}
    for link in graph["links"]:
        uses[link["from"]] = uses.get(link["from"], 0) + 1
    names = {}
    done = set()
    layer_named = [False]

    def chain(node_id):
        """The tokens that give node_id's output."""
        if node_id == LAYER:
            layer_named[0] = True
            return ["ref=L"]
        if node_id in done:
            return ["ref=" + names[node_id]]
        spec = graph["nodes"][node_id]
        pads = [p for p in _input_pads(spec["op"])]
        upstream = input_of(graph, node_id, "input") if "input" in pads else None
        tokens = chain(upstream) if upstream is not None else []
        tokens.append(_node_text(spec))
        for pad in pads:
            if pad == "input":
                continue
            other = input_of(graph, node_id, pad)
            if other is not None:
                tokens += ["%s=[" % pad] + chain(other) + ["]"]
        done.add(node_id)
        if uses.get(node_id, 0) > 1:
            names[node_id] = "r%d" % (len(names) + 1)
            tokens.append("id=" + names[node_id])
        return tokens

    tokens = chain(input_of(graph, OUTPUT, "input"))
    # the chain starts at the layer: no ref needed there
    if tokens and tokens[0] == "ref=L":
        tokens = tokens[1:]
        layer_named[0] = "ref=L" in tokens
    if layer_named[0]:
        tokens.insert(0, "id=L")
    return " ".join(" ".join(tokens).split())


def _input_pads(op):
    pads = Gegl.Node().create_child(op).list_input_pads()
    # input first, then the others in their natural order (aux2 before aux10)
    return sorted(pads, key=lambda p: (p != "input", [
        int(t) if t.isdigit() else t for t in re.split(r"(\d+)", p)]))


def _node_text(spec):
    """One operation with its values, as GEGL writes it (defaults left
    out), with colors exact."""
    parent = Gegl.Node()
    node = parent.create_child(spec["op"])
    pspecs = {p.name: p for p in Gegl.Operation.list_properties(spec["op"])}
    for name, value in spec.get("props", {}).items():
        if name in pspecs:
            set_value(node, pspecs[name], value)
    start = parent.create_child("gegl:nop")
    if "input" in node.list_input_pads():
        start.connect_to("output", node, "input")
    text = Gegl.serialize(start, node, "", Gegl.SerializeFlag.TRIM_DEFAULTS)
    return _exact_colors({"nodes": {"n": spec}}, " ".join(text.split()))


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
