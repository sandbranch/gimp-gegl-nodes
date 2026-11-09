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
import os
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


# saved setups: a graph in a file of its own, to use again on another
# layer (the editor's Setups menu)

SETUP_SUFFIX = ".json"


def setup_path(folder, name):
    """The file of the setup called name. A name is a file name without
    its suffix: no folders in it."""
    name = name.strip()
    if not name or name.startswith(".") or "/" in name or "\\" in name or "\0" in name:
        raise GraphError("not a name for a setup: %r" % name)
    return os.path.join(folder, name + SETUP_SUFFIX)


def setups(folder):
    """The names of the setups saved in folder, sorted."""
    try:
        names = os.listdir(folder)
    except FileNotFoundError:
        return []
    return sorted((n[:-len(SETUP_SUFFIX)] for n in names
                   if n.endswith(SETUP_SUFFIX) and not n.startswith(".")), key=str.lower)


def save_setup(folder, name, graph):
    """Saves the graph as a setup (written to a new file first, so a
    failed save leaves an older setup of that name as it was)."""
    path = setup_path(folder, name)
    os.makedirs(folder, exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w") as f:
        # one value per line, so that setups kept in git diff well
        json.dump(graph, f, sort_keys=True, indent=1)
        f.write("\n")
    os.replace(tmp, path)
    return path


def read_setup(folder, name):
    try:
        with open(setup_path(folder, name)) as f:
            return from_json(f.read())
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise GraphError("setup %s could not be read: %s" % (name, e)) from e


def insert(graph, setup, gap=60):
    """The graph with the setup added at its end: what fed the output now
    feeds what the setup's layer fed, and the setup's result goes to the
    output. The setup's nodes get new ids, and are placed to the right of
    the graph's, with the output after them."""
    graph = json.loads(json.dumps(graph))
    nodes = graph["nodes"]
    ids = {}
    for node_id in setup["nodes"]:
        if node_id not in (LAYER, OUTPUT):
            ids[node_id] = new_id(graph)
            nodes[ids[node_id]] = None  # (taken)
    if not ids:
        return graph
    end = input_of(graph, OUTPUT, "input")
    ours = [n for k, n in nodes.items() if n is not None and k != OUTPUT]
    theirs = [setup["nodes"][k] for k in ids]
    dx = max(n.get("x", 0) for n in ours) + 200 + gap - min(n.get("x", 0) for n in theirs)
    dy = min(n.get("y", 0) for n in ours) - min(n.get("y", 0) for n in theirs)
    for old, new in ids.items():
        spec = json.loads(json.dumps(setup["nodes"][old]))
        spec["x"] = spec.get("x", 0) + dx
        spec["y"] = spec.get("y", 0) + dy
        nodes[new] = spec
    graph["links"] = [link for link in graph["links"] if link["to"] != OUTPUT]
    for link in setup["links"]:
        src = ids.get(link["from"], end if link["from"] == LAYER else None)
        dst = OUTPUT if link["to"] == OUTPUT else ids.get(link["to"])
        if src is not None and dst is not None:
            graph["links"].append({"from": src, "to": dst, "pad": link["pad"]})
    nodes[OUTPUT]["x"] = max(n["x"] for k, n in nodes.items() if k != OUTPUT) + 200 + gap
    return graph


def duplicate(graph, ids, dx=40, dy=40):
    """The graph with copies of the nodes ids (not the layer or the
    output), offset by dx, dy, and the links between them copied too
    (not their links to the other nodes); and the copies' ids."""
    graph = json.loads(json.dumps(graph))
    copies = {}
    for old in sorted(ids):
        if old in (LAYER, OUTPUT) or old not in graph["nodes"]:
            continue
        copies[old] = new_id(graph)
        spec = json.loads(json.dumps(graph["nodes"][old]))
        spec["x"] = spec.get("x", 0) + dx
        spec["y"] = spec.get("y", 0) + dy
        graph["nodes"][copies[old]] = spec
    for link in list(graph["links"]):
        if link["from"] in copies and link["to"] in copies:
            graph["links"].append({"from": copies[link["from"]], "to": copies[link["to"]],
                                   "pad": link["pad"]})
    return graph, list(copies.values())


def subgraph(graph, ids):
    """The nodes ids as a graph of their own (to save as a setup): links
    between them kept, links into them from other nodes coming from the
    layer, and the one whose result went on (to another node or the
    output; else the one that feeds none of the others; the rightmost
    when there are several) going to the output."""
    ids = [k for k in ids if k in graph["nodes"] and k not in (LAYER, OUTPUT)]
    sub = new_graph()
    sub["links"] = []
    if not ids:
        return sub
    chosen = set(ids)
    for k in ids:
        sub["nodes"][k] = json.loads(json.dumps(graph["nodes"][k]))
    out_of = set()
    feeds_inside = set()
    for link in graph["links"]:
        f, t = link["from"], link["to"]
        if t in chosen:
            sub["links"].append({"from": f if f in chosen else LAYER, "to": t, "pad": link["pad"]})
        if f in chosen and t in chosen:
            feeds_inside.add(f)
        elif f in chosen:
            out_of.add(f)
    ends = out_of or (chosen - feeds_inside)
    if ends:
        last = max(ends, key=lambda k: (graph["nodes"][k].get("x", 0), k))
        sub["links"].append({"from": last, "to": OUTPUT, "pad": "input"})
    xs = [graph["nodes"][k].get("x", 0) for k in ids]
    ys = [graph["nodes"][k].get("y", 0) for k in ids]
    sub["nodes"][LAYER].update(x=min(xs) - 200, y=min(ys))
    sub["nodes"][OUTPUT].update(x=max(xs) + 300, y=min(ys))
    return sub


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


# PyGObject (3.56 here) makes a Python class for an enum without
# introspection data (GEGL's operations' own enums) on the first
# pspec.enum_class, but keeps only a borrowed pointer to it on the GType:
# once the class is garbage collected, enum_class returns whatever Python
# object took its memory (seen as "type object 'SpecialFileError' has no
# attribute '__enum_values__'"). So every enum class is held here, for as
# long as the process runs, and taken only through enum_values().
_enum_classes = {}


def enum_values(pspec):
    """{number: GEnumValue} of an enum property."""
    name = pspec.value_type.name
    if name not in _enum_classes:
        _enum_classes[name] = pspec.enum_class
    return _enum_classes[name].__enum_values__


def set_value(node, pspec, value):
    """Sets a property of a GEGL node from its JSON form (enums by
    nick, colors as CSS strings)."""
    kind = pspec.__gtype__.name
    if kind == "GeglParamEnum" or kind == "GParamEnum":
        if isinstance(value, str):
            values = enum_values(pspec)
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
