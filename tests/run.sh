#!/bin/sh
# Tests the Node Editor. One command, no network:
#
#   tests/run.sh           everything (the GIMP GUI test if Chrome is there)
#   GN_GUI=0 tests/run.sh  without the GIMP GUI test
#
# Steps: lint (no em or en dashes, Python compiles), build of the GEGL
# operation nodes:graph (meson, into build/), unit tests in plain Python
# with GEGL (the graph module, the catalog, and what nodes:graph
# refuses), the editor widget on a Broadway display (no GIMP), headless
# GIMP (the filter in GIMP, changes, XCF), and the editor in a real GIMP
# on Broadway with a screenshot (tests/gui/gui-test.sh).
#
# GIMP is the native one. It runs isolated from your folders
# (tests/isolate.sh, with gimp-devtools/gimp-run.sh): a throwaway home
# in tests/output/home, where nodes:graph is installed, and a throwaway
# profile. Before and after, your folders of GIMP and the other apps are
# listed (gimp-devtools/snapshot.sh); if anything there changed, the run
# fails.
#
# GtkNodes: ../gtknodes/_inst (GEGL_NODES_GTKNODES to change), built
# from the sandbranch branch of sandbranch/gtknodes, which has the fixes
# the editor needs.
#
# Prints PASS or FAIL for each check; exits non-zero if any fails.
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later
here=$(cd "$(dirname "$0")" && pwd)
src=$(dirname "$here")
out=$here/output
home=$out/home
gtknodes=${GEGL_NODES_GTKNODES:-$src/../gtknodes/_inst}
status=0

# shellcheck source=SCRIPTDIR/isolate.sh
. "$here/isolate.sh"
mkdir -p "$out"
snapshot_take "$out/snapshot-before.txt"

# counts the PASS and FAIL lines of a log, prints them, and checks that
# the script got to its "<name> failures: 0" line
report () {
    grep -E "^(PASS|FAIL)" "$1"
    if ! grep -q "^$2 failures: 0$" "$1"; then
        grep -E "Traceback|Error|^  File" "$1" | head -20
        echo "FAIL $2: did not finish cleanly, see $1"
        status=1
    fi
}

# --- lint
dashes=$(grep -rlP '[\x{2013}\x{2014}]' "$src" --include='*.py' --include='*.sh' \
  --include='*.c' --include='*.md' --include='*.build' --include='*.txt' \
  --exclude-dir=output --exclude-dir=build 2>/dev/null)
if [ -z "$dashes" ]; then
    echo "PASS lint: no em or en dashes"
else
    echo "FAIL lint: em or en dashes in: $dashes"
    status=1
fi
if python3 -m py_compile "$src"/plug-ins/gegl-nodes/*.py "$here"/*.py "$here"/gui/*.py; then
    echo "PASS lint: Python compiles"
else
    echo "FAIL lint: Python does not compile"
    status=1
fi

# --- build nodes:graph
if { [ -d "$src/build" ] || meson setup "$src/build" "$src"; } >"$out/build.log" 2>&1 &&
   ninja -C "$src/build" >>"$out/build.log" 2>&1; then
    echo "PASS build: nodes:graph"
else
    echo "FAIL build: see $out/build.log"
    exit 1
fi
if grep -q "warning:" "$out/build.log"; then
    echo "FAIL build: compiler warnings (see $out/build.log)"
    status=1
fi
# (not build/ itself on GEGL_PATH: GEGL would take meson's JSON files
# for operations)
rm -rf "$out/gegl" "$home"
mkdir -p "$out/gegl" "$home/.local/share/gegl-0.4/plug-ins" "$out/work"
cp "$src/build/node-graph.so" "$out/gegl/"
cp "$src/build/node-graph.so" "$home/.local/share/gegl-0.4/plug-ins/"
gegl_path=$out/gegl:$(pkg-config --variable=pluginsdir gegl-0.4)

# --- unit tests, plain GEGL
GEGL_PATH=$gegl_path timeout 600 python3 "$here/unit_test.py" >"$out/unit.log" 2>&1
report "$out/unit.log" unit

# --- the editor widget on Broadway, no GIMP
if [ ! -d "$gtknodes/lib/girepository-1.0" ]; then
    echo "FAIL editor: no GtkNodes in $gtknodes (build sandbranch/gtknodes, branch sandbranch)"
    status=1
elif ! command -v broadwayd >/dev/null; then
    echo "SKIP editor: no broadwayd"
else
    port=$(python3 -c '
import socket
for port in range(8087, 8180):
    try:
        s = socket.socket(socket.AF_INET6); s.bind(("::", port)); s.close(); print(port); break
    except OSError:
        pass')
    broadwayd --port "$port" ":$((port - 8080))" >/dev/null 2>&1 &
    bw=$!
    sleep 1
    GDK_BACKEND=broadway BROADWAY_DISPLAY=":$((port - 8080))" GEGL_PATH=$gegl_path \
      GI_TYPELIB_PATH="$gtknodes/lib/girepository-1.0" LD_LIBRARY_PATH="$gtknodes/lib" \
      timeout 300 python3 "$here/editor_test.py" >"$out/editor.log" 2>&1
    kill "$bw" 2>/dev/null
    report "$out/editor.log" editor

    # dragging nodes with real pointer events (XTest), on Xvfb
    if command -v Xvfb >/dev/null; then
        Xvfb :97 -screen 0 1200x700x24 >/dev/null 2>&1 &
        xvfb=$!
        sleep 1
        DISPLAY=:97 GDK_BACKEND=x11 GEGL_PATH=$gegl_path \
          GI_TYPELIB_PATH="$gtknodes/lib/girepository-1.0" LD_LIBRARY_PATH="$gtknodes/lib" \
          timeout 120 python3 "$here/drag/drag_test.py" >"$out/drag.log" 2>&1
        kill "$xvfb" 2>/dev/null
        report "$out/drag.log" drag
    else
        echo "SKIP drag: no Xvfb"
    fi
    if grep -q "invalid (NULL) class pointer" "$out/editor.log"; then
        echo "FAIL editor: GtkNodes signals into freed nodes (GtkNodes without fix/socket-handlers?)"
        status=1
    fi
fi

# --- headless GIMP
gimp_run --timeout=600 --native --home="$home" --env=GIMP3_DIRECTORY="$out/profile" \
  --env=TEST_HERE="$here" --env=TEST_WORK="$out/work" -- \
  gimp-console-3.2 --no-interface --no-data --no-fonts --quit \
  --batch-interpreter python-fu-eval -b "exec(open('$here/gimp_test.py').read())" \
  >"$out/gimp.log" 2>&1
report "$out/gimp.log" gimp

# --- the editor in GIMP, live
if [ "${GN_GUI:-1}" = 1 ]; then
    GEGL_NODES_GTKNODES=$gtknodes "$here/gui/gui-test.sh" || status=1
fi

snapshot_check "$out/snapshot-before.txt" "" || status=1
[ $status = 0 ] && echo "ALL PASS" || echo "SOME FAILED"
exit $status
