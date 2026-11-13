#!/usr/bin/env python3
"""Goldfish, a photoreal fish swimming the whole desktop.

The picture is a keyed photograph with a wagging tail, not a vector drawing.
The nose follows the swim direction and the belly stays down. Click him and
he darts. Right-click for food or to quit. The old sphere monster remains
behind --self-test.

About 4 cm long. The window is clear apart from the fish.

    puff                 open the fish (a second launch makes it dart)
    puff --replace       restart
    puff --self-test     check the monster model, the stats, and the folder grid
"""

from __future__ import annotations

import ctypes
import json
import math
import os
import random
import signal
import struct
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np

HOME = Path.home()

def prefer_xwayland(max_wait_s: float = 25.0) -> str:
    """Use XWayland so the goldfish can keep his place. Call before Gdk or Gtk."""
    if os.environ.get("GOLDFISH_NATIVE_WAYLAND"):
        os.environ["GDK_BACKEND"] = "wayland"
        return "wayland"
    forced = os.environ.get("GDK_BACKEND")
    if forced and forced != "x11":
        return forced
    display = os.environ.get("DISPLAY") or ":0"
    os.environ["DISPLAY"] = display
    screen = display.split(":")[-1].split(".")[0]
    if not screen.isdigit():
        screen = "0"
    sock = f"/tmp/.X11-unix/X{screen}"
    deadline = time.time() + max_wait_s
    while not os.path.exists(sock):
        if time.time() >= deadline:
            sys.stderr.write("goldfish: XWayland is not up; the fish cannot keep its place\n")
            return forced or "wayland"
        time.sleep(0.2)
    os.environ["GDK_BACKEND"] = "x11"
    return "x11"


def sink_above_icons(window) -> bool:
    """Lower the fish under ordinary windows when he is not kept above them."""
    try:
        window.set_keep_above(False)
        window.set_keep_below(False)
        gdkwin = window.get_window()
        if gdkwin is not None:
            gdkwin.set_keep_above(False)
            gdkwin.set_keep_below(False)
            gdkwin.lower()
    except Exception:
        pass
    return False


CFG = HOME / ".config" / "goldfish" / "config.json"
PID = HOME / ".cache" / "goldfish.pid"
LOG = Path("/tmp/goldfish.log")
SHARE = HOME / ".local" / "share" / "goldfish"
SFX = SHARE / "sfx"
ICON = HOME / ".local" / "share" / "icons" / "hicolor" / "256x256" / "apps" / "goldfish.png"
APP_DESKTOP = HOME / ".local" / "share" / "applications" / "goldfish.desktop"
AUTOSTART = HOME / ".config" / "autostart" / "goldfish.desktop"
PRGNAME = "goldfish"

# Character space: Y up, face +Z, soles near Y = 0. Horn tip to sole is LOCAL_H.
LOCAL_H = 2.18
SHADOW = 18
FISH_CM = 4.0
FISH_DIR = SHARE / "fish"

FUR, BELLY, EAR_IN, EYE, IRIS, PUPIL, NOSE, PAD, TONGUE, MOUTH, TOOTH, BLUSH, HORN, SNACK, DROP, CURL, WING, CATCH = range(18)
NMAT = 18

FURS = {
    "honey": np.array([0.84, 0.54, 0.26], np.float32),
    "snow": np.array([0.93, 0.94, 0.96], np.float32),
    "cocoa": np.array([0.40, 0.22, 0.13], np.float32),
    "blush": np.array([0.90, 0.58, 0.64], np.float32),
    "slate": np.array([0.48, 0.56, 0.64], np.float32),
    "mint": np.array([0.63, 0.90, 0.82], np.float32),
    "lilac": np.array([0.64, 0.52, 0.80], np.float32),
    "berry": np.array([0.56, 0.22, 0.38], np.float32),
    "ink": np.array([0.16, 0.15, 0.20], np.float32),
    "gold": np.array([0.86, 0.66, 0.26], np.float32),
}
FUR_LABELS = (
    ("honey", "Honey"),
    ("snow", "Snow"),
    ("cocoa", "Cocoa"),
    ("blush", "Blush"),
    ("slate", "Slate"),
    ("mint", "Mint"),
    ("lilac", "Lilac"),
    ("berry", "Berry"),
    ("ink", "Ink"),
    ("gold", "Gold"),
)

_wave_flag = False


def log(message: str) -> None:
    try:
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(time.strftime("%H:%M:%S ") + message + "\n")
    except OSError:
        pass


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def approach(value: float, target: float, rate: float, dt: float) -> float:
    return value + (target - value) * (1.0 - math.exp(-rate * dt))


def approach_angle(value: float, target: float, rate: float, dt: float) -> float:
    delta = (target - value + math.pi) % math.tau - math.pi
    return value + delta * (1.0 - math.exp(-rate * dt))


def lerp3(a, b, t: float):
    return (
        a[0] + (b[0] - a[0]) * t,
        a[1] + (b[1] - a[1]) * t,
        a[2] + (b[2] - a[2]) * t,
    )


def smoothstep(t: float) -> float:
    t = clamp(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def cm_for(growth: float) -> float:
    """2.55 cm as a baby, 3.0 cm at the start, 3.5 cm fully grown."""
    return 2.55 + clamp(growth, 0.0, 1.0) * 0.95


def tummy_word(fat: float) -> str:
    if fat < 0.22:
        return "slim"
    if fat < 0.48:
        return "soft"
    if fat < 0.74:
        return "round"
    return "stuffed"


def _colours(fur_name: str) -> dict:
    """Fur changes. Belly, cheeks, pads, horns, and the curl stay pink."""
    fur = FURS.get(fur_name, FURS["mint"])
    wing = np.clip(fur * np.array([1.04, 1.06, 1.02], np.float32) + 0.04, 0, 1)
    return {
        FUR: fur,
        BELLY: np.array([0.99, 0.74, 0.80], np.float32),
        EAR_IN: np.array([0.99, 0.55, 0.68], np.float32),
        EYE: np.array([0.94, 0.96, 1.00], np.float32),
        IRIS: np.array([0.34, 0.22, 0.82], np.float32),
        PUPIL: np.array([0.07, 0.04, 0.16], np.float32),
        NOSE: np.array([0.99, 0.58, 0.70], np.float32),
        PAD: np.array([0.99, 0.62, 0.74], np.float32),
        TONGUE: np.array([0.98, 0.46, 0.58], np.float32),
        MOUTH: np.array([0.55, 0.16, 0.28], np.float32),
        TOOTH: np.array([0.98, 0.97, 0.95], np.float32),
        BLUSH: np.array([1.00, 0.52, 0.66], np.float32),
        HORN: np.array([0.99, 0.78, 0.84], np.float32),
        SNACK: np.array([0.86, 0.24, 0.30], np.float32),
        DROP: np.array([0.42, 0.74, 0.96], np.float32),
        CURL: np.array([0.99, 0.60, 0.74], np.float32),
        WING: wing,
        CATCH: np.array([1.0, 1.0, 1.0], np.float32),
    }


def _spheres(pose: dict) -> tuple[list, list, float]:
    """Spheres and tapered capsules. Fur grows a soft shell in the rasterizer."""
    fat = clamp(float(pose.get("fat", 0.18)), 0.0, 1.0)
    sit = clamp(float(pose.get("sit", 0.0)), 0.0, 1.0)
    lay = clamp(float(pose.get("lay", 0.0)), 0.0, 1.0)
    phase = float(pose.get("phase", 0.0))
    blink = clamp(float(pose.get("blink", 0.0)), 0.0, 1.0)
    mouth = clamp(float(pose.get("mouth", 0.0)), 0.0, 1.0)
    wave = clamp(float(pose.get("wave", 0.0)), 0.0, 1.0)
    reach = clamp(float(pose.get("reach", 0.0)), 0.0, 1.0)
    climb = clamp(float(pose.get("climb", 0.0)), 0.0, 1.0)
    eat = clamp(float(pose.get("eat", 0.0)), 0.0, 1.0)
    drink = clamp(float(pose.get("drink", 0.0)), 0.0, 1.0)
    stretch = clamp(float(pose.get("stretch", 0.0)), 0.0, 1.0)
    cheek = clamp(float(pose.get("cheek", 0.0)), 0.0, 1.0)
    look_x = float(pose.get("look_x", 0.0))
    look_y = float(pose.get("look_y", 0.0))
    breathe = 1.0 + 0.028 * math.sin(float(pose.get("breathe", 0.0)))

    body_r = (0.70 + fat * 0.16) * breathe
    body_y = 1.10 - sit * 0.16 - lay * 0.08
    out: list[tuple] = []
    caps: list[tuple] = []

    def add(x, y, z, r, mat):
        out.append((float(x), float(y), float(z), max(0.02, float(r)), int(mat)))

    def cap(a, b, r0, r1, mat):
        caps.append(
            (
                float(a[0]), float(a[1]), float(a[2]),
                float(b[0]), float(b[1]), float(b[2]),
                max(0.02, float(r0)), max(0.02, float(r1)), int(mat),
            )
        )

    add(0.0, body_y, 0.0, body_r, FUR)
    add(0.0, body_y - 0.26, 0.22 + fat * 0.06, 0.46 + fat * 0.12, BELLY)

    flop = sit * 0.04 + lay * 0.08
    for sx in (-1.0, 1.0):
        ear = (sx * (body_r * 0.92), body_y + 0.18 - flop, -0.02)
        add(ear[0], ear[1], ear[2], 0.13, FUR)
        add(sx * (body_r * 0.98), body_y + 0.16 - flop, 0.08, 0.065, EAR_IN)

    # Pink horns. The bow sits on his left horn, which is the right side when he faces us.
    for sx in (-1.0, 1.0):
        cap(
            (sx * 0.28, body_y + 0.46, 0.06),
            (sx * 0.40, body_y + 0.90, 0.10),
            0.085,
            0.026,
            HORN,
        )
    bow_x, bow_y, bow_z = 0.40, body_y + 0.70, 0.20
    add(bow_x + 0.055, bow_y + 0.03, bow_z, 0.050, CURL)
    add(bow_x - 0.045, bow_y + 0.035, bow_z, 0.046, CURL)
    add(bow_x + 0.005, bow_y - 0.015, bow_z + 0.03, 0.030, CURL)
    for step in range(5):
        ang = step * math.tau / 5.0 - 0.4
        add(
            bow_x + 0.01 + math.cos(ang) * 0.042,
            bow_y - 0.03 + math.sin(ang) * 0.042,
            bow_z + 0.05,
            0.024,
            CURL,
        )
    add(bow_x + 0.01, bow_y - 0.03, bow_z + 0.08, 0.014, TOOTH)

    # One pink curl on the forehead, just left of the bow.
    for step in range(5):
        ang = 2.2 + step * 0.85
        rad = 0.012 + step * 0.008
        add(
            -0.02 + math.cos(ang) * rad,
            body_y + 0.56 + math.sin(ang) * rad * 0.8,
            body_r * 0.93,
            0.038,
            CURL,
        )

    for sx in (-1.0, 1.0):
        cap(
            (sx * body_r * 0.42, body_y - 0.10, -0.28),
            (sx * body_r * 0.78, body_y + 0.02, -0.48),
            0.07,
            0.028,
            WING,
        )

    # Huge galaxy eyes. The lid only caps them until he blinks.
    eye_y = body_y + 0.10
    eye_z = body_r * 0.70
    eye_r = 0.30
    for sx in (-1.0, 1.0):
        ex = sx * 0.25
        gx = look_x * 0.05
        gy = look_y * 0.04
        add(ex, eye_y, eye_z, eye_r, EYE)
        add(ex + gx, eye_y + gy, eye_z + 0.08, 0.255, IRIS)
        add(ex + gx * 1.15, eye_y + gy * 1.15, eye_z + 0.16, 0.075, PUPIL)
        add(ex + gx - 0.07, eye_y + gy + 0.07, eye_z + 0.30, 0.050, CATCH)
        add(ex + gx + 0.045, eye_y + gy - 0.02, eye_z + 0.28, 0.024, CATCH)
        if blink > 0.08:
            lid_r = 0.06 + blink * 0.22
            lid_y = eye_y + eye_r * 0.72 - blink * eye_r * 0.95
            add(ex, lid_y, eye_z + 0.26, lid_r, FUR)
        brow_y = eye_y + 0.32
        for step in range(3):
            t = (step - 1) / 2.0
            add(ex + t * 0.11, brow_y - t * t * 0.025, eye_z + 0.04, 0.020, MOUTH)

    mouth_y = body_y - 0.30
    smile = 0.20 + mouth * 0.80
    if blink > 0.9:
        smile *= 0.45
    open_r = 0.050 + smile * 0.055
    open_y = mouth_y - smile * 0.02
    add(0.0, open_y, body_r + 0.02, open_r, MOUTH)
    add(0.0, open_y - open_r * 0.35, body_r + 0.08, open_r * 0.55, TONGUE)
    if smile > 0.55:
        add(-0.030, open_y + open_r * 0.45, body_r + 0.07, 0.022, TOOTH)
        add(0.030, open_y + open_r * 0.45, body_r + 0.07, 0.022, TOOTH)
    add(0.0, body_y - 0.12, body_r * 0.92, 0.050, NOSE)
    for sx in (-1.0, 1.0):
        nx = sx * 0.48
        ny = -0.20
        nz = math.sqrt(max(0.05, 1.0 - nx * nx - ny * ny))
        add(nx * body_r, body_y + ny * body_r, nz * body_r * 0.96, 0.12 + cheek * 0.04, BLUSH)
    if eat > 0.25:
        add(0.04, mouth_y + 0.05, body_r + 0.18, 0.055, SNACK)
    if drink > 0.25:
        add(0.0, mouth_y + 0.10, body_r + 0.20, 0.048, DROP)

    swing = math.sin(phase)
    # Short thick arms. At rest the paws sit in front of the belly.
    for sx, arm_swing in ((-1.0, -swing), (1.0, swing)):
        shoulder = (sx * (body_r * 0.72), body_y + 0.06, 0.18)
        rest_el = (sx * 0.88, body_y - 0.16, 0.26)
        rest_hand = (sx * 0.62, body_y - 0.48, 0.46)
        el = (
            rest_el[0],
            rest_el[1] + arm_swing * 0.05 * (1.0 - sit),
            rest_el[2] + arm_swing * 0.22 * (1.0 - sit),
        )
        hand = (
            rest_hand[0] + arm_swing * 0.03,
            rest_hand[1] + arm_swing * 0.04 * (1.0 - sit),
            rest_hand[2] + arm_swing * 0.30 * (1.0 - sit),
        )
        if sx > 0.0:
            wave_el = (sx * 0.58, body_y + 0.48, 0.28)
            wave_hand = (
                sx * (0.78 + math.sin(phase * 7.0) * 0.14),
                body_y + 1.38 + math.sin(phase * 7.0) * 0.05,
                0.36,
            )
            el = lerp3(el, wave_el, wave)
            hand = lerp3(hand, wave_hand, wave)
            hand = lerp3(hand, (sx * 0.22, body_y + 0.02, 1.22), reach)
            el = lerp3(el, (sx * 0.48, body_y + 0.02, 0.70), reach)
        hand_eat = (sx * 0.16, mouth_y + 0.06, body_r + 0.12)
        if sx > 0.0:
            el = lerp3(el, (sx * 0.40, body_y - 0.08, 0.55), max(eat, drink))
            hand = lerp3(hand, hand_eat, max(eat, drink))
        else:
            hand = lerp3(hand, hand_eat, eat * 0.45)
        up_el = (sx * 0.42, body_y + 0.40, 0.22)
        up_hand = (sx * 0.28, body_y + 0.78, 0.26)
        both = max(climb, stretch)
        el = lerp3(el, up_el, both)
        hand = lerp3(hand, up_hand, both)
        if sit > 0.35 and wave < 0.15 and climb < 0.15 and stretch < 0.15:
            el = lerp3(el, (sx * 0.64, body_y - 0.22, 0.58), sit)
            hand = lerp3(hand, (sx * 0.36, body_y - 0.38, 0.82), sit)
        cap(shoulder, el, 0.145 + fat * 0.02, 0.125, FUR)
        cap(el, hand, 0.125, 0.11, FUR)
        add(hand[0], hand[1], hand[2], 0.15, FUR)
        add(hand[0], hand[1] - 0.02, hand[2] + 0.10, 0.055, PAD)
        for ox, oy in ((-0.045, 0.02), (0.045, 0.02), (0.0, 0.055)):
            add(hand[0] + ox, hand[1] + oy, hand[2] + 0.12, 0.028, PAD)

    step = (1.0 - sit) * (1.0 - lay)
    for sx, leg_swing in ((-1.0, swing), (1.0, -swing)):
        hip = (sx * (0.22 + fat * 0.05), body_y - body_r * 0.72, 0.02)
        knee = (
            sx * (0.24 + fat * 0.04),
            hip[1] - 0.24,
            0.10 + leg_swing * 0.20 * step,
        )
        foot = (
            sx * (0.26 + sit * 0.08),
            0.15 + max(0.0, leg_swing) * 0.08 * step + climb * abs(swing) * 0.05,
            0.24 + leg_swing * 0.40 * step + sit * 0.42 - lay * 0.16,
        )
        if lay > 0.45:
            knee = lerp3(knee, (sx * 0.36, 0.22, 0.05), lay)
            foot = lerp3(foot, (sx * 0.42, 0.14, -0.02), lay)
        cap(hip, knee, 0.155 + fat * 0.02, 0.125, FUR)
        cap(knee, foot, 0.125, 0.10, FUR)
        add(foot[0], foot[1], foot[2], 0.18 + fat * 0.02, FUR)
        add(foot[0], foot[1] - 0.04, foot[2] + 0.08, 0.075, PAD)
        for ox, oy in ((-0.05, 0.02), (0.05, 0.02), (0.0, 0.06)):
            add(foot[0] + sx * ox, foot[1] - 0.02 + oy * 0.2, foot[2] + 0.12, 0.032, PAD)

    wag = math.sin(phase * 0.85) * 0.06 * (1.0 - lay)
    tail = []
    for step in range(4):
        ang = 0.6 + step * 1.05
        tail.append(
            (
                -0.20 - math.sin(ang) * (0.16 + step * 0.08) + wag,
                body_y - 0.22 + math.cos(ang) * 0.14,
                -body_r * 0.92 - step * 0.05,
            )
        )
    for step in range(3):
        cap(tail[step], tail[step + 1], 0.11 - step * 0.012, 0.085 - step * 0.012, FUR)
    return out, caps, body_y


def _turn(x, y, z, yaw, pitch, roll, pivot_y, cy, sy, cp, sp, cr, sr):
    y -= pivot_y
    x, y = x * cr - y * sr, x * sr + y * cr
    y, z = y * cp - z * sp, y * sp + z * cp
    x, z = x * cy + z * sy, -x * sy + z * cy
    y += pivot_y
    return x, y, z


def _rotate(spheres, yaw: float, pitch: float, roll: float, pivot_y: float):
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cr, sr = math.cos(roll), math.sin(roll)
    out = []
    for x, y, z, radius, mat in spheres:
        x, y, z = _turn(x, y, z, yaw, pitch, roll, pivot_y, cy, sy, cp, sp, cr, sr)
        out.append((x, y, z, radius, mat))
    return out


def _rotate_caps(caps, yaw: float, pitch: float, roll: float, pivot_y: float):
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cr, sr = math.cos(roll), math.sin(roll)
    out = []
    for x0, y0, z0, x1, y1, z1, r0, r1, mat in caps:
        x0, y0, z0 = _turn(x0, y0, z0, yaw, pitch, roll, pivot_y, cy, sy, cp, sp, cr, sr)
        x1, y1, z1 = _turn(x1, y1, z1, yaw, pitch, roll, pivot_y, cy, sy, cp, sp, cr, sr)
        out.append((x0, y0, z0, x1, y1, z1, r0, r1, mat))
    return out


def _screen_y(y: float, squash: float) -> float:
    return (y - 0.02) * squash + 0.02


def render(pose: dict, fur_name: str = "mint", width: int = 240, height: int = 300, target_px: float = 180.0) -> np.ndarray:
    """RGBA uint8, straight alpha. The monster is target_px tall at growth 0.47, before growth scaling."""
    growth = clamp(float(pose.get("growth", 0.48)), 0.0, 1.0)
    height_px = float(target_px) * (cm_for(growth) / 3.0)
    scale = height_px / LOCAL_H
    yaw = float(pose.get("yaw", 0.0))
    pitch = float(pose.get("pitch", 0.0))
    roll = float(pose.get("roll", 0.0)) + float(pose.get("lay", 0.0)) * 1.0
    squash = clamp(float(pose.get("squash", 1.0)), 0.7, 1.25)
    air = max(0.0, float(pose.get("air", 0.0)))
    spheres, caps, body_y = _spheres(pose)
    spheres = _rotate(spheres, yaw, pitch, roll, body_y)
    caps = _rotate_caps(caps, yaw, pitch, roll, body_y)
    colours = _colours(fur_name)

    w, h = int(width), int(height)
    cx = (w - 1) * 0.5
    feet = h - SHADOW
    body_sy = feet - _screen_y(body_y, squash) * scale
    packed = np.empty((max(1, len(spheres)), 6), np.float32)
    for index, (x, y, z, radius, mat) in enumerate(spheres):
        y = _screen_y(y, squash)
        packed[index, 0] = cx + x * scale
        packed[index, 1] = feet - y * scale
        packed[index, 2] = z
        packed[index, 3] = max(radius * scale, 0.8)
        packed[index, 4] = radius
        packed[index, 5] = mat
    palette = np.zeros((NMAT, 3), np.float32)
    for mat, colour in colours.items():
        palette[int(mat)] = colour
    out = np.zeros((h, w, 4), np.uint8)
    zbuf = np.empty(h * w, np.float32)
    lib = _splat()
    lib.puff_splat(
        packed.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        len(spheres),
        palette.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        out.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        zbuf.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        w,
        h,
        ctypes.c_float(cx),
        ctypes.c_float(body_sy),
    )
    if caps:
        packed_c = np.empty((len(caps), 11), np.float32)
        for index, (x0, y0, z0, x1, y1, z1, r0, r1, mat) in enumerate(caps):
            y0 = _screen_y(y0, squash)
            y1 = _screen_y(y1, squash)
            packed_c[index, 0] = cx + x0 * scale
            packed_c[index, 1] = feet - y0 * scale
            packed_c[index, 2] = z0
            packed_c[index, 3] = cx + x1 * scale
            packed_c[index, 4] = feet - y1 * scale
            packed_c[index, 5] = z1
            packed_c[index, 6] = max(r0 * scale, 0.8)
            packed_c[index, 7] = max(r1 * scale, 0.8)
            packed_c[index, 8] = r0
            packed_c[index, 9] = r1
            packed_c[index, 10] = mat
        lib.puff_caps(
            packed_c.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            len(caps),
            palette.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            out.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
            zbuf.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            w,
            h,
        )
    fur = palette[FUR]
    lib.puff_shell(
        ctypes.c_float(packed[0, 0]),
        ctypes.c_float(packed[0, 1]),
        ctypes.c_float(packed[0, 2]),
        ctypes.c_float(packed[0, 3]),
        ctypes.c_float(packed[0, 4]),
        ctypes.c_float(float(fur[0])),
        ctypes.c_float(float(fur[1])),
        ctypes.c_float(float(fur[2])),
        out.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        zbuf.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        w,
        h,
    )

    # Soft contact shadow. It fades while he is in the air.
    shadow_w = height_px * (0.34 + float(pose.get("fat", 0.2)) * 0.08)
    shadow_h = max(4.0, height_px * 0.055)
    ys, xs = np.ogrid[0:h, 0:w]
    dx = (xs - cx) / shadow_w
    dy = (ys - (feet - 2.0)) / shadow_h
    disk = dx * dx + dy * dy
    mask = (disk <= 1.0) & (out[..., 3] == 0)
    fade = 0.30 if air < 12.0 else 0.08
    shade = (np.clip(1.0 - disk, 0.0, 1.0) * fade * 255.0).astype(np.uint8)
    out[..., 0] = np.where(mask, 18, out[..., 0])
    out[..., 1] = np.where(mask, 12, out[..., 1])
    out[..., 2] = np.where(mask, 10, out[..., 2])
    out[..., 3] = np.where(mask, shade, out[..., 3])
    return out


_SPLAT = None


def _splat():
    global _SPLAT
    if _SPLAT is None:
        lib = ctypes.CDLL(str(SHARE / "splat.so"))
        lib.puff_splat.argtypes = [
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_float,
            ctypes.c_float,
        ]
        lib.puff_splat.restype = None
        lib.puff_caps.argtypes = [
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_int,
            ctypes.c_int,
        ]
        lib.puff_caps.restype = None
        lib.puff_shell.argtypes = [
            ctypes.c_float,
            ctypes.c_float,
            ctypes.c_float,
            ctypes.c_float,
            ctypes.c_float,
            ctypes.c_float,
            ctypes.c_float,
            ctypes.c_float,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_int,
            ctypes.c_int,
        ]
        lib.puff_shell.restype = None
        lib.puff_fur.argtypes = [
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_float,
            ctypes.c_float,
        ]
        lib.puff_fur.restype = None
        _SPLAT = lib
    return _SPLAT


def opaque_box(image: np.ndarray, minimum: int = 90):
    hit = image[..., 3] > minimum
    ys, xs = np.where(hit)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def eye_whites(image: np.ndarray) -> int:
    rgb = image[..., :3]
    white = (rgb[..., 0] > 225) & (rgb[..., 1] > 225) & (rgb[..., 2] > 220) & (image[..., 3] > 200)
    return int(white.sum())


# --- tamagotchi -------------------------------------------------------------


def default_cfg() -> dict:
    return {
        "fur": "mint",
        "fur_set": False,
        "hunger": 0.76,
        "thirst": 0.72,
        "energy": 0.86,
        "happy": 0.8,
        "fat": 0.18,
        "growth": 0.48,
        "sound": True,
        "above": True,
        "x": None,
        "y": None,
        "z": None,
        "facing": 1,
        "asleep": False,
        "last": time.time(),
    }


def load_cfg() -> dict:
    cfg = default_cfg()
    existed = CFG.exists()
    try:
        raw = json.loads(CFG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    if isinstance(raw, dict):
        for key in cfg:
            if key in raw:
                cfg[key] = raw[key]
    cfg["fur"] = cfg["fur"] if cfg["fur"] in FURS else "mint"
    # The first launch stored the old honey default. The reference coat is mint.
    chosen = bool(raw.get("fur_set", False)) if isinstance(raw, dict) else False
    if not chosen and cfg["fur"] == "honey":
        cfg["fur"] = "mint"
        chosen = True
    cfg["fur_set"] = chosen
    for key, low, high in (
        ("hunger", 0.0, 1.0),
        ("thirst", 0.0, 1.0),
        ("energy", 0.0, 1.0),
        ("happy", 0.0, 1.0),
        ("fat", 0.0, 1.0),
        ("growth", 0.0, 1.0),
    ):
        try:
            cfg[key] = clamp(float(cfg[key]), low, high)
        except (TypeError, ValueError):
            cfg[key] = default_cfg()[key]
    cfg["sound"] = bool(cfg["sound"])
    cfg["above"] = bool(cfg["above"])
    cfg["asleep"] = bool(cfg.get("asleep", False))
    try:
        cfg["facing"] = 1 if int(cfg.get("facing", 1)) >= 0 else -1
    except (TypeError, ValueError):
        cfg["facing"] = 1
    try:
        cfg["x"] = None if cfg["x"] is None else float(cfg["x"])
    except (TypeError, ValueError):
        cfg["x"] = None
    try:
        cfg["y"] = None if cfg.get("y") is None else float(cfg["y"])
    except (TypeError, ValueError):
        cfg["y"] = None
    try:
        cfg["z"] = None if cfg.get("z") is None else float(cfg["z"])
    except (TypeError, ValueError):
        cfg["z"] = None
    try:
        cfg["last"] = float(cfg.get("last", time.time()))
    except (TypeError, ValueError):
        cfg["last"] = time.time()
    cfg["_existed"] = existed
    return cfg


def save_cfg(cfg: dict) -> None:
    CFG.parent.mkdir(parents=True, exist_ok=True)
    body = {key: cfg.get(key) for key in default_cfg()}
    body["last"] = time.time()
    tmp = CFG.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    tmp.replace(CFG)


# --- photographic goldfish --------------------------------------------------


# Yaw 0 is head-on, 90 is the side with the nose to the right, 180 is the tail.
VIEW_YAWS = (0, 45, 90, 120, 150, 180)
Z_NEAR = 0.74
Z_FAR = 2.45
Z_REF = 1.12
_VIEW_BANK: dict[int, tuple[list[np.ndarray], list[np.ndarray]]] | None = None
_PLATE_W: dict[int, int] = {}
_YAW_FIT: dict[int, float] = {}
_RESIZE_CACHE: dict[tuple, np.ndarray] = {}


def fish_length_px(px_per_cm: float) -> int:
    return max(120, int(round(px_per_cm * FISH_CM)))


def _premultiply(sprite: np.ndarray) -> np.ndarray:
    out = sprite.copy()
    alpha = sprite[..., 3:4].astype(np.float32) / 255.0
    out[..., :3] = np.clip(sprite[..., :3].astype(np.float32) * alpha, 0, 255).astype(np.uint8)
    return out


def _load_yaw_folder(yaw: int) -> list[np.ndarray]:
    from PIL import Image

    folder = FISH_DIR / f"yaw_{yaw:03d}"
    # The saved wag is an up-down bend. The live tail swings sideways from the rest pose.
    paths = sorted(folder.glob("wag_*.png"))[:1]
    if not paths and yaw == 90:
        paths = sorted(FISH_DIR.glob("wag_*.png"))[:1]
    if not paths:
        raise FileNotFoundError(f"no goldfish frames for {yaw}° in {folder}")
    frames = []
    flips = []
    for path in paths:
        sprite = _keep_main_body(np.asarray(Image.open(path).convert("RGBA")))
        prem = _feather_clipped(_premultiply(sprite))
        frames.append(prem)
        flips.append(_feather_clipped(_premultiply(np.ascontiguousarray(sprite[:, ::-1]))))
    if not frames:
        raise FileNotFoundError(f"no goldfish frames for {yaw}° in {folder}")
    _PLATE_W[yaw] = int(frames[0].shape[1])
    return _crop_folder(frames), _crop_folder(flips)


def _keep_main_body(sprite: np.ndarray) -> np.ndarray:
    """Drop a stray speck or a broken scrap that flashes beside the fish."""
    import cv2

    mask = (sprite[..., 3] > 28).astype(np.uint8)
    count, labels = cv2.connectedComponents(mask)
    if count <= 2:
        return sprite
    sizes = [(int((labels == index).sum()), index) for index in range(1, count)]
    main_size, main = max(sizes)
    drop = np.zeros(mask.shape, bool)
    for size, index in sizes:
        if index == main or size >= main_size * 0.12:
            continue
        drop |= labels == index
    if not bool(drop.any()):
        return sprite
    out = sprite.copy()
    out[drop] = 0
    return out


def _feather_clipped(sprite: np.ndarray) -> np.ndarray:
    """Fade a fin chopped by the picture edge, then pad clear pixels so no box shows."""
    out = sprite.astype(np.float32)
    alpha = out[..., 3]
    height, width = alpha.shape
    ramp = 26.0
    xs = np.arange(width, dtype=np.float32)
    ys = np.arange(height, dtype=np.float32)
    fade = np.ones((height, width), np.float32)
    if float(alpha[:, :3].max()) > 70:
        fade *= np.clip(xs / ramp, 0.0, 1.0)[None, :]
    if float(alpha[:, -3:].max()) > 70:
        fade *= np.clip((width - 1 - xs) / ramp, 0.0, 1.0)[None, :]
    if float(alpha[:3, :].max()) > 70:
        fade *= np.clip(ys / ramp, 0.0, 1.0)[:, None]
    if float(alpha[-3:, :].max()) > 70:
        fade *= np.clip((height - 1 - ys) / ramp, 0.0, 1.0)[:, None]
    if float(fade.min()) < 1.0:
        out[..., :3] *= fade[..., None]
        out[..., 3] *= fade
    pad = 10
    canvas = np.zeros((height + pad * 2, width + pad * 2, 4), np.float32)
    canvas[pad : pad + height, pad : pad + width] = out
    soft = canvas[..., 3] < 24
    canvas[soft] = 0
    return canvas.astype(np.uint8)


def _opaque_span(counts: np.ndarray, minimum: int = 6, gap: int = 8) -> tuple[int, int]:
    """Widest run of real fish. A few leftover key specks must not stretch the window."""
    keep = counts >= minimum
    if not bool(keep.any()):
        return 0, len(counts)
    closed = np.convolve(keep.astype(np.uint8), np.ones(gap, dtype=np.uint8), mode="same") > 0
    best_start, best_end, start, longest = 0, len(counts), None, -1
    for index, on in enumerate(list(closed)):
        if on and start is None:
            start = index
        elif not on and start is not None:
            if index - start > longest:
                longest = index - start
                best_start, best_end = start, index
            start = None
    if start is not None and len(counts) - start > longest:
        best_start, best_end = start, len(counts)
    return best_start, best_end


def _crop_folder(frames: list[np.ndarray], pad: int = 14) -> list[np.ndarray]:
    """One shared crop for the whole wag, tight on the fish so the window is not a plate."""
    height, width = frames[0].shape[:2]
    columns = np.zeros(width, np.int32)
    rows = np.zeros(height, np.int32)
    for sprite in frames:
        opaque = sprite[..., 3] > 24
        columns = np.maximum(columns, opaque.sum(axis=0))
        rows = np.maximum(rows, opaque.sum(axis=1))
    x0, x1 = _opaque_span(columns)
    y0, y1 = _opaque_span(rows)
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(width, x1 + pad)
    y1 = min(height, y1 + pad)
    if x1 <= x0 or y1 <= y0:
        return frames
    return [np.ascontiguousarray(sprite[y0:y1, x0:x1]) for sprite in frames]


def discover_yaws() -> tuple[int, ...]:
    """Every saved camera angle, including the extra stages between the originals."""
    found = []
    if FISH_DIR.is_dir():
        for folder in FISH_DIR.glob("yaw_*"):
            try:
                yaw = int(folder.name.split("_", 1)[1])
            except ValueError:
                continue
            if any(folder.glob("wag_*.png")):
                found.append(yaw)
    return tuple(sorted(set(found))) if found else (0, 45, 90, 120, 150, 180)


def view_bank() -> dict[int, tuple[list[np.ndarray], list[np.ndarray]]]:
    """Premultiplied turnaround. Each yaw stores the photo and its mirror."""
    global _VIEW_BANK, VIEW_YAWS
    if _VIEW_BANK is None:
        VIEW_YAWS = discover_yaws()
        _VIEW_BANK = {yaw: _load_yaw_folder(yaw) for yaw in VIEW_YAWS}
        _measure_fit()
    return _VIEW_BANK


def _measure_fit() -> None:
    """One scale per photo so a turn does not inflate the fish."""
    counts: dict[int, int] = {}
    for yaw, (frames, _flips) in (_VIEW_BANK or {}).items():
        sized = _resize_prem(frames[0], 400, _PLATE_W[yaw])
        counts[yaw] = max(1, int((sized[..., 3] > 24).sum()))
    if not counts:
        return
    ref = counts.get(90, next(iter(counts.values())))
    for yaw, count in counts.items():
        _YAW_FIT[yaw] = math.sqrt(ref / count)


def resolve_view(yaw_deg: float) -> tuple[int, bool]:
    """Map a heading to a stored angle. Above 180° is the mirror of the other side."""
    degree = yaw_deg % 360.0
    if degree <= 180.0:
        source, flip = degree, False
    else:
        source, flip = 360.0 - degree, True
    bank_yaw = min(VIEW_YAWS, key=lambda yaw: abs(yaw - source))
    return bank_yaw, flip


def turn_pair(yaw_deg: float) -> tuple[tuple[int, bool], tuple[int, bool], float, float]:
    """Neighbouring photos, a sharp choice, and a soft mix through the gap.

    Straight swimming uses the sharp choice. A turn uses the soft mix so the
    picture eases instead of popping to the next angle.
    """
    degree = yaw_deg % 360.0
    if degree <= 180.0:
        source, flip = degree, False
    else:
        source, flip = 360.0 - degree, True
    left, right = VIEW_YAWS[0], VIEW_YAWS[-1]
    raw = 0.0
    for index in range(len(VIEW_YAWS) - 1):
        start, end = VIEW_YAWS[index], VIEW_YAWS[index + 1]
        if source <= end or index == len(VIEW_YAWS) - 2:
            raw = clamp((source - start) / max(1.0, float(end - start)), 0.0, 1.0)
            left, right = start, end
            break
    sharp = 0.0 if raw < 0.5 else 1.0
    return (left, flip), (right, flip), sharp, smoothstep(raw)


def _centroid(sprite: np.ndarray) -> tuple[float, float]:
    """Body centre. Not cached: wag frames are new arrays and ids get reused."""
    alpha = sprite[..., 3].astype(np.float32)
    total = float(alpha.sum())
    height, width = alpha.shape
    if total < 1.0:
        return width / 2.0, height / 2.0
    xs = np.arange(width, dtype=np.float32)
    ys = np.arange(height, dtype=np.float32)
    return (
        float((alpha.sum(axis=0) * xs).sum() / total),
        float((alpha.sum(axis=1) * ys).sum() / total),
    )


def _stamp(canvas: np.ndarray, sprite: np.ndarray, weight: float) -> None:
    """Add a premultiplied photo with its body on the canvas centre."""
    if weight <= 0.001:
        return
    canvas_h, canvas_w = canvas.shape[:2]
    sprite_h, sprite_w = sprite.shape[:2]
    body_x, body_y = _centroid(sprite)
    left = int(round(canvas_w / 2.0 - body_x))
    top = int(round(canvas_h / 2.0 - body_y))
    src_x0 = max(0, -left)
    src_y0 = max(0, -top)
    src_x1 = min(sprite_w, canvas_w - left)
    src_y1 = min(sprite_h, canvas_h - top)
    if src_x1 <= src_x0 or src_y1 <= src_y0:
        return
    patch = sprite[src_y0:src_y1, src_x0:src_x1].astype(np.float32)
    if weight < 0.999:
        patch *= weight
    dest_y = max(0, top)
    dest_x = max(0, left)
    canvas[dest_y : dest_y + patch.shape[0], dest_x : dest_x + patch.shape[1]] += patch


def fish_frames() -> list[np.ndarray]:
    """Straight side-view stills, used for the icon if the three-quarter is missing."""
    from PIL import Image

    paths = sorted((FISH_DIR / "yaw_090").glob("wag_*.png")) or sorted(FISH_DIR.glob("wag_*.png"))
    return [np.asarray(Image.open(path).convert("RGBA")) for path in paths]


def _spin_rgba(sprite: np.ndarray, angle: float) -> np.ndarray:
    """Rotate a premultiplied photo. RGB stays premultiplied so the fins stay clean."""
    import cv2

    height, width = sprite.shape[:2]
    center = (width / 2.0, height / 2.0)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    cosine = abs(float(matrix[0, 0]))
    sine = abs(float(matrix[0, 1]))
    bound_w = int(math.ceil(height * sine + width * cosine)) + 2
    bound_h = int(math.ceil(height * cosine + width * sine)) + 2
    matrix[0, 2] += bound_w / 2.0 - center[0]
    matrix[1, 2] += bound_h / 2.0 - center[1]
    # OpenCV's positive angle is counter-clockwise, the same way PIL rotates.
    return cv2.warpAffine(
        sprite,
        matrix,
        (bound_w, bound_h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )


def _place_square(sprite: np.ndarray, side: int) -> np.ndarray:
    height, width = sprite.shape[:2]
    if height > side or width > side:
        y0 = max(0, (height - side) // 2)
        x0 = max(0, (width - side) // 2)
        sprite = sprite[y0 : y0 + side, x0 : x0 + side]
        height, width = sprite.shape[:2]
    canvas = np.zeros((side, side, 4), np.uint8)
    top = (side - height) // 2
    left = (side - width) // 2
    canvas[top : top + height, left : left + width] = sprite
    return canvas


def _resize_prem(sprite: np.ndarray, length: int, plate_w: int) -> np.ndarray:
    """Scale as if `plate_w` were `length` px, so a tight crop stays the same fish size."""
    import cv2

    key = (id(sprite), length, plate_w)
    cached = _RESIZE_CACHE.get(key)
    if cached is not None:
        return cached
    span = max(plate_w, 1)
    target_w = max(1, int(round(length * sprite.shape[1] / span)))
    target_h = max(1, int(round(length * sprite.shape[0] / span)))
    if target_w == sprite.shape[1] and target_h == sprite.shape[0]:
        out = sprite
    else:
        out = cv2.resize(sprite, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
    low = out[..., 3] < 18
    if bool(low.any()):
        out = out.copy()
        out[low] = 0
    if len(_RESIZE_CACHE) > 320:
        _RESIZE_CACHE.clear()
    _RESIZE_CACHE[key] = out
    return out


_FLAP_CACHE: dict[tuple, np.ndarray] = {}
_FLAP_CACHE_ID: int | None = None


def _flap_leftright(sprite: np.ndarray, phase: float) -> np.ndarray:
    """Swing the tail left and right. Side views move the tail end; head-on views open both sides."""
    global _FLAP_CACHE_ID
    import cv2

    bucket = int(math.floor((phase / math.tau) % 1.0 * 16.0)) % 16
    if _FLAP_CACHE_ID != id(sprite):
        _FLAP_CACHE.clear()
        _FLAP_CACHE_ID = id(sprite)
    cached = _FLAP_CACHE.get(bucket)
    if cached is not None:
        return cached
    alpha = sprite[..., 3]
    opaque = alpha > 40
    if int(opaque.sum()) < 30:
        return sprite
    ys, xs = np.nonzero(opaque)
    del ys
    x0 = int(xs.min())
    x1 = int(xs.max())
    span = max(1, x1 - x0)
    cx = (x0 + x1) * 0.5
    rgb_sum = sprite[..., 0].astype(np.int16) + sprite[..., 1].astype(np.int16) + sprite[..., 2].astype(np.int16)
    dark = (rgb_sum < 120) & (alpha > 170)
    if int(dark.sum()) < 25:
        dark = (rgb_sum < 180) & (alpha > 150)
    eye_x = float(np.nonzero(dark)[1].mean()) if bool(dark.any()) else cx
    side_on = abs(eye_x - cx) > span * 0.12
    amp = span * 0.085
    margin = int(math.ceil(amp)) + 6
    height, width = sprite.shape[:2]
    padded = np.zeros((height, width + margin * 2, 4), np.uint8)
    padded[:, margin : margin + width] = sprite
    x0p = x0 + margin
    x1p = x1 + margin
    cxp = cx + margin
    cols = np.arange(padded.shape[1], dtype=np.float32)
    if side_on:
        tail_on_left = (eye_x + margin) > cxp
        along = (cols - x0p) / span if tail_on_left else (x1p - cols) / span
        strength = np.clip(1.0 - along / 0.38, 0.0, 1.0) ** 2
        shift = math.sin(phase) * amp * strength
    else:
        along = np.abs(cols - cxp) / (span * 0.5)
        strength = np.clip((along - 0.58) / 0.42, 0.0, 1.0) ** 2
        sign = np.where(cols >= cxp, 1.0, -1.0).astype(np.float32)
        shift = sign * math.sin(phase) * amp * strength
    map_x = np.broadcast_to(cols - shift.astype(np.float32), (height, padded.shape[1])).copy()
    map_y = np.broadcast_to(np.arange(height, dtype=np.float32)[:, None], map_x.shape).copy()
    warped = cv2.remap(
        padded,
        map_x,
        map_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )
    low = warped[..., 3] < 18
    if bool(low.any()):
        warped = warped.copy()
        warped[low] = 0
    _FLAP_CACHE[bucket] = warped
    return warped


def _wag_frame(frames: list[np.ndarray], phase: float, length: int, plate_w: int) -> np.ndarray:
    """Blend the two nearest tail photos so the wag does not step."""
    count = len(frames)
    pos = (phase / math.tau) % 1.0 * count
    index = int(math.floor(pos)) % count
    frac = pos - math.floor(pos)
    current = _resize_prem(frames[index], length, plate_w)
    if frac < 0.04:
        return current
    nxt = _resize_prem(frames[(index + 1) % count], length, plate_w)
    if current.shape != nxt.shape:
        width = max(current.shape[1], nxt.shape[1])
        height = max(current.shape[0], nxt.shape[0])
        current = _center_on(current, width, height)
        nxt = _center_on(nxt, width, height)
    mix = current.astype(np.float32) * (1.0 - frac) + nxt.astype(np.float32) * frac
    mix[mix[..., 3] < 18] = 0
    return mix.astype(np.uint8)


def _center_on(sprite: np.ndarray, width: int, height: int) -> np.ndarray:
    canvas = np.zeros((height, width, 4), np.uint8)
    src_h, src_w = sprite.shape[:2]
    if src_h > height or src_w > width:
        y0 = max(0, (src_h - height) // 2)
        x0 = max(0, (src_w - width) // 2)
        sprite = sprite[y0 : y0 + height, x0 : x0 + width]
        src_h, src_w = sprite.shape[:2]
    top = (height - src_h) // 2
    left = (width - src_w) // 2
    canvas[top : top + src_h, left : left + src_w] = sprite
    return canvas


def display_length(scale: float, px_per_cm: float) -> int:
    """Sprite size. Steps of 4 px keep the tail smooth without a new picture every pixel."""
    raw = fish_length_px(px_per_cm) * max(0.35, scale)
    return max(96, int(round(raw / 4.0)) * 4)


def canvas_side(px_per_cm: float) -> int:
    """One window for the whole swim. Depth only changes the fish inside it."""
    return display_length(Z_REF / Z_NEAR, px_per_cm)


FLAKE_COLOURS = (
    (0.96, 0.62, 0.14),
    (0.99, 0.80, 0.28),
    (0.86, 0.46, 0.10),
    (0.68, 0.38, 0.12),
    (1.0, 0.88, 0.48),
)


def spawn_flakes(spread: float = 72.0) -> list[dict]:
    """Food flakes bursting over the fish. Offsets are from the middle of his body."""
    flakes = []
    reach = max(36.0, float(spread))
    for _ in range(40):
        ang = random.uniform(0.0, math.tau)
        start = random.uniform(0.0, reach * 0.16)
        speed = random.uniform(reach * 1.1, reach * 2.6)
        flakes.append(
            {
                "x": math.cos(ang) * start,
                "y": -reach * 0.22 + math.sin(ang) * start * 0.35,
                "vx": math.cos(ang) * speed * random.uniform(0.45, 1.0),
                "vy": -random.uniform(reach * 0.35, reach * 1.15),
                "life": random.uniform(0.9, 1.45),
                "age": 0.0,
                "rot": random.uniform(0.0, math.tau),
                "spin": random.uniform(-12.0, 12.0),
                "rx": random.uniform(reach * 0.05, reach * 0.12),
                "ry": random.uniform(reach * 0.018, reach * 0.042),
                "rgb": random.choice(FLAKE_COLOURS),
            }
        )
    return flakes


def step_flakes(flakes: list[dict], dt: float) -> list[dict]:
    alive = []
    for flake in flakes:
        flake["age"] += dt
        if flake["age"] >= flake["life"]:
            continue
        flake["vy"] += 640.0 * dt
        flake["x"] += flake["vx"] * dt
        flake["y"] += flake["vy"] * dt
        flake["rot"] += flake["spin"] * dt
        alive.append(flake)
    return alive


def paint_flakes(cr, flakes: list[dict], width: int, height: int) -> None:
    if not flakes:
        return
    cx = width * 0.5
    cy = height * 0.5
    for flake in flakes:
        life = max(0.001, flake["life"])
        u = flake["age"] / life
        alpha = (1.0 - u * u) * min(1.0, flake["age"] / 0.06)
        if alpha <= 0.02:
            continue
        cr.save()
        cr.translate(cx + flake["x"], cy + flake["y"])
        cr.rotate(flake["rot"])
        red, green, blue = flake["rgb"]
        cr.set_source_rgba(red, green, blue, alpha)
        cr.save()
        cr.scale(flake["rx"], flake["ry"])
        cr.arc(0, 0, 1, 0, math.tau)
        cr.fill()
        cr.restore()
        cr.set_source_rgba(1.0, 0.95, 0.75, alpha * 0.75)
        cr.save()
        cr.translate(-flake["rx"] * 0.15, -flake["ry"] * 0.1)
        cr.scale(flake["rx"] * 0.38, flake["ry"] * 0.38)
        cr.arc(0, 0, 1, 0, math.tau)
        cr.fill()
        cr.restore()
        cr.restore()


def _fitted_length(yaw: int, length: int) -> int:
    view_bank()
    return max(48, int(round(length * _YAW_FIT.get(yaw, 1.0))))


def compose_fish(
    view: float,
    pitch: float,
    phase: float,
    scale: float,
    px_per_cm: float,
) -> np.ndarray:
    """One cut-out photo. The nearest angle only, tail beating left and right."""
    del pitch  # the belly stays down; heading comes from the turnaround
    length = display_length(scale, px_per_cm)
    side = canvas_side(px_per_cm)
    yaw, flip = resolve_view(math.degrees(view))
    bank = view_bank()
    frames, flips = bank[yaw]
    rest = (flips if flip else frames)[0]
    current = _flap_leftright(_resize_prem(rest, _fitted_length(yaw, length), _PLATE_W[yaw]), phase)
    canvas = np.zeros((side, side, 4), np.float32)
    _stamp(canvas, current, 1.0)
    canvas[canvas[..., 3] < 18] = 0
    np.clip(canvas, 0, 255, out=canvas)
    return canvas.astype(np.uint8)


class Pet:
    """Mood, tummy, and where the goldfish is swimming."""

    def __init__(self, cfg: dict, now: float | None = None) -> None:
        self.fur = cfg["fur"]
        self.hunger = float(cfg["hunger"])
        self.thirst = float(cfg["thirst"])
        self.energy = float(cfg["energy"])
        self.happy = float(cfg["happy"])
        self.fat = float(cfg["fat"])
        self.growth = float(cfg["growth"])
        self.facing = 1 if int(cfg.get("facing", 1)) >= 0 else -1
        self.x = cfg.get("x")
        try:
            self.y = 0.0 if cfg.get("y") is None else float(cfg["y"])
        except (TypeError, ValueError):
            self.y = 0.0
        self.floor_y = 0.0
        self.swim_free = False
        self.dart = 0.0
        self.goal_until = 0.0
        self.need_until = 0.0
        self.goal_z = Z_REF
        try:
            self.wz = Z_FAR - 0.25 if cfg.get("z") is None else float(cfg["z"])
        except (TypeError, ValueError):
            self.wz = Z_FAR - 0.25
        self.vz = 0.0
        self.wx = None
        self.wy = 0.0
        self.view = 0.2
        self.pitch = 0.0
        self.depth_scale = Z_REF / max(self.wz, Z_NEAR)
        self.shown_scale = self.depth_scale
        self.draw_x: float | None = None
        self.draw_y = 0.0
        self.turn_rate = 0.0
        self.turn_amount = 0.0
        self.fade = 1.0
        self.fade_from: tuple[int, bool] | None = None
        self.draw_yaw: int | None = None
        self.draw_flip = False
        self.yaw = self.facing * math.pi / 2.0
        self.yaw_target = self.yaw
        self.spinning = False
        self.state = "sleep" if cfg.get("asleep") else "wave"
        self.t = 0.0
        self.hold = 1.6
        self.phase = random.random() * math.tau
        self.blink = 1.0 if self.state == "sleep" else 0.0
        self.blink_left = 0.0
        self.next_blink = 1.2
        self.wave = 1.0 if self.state == "wave" else 0.0
        self.sit = 0.0
        self.lay = 1.0 if self.state == "sleep" else 0.0
        self.climb = 0.0
        self.reach = 0.0
        self.stretch = 0.0
        self.mouth = 0.0
        self.eat = 0.0
        self.drink = 0.0
        self.cheek = 0.0
        self.look_x = 0.0
        self.look_y = 0.0
        self.squash = 1.0
        self.squash_kick = 1.0
        self.roll = 0.0
        self.airborne = False
        self.vx = 0.0
        self.vy = 0.0
        self.after_land = "walk"
        self.attention = False
        self.cue = "squeak" if self.state == "wave" and not cfg.get("_existed", True) else None
        self.mode = ""
        self.path: list[tuple] = []
        self.path_i = 0
        self.goal_x = 0.0
        self.goal_y = 0.0
        self.x0 = 0.0
        self.y0 = 0.0
        self.last_tap = -100.0
        now = time.monotonic() if now is None else now
        first = not cfg.get("_existed", True) and not cfg.get("asleep")
        self.next_hop = now + (8.0 if first else random.uniform(20.0, 40.0))
        self.next_climb = now + (16.0 if first else random.uniform(40.0, 70.0))
        self.next_idle = now + (28.0 if first else random.uniform(12.0, 24.0))
        self._settle_offline(cfg)

    def _settle_offline(self, cfg: dict) -> None:
        try:
            elapsed = time.time() - float(cfg.get("last", time.time()))
        except (TypeError, ValueError):
            elapsed = 0.0
        elapsed = clamp(elapsed, 0.0, 6.0 * 3600.0)
        left = elapsed
        while left > 0.5:
            dt = min(20.0, left)
            if self.energy < 0.22:
                self.energy = min(1.0, self.energy + dt / 150.0)
                self.state = "sleep"
                self.lay = 1.0
                self.blink = 1.0
            else:
                self.energy = max(0.0, self.energy - dt / (28.0 * 60.0))
            self.hunger = max(0.0, self.hunger - dt / (50.0 * 60.0))
            self.happy = max(0.0, self.happy - dt / (40.0 * 60.0))
            if self.hunger < 0.55:
                self.fat = max(0.04, self.fat - dt / (5.0 * 3600.0))
            if self.hunger > 0.35 and self.happy > 0.25:
                self.growth = min(1.0, self.growth + dt / (36.0 * 3600.0))
            left -= dt

    def enter(self, state: str, cue: str | None = None) -> None:
        if state == self.state and cue is None:
            return
        self.state = state
        self.t = 0.0
        self.spinning = state == "spin"
        if cue:
            self.cue = cue
        if state == "wave":
            self.hold = 1.7
        elif state == "idle":
            self.hold = random.uniform(1.5, 3.2)
        elif state == "sit":
            self.hold = random.uniform(2.0, 4.4)
        elif state == "lay":
            self.hold = random.uniform(2.6, 5.2)
        elif state == "laugh":
            self.hold = 1.15
        elif state == "eat":
            self.hold = 1.15
        elif state == "drink":
            self.hold = 1.05
        elif state == "burp":
            self.hold = 0.78
        elif state == "tap":
            self.hold = 1.9
        elif state == "yawn":
            self.hold = 2.15
        elif state == "spin":
            self.hold = 3.2
            self._spin_from = self.yaw

    def feed_snack(self) -> None:
        over = self.hunger > 0.68
        self.hunger = min(1.0, self.hunger + 0.30)
        self.happy = min(1.0, self.happy + 0.12)
        self.fat = min(1.0, self.fat + (0.09 if over else 0.02))
        self.attention = False
        self.airborne = False
        self.enter("eat", "munch")
        if self.swim_free:
            self.dart = 0.75

    def on_pet(self) -> None:
        self.happy = min(1.0, self.happy + 0.10)
        self.attention = False
        if self.swim_free:
            self.dart = 0.9
            self.cue = "squeak"
            return
        if random.random() < 0.55:
            self.enter("wave", "squeak")
        else:
            self.enter("laugh", "giggle")

    def on_attention(self) -> None:
        if self.swim_free:
            self.attention = True
            self.dart = max(self.dart, 0.35)
            return
        self.attention = True
        if self.state == "sleep":
            self.energy = min(1.0, self.energy + 0.08)
            self.enter("sit", "squeak")
            return
        if self.state in {"eat", "drink", "burp", "yawn", "air"}:
            return
        if self.state == "climb":
            return
        self.enter("sit")

    def hop(self, vy: float = -460.0, vx: float = 0.0, after: str = "walk") -> None:
        self.airborne = True
        self.vy = vy
        self.vx = vx
        self.after_land = after
        self.squash_kick = 0.82
        self.enter("air")

    def start_climb(self, grid, x_min: float, x_max: float, now: float) -> bool:
        cols, _rows, cells, occupied = grid
        if cols < 2 or not occupied or not cells:
            return False
        column = random.choice(occupied)
        mine = [cell for cell in cells if cell[0] == column and cell[3] < self.floor_y - 56]
        mine.sort(key=lambda cell: -cell[1])
        if len(mine) < 2:
            return False
        steps = min(len(mine) - 1, random.randint(2, 5))
        self.path = mine[: steps + 1]
        self.path_i = 0
        self.mode = "goto"
        self.goal_x = clamp(self.path[0][2], x_min, x_max)
        self.goal_y = self.path[0][3]
        self.enter("climb")
        self.next_climb = now + random.uniform(45.0, 80.0)
        return True

    def _climb_mode(self, mode: str) -> None:
        self.mode = mode
        self.t = 0.0
        self.x0 = self.x
        self.y0 = self.y

    def update(
        self,
        dt: float,
        now: float,
        grid,
        floor_y: float,
        x_min: float,
        x_max: float,
        y_min: float | None = None,
        y_max: float | None = None,
    ) -> None:
        if self.swim_free:
            if y_min is None or y_max is None:
                y_min = floor_y - 700.0
                y_max = floor_y
            self._swim(dt, now, x_min, x_max, y_min, y_max)
            return
        self.floor_y = floor_y
        if self.x is None:
            self.x = (x_min + x_max) * 0.5
        if self.y <= 1.0:
            self.y = floor_y
        self._needs(dt)
        self._blinks(dt)
        if self.airborne:
            self._fly(dt, x_min, x_max)
        elif self.attention and self.state in {"walk", "idle", "turn", "spin"}:
            self.enter("sit")
            self._act(dt, now, grid, x_min, x_max)
        else:
            self._act(dt, now, grid, x_min, x_max)
        self._smooth(dt)
        self.phase += dt * (8.5 if self.state in {"walk", "turn", "climb"} else 3.2)
        if self.state == "wave":
            self.phase += dt * 4.0

    def _needs(self, dt: float) -> None:
        sleeping = self.state == "sleep"
        if self.state == "swim":
            self.energy += (0.62 - self.energy) * dt / 90.0
            self.energy = clamp(self.energy, 0.0, 1.0)
        elif sleeping:
            self.energy = min(1.0, self.energy + dt / 150.0)
        else:
            self.energy = max(0.0, self.energy - dt / (28.0 * 60.0))
        self.hunger = max(0.0, self.hunger - dt / (50.0 * 60.0))
        self.happy = max(0.0, self.happy - dt / (40.0 * 60.0))
        if self.state in {"walk", "swim"} and self.hunger < 0.6:
            self.fat = max(0.04, self.fat - dt / (3.5 * 3600.0))
        if self.hunger > 0.35 and self.happy > 0.25 and not sleeping:
            self.growth = min(1.0, self.growth + dt / (36.0 * 3600.0))

    def _blinks(self, dt: float) -> None:
        if self.state in {"sleep", "yawn"}:
            return
        self.next_blink -= dt
        if self.blink_left > 0.0:
            self.blink_left -= dt
            self.blink = 1.0 if self.blink_left > 0.05 else 0.15
            return
        if self.next_blink <= 0.0:
            self.blink_left = 0.14
            self.blink = 1.0
            self.next_blink = random.uniform(2.2, 5.8)

    def _fly(self, dt: float, x_min: float, x_max: float) -> None:
        self.vy += 1750.0 * dt
        self.y += self.vy * dt
        self.x = clamp(self.x + self.vx * dt, x_min, x_max)
        self.yaw_target = self.facing * math.pi / 2.0 + self.vx * 0.002
        if self.y >= self.floor_y:
            self.y = self.floor_y
            self.vy = 0.0
            self.vx = 0.0
            self.airborne = False
            self.squash_kick = 0.74
            landed = self.after_land
            self.after_land = "walk"
            self.enter(landed)
            if landed == "walk":
                self.cue = self.cue or None

    def _hungry_interrupt(self, now: float) -> bool:
        if self.attention:
            return False
        if self.energy < 0.16 and self.state in {"walk", "idle", "sit", "lay", "wave", "spin", "turn"}:
            self.enter("yawn", "yawn")
            return True
        needy = self.hunger < 0.30 or self.happy < 0.28
        if needy and self.state in {"walk", "idle", "sit"} and now - self.last_tap > 26.0:
            self.last_tap = now
            self.enter("tap", "tap")
            return True
        return False

    def _act(self, dt: float, now: float, grid, x_min: float, x_max: float) -> None:
        self.t += dt
        if self.state == "air":
            return
        if self._hungry_interrupt(now):
            return
        if self.state == "wave":
            self.yaw_target = 0.25
            if self.t > self.hold:
                self.enter("walk")
        elif self.state == "laugh":
            self.yaw_target = 0.15
            if self.t > self.hold:
                self.enter("sit")
        elif self.state == "eat":
            self.yaw_target = 0.35
            if self.t > self.hold:
                self.enter("laugh" if self.fat > 0.45 else "sit", "giggle" if self.fat > 0.55 else None)
        elif self.state == "drink":
            self.yaw_target = 0.3
            if self.t > self.hold:
                self.enter("burp", "burp")
        elif self.state == "burp":
            self.yaw_target = 0.2
            if self.t > self.hold:
                self.enter("sit")
        elif self.state == "tap":
            self.yaw_target = 0.05
            if self.t > self.hold:
                self.enter("sit")
        elif self.state == "yawn":
            self.yaw_target = 0.4
            if self.t > self.hold:
                self.enter("sleep")
        elif self.state == "sleep":
            self.yaw_target = 0.9
            if self.energy > 0.82 and not self.attention:
                self.enter("sit", "squeak")
        elif self.state == "spin":
            self.yaw = self._spin_from + smoothstep(self.t / self.hold) * math.tau
            self.yaw_target = self.yaw
            if self.t > self.hold:
                self.enter("wave", "squeak")
        elif self.state == "turn":
            self.yaw_target = self.facing * math.pi / 2.0
            if abs((self.yaw_target - self.yaw + math.pi) % math.tau - math.pi) < 0.12:
                self.enter("walk")
        elif self.state == "idle":
            self.yaw_target = 0.35 + 0.7 * math.sin(self.t * 0.8)
            if self.t > self.hold:
                roll = random.random()
                if roll < 0.28 and self.happy > 0.45:
                    self.enter("wave", "squeak")
                elif roll < 0.42 and self.happy > 0.62:
                    self.enter("spin")
                elif roll < 0.7:
                    self.enter("sit")
                else:
                    self.enter("walk")
        elif self.state == "sit":
            self.yaw_target = 0.42 + 0.25 * math.sin(self.t * 0.6)
            if not self.attention and self.t > self.hold:
                if self.energy < 0.4 or random.random() < 0.35:
                    self.enter("lay")
                elif random.random() < 0.3:
                    self.enter("wave", "squeak")
                else:
                    self.enter("walk")
        elif self.state == "lay":
            self.yaw_target = 1.05
            if self.t > self.hold:
                if self.energy < 0.34:
                    self.enter("yawn", "yawn")
                else:
                    self.enter("sit")
        elif self.state == "climb":
            self._climb(dt, x_min, x_max)
        else:
            # Walk the bottom edge.
            speed = 62.0 * (1.0 - self.fat * 0.28)
            self.x += self.facing * speed * dt
            self.y = self.floor_y
            self.yaw_target = self.facing * math.pi / 2.0
            if self.x <= x_min or self.x >= x_max:
                self.x = clamp(self.x, x_min, x_max)
                self.facing *= -1
                self.enter("turn")
                return
            if not self.attention and now > self.next_hop:
                self.next_hop = now + random.uniform(18.0, 36.0)
                self.hop(-420.0, 0.0, "walk")
                return
            if not self.attention and now > self.next_climb:
                if self.start_climb(grid, x_min, x_max, now):
                    return
                self.next_climb = now + 25.0
            if not self.attention and now > self.next_idle:
                self.next_idle = now + random.uniform(16.0, 32.0)
                self.enter("idle")

    def _climb(self, dt: float, x_min: float, x_max: float) -> None:
        if self.mode == "goto":
            dx = self.goal_x - self.x
            self.facing = 1 if dx >= 0 else -1
            self.yaw_target = self.facing * math.pi / 2.0
            step = min(abs(dx), 70.0 * dt)
            self.x += math.copysign(step, dx) if dx else 0.0
            self.y = self.floor_y
            if abs(self.goal_x - self.x) < 6.0:
                self.x = self.goal_x
                self._climb_mode("leap")
        elif self.mode == "leap":
            u = smoothstep(self.t / 0.38)
            self.x = self.goal_x
            self.y = self.y0 + (self.goal_y - self.y0) * u
            self.yaw_target = 0.45
            if self.t > 0.38:
                self.y = self.goal_y
                self.path_i = 1
                if self.path_i < len(self.path):
                    self.goal_x = clamp(self.path[self.path_i][2], x_min, x_max)
                    self.goal_y = self.path[self.path_i][3]
                    self._climb_mode("up")
                else:
                    self._climb_mode("perch")
        elif self.mode == "up":
            u = smoothstep(self.t / 0.42)
            self.x = self.x0 + (self.goal_x - self.x0) * u
            self.y = self.y0 + (self.goal_y - self.y0) * u
            self.yaw_target = 0.5
            if self.t > 0.42:
                self.x = self.goal_x
                self.y = self.goal_y
                self.path_i += 1
                if self.path_i < len(self.path):
                    self.goal_x = clamp(self.path[self.path_i][2], x_min, x_max)
                    self.goal_y = self.path[self.path_i][3]
                    self._climb_mode("up")
                else:
                    self._climb_mode("perch")
        elif self.mode == "perch":
            self.yaw_target = 0.2 + 0.8 * math.sin(self.t * 0.7)
            if self.t > 2.3:
                direction = 1 if self.x < (x_min + x_max) * 0.5 else -1
                self.facing = direction
                self.hop(-380.0, direction * 160.0, "walk")
        else:
            self.enter("walk")

    def _smooth(self, dt: float) -> None:
        if not self.spinning:
            self.yaw = approach_angle(self.yaw, self.yaw_target, 2.6, dt)
        targets = {
            "wave": 1.0 if self.state == "wave" else (0.25 if self.state == "laugh" else 0.0),
            "sit": 1.0 if self.state in {"sit", "eat", "drink", "burp"} else (0.45 if self.state == "perch" else 0.0),
            "lay": 1.0 if self.state in {"lay", "sleep", "yawn"} else 0.0,
            "climb": 1.0 if self.state == "climb" and self.mode in {"leap", "up"} else 0.0,
            "stretch": 1.0 if self.state == "yawn" else (0.35 if self.state == "air" else 0.0),
            "eat": 1.0 if self.state == "eat" else 0.0,
            "drink": 1.0 if self.state == "drink" else 0.0,
            "cheek": 1.0 if self.state == "burp" else 0.0,
        }
        if self.state == "climb" and self.mode == "perch":
            targets["sit"] = 0.7
        self.wave = approach(self.wave, targets["wave"], 7.0, dt)
        self.sit = approach(self.sit, targets["sit"], 5.0, dt)
        self.lay = approach(self.lay, targets["lay"], 3.5, dt)
        self.climb = approach(self.climb, targets["climb"], 6.0, dt)
        self.stretch = approach(self.stretch, targets["stretch"], 4.0, dt)
        self.eat = approach(self.eat, targets["eat"], 6.0, dt)
        self.drink = approach(self.drink, targets["drink"], 6.0, dt)
        self.cheek = approach(self.cheek, targets["cheek"], 8.0, dt)
        mouth = 0.08
        if self.state == "yawn":
            mouth = math.sin(min(1.0, self.t / self.hold) * math.pi)
        elif self.state == "laugh":
            mouth = 0.75
        elif self.state == "eat":
            mouth = 0.35 + 0.25 * abs(math.sin(self.t * 10.0))
        elif self.state == "burp":
            mouth = 0.5
        elif self.state == "drink":
            mouth = 0.28
        elif self.state == "wave":
            mouth = 0.18
        self.mouth = approach(self.mouth, mouth, 8.0, dt)
        reach = 0.0
        if self.state == "tap":
            reach = max(0.0, math.sin(self.t * 10.4))
        self.reach = approach(self.reach, reach, 14.0, dt)
        if self.state == "sleep":
            self.blink = 1.0
        elif self.state == "yawn":
            self.blink = max(self.blink, 0.45 + 0.4 * self.mouth)
        look_y = 0.55 if self.attention or self.state == "tap" else (0.2 if self.state in {"sit", "eat"} else 0.0)
        look_x = 0.3 * math.sin(self.phase * 0.35) if self.state in {"idle", "sit", "spin"} else 0.0
        self.look_x = approach(self.look_x, look_x, 4.0, dt)
        self.look_y = approach(self.look_y, look_y, 4.0, dt)
        self.squash_kick = approach(self.squash_kick, 1.0, 6.0, dt)
        bob = 1.0
        if self.state == "walk":
            bob = 1.0 - 0.04 * abs(math.sin(self.phase))
        elif self.state == "laugh":
            bob = 1.0 - 0.06 * abs(math.sin(self.t * 14.0))
        elif self.state == "burp":
            bob = 0.9 if self.t < 0.15 else 1.0
        self.squash = bob * self.squash_kick

    def _pick_goal(self, x_min: float, x_max: float, y_min: float, y_max: float, now: float) -> None:
        here_x = self.wx if self.wx is not None else (x_min + x_max) * 0.5
        heading = math.atan2(self.vy, self.vx)
        turn = random.uniform(-0.55, 0.55)
        if random.random() < 0.16:
            turn = random.uniform(-1.8, 1.8)
        reach = random.uniform(340.0, 720.0)
        self.goal_x = clamp(here_x + math.cos(heading + turn) * reach, x_min, x_max)
        self.goal_y = clamp(self.wy + math.sin(heading + turn) * reach, y_min, y_max)
        # Depth drifts. A sudden dive was turning the photo to face the glass.
        self.goal_z = clamp(self.wz + random.uniform(-0.45, 0.45), Z_NEAR + 0.08, Z_FAR - 0.08)
        self.goal_until = now + random.uniform(6.5, 11.0)

    def _swim(self, dt: float, now: float, x_min: float, x_max: float, y_min: float, y_max: float) -> None:
        """Swim a tank with depth. Close to the glass he is large and head-on. Far away he is small, tail toward us."""
        if x_max < x_min:
            x_min, x_max = x_max, x_min
        if y_max < y_min:
            y_min, y_max = y_max, y_min
        if self.wx is None:
            self.wx = (x_min + x_max) * 0.5 if self.x is None else clamp(float(self.x), x_min, x_max)
            self.wy = (y_min + y_max) * 0.5 if self.y <= 1.0 else clamp(self.y, y_min, y_max)
            self.wz = clamp(self.wz, Z_NEAR, Z_FAR)
            self.vx = 36.0
            self.vy = 8.0
            self.vz = -0.36
            self.goal_x = clamp(self.wx + 220.0, x_min, x_max)
            self.goal_y = self.wy
            self.goal_z = Z_NEAR + 0.06
            self.goal_until = now + 5.0
        self.wx = clamp(self.wx, x_min, x_max)
        self.wy = clamp(self.wy, y_min, y_max)
        flat = math.hypot(self.goal_x - self.wx, self.goal_y - self.wy)
        if self.goal_until <= now or (flat < 90.0 and abs(self.goal_z - self.wz) < 0.12):
            self._pick_goal(x_min, x_max, y_min, y_max, now)
        self._needs(dt)
        self.t += dt
        if self.state in {"eat", "burp", "laugh", "wave"} and self.t > self.hold:
            self.state = "swim"
        elif self.state not in {"eat", "burp"}:
            self.state = "swim"
        if self.hunger < 0.30 and now - self.last_tap > 36.0 and now > self.need_until:
            self.last_tap = now
            self.need_until = now + 2.6
            self.cue = self.cue or "tap"

        steer_x = self.goal_x - self.wx
        steer_y = self.goal_y - self.wy
        edge = 90.0
        if self.wx < x_min + edge:
            steer_x += (x_min + edge - self.wx) * 2.2
        elif self.wx > x_max - edge:
            steer_x -= (self.wx - (x_max - edge)) * 2.2
        if self.wy < y_min + edge:
            steer_y += (y_min + edge - self.wy) * 2.2
        elif self.wy > y_max - edge:
            steer_y -= (self.wy - (y_max - edge)) * 2.2
        dist = max(1.0, math.hypot(steer_x, steer_y))
        cruise = 168.0 if self.dart > 0.0 else 108.0 + 8.0 * math.sin(self.phase * 0.13)
        if self.dart > 0.0:
            self.dart = max(0.0, self.dart - dt)
        desired_vz = clamp((self.goal_z - self.wz) * 0.45, -0.22, 0.22)
        self.vz = approach(self.vz, desired_vz, 0.85, dt)
        # Arc toward the goal. A direct velocity blend used to spin the picture.
        heading = math.atan2(self.vy, self.vx)
        if math.hypot(self.vx, self.vy) < 6.0:
            heading = math.atan2(steer_y, steer_x)
        desired = math.atan2(steer_y, steer_x)
        turn = (desired - heading + math.pi) % math.tau - math.pi
        max_rate = 1.55 if self.dart > 0.0 else 0.58
        heading += clamp(turn, -max_rate * dt, max_rate * dt)
        speed = approach(math.hypot(self.vx, self.vy), cruise, 1.35, dt)
        self.vx = math.cos(heading) * speed
        self.vy = math.sin(heading) * speed
        self.wx = clamp(self.wx + self.vx * dt, x_min, x_max)
        self.wy = clamp(self.wy + self.vy * dt, y_min, y_max)
        self.wz = clamp(self.wz + self.vz * dt, Z_NEAR, Z_FAR)
        if self.wx <= x_min + 6.0 and self.vx < 0.0:
            self.vx = approach(self.vx, 8.0, 6.0, dt)
        elif self.wx >= x_max - 6.0 and self.vx > 0.0:
            self.vx = approach(self.vx, -8.0, 6.0, dt)
        if self.wy <= y_min + 6.0 and self.vy < 0.0:
            self.vy = approach(self.vy, 8.0, 6.0, dt)
        elif self.wy >= y_max - 6.0 and self.vy > 0.0:
            self.vy = approach(self.vy, -8.0, 6.0, dt)
        if self.wz <= Z_NEAR + 0.04 and self.vz < 0.0:
            self.vz = approach(self.vz, 0.04, 4.0, dt)
        elif self.wz >= Z_FAR - 0.04 and self.vz > 0.0:
            self.vz = approach(self.vz, -0.04, 4.0, dt)
        scale = Z_REF / self.wz
        cx = (x_min + x_max) * 0.5
        cy = (y_min + y_max) * 0.5
        self.depth_scale = scale
        self.shown_scale = approach(self.shown_scale, scale, 1.6, dt)
        raw_x = cx + (self.wx - cx) * self.shown_scale
        raw_y = cy + (self.wy - cy) * self.shown_scale
        if self.draw_x is None:
            self.draw_x = raw_x
            self.draw_y = raw_y
        else:
            self.draw_x = approach(self.draw_x, raw_x, 7.0, dt)
            self.draw_y = approach(self.draw_y, raw_y, 7.0, dt)
        sway = math.sin(self.phase * 0.45) * 3.5
        self.x = self.draw_x + (-self.vy / max(speed, 1.0)) * sway
        self.y = self.draw_y + (self.vx / max(speed, 1.0)) * sway
        # Sideways cruise stays a side view. Only a real dive turns the face to the glass.
        target_view = math.atan2(self.vx, -self.vz * 260.0)
        self.view = approach_angle(self.view, target_view, 1.15, dt)
        horiz = max(0.2, math.hypot(self.vx / 140.0, self.vz / 0.55))
        target_pitch = clamp(math.atan2(-self.vy / 220.0, horiz), -0.35, 0.35)
        self.pitch = approach(self.pitch, target_pitch, 1.6, dt)
        self.facing = -1 if self.vx < 0.0 else 1
        self.phase += dt * (5.4 + min(2.4, speed / 70.0))

    def pose(self) -> dict:
        air = max(0.0, self.floor_y - self.y)
        return {
            "yaw": self.yaw,
            "phase": self.phase,
            "fat": self.fat,
            "growth": self.growth,
            "blink": self.blink,
            "mouth": self.mouth,
            "wave": self.wave,
            "sit": self.sit,
            "lay": self.lay,
            "climb": self.climb,
            "reach": self.reach,
            "stretch": self.stretch,
            "eat": self.eat,
            "drink": self.drink,
            "cheek": self.cheek,
            "look_x": self.look_x,
            "look_y": self.look_y,
            "squash": self.squash,
            "air": air,
            "breathe": self.phase * 0.55,
        }

    def snapshot(self, sound: bool, above: bool) -> dict:
        return {
            "fur": self.fur,
            "hunger": round(self.hunger, 4),
            "thirst": round(self.thirst, 4),
            "energy": round(self.energy, 4),
            "happy": round(self.happy, 4),
            "fat": round(self.fat, 4),
            "growth": round(self.growth, 4),
            "sound": sound,
            "above": above,
            "x": None if self.x is None else round(self.x, 1),
            "y": round(self.y, 1),
            "z": round(self.wz, 3),
            "facing": self.facing,
            "asleep": self.state == "sleep",
            "last": time.time(),
        }


def folder_grid(work: tuple[int, int, int, int], icon_size: str = "small", items: int = 12):
    """Desktop Icons NG cell centres. Returns cols, rows, cells, occupied columns."""
    widths = {"tiny": 70, "small": 90, "standard": 120, "large": 130}
    heights = {"tiny": 80, "small": 90, "standard": 106, "large": 138}
    icons = {"tiny": 36, "small": 48, "standard": 64, "large": 96}
    wx, wy, ww, wh = work
    dw = widths.get(icon_size, 90) + 8
    dh = heights.get(icon_size, 90) + 8
    cols = max(1, int(ww) // dw)
    rows = max(1, int(wh) // dh)
    ew = max(1, int(ww) // cols)
    eh = max(1, int(wh) // rows)
    icon = icons.get(icon_size, 48)
    cells = []
    for row in range(rows):
        for col in range(cols):
            cx = wx + col * ew + ew * 0.5
            top = wy + row * eh + 4
            feet = top + icon * 0.92
            cells.append((col, row, cx, feet))
    occupied = []
    for index in range(min(max(items, 1), cols * rows)):
        # Column-major, top-left, matching Desktop Icons NG keep-arranged.
        column = index // rows
        if column not in occupied:
            occupied.append(column)
    if not occupied:
        occupied = [0]
    return cols, rows, cells, occupied


def desktop_items() -> int:
    count = 1  # Home
    desk = HOME / "Desktop"
    try:
        for path in desk.iterdir():
            if not path.name.startswith("."):
                count += 1
    except OSError:
        pass
    return count


def icon_size_name() -> str:
    try:
        out = subprocess.check_output(
            ["gsettings", "get", "org.gnome.shell.extensions.ding", "icon-size"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).strip().strip("'")
    except (OSError, subprocess.SubprocessError):
        return "small"
    return out if out in {"tiny", "small", "standard", "large"} else "small"


# --- sounds -----------------------------------------------------------------


def _write_wav(path: Path, samples: list[float], rate: int = 22050) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = bytearray()
    for sample in samples:
        value = int(clamp(sample, -1.0, 1.0) * 32000)
        frames += struct.pack("<h", value)
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames)


def _envelope(index: int, total: int, attack: float = 0.02, release: float = 0.08, rate: int = 22050) -> float:
    t = index / rate
    dur = total / rate
    return clamp(min(t / attack, (dur - t) / release, 1.0), 0.0, 1.0)


def ensure_sfx() -> None:
    specs = {
        "munch": 0.22,
        "sip": 0.18,
        "burp": 0.42,
        "giggle": 0.36,
        "yawn": 0.7,
        "tap": 0.09,
        "squeak": 0.14,
    }
    missing = [name for name in specs if not (SFX / f"{name}.wav").is_file()]
    if not missing:
        return
    rate = 22050
    rng = random.Random(7)

    def tone(freq, dur, vol=0.35, slide=0.0):
        n = int(dur * rate)
        out = []
        for i in range(n):
            t = i / rate
            f = freq + slide * t
            out.append(math.sin(math.tau * f * t) * _envelope(i, n, 0.01, 0.05) * vol)
        return out

    sounds = {
        "squeak": tone(740, 0.12, 0.28, slide=-400),
        "yawn": tone(300, 0.65, 0.22, slide=-180),
        "burp": tone(210, 0.4, 0.4, slide=-280),
        "tap": tone(180, 0.07, 0.45, slide=-80),
    }
    n = int(0.2 * rate)
    munch = []
    for i in range(n):
        env = _envelope(i, n, 0.005, 0.04)
        burst = 1.0 if (i / rate) % 0.1 < 0.035 else 0.0
        munch.append(rng.uniform(-1, 1) * env * burst * 0.35)
    sounds["munch"] = munch
    n = int(0.16 * rate)
    sip = [rng.uniform(-1, 1) * _envelope(i, n, 0.01, 0.06) * 0.18 for i in range(n)]
    sounds["sip"] = sip
    giggle = []
    for hop, freq in enumerate((520, 640, 760)):
        bit = tone(freq, 0.1, 0.22, slide=80)
        giggle.extend(bit)
    sounds["giggle"] = giggle
    for name, samples in sounds.items():
        _write_wav(SFX / f"{name}.wav", samples)


_player: subprocess.Popen | None = None


def play_sound(name: str, enabled: bool) -> None:
    global _player
    if not enabled or not name:
        return
    path = SFX / f"{name}.wav"
    if not path.is_file():
        return
    if _player is not None and _player.poll() is None:
        if name not in {"burp", "tap"}:
            return
        try:
            _player.terminate()
        except OSError:
            pass
    try:
        _player = subprocess.Popen(
            ["paplay", "--volume=26000", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        log(f"sound {exc}")


# --- self test --------------------------------------------------------------


def _width(image: np.ndarray) -> int:
    box = opaque_box(image)
    if box is None:
        return 0
    return box[2] - box[0]


def self_test() -> int:
    front = render({"yaw": 0.0, "phase": 0.2, "growth": 0.48, "fat": 0.18, "blink": 0.0}, "honey", 220, 280, 180)
    side = render({"yaw": math.pi / 2, "phase": 1.2, "growth": 0.48, "fat": 0.18}, "honey", 220, 280, 180)
    back = render({"yaw": math.pi, "phase": 0.4, "growth": 0.48, "fat": 0.18}, "honey", 220, 280, 180)
    wave = render({"yaw": 0.2, "wave": 1.0, "phase": 0.8, "growth": 0.48, "mouth": 0.2}, "honey", 220, 280, 180)
    if front[0, 0, 3] != 0 or front[-1, -1, 3] != 0:
        print("background is not clear", file=sys.stderr)
        return 1
    box = opaque_box(front)
    if box is None:
        print("front render is empty", file=sys.stderr)
        return 1
    height = box[3] - box[1]
    # About 3 cm. The shadow sits under the feet and is not part of him.
    if not 150 <= height <= 210:
        print(f"height {height}px is not about 3 cm at 180 px", file=sys.stderr)
        return 1
    front_m = front[..., 3] > 90
    side_m = side[..., 3] > 90
    differ = float(np.mean(front_m != side_m))
    if differ < 0.015:
        print(f"front and side silhouettes match ({differ:.3f})", file=sys.stderr)
        return 1
    if eye_whites(front) < eye_whites(back) + 15:
        print(f"eyes show on the back ({eye_whites(back)}) as much as the front ({eye_whites(front)})", file=sys.stderr)
        return 1
    # A wave lifts the paw on the right of the picture.
    idle_right = front[:, front.shape[1] // 2 :, 3] > 90
    wave_right = wave[:, wave.shape[1] // 2 :, 3] > 90
    idle_top = int(np.where(idle_right.any(axis=1))[0][0])
    wave_top = int(np.where(wave_right.any(axis=1))[0][0])
    if wave_top > idle_top - 6:
        print(f"wave paw did not rise ({wave_top} vs {idle_top})", file=sys.stderr)
        return 1
    # Feet: opaque pixels near the bottom, on both sides.
    foot_band = front[box[3] - 16 : box[3], :, 3] > 90
    cols = np.where(foot_band.any(axis=0))[0]
    if len(cols) < 4 or cols[0] > front.shape[1] * 0.45 or cols[-1] < front.shape[1] * 0.55:
        print("feet are not apart", file=sys.stderr)
        return 1

    pet = Pet(default_cfg(), now=100.0)
    pet.x = 400.0
    pet.y = 1200.0
    before = pet.fat
    pet.hunger = 0.9
    pet.feed_snack()
    if pet.fat <= before or pet.state != "eat":
        print("overfeeding did not fill him out", file=sys.stderr)
        return 1
    if hasattr(pet, "feed_water"):
        print("water feed is still on the fish", file=sys.stderr)
        return 1
    grid = folder_grid((55, 32, 1900, 1200), "small", 20)
    for _ in range(40):
        pet.update(0.05, 200.0, grid, 1240.0, 70.0, 2000.0)
        if pet.cue:
            pet.cue = None
    if pet.state == "drink":
        print("he started drinking with no water", file=sys.stderr)
        return 1
    flakes = spawn_flakes(80.0)
    if len(flakes) < 20:
        print("food flakes did not burst", file=sys.stderr)
        return 1
    fallen = step_flakes([{**flake} for flake in flakes], 0.45)
    if len(fallen) < 10 or not any(flake["y"] != flakes[index]["y"] for index, flake in enumerate(fallen)):
        print("food flakes did not move", file=sys.stderr)
        return 1
    sleepy = Pet({**default_cfg(), "energy": 0.5, "last": time.time() - 3600, "_existed": True})
    if sleepy.hunger >= 0.76:
        print("an hour offline did not make him hungry", file=sys.stderr)
        return 1
    cols, rows, _cells, occupied = grid
    if cols < 8 or rows < 4 or not occupied:
        print(f"folder grid failed cols={cols} rows={rows}", file=sys.stderr)
        return 1
    # Every 15 degrees is a different camera, not a flipped card.
    hashes = []
    for degree in range(0, 360, 15):
        image = render({"yaw": math.radians(degree), "phase": 0.5, "growth": 0.48}, "honey", 120, 150, 100)
        hashes.append(int(image[..., 3].sum()) + int(image[::3, ::3, 0].sum()))
    if len(set(hashes)) < 20:
        print("turnaround angles collapsed", file=sys.stderr)
        return 1
    print(
        f"puff self-test ok · {height}px · {cm_for(0.48):.2f} cm · "
        f"{cols}x{rows} grid · {len(set(hashes))} angles"
    )
    return 0


def render_sheet(path: Path) -> None:
    import cairo

    cell_w, cell_h = 160, 190
    poses = []
    for degree in range(0, 360, 15):
        poses.append({"yaw": math.radians(degree), "phase": 0.6, "growth": 0.48, "fat": 0.18})
    poses.extend(
        [
            {"yaw": math.pi / 2, "phase": 0.0, "growth": 0.48},
            {"yaw": math.pi / 2, "phase": 1.2, "growth": 0.48},
            {"yaw": math.pi / 2, "phase": 2.4, "growth": 0.48},
            {"yaw": math.pi / 2, "phase": 3.6, "growth": 0.48},
            {"yaw": 0.2, "wave": 1, "phase": 0.4, "mouth": 0.2, "growth": 0.48},
            {"yaw": 0.2, "wave": 1, "phase": 1.3, "mouth": 0.2, "growth": 0.48},
            {"yaw": 0.45, "climb": 1, "phase": 0.8, "growth": 0.48},
            {"yaw": 0.4, "sit": 1, "growth": 0.48},
            {"yaw": 1.0, "lay": 1, "blink": 1, "growth": 0.48},
            {"yaw": 0.2, "stretch": 1, "mouth": 0.85, "blink": 0.6, "growth": 0.48},
            {"yaw": 0.05, "reach": 1, "growth": 0.48},
            {"yaw": 0.3, "fat": 0.92, "sit": 0.4, "growth": 0.75},
        ]
    )
    cols = 12
    rows = math.ceil(len(poses) / cols)
    sheet = np.zeros((rows * cell_h, cols * cell_w, 4), np.uint8)
    for index, pose in enumerate(poses):
        image = render(pose, "mint", cell_w, cell_h, 148)
        row, col = divmod(index, cols)
        sheet[row * cell_h : (row + 1) * cell_h, col * cell_w : (col + 1) * cell_w] = image
    h, w, _ = sheet.shape
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    buf = surface.get_data()
    arr = np.ndarray((h, w, 4), np.uint8, buf)
    rgb = sheet[..., :3].astype(np.float32)
    alpha = sheet[..., 3:4].astype(np.float32) / 255.0
    prem = rgb * alpha
    arr[..., 0] = prem[..., 2]
    arr[..., 1] = prem[..., 1]
    arr[..., 2] = prem[..., 0]
    arr[..., 3] = sheet[..., 3]
    surface.mark_dirty()
    path.parent.mkdir(parents=True, exist_ok=True)
    surface.write_to_png(str(path))
    print(path)


# --- desktop integration ----------------------------------------------------


def desktop_file() -> str:
    return "\n".join(
        (
            "[Desktop Entry]",
            "Version=1.0",
            "Type=Application",
            "Name=Goldfish",
            "Comment=A photoreal goldfish swimming the desktop.",
            f"Exec={Path(__file__).resolve()}",
            f"Icon={ICON}",
            "Terminal=false",
            "Categories=Utility;GTK;",
            "StartupNotify=false",
            "StartupWMClass=goldfish",
            "Keywords=puff;goldfish;fish;pet;desktop;",
            "",
        )
    )


def _trust(path: Path) -> None:
    if not path.exists():
        return
    try:
        subprocess.run(
            ["gio", "set", str(path), "metadata::trusted", "true"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def install_launchers() -> None:
    body = desktop_file()
    APP_DESKTOP.parent.mkdir(parents=True, exist_ok=True)
    if not APP_DESKTOP.exists() or APP_DESKTOP.read_text(encoding="utf-8") != body:
        APP_DESKTOP.write_text(body, encoding="utf-8")
    kept = {}
    if AUTOSTART.exists():
        for line in AUTOSTART.read_text(encoding="utf-8").splitlines():
            if line.startswith("X-GNOME-Autostart-") or line.startswith("Hidden="):
                kept[line.split("=", 1)[0]] = line
    else:
        kept["X-GNOME-Autostart-enabled"] = "X-GNOME-Autostart-enabled=false"
        kept["X-GNOME-Autostart-Delay"] = "X-GNOME-Autostart-Delay=5"
        kept["Hidden"] = "Hidden=true"
    lines = [line for line in body.splitlines() if line]
    for key in ("X-GNOME-Autostart-enabled", "X-GNOME-Autostart-Delay", "Hidden"):
        lines.append(kept.get(key, ""))
    text = "\n".join(line for line in lines if line) + "\n"
    AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
    if not AUTOSTART.exists() or AUTOSTART.read_text(encoding="utf-8") != text:
        AUTOSTART.write_text(text, encoding="utf-8")


def autostart_enabled() -> bool:
    if not AUTOSTART.exists():
        return False
    hidden = False
    enabled = False
    for line in AUTOSTART.read_text(encoding="utf-8").splitlines():
        if line.startswith("Hidden="):
            hidden = line.split("=", 1)[1].strip().lower() == "true"
        elif line.startswith("X-GNOME-Autostart-enabled="):
            enabled = line.split("=", 1)[1].strip().lower() == "true"
    return enabled and not hidden


def set_autostart(enabled: bool) -> None:
    install_launchers()
    lines = AUTOSTART.read_text(encoding="utf-8").splitlines()
    found_e = found_h = False
    out = []
    for line in lines:
        if line.startswith("X-GNOME-Autostart-enabled="):
            out.append("X-GNOME-Autostart-enabled=" + ("true" if enabled else "false"))
            found_e = True
        elif line.startswith("Hidden="):
            out.append("Hidden=" + ("false" if enabled else "true"))
            found_h = True
        else:
            out.append(line)
    if not found_e:
        out.append("X-GNOME-Autostart-enabled=" + ("true" if enabled else "false"))
    if not found_h:
        out.append("Hidden=" + ("false" if enabled else "true"))
    AUTOSTART.write_text("\n".join(out) + "\n", encoding="utf-8")


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        text = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
    except OSError:
        return False
    name = Path(__file__).name
    folded = text.strip()
    return name in folded or folded.endswith("/puff") or folded.endswith("/goldfish")


def claim_pid(replace: bool) -> None:
    PID.parent.mkdir(parents=True, exist_ok=True)
    if PID.exists():
        text = PID.read_text(encoding="utf-8").strip()
        if text.isdigit():
            old = int(text)
            if old != os.getpid() and pid_alive(old):
                if not replace:
                    os.kill(old, signal.SIGUSR1)
                    print("Puff is already on the desktop.", file=sys.stderr)
                    raise SystemExit(0)
                os.kill(old, signal.SIGTERM)
                for _ in range(40):
                    if not pid_alive(old):
                        break
                    time.sleep(0.05)
    PID.write_text(str(os.getpid()) + "\n", encoding="utf-8")


def release_pid() -> None:
    try:
        if PID.exists() and PID.read_text(encoding="utf-8").strip() == str(os.getpid()):
            PID.unlink()
    except OSError:
        pass


def write_icon() -> None:
    import cairo
    from PIL import Image

    quarter = FISH_DIR / "yaw_045" / "wag_00.png"
    photo = np.asarray(Image.open(quarter).convert("RGBA")) if quarter.exists() else fish_frames()[2]
    thumb = Image.fromarray(photo, "RGBA")
    thumb.thumbnail((228, 228), Image.Resampling.LANCZOS)
    sprite = np.asarray(thumb)
    image = np.zeros((256, 256, 4), np.uint8)
    top = (256 - sprite.shape[0]) // 2
    left = (256 - sprite.shape[1]) // 2
    image[top : top + sprite.shape[0], left : left + sprite.shape[1]] = sprite
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 256, 256)
    buf = surface.get_data()
    arr = np.ndarray((256, 256, 4), np.uint8, buf)
    alpha = image[..., 3:4].astype(np.float32) / 255.0
    prem = image[..., :3].astype(np.float32) * alpha
    arr[..., 0] = prem[..., 2]
    arr[..., 1] = prem[..., 1]
    arr[..., 2] = prem[..., 0]
    arr[..., 3] = image[..., 3]
    surface.mark_dirty()
    ICON.parent.mkdir(parents=True, exist_ok=True)
    surface.write_to_png(str(ICON))


def _on_usr1(_signum, _frame) -> None:
    global _wave_flag
    _wave_flag = True


def _on_term(_signum, _frame) -> None:
    # The frame tick is busier than idle, so the tick itself must see this flag.
    global _quit_flag
    _quit_flag = True
    try:
        from gi.repository import GLib

        GLib.idle_add(_quit_from_signal)
    except Exception:
        raise SystemExit(0)


def _quit_from_signal() -> bool:
    global _APP
    if _APP is not None:
        _APP.quit()
    return False


_APP = None
_quit_flag = False


def main(argv: list[str]) -> int:
    global _APP
    if "--self-test" in argv:
        return self_test()
    if "--render-sheet" in argv:
        target = Path("/tmp/puff-sheet.png")
        if "--render-sheet" in argv:
            index = argv.index("--render-sheet")
            if index + 1 < len(argv) and not argv[index + 1].startswith("-"):
                target = Path(argv[index + 1])
        render_sheet(target)
        return 0

    prefer_xwayland()
    import cairo
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GLib, Gtk

    ensure_sfx()
    write_icon()
    install_launchers()
    claim_pid("--replace" in argv)
    signal.signal(signal.SIGUSR1, _on_usr1)
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
    GLib.set_prgname(PRGNAME)
    try:
        Gdk.set_program_class(PRGNAME)
    except Exception:
        pass

    class Stage(Gtk.Window):
        def __init__(self, app: "App") -> None:
            super().__init__(title="Goldfish")
            self.app = app
            self.set_name(PRGNAME)
            self.set_app_paintable(True)
            self.set_decorated(False)
            self.set_resizable(False)
            self.set_skip_taskbar_hint(True)
            self.set_skip_pager_hint(True)
            self.set_accept_focus(True)
            self.set_focus_on_map(False)
            self.set_type_hint(Gdk.WindowTypeHint.NORMAL)
            screen = self.get_screen()
            visual = screen.get_rgba_visual() if screen is not None else None
            if visual is not None:
                self.set_visual(visual)
            self.connect("draw", self._draw)
            self.connect("button-press-event", self._press)
            self.connect("realize", self._clear_frame)
            self.connect("map-event", self._mapped)
            self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            provider = Gtk.CssProvider()
            provider.load_from_data(
                b"""
                window, window.background {
                  background-color: transparent;
                  background-image: none;
                  border: none;
                  box-shadow: none;
                }
                """
            )
            Gtk.StyleContext.add_provider_for_screen(
                self.get_screen(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            self._hit_key = None

        def _clear_frame(self, *_args) -> bool:
            """No compositor shadow and no opaque plate. A shadow reads as a square around the fish."""
            gdkwin = self.get_window()
            if gdkwin is None:
                return False
            try:
                gdkwin.set_opaque_region(cairo.Region())
            except Exception:
                pass
            try:
                gdkwin.set_shadow_width(0, 0, 0, 0)
            except Exception:
                pass
            return False

        def _mapped(self, *_args) -> bool:
            self._clear_frame()
            return False

        def _draw(self, _widget, cr: cairo.Context) -> bool:
            width = max(1, self.get_allocated_width())
            height = max(1, self.get_allocated_height())
            cr.set_operator(cairo.OPERATOR_SOURCE)
            cr.set_source_rgba(0, 0, 0, 0)
            cr.paint()
            cr.set_operator(cairo.OPERATOR_OVER)
            self.app.paint(cr, width, height)
            return True

        def _press(self, _widget, event) -> bool:
            if int(event.button) == 3:
                self.app.popup(event)
                return True
            if int(event.button) == 1:
                self.app.click(float(event.x), float(event.y))
                return True
            return False

        def set_body_hit(self, image: np.ndarray, buttons: list[tuple]) -> None:
            height, width, _ = image.shape
            key = (
                width,
                height,
                len(buttons),
                int(image[..., 3].sum()) // 5000,
                int(getattr(self, "_hit_ox", 0)),
                int(getattr(self, "_hit_oy", 0)),
            )
            if key == self._hit_key:
                return
            gdkwin = self.get_window()
            if gdkwin is None:
                return
            self._hit_key = key
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
            buf = surface.get_data()
            arr = np.ndarray((height, width, 4), np.uint8, buf)
            mask = image[..., 3] > 28
            arr[..., 3] = np.where(mask, 255, 0)
            arr[..., 0] = arr[..., 3]
            cr = cairo.Context(surface)
            cr.set_source_rgba(0, 0, 0, 1)
            for left, top, right, bottom in buttons:
                cr.rectangle(left, top, right - left, bottom - top)
                cr.fill()
            surface.mark_dirty()
            region = Gdk.cairo_region_create_from_surface(surface)
            try:
                gdkwin.input_shape_combine_region(region, int(getattr(self, "_hit_ox", 0)), int(getattr(self, "_hit_oy", 0)))
            except Exception as exc:
                log(f"hit {exc}")

    class App:
        def __init__(self) -> None:
            self.cfg = load_cfg()
            self.pet = Pet(self.cfg)
            self.pet.fur = self.cfg["fur"]
            self.pet.swim_free = True
            self.window = Stage(self)
            self.buf: np.ndarray | None = None
            self.surface = None
            self.buttons: list[tuple] = []
            self.menu_until = 0.0
            self.flakes: list[dict] = []
            self._last = time.monotonic()
            self._save_at = self._last + 8.0
            self._hit_at = 0.0
            self._grid_at = 0.0
            self._grid = folder_grid((0, 0, 1600, 1000), "small", 8)
            self._size = (0, 0)
            self._origin = None
            self.px_per_cm = self._px_per_cm()
            self._frame(0.0)
            self.window.show_all()
            self.window._clear_frame()
            self.apply_layer()
            try:
                self.window.stick()
            except Exception:
                pass
            log(
                f"started goldfish {FISH_CM:.1f}cm window {self._size[0]}px "
                f"tummy={tummy_word(self.pet.fat)}"
            )

        def _px_per_cm(self) -> float:
            display = Gdk.Display.get_default()
            if display is None or display.get_n_monitors() < 1:
                return 60.0
            monitor = display.get_primary_monitor() or display.get_monitor(0)
            if monitor is None:
                return 60.0
            geo = monitor.get_geometry()
            mm = monitor.get_width_mm()
            if not mm:
                return 60.0
            return float(geo.width) / (mm / 10.0)

        def screen(self):
            display = Gdk.Display.get_default()
            monitor = display.get_primary_monitor() or display.get_monitor(0)
            geo = monitor.get_geometry()
            work = monitor.get_workarea()
            return (
                int(geo.x),
                int(geo.y),
                int(geo.width),
                int(geo.height),
                int(work.x),
                int(work.y),
                int(work.width),
                int(work.height),
            )

        def apply_layer(self) -> None:
            above = bool(self.cfg["above"])
            self.window.set_keep_above(above)
            if not above:
                sink_above_icons(self.window)

        def _frame_size(self) -> tuple[int, int]:
            if self.pet.swim_free:
                side = canvas_side(self.px_per_cm)
                return side, side
            height_px = self.px_per_cm * cm_for(self.pet.growth)
            width = int(height_px * (1.72 + self.pet.fat * 0.28))
            height = int(height_px + 108)
            return max(180, width), max(220, height)

        def _frame(self, dt: float) -> None:
            global _wave_flag
            sx, sy, sw, sh, wx, wy, ww, wh = self.screen()
            width, height = self._frame_size()
            if self.pet.swim_free:
                x_min = wx + 16
                x_max = wx + ww - 16
                y_min = wy + 16
                y_max = wy + wh - 16
                floor_y = y_max
            else:
                floor_y = sy + sh - 6
                x_min = wx + 24
                x_max = wx + ww - 24
                y_min = None
                y_max = None
            now = time.monotonic()
            if _wave_flag:
                _wave_flag = False
                self.menu_until = 0.0
                self.pet.attention = False
                if self.pet.swim_free:
                    self.pet.dart = 1.3
                    self.pet.cue = "squeak"
                else:
                    self.pet.enter("wave", "squeak")
            if now - self._grid_at > 5.0:
                self._grid = folder_grid((wx, wy, ww, wh), icon_size_name(), desktop_items())
                self._grid_at = now
            grid = self._grid
            self.pet.attention = now < self.menu_until
            self.pet.update(dt, now, grid, floor_y, x_min, x_max, y_min, y_max)
            if self.pet.cue:
                play_sound(self.pet.cue, bool(self.cfg["sound"]))
                if self.pet.cue:
                    log(f"{self.pet.state} · {self.pet.cue}")
                self.pet.cue = None
            if self.pet.swim_free:
                image = compose_fish(
                    self.pet.view,
                    self.pet.pitch,
                    self.pet.phase,
                    self.pet.shown_scale,
                    self.px_per_cm,
                )
                height, width = image.shape[:2]
            else:
                target = self.px_per_cm * 3.0
                pose = self.pet.pose()
                image = render(pose, self.pet.fur, width, height, target)
            self.buf = image
            stride = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_ARGB32, width)
            prem = np.zeros((height, stride // 4, 4), np.uint8)
            if self.pet.swim_free:
                # compose_fish already premultiplied the photograph.
                prem[:, :width, 0] = image[..., 2]
                prem[:, :width, 1] = image[..., 1]
                prem[:, :width, 2] = image[..., 0]
                prem[:, :width, 3] = image[..., 3]
            else:
                rgb = image[..., :3].astype(np.float32)
                alpha = image[..., 3:4].astype(np.float32) / 255.0
                coloured = rgb * alpha
                prem[:, :width, 0] = coloured[..., 2]
                prem[:, :width, 1] = coloured[..., 1]
                prem[:, :width, 2] = coloured[..., 0]
                prem[:, :width, 3] = image[..., 3]
            self.surface = cairo.ImageSurface.create_for_data(
                prem, cairo.FORMAT_ARGB32, width, height, stride
            )
            self._prem = prem
            if self._size != (width, height):
                self.window.set_size_request(width, height)
                self.window.resize(width, height)
                self._size = (width, height)
                self.window._hit_key = None
                self.window._clear_frame()
                self.window.resize(width, height)
            if self.pet.swim_free:
                half_w = width / 2.0
                half_h = height / 2.0
                self.pet.x = clamp(self.pet.x, wx + half_w, max(wx + half_w, wx + ww - half_w))
                self.pet.y = clamp(self.pet.y, wy + half_h, max(wy + half_h, wy + wh - half_h))
                origin = (int(round(self.pet.x - half_w)), int(round(self.pet.y - half_h)))
            else:
                origin = (int(self.pet.x - width / 2), int(self.pet.y - height + SHADOW))
            if origin != self._origin:
                self.window.move(*origin)
                self._origin = origin
            if now - self._hit_at > 0.2 or self.pet.attention:
                alloc_w = max(width, int(self.window.get_allocated_width()))
                alloc_h = max(height, int(self.window.get_allocated_height()))
                self.window._hit_ox = max(0, (alloc_w - width) // 2)
                self.window._hit_oy = max(0, (alloc_h - height) // 2)
                self.window.set_body_hit(image, [])
                self._hit_at = now
            if now > self._save_at:
                self._store()
                self._save_at = now + 12.0

        def paint(self, cr: cairo.Context, width: int, height: int) -> None:
            if self.surface is not None:
                sw = self.surface.get_width()
                sh = self.surface.get_height()
                cr.set_source_surface(self.surface, (width - sw) * 0.5, (height - sh) * 0.5)
                cr.paint()
            paint_flakes(cr, self.flakes, width, height)

        def click(self, x: float, y: float) -> None:
            del x, y
            self.pet.on_pet()
            log(self.pet.state)

        def feed(self) -> None:
            self.pet.feed_snack()
            length = display_length(self.pet.shown_scale, self.px_per_cm)
            self.flakes = spawn_flakes(max(56.0, length * 0.62))
            log("food")

        def popup(self, event) -> None:
            menu = Gtk.Menu()
            food = Gtk.MenuItem(label="Food")
            food.connect("activate", lambda *_args: self.feed())
            menu.append(food)
            quit_item = Gtk.MenuItem(label="Quit")
            quit_item.connect("activate", lambda *_args: self.quit())
            menu.append(quit_item)
            menu.show_all()
            menu.popup_at_pointer(event)

        def _store(self) -> None:
            self.cfg.update(self.pet.snapshot(bool(self.cfg["sound"]), bool(self.cfg["above"])))
            save_cfg(self.cfg)

        def tick(self) -> bool:
            if _quit_flag:
                self.quit()
                return False
            now = time.monotonic()
            dt = clamp(now - self._last, 0.0, 0.08)
            self._last = now
            if dt > 0.0:
                self.flakes = step_flakes(self.flakes, dt)
                try:
                    self._frame(dt)
                except Exception as exc:
                    log(f"frame {exc}")
            self.window.queue_draw()
            return True

        def quit(self) -> None:
            log("quit")
            self._store()
            release_pid()
            self.window.destroy()
            Gtk.main_quit()

    _APP = App()
    GLib.timeout_add(16, _APP.tick)
    try:
        Gtk.main()
    finally:
        release_pid()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except SystemExit:
        raise
    except Exception:
        import traceback

        log(traceback.format_exc())
        raise
