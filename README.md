# Node Editor for GIMP

A node editor for GEGL in GIMP 3: put GEGL operations on a canvas as
nodes, link them as you like (branches, a result used twice, blend modes
with a second input), and see the result on the layer as you edit. The
graph is one non-destructive filter on the layer, **Node Graph**, that
stays editable: run the editor again on the layer to change it, also
after saving and opening the XCF.

![The editor under GIMP's window, with the result on the canvas](docs/live.png)

*Filters > Generic > Node Editor...* The editor opens as its own window
(see "Docked like Blender" below).

To add a node: pick a filter from the menus along the top (one per GEGL
category, on as many rows as the window's width needs), click **Add**,
press **Shift+A** or right-click on the canvas (the node goes where you clicked), or type in the search
field and press Enter. Link nodes by dragging from a green Output dot to
an Input dot; the Output node's input is what the layer shows. The Layer
and Output nodes have a bold title and a badge, and cannot be deleted.
**Arrange** lays the nodes out in columns from the layer to the output.

## What is in here

- **`operations/node-graph.c`**: the GEGL operation `nodes:graph`
  (LGPL-3.0-or-later, written to be offered to GEGL). It runs a graph in
  GEGL's chain syntax, like `gegl:gegl`. GIMP refuses `gegl:gegl` as a
  layer filter ("The filter is unsafe") because a graph may hold an
  operation that writes files, which an XCF would then do when it is
  opened. `nodes:graph` builds each graph in a scratch graph first,
  never processed, and refuses it if any operation in it is a sink, is in
  the category input, output, programming, display, video or debug, has a
  file path or URI property, is a graph itself, or is `gegl:introspect`
  (it runs a program). It then shows the layer unchanged and says why in
  its `error` property. It also keeps the editor's node layout in a
  `layout` property, so that the layout is saved with the filter.
- **`plug-ins/gegl-nodes/`**: the GIMP plug-in (Python, GPL-3.0-or-later):
  - `gn_graph.py`: the graph as plain data (JSON), the GEGL graph made
    from it, and the string for the filter. The string is written here,
    with every linked input as `pad=[ ... ]` and a result used more than
    once named with `id=`/`ref=`: GEGL's own `gegl_serialize` writes only
    the `input` and `aux` pads, and would drop `aux2` (Remap), `aux1` to
    `aux16` (Piecewise Blend) and the exposures of Combine Exposures.
    Each operation's values are still written by GEGL, with colors put
    back exactly (GEGL keeps three decimals).
  - `gn_ops.py`: which operations are offered, with their input pads and
    editable properties, all from GEGL's introspection: every GEGL
    operation installed shows up, your own ones too.
  - `gn_editor.py`: the editor, made with
    [GtkNodes](https://github.com/aluntzer/gtknodes).
  - `gegl-nodes.py`: the plug-in: finds or makes the filter, opens the
    editor, and updates the filter 120 ms after each change.

## Install

Needs a native GIMP 3.2 with its GEGL development files (meson, ninja, a
compiler), and GtkNodes with GObject introspection. GtkNodes is not
packaged; build the `sandbranch` branch of
[sandbranch/gtknodes](https://github.com/sandbranch/gtknodes) next to
this repository, with its `dev/build-local.sh` (into `../gtknodes/_inst`).
That branch has the fixes the editor needs (below). Then:

    dev/install.sh             # nodes:graph and the plug-in, for you
    dev/install.sh --remove

and restart GIMP. Elsewhere, point `GEGL_NODES_GTKNODES` at the install
prefix of GtkNodes.

## Tests

    tests/run.sh               # all; GN_GUI=0 without the GIMP window test

110 checks, plus lint, the build and the isolation check: unit tests in plain GEGL (the graph, the catalog, what
`nodes:graph` refuses, and that every one of the 155 operations offered is
accepted by it), the editor widget on a Broadway display, headless GIMP
(the filter, changes, a graph that would write a file, an XCF saved and
opened again), and the editor in a real GIMP on Broadway, driven through
the editor's own calls, with the layer checked pixel by pixel against
plain GEGL after each change and a screenshot in
`tests/output/gui/live.png`. GIMP runs isolated from your own folders,
and the run fails if anything in them changed.

## Changes are incremental

When an edit changes only values (the same operations, linked the same
way), `nodes:graph` sets the new values on the nodes it already has,
instead of building the graph again; and every node in it keeps its
result (GEGL's cache policy "always"; by itself GEGL keeps it only for
some operations). So only the changed node and what follows it are
computed again. Measured on a 2000x2000 image, a brightness change after
a bilateral filter: 0.9 s when built anew, 0.01 s now. Adding, removing
or relinking a node builds the graph anew. The cached results use
memory, within GEGL's tile cache (in GIMP, its tile cache size); how
much in practice has not been measured yet.

## Fixes in GtkNodes

Found while making this, on their own branches in sandbranch/gtknodes,
for upstream:

- `fix/socket-handlers`: removing a node left its sockets' signal
  handlers pointing at the node; a socket that outlived it (Python holds
  a reference) then signalled into the freed node.
- `fix/child-y`: a node's `y` place was set from, and read back as, its
  `x`, so nodes ended up on the diagonal (also in
  `gtk_nodes_node_view_save` and `load`).
- `fix/icon-nullable`: `gtk_nodes_node_set_icon_name` takes `NULL` (no
  icon, no button), but its introspection data refused `None`. Without
  it, the Layer and Output nodes keep a delete button that does
  nothing.

## GIMP's theme

GIMP's theme gives every vertical box a background (`.vertical` in
`themes/Default/common.css`). A GtkNodes node is a vertical box, so that
background was painted over parts of the node's frame, and left bits of
frames behind when a node moved. The editor makes its nodes' background
transparent.

## Docked like Blender

A GIMP plug-in runs as a process of its own, and libgimpui has no way to
put a plug-in's widget into GIMP's docks (only to keep its window above
GIMP's). So the editor is a window of its own. A panel docked in GIMP's
window would have to be part of GIMP itself (a dockable in
`app/widgets`). The graph, the string and the `nodes:graph` operation do
not depend on where the editor is, so they carry over unchanged.

## Not yet

- GIMP's own operations (`gimp:levels`, `gimp:curves`, ...) exist only
  inside GIMP, not in the plug-in's GEGL, so they are not offered yet.
- A second layer as input (the filter has an aux input,
  `Gimp.DrawableFilter.set_aux_input`).
- Properties without a widget yet: curves, paths, audio fragments,
  formats (they keep their defaults).
- A preview on each node.
- Undo: how GIMP records the filter's changes while the editor is open
  has not been looked at yet.

## License

`operations/` is LGPL-3.0-or-later (COPYING.LESSER), as GEGL. The rest is
GPL-3.0-or-later (COPYING).
