#!/bin/sh
# Installs the Node Editor for your own native GIMP 3:
#
#   - nodes:graph into ~/.local/share/gegl-0.4/plug-ins (built in
#     build-install/);
#   - the plug-in as a link to plug-ins/gegl-nodes in this repository, in
#     your GIMP's plug-in folder (so a git pull updates it);
#
# and checks that GtkNodes is in ../gtknodes/_inst (sandbranch/gtknodes,
# branch sandbranch, built with its dev/build-local.sh). Restart GIMP
# afterwards: Filters > Generic > Node Editor...
#
#   dev/install.sh
#   dev/install.sh --remove    removes both again
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later
set -eu
src=$(cd "$(dirname "$0")/.." && pwd)
version=$(gimp-console-3.2 --version 2>/dev/null | sed 's/.* \([0-9]*\.[0-9]*\).*/\1/')
[ -n "$version" ] || { echo "No native GIMP 3 (gimp-console-3.2) found" >&2; exit 1; }
plugdir=${GIMP3_DIRECTORY:-${XDG_CONFIG_HOME:-$HOME/.config}/GIMP/$version}/plug-ins
opdir=${XDG_DATA_HOME:-$HOME/.local/share}/gegl-0.4/plug-ins

if [ "${1:-}" = --remove ]; then
    rm -f "$plugdir/gegl-nodes" "$opdir/node-graph.so"
    echo "Removed $plugdir/gegl-nodes and $opdir/node-graph.so"
    exit 0
fi

[ -d "$src/build-install" ] || meson setup "$src/build-install" "$src" -Dmoduledir="$opdir"
ninja -C "$src/build-install" install

mkdir -p "$plugdir"
if [ -e "$plugdir/gegl-nodes" ] && [ ! -L "$plugdir/gegl-nodes" ]; then
    echo "$plugdir/gegl-nodes is there and is not a link; not touched" >&2
    exit 1
fi
ln -sfn "$src/plug-ins/gegl-nodes" "$plugdir/gegl-nodes"
chmod +x "$src/plug-ins/gegl-nodes/gegl-nodes.py"
echo "Plug-in: $plugdir/gegl-nodes -> $src/plug-ins/gegl-nodes"
echo "Operation: $opdir/node-graph.so"

gtknodes=${GEGL_NODES_GTKNODES:-$(dirname "$src")/gtknodes/_inst}
if [ -f "$gtknodes/lib/libgtknodes-0.1.so.0" ]; then
    echo "GtkNodes: $gtknodes"
else
    echo "GtkNodes not found in $gtknodes: build sandbranch/gtknodes (branch sandbranch)" \
      "with its dev/build-local.sh" >&2
    exit 1
fi
echo "Restart GIMP, then Filters > Generic > Node Editor..."
