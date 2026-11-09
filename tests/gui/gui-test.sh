#!/bin/sh
# The Node Editor in a real GIMP on a Broadway display, looked at with a
# headless Chrome: GIMP's window across the top, the editor under it.
# tests/gui/hook.py builds a graph in the editor and changes a value,
# and checks that the layer follows; a screenshot of the whole screen is
# left in tests/output/gui/live.png. Run tests/run.sh first (it builds
# nodes:graph and makes the throwaway home and profile).
#
#   tests/gui/gui-test.sh
#
# Skips (exit 0) without Chrome, node 22 or broadwayd. GIMP is the
# native one (GIMP_FLATPAK=0), isolated from your folders
# (tests/isolate.sh); only the GIMP and Chrome started here are stopped.
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later
here=$(cd "$(dirname "$0")" && pwd)
tests=$(dirname "$here")
src=$(dirname "$tests")
out=$tests/output/gui
profile=$tests/output/profile-gui
home=$tests/output/home
devtools=${GIMP_PLUGIN_DEVTOOLS:-$src/../gimp-devtools}
gtknodes=${GEGL_NODES_GTKNODES:-$src/../gtknodes/_inst}
status=0

chrome=$(command -v google-chrome || command -v chromium || command -v chromium-browser)
[ -n "$chrome" ] || { echo "GUI SKIP: no Chrome or Chromium"; exit 0; }
node -e 'process.exit(typeof WebSocket === "undefined" ? 1 : 0)' 2>/dev/null ||
  { echo "GUI SKIP: needs node 22 or later"; exit 0; }
command -v broadwayd >/dev/null || { echo "GUI SKIP: no broadwayd"; exit 0; }
[ -f "$devtools/gui/cdp.mjs" ] || { echo "GUI SKIP: no $devtools/gui/cdp.mjs"; exit 0; }
[ -f "$home/.local/share/gegl-0.4/plug-ins/node-graph.so" ] ||
  { echo "GUI FAIL: run tests/run.sh first"; exit 1; }
cdp="node $devtools/gui/cdp.mjs"
GIMP_FLATPAK=0
export GIMP_FLATPAK
# shellcheck source=SCRIPTDIR/../isolate.sh
. "$tests/isolate.sh"

rm -rf "$out" "$profile"
mkdir -p "$out" "$profile/plug-ins/gegl-nodes"
cp "$src"/plug-ins/gegl-nodes/*.py "$profile/plug-ins/gegl-nodes/"
chmod +x "$profile/plug-ins/gegl-nodes/gegl-nodes.py"
# one window across the top, no welcome dialog
cat >"$profile/sessionrc" <<'EOF'
(session-info "toplevel"
    (factory-entry "gimp-single-image-window")
    (position 0 0)
    (size 1400 470))
(single-window-mode yes)
EOF
printf '(show-welcome-dialog no)\n(config-version "%s")\n' \
  "$(gimp-console-3.2 --version | sed 's/.* //')" >"$profile/gimprc"

port=$(python3 -c '
import socket
for port in range(8087, 8180):
    try:
        s = socket.socket(socket.AF_INET6); s.bind(("::", port)); s.close(); print(port); break
    except OSError:
        pass')
display=$((port - 8080))
CDP_PORT=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
export CDP_PORT
view=size:1400,1000

gimp_pid=
chrome_pid=
stop_all () {
    [ -n "$chrome_pid" ] && kill "$chrome_pid" 2>/dev/null
    [ -n "$gimp_pid" ] && pkill -P "$gimp_pid" 2>/dev/null
    [ -n "$gimp_pid" ] && kill "$gimp_pid" 2>/dev/null
    sleep 1
    rm -rf "$out/chrome"
}
trap stop_all EXIT
trap 'exit 1' INT TERM HUP

gimp_run --timeout=300 --native --home="$home" \
  --env=GDK_BACKEND=broadway --env=BROADWAY_DISPLAY=:$display \
  --env=GIMP3_DIRECTORY="$profile" --env=GN_OUT="$out" \
  --env=GEGL_NODES_TEST="$here/hook.py" --env=GEGL_NODES_GTKNODES="$gtknodes" -- \
  sh -c "broadwayd --port $port :$display & bw=\$!; trap 'kill \$bw' EXIT; \
   i=0; while [ ! -f '$out/page-open' ] && [ \$i -lt 300 ]; do sleep 0.2; i=\$((i+1)); done; \
   gimp-3.2 --new-instance --no-splash --no-fonts \
   --batch-interpreter python-fu-eval -b \"exec(open('$here/gui-script.py').read())\"" \
  >"$out/gimp.log" 2>&1 &
gimp_pid=$!

"$chrome" --headless=new --remote-debugging-port="$CDP_PORT" --window-size=1400,1000 \
  --user-data-dir="$out/chrome" --password-store=basic about:blank >/dev/null 2>&1 &
chrome_pid=$!
i=0
until python3 -c "import socket; socket.create_connection(('127.0.0.1', $port), 1)" 2>/dev/null ||
      [ $i -gt 60 ]; do
    sleep 0.5
    i=$((i + 1))
done
$cdp "$view" nav:"http://127.0.0.1:$port/" wait:1500 >/dev/null 2>&1
touch "$out/page-open"

i=0
while [ ! -f "$out/ready" ] && [ ! -f "$out/result.txt" ] && [ $i -lt 240 ]; do
    sleep 1
    i=$((i + 1))
done
if [ -f "$out/ready" ]; then
    # Broadway opens GIMP's window off the page, at its top left corner:
    # moved into view on the page only (Broadway does not know), for the
    # screenshot
    show_gimp="eval:(() => { for (const c of document.querySelectorAll('canvas')) {
      const r = c.getBoundingClientRect();
      if (r.top < 400 && r.width > 600)
        c.style.transform = 'translate(' + (-r.left) + 'px,' + (-r.top) + 'px)' }
      return 1 })()"
    $cdp "$view" wait:1500 "$show_gimp" wait:300 shot:"$out/live.png" >/dev/null &&
      echo "GUI screenshot: $out/live.png"
fi
touch "$out/shot-taken"
i=0
while [ ! -f "$out/result.txt" ] && [ $i -lt 60 ]; do
    sleep 1
    i=$((i + 1))
done

cat "$out/hook.txt" "$out/result.txt" 2>/dev/null | sed 's/^/GUI /'
if [ ! -f "$out/result.txt" ] || [ ! -f "$out/hook.txt" ]; then
    echo "GUI FAIL: no result (log: $out/gimp.log)"
    grep -E "Traceback|Error|^  File" "$out/gimp.log" | head -20
    status=1
fi
grep -q '^FAIL' "$out/hook.txt" "$out/result.txt" 2>/dev/null && status=1
exit $status
