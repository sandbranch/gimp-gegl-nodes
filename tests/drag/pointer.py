# Real pointer events on an X display (XTest), from a process of its own:
#   python3 pointer.py move X Y | press | release | drag X0 Y0 X1 Y1 STEPS
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later

import ctypes
import sys
import time

x11 = ctypes.CDLL("libX11.so.6")
xtst = ctypes.CDLL("libXtst.so.6")
x11.XOpenDisplay.restype = ctypes.c_void_p
x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
x11.XFlush.argtypes = [ctypes.c_void_p]
xtst.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_ulong]
xtst.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int,
                                      ctypes.c_ulong]
d = x11.XOpenDisplay(None)
if not d:
    sys.exit("no X display")


def move(x, y):
    xtst.XTestFakeMotionEvent(d, -1, int(x), int(y), 0)
    x11.XFlush(d)


def button(down, which=1):
    xtst.XTestFakeButtonEvent(d, which, 1 if down else 0, 0)
    x11.XFlush(d)


cmd = sys.argv[1]
if cmd == "move":
    move(*map(int, sys.argv[2:4]))
elif cmd == "press":
    button(True)
elif cmd == "release":
    button(False)
elif cmd == "drag":
    x0, y0, x1, y1, steps = map(int, sys.argv[2:7])
    move(x0, y0)
    time.sleep(0.2)
    button(True)
    time.sleep(0.2)
    for i in range(1, steps + 1):
        move(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
        time.sleep(0.02)
    time.sleep(0.3)
    button(False)
    time.sleep(0.3)
