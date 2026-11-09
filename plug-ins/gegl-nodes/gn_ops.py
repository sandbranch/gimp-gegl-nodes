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
        self._node = self._inputs = self._outputs = self._props = None
        # (the editable ones are read when first needed: see props)
        pspecs = Gegl.Operation.list_properties(name)
        self.file_params = any(p.__gtype__.name in FILE_PARAMS for p in pspecs)

    def _pads(self):
        # making a node is what is slow here (some third-party operations
        # build a whole graph of their own: 0.2 s for lb:twirl), so it is
        # made for an operation only when its pads are first needed, not
        # for all of them when the editor starts
        if self._node is None:
            # (kept, so that it is made once)
            node = self._node = Gegl.Node().create_child(self.name)
            # input first, then the others in their natural order (aux2
            # before aux10)
            self._inputs = sorted(node.list_input_pads(), key=lambda p: (p != "input", [
                int(t) if t.isdigit() else t for t in re.split(r"(\d+)", p)]))
            self._outputs = list(node.list_output_pads())

    @property
    def props(self):
        """The properties a node of it shows, as GParamSpecs."""
        if self._props is None:
            self._pads()
            self._props = [p for p in Gegl.Operation.list_properties(self.name)
                           if p.__gtype__.name in EDITABLE or p.value_type.name == "GeglColor"]
        return self._props

    @property
    def inputs(self):
        self._pads()
        return self._inputs

    @property
    def outputs(self):
        self._pads()
        return self._outputs

    def why_not(self):
        """Why the filter would refuse this operation, or None. (Without
        making a node: that it has an output is checked by usable().)"""
        if self.name in FORBIDDEN_NAMES:
            return "not allowed in the graph"
        bad = FORBIDDEN_CATEGORIES.intersection(self.categories)
        if bad:
            return "category " + sorted(bad)[0]
        if self.file_params:
            return "reads or writes files"
        return None

    def usable(self):
        """Also that it has an output: every operation that has none is in
        a category refused above today (a sink), but nodes:graph checks it
        too, so one that is not is refused when it is added."""
        return self.why_not() is None and "output" in self.outputs


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


# The menus: GEGL's categories put together into fewer groups, as in
# GIMP's Filters menu. An operation goes by its categories first; one
# without any (many third-party ones) by words in its title and name; and
# else to Other. The first group that matches takes it.
GROUPS = [
    # title, GEGL categories, words
    ("Blur", {"blur"}, ("blur", "bokeh")),
    ("Sharpen & Denoise", {"enhance", "frequency", "noise-reduction", "sharpen"},
     ("sharpen", "denoise", "noise reduction", "repair", "smoother", "high pass")),
    ("Color", {"color", "grayscale", "tonemapping", "dither", "transparency"},
     ("color", "colour", "tone", "gray", "grey", "black and white", "sepia", "duotone",
      "cmyk", "threshold", "alpha", "hue", "saturation", "contrast", "levels", "curves")),
    ("Blend", {"compositors", "blend", "porter-duff"}, ("blend", "mix", "composite")),
    ("Light & Shadow", {"light"}, ("light", "shadow", "glow", "shine", "bloom", "aura",
                                    "neon", "sun", "lens flare")),
    ("Text & Bevel", set(), ("text", "bevel", "emboss", "chrome", "metal", "glass", "gel",
                             "ice", "extrusion", "extrude", "engrave", "chisel", "gold",
                             "3d", "plastic", "jelly", "goo", "slime", "candy", "clay")),
    ("Edges & Outlines", {"edge-detect", "segmentation", "matting"},
     ("edge", "outline", "stroke", "border", "frame", "mask", "background")),
    ("Distort", {"distort", "transform", "map", "tile", "layout", "core"},
     ("distort", "twirl", "twist", "wave", "ripple", "warp", "tile", "mirror", "kaleido",
      "align", "rotate", "polar", "swirl", "spiral", "pixel", "mosaic", "zoom", "glitch")),
    ("Artistic", {"artistic", "aristic", "illusions"},
     ("sketch", "paint", "crayon", "charcoal", "comic", "cartoon", "oil", "art", "photo",
      "antique", "vintage", "pencil", "drawn", "watercolor", "action lines", "halftone")),
    ("Noise & Texture", {"noise"}, ("noise", "grain", "texture", "pattern", "cell",
                                     "checker", "stripe", "wallpaper", "dots", "fabric")),
    ("Render", {"render"}, ("render", "cloud", "gradient", "fill", "fog", "earth", "star",
                            "ball", "bubble", "electric", "fire", "smoke", "rain", "snow")),
    ("Analysis", {"forensics", "analysis"}, ()),
]
OTHER = "Other"
_WORDS = [(title, categories, re.compile(r"\b(?:%s)" % "|".join(map(re.escape, words))))
          for title, categories, words in GROUPS if words]
_CATEGORIES = {c: title for title, categories, _ in GROUPS for c in categories}


def group_of(info):
    """The menu an operation goes in, and the heading it goes under there
    (its own first category, or "More" when it was placed by words)."""
    cats = [c.lower() for c in info.categories if c]
    order = [title for title, _, _ in GROUPS]
    found = sorted((order.index(_CATEGORIES[c]), c) for c in cats if c in _CATEGORIES)
    if found:
        return order[found[0][0]], found[0][1]
    if not cats or cats == ["other"] or cats == ["plugins"]:
        text = ("%s %s" % (info.title, info.name.split(":")[-1].replace("-", " "))).lower()
        for title, categories, words in _WORDS:
            if words.search(text):
                return title, "more"
    return OTHER, cats[0] if cats else "more"


SMALLEST = 3  # a menu with fewer goes into Other


def offered_groups():
    """groups() of the catalog, made once."""
    global _offered_groups
    if _offered_groups is None:
        _offered_groups = groups(catalog().values())
    return _offered_groups


_offered_groups = None


def groups(infos):
    """{group title: {heading: [infos sorted by title]}}, the groups in
    their order, Other last. A group with fewer than SMALLEST operations
    (with only GEGL's own operations, Text & Bevel has one) goes into
    Other, under its own name."""
    out = {title: {} for title, _, _ in GROUPS}
    out[OTHER] = {}
    for info in infos:
        title, heading = group_of(info)
        out[title].setdefault(heading, []).append(info)
    for title, _, _ in GROUPS:
        if 0 < sum(len(ops) for ops in out[title].values()) < SMALLEST:
            for heading, ops in out.pop(title).items():
                out[OTHER].setdefault(title.lower() if heading == "more" else heading,
                                      []).extend(ops)
    return {t: {h: sorted(ops, key=lambda i: i.title.lower())
                for h, ops in sorted(hs.items(), key=lambda kv: (kv[0] == "more", kv[0]))}
            for t, hs in out.items() if hs}
