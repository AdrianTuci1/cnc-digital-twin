#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Planul celulei CNC, văzut de sus, pe un grid de 0,5 m.

Fluxul materialului, în ordine: banda 1 alimentează dinspre vest, brațul 1 preia piesa și o
încarcă în mașină; după ce mașina termină, tot brațul 1 o descarcă și o așază pe banda 2, care
merge într-un singur sens spre vest. Brațul 2 stă la marginea sudică a benzii 2, iar cutiile sunt
de partea cealaltă a benzii, pe latura nordică: brațul ridică piesa de pe bandă și o lasă în
cutia potrivită fără să se întoarcă, fiindcă ambele ținte cad în același sector, spre nord.
Camera de inspecție a calității este separată de brațe și privește banda 2 înainte de brațul 2,
ca verdictul să fie gata când piesa ajunge la el; fiecare braț are în plus camera lui, pe flanșă,
pentru prindere.

Coordonatele de mai jos sunt singura sursă de adevăr pentru așezarea în spațiu: când se scrie
scena MJCF, `simulation/mujoco/scene/layout.py` preia aceleași valori. Gridul are coloane A-L
spre est (0,5 m fiecare) și rânduri 1-8 spre nord, cu originea în colțul sud-vest al celulei.
Conversia la lumea MuJoCo este identitatea: planul (x, y) devine lumea (x, y), cu +Z în sus.

Rulare:
    python docs/diagrams/cell_layout.py            # scrie SVG-ul și, cu rsvg-convert, PNG-ul
    python docs/diagrams/cell_layout.py --table    # tabelul de coordonate, în Markdown
"""

from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

CELL_W, CELL_H = 6.0, 4.0  # gabaritul celulei, în metri
GRID = 0.5
SCALE = 190  # pixeli pe metru

MARGIN_L, MARGIN_T, MARGIN_B = 88, 96, 88
LEGEND_W = 430
FONT = "Helvetica, Arial, sans-serif"

COLORS = {
    "belt": ("#d3dbe5", "#5b6b7f"),
    "arm": ("#f4b071", "#a85f13"),
    "cnc": ("#a6cbea", "#2f6ba8"),
    "accepted": ("#aed7a8", "#3f7a37"),
    "trash": ("#ecaaaa", "#9b3b3b"),
    "camera": ("#cdb0ea", "#6a3f9b"),
    "camera_wrist": ("#e6dbf6", "#8a63b8"),
}

# direction = +1: banda merge spre est; -1: spre vest. Fiecare bandă are un singur sens.
BELT_IN = dict(name="infeed_conveyor", kind="belt", x=0.4, y=3.1, w=2.4, h=0.4, direction=1,
               caption="Bandă 1: alimentare", caption_at=(1.6, 3.86))
BELT_OUT = dict(name="outfeed_conveyor", kind="belt", x=0.8, y=1.8, w=3.8, h=0.4, direction=-1,
                caption="Bandă 2: evacuare, un singur sens", caption_at=(4.0, 1.68))
CNC = dict(name="cnc_3axis", kind="cnc", x=3.4, y=2.4, w=1.4, h=1.0)
# Cutiile stau lipite de latura nordică a benzii 2, adică dincolo de bandă față de brațul 2.
BIN_OK = dict(name="bin_accepted", kind="accepted", x=2.1, y=2.2, w=0.4, h=0.4)
BIN_NG = dict(name="bin_trash", kind="trash", x=2.7, y=2.2, w=0.4, h=0.4)

ARMS = [
    dict(key="arm1", name="arm1_cnc", x=2.95, y=2.65, reach=0.855,
         label="Braț 1 (Panda #1)", label_at=(0.55, 2.65), anchor="start",
         note="încarcă și descarcă CNC, așază piesa pe banda 2",
         wrist_camera=(3.17, 2.42)),
    dict(key="arm2", name="arm2_sort", x=2.7, y=1.55, reach=0.855,
         label="Braț 2 (Panda #2)", label_at=(0.55, 1.55), anchor="start",
         note="ridică piesa de pe banda 2, o lasă în cutiile de dincolo de bandă",
         wrist_camera=(2.45, 1.72)),
]

# Camera de inspecție a calității: singura cameră fixă, separată de brațe. Stă pe banda 2,
# între locul unde brațul 1 așază piesa (x=3,4) și locul de unde o ridică brațul 2 (x=2,7),
# deci verdictul e gata înainte ca piesa să ajungă la brațul 2.
CAMERAS = [
    dict(name="camera_quality", x=3.2, y=1.55, zone=0.35, size=0.10,
         label="Cameră inspecție calitate", label_at=(3.62, 1.4), anchor="start"),
]

# Ținte de prindere: punctele pe care brațele trebuie să le atingă.
TARGETS = [
    dict(name="belt1_pick", key="arm1", x=2.7, y=3.3, label="preluare de pe banda 1"),
    dict(name="cnc_chuck", key="arm1", x=3.5, y=2.95, label="încărcare/descărcare CNC"),
    dict(name="belt2_drop", key="arm1", x=3.4, y=2.0, label="așezare pe banda 2"),
    dict(name="belt2_pick", key="arm2", x=2.7, y=2.0, label="preluare de pe banda 2"),
    dict(name="bin_ok", key="arm2", x=2.4, y=2.32, label="piesă bună în cutie"),
    dict(name="bin_ng", key="arm2", x=3.0, y=2.32, label="rebut în cutie"),
]

# Fluxul materialului, ca polilinie punctată: bandă 1 -> braț 1 -> CNC -> braț 1 -> bandă 2
# (trece prin dreptul camerei de inspecție) -> braț 2 -> cutia potrivită.
FLOW = [(0.55, 3.3), (2.7, 3.3), (3.5, 2.95), (3.4, 2.0), (2.7, 2.0)]
FLOW_BRANCHES = [
    [(2.7, 2.0), (2.4, 2.32)],
    [(2.7, 2.0), (3.0, 2.32)],
]

PLAN_W = CELL_W * SCALE
PLAN_H = CELL_H * SCALE
WIDTH = MARGIN_L + PLAN_W + LEGEND_W
HEIGHT = MARGIN_T + PLAN_H + MARGIN_B


def px(x: float) -> float:
    return MARGIN_L + x * SCALE


def py(y: float) -> float:
    return MARGIN_T + (CELL_H - y) * SCALE


def grid_ref(x: float, y: float) -> str:
    """Referința celulei de grid, de exemplu F6: coloane A-L, rânduri 1-8."""
    column = "ABCDEFGHIJKL"[min(int(x / GRID), 11)]
    row = min(int(y / GRID), 7) + 1
    return column + str(row)


def grid_span(x: float, y: float, w: float, h: float) -> str:
    first = grid_ref(x, y)
    last = grid_ref(x + w - 1e-6, y + h - 1e-6)
    return first if first == last else first + "-" + last


def line(x1, y1, x2, y2, stroke="#333", width=1.0, dash=None, marker=False, opacity=1.0,
         marker_id="arrow"):
    parts = [
        '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f"' % (x1, y1, x2, y2),
        'stroke="%s" stroke-width="%.1f"' % (stroke, width),
    ]
    if dash:
        parts.append('stroke-dasharray="%s"' % dash)
    if marker:
        parts.append('marker-end="url(#%s)"' % marker_id)
    if opacity < 1.0:
        parts.append('opacity="%.2f"' % opacity)
    return " ".join(parts) + "/>"


def text(x, y, value, size=13, anchor="start", color="#1b1b1b", weight="normal", opacity=1.0):
    return (
        '<text x="%.1f" y="%.1f" font-size="%d" text-anchor="%s" fill="%s" '
        'font-weight="%s" opacity="%.2f">%s</text>'
        % (x, y, size, anchor, color, weight, opacity, value)
    )


def rect(x, y, w, h, fill, stroke, rx=3, opacity=1.0, dash=None):
    parts = [
        '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="%d"' % (x, y, w, h, rx),
        'fill="%s" stroke="%s" stroke-width="1.6"' % (fill, stroke),
    ]
    if dash:
        parts.append('stroke-dasharray="%s"' % dash)
    if opacity < 1.0:
        parts.append('opacity="%.2f"' % opacity)
    return " ".join(parts) + "/>"


def polyline(points, stroke="#e07b39", width=1.6, dash="5 4", marker=False):
    coords = " ".join("%.1f,%.1f" % (px(x), py(y)) for x, y in points)
    attrs = 'fill="none" stroke="%s" stroke-width="%.1f"' % (stroke, width)
    if dash:
        attrs += ' stroke-dasharray="%s"' % dash
    if marker:
        attrs += ' marker-end="url(#arrow)"'
    return '<polyline points="%s" %s/>' % (coords, attrs)


def camera_glyph(x, y, size, fill, stroke):
    """Diamantul care marchează o cameră; ``size`` este jumătatea diagonalei, în metri."""
    d = size * SCALE
    return ('<path d="M %.1f %.1f L %.1f %.1f L %.1f %.1f L %.1f %.1f Z" fill="%s" '
            'stroke="%s" stroke-width="1.4"/>'
            % (px(x), py(y) - d, px(x) + d, py(y), px(x), py(y) + d, px(x) - d, py(y),
               fill, stroke))


def build_svg() -> str:
    svg = []
    svg.append(
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 %d %d" font-family="%s">' % (WIDTH, HEIGHT, WIDTH, HEIGHT, FONT)
    )
    svg.append(
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto-start-reverse">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#e07b39"/></marker>'
        '<marker id="arrow-gray" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
        'markerHeight="6" orient="auto-start-reverse">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#555"/></marker></defs>'
    )
    svg.append(rect(0, 0, WIDTH, HEIGHT, "#ffffff", "#ffffff", rx=0))
    svg.append(text(MARGIN_L, 40, "Celula CNC, plan de sus: geamăn MuJoCo", 19, "start", "#111", "bold"))
    svg.append(text(MARGIN_L, 64, "grid de 0,5 m; originea (0,0) în colțul sud-vest; "
                                  "+X spre est, +Y spre nord; gabarit 6,0 × 4,0 m", 13, "start", "#444"))
    svg.append(rect(px(0), py(CELL_H), PLAN_W, PLAN_H, "#fafbfc", "#333", rx=0))

    # Gridul: linii subțiri la 0,5 m, mai pronunțate la 1 m.
    steps_x = int(round(CELL_W / GRID))
    steps_y = int(round(CELL_H / GRID))
    for i in range(1, steps_x):
        x = i * GRID
        svg.append(line(px(x), py(0), px(x), py(CELL_H), "#dde2e8" if i % 2 else "#c3cad3", 1.0))
    for j in range(1, steps_y):
        y = j * GRID
        svg.append(line(px(0), py(y), px(CELL_W), py(y), "#dde2e8" if j % 2 else "#c3cad3", 1.0))
    for i in range(steps_x):
        cx = px(i * GRID + GRID / 2)
        svg.append(text(cx, py(CELL_H) - 8, "ABCDEFGHIJKL"[i], 12, "middle", "#8a939d"))
        svg.append(text(cx, py(0) + 18, "ABCDEFGHIJKL"[i], 12, "middle", "#8a939d"))
    for j in range(steps_y):
        cy = py(j * GRID + GRID / 2) + 4
        svg.append(text(px(0) - 10, cy, str(j + 1), 12, "end", "#8a939d"))
        svg.append(text(px(CELL_W) + 10, cy, str(j + 1), 12, "start", "#8a939d"))

    # Componentele.
    for item in (BELT_IN, BELT_OUT, CNC, BIN_OK, BIN_NG):
        fill, stroke = COLORS[item["kind"]]
        x, y, w, h = item["x"], item["y"], item["w"], item["h"]
        svg.append(rect(px(x), py(y + h), w * SCALE, h * SCALE, fill, stroke))
        if item["kind"] == "belt":
            # Săgeata stă în jumătatea de nord a benzii, ca fluxul materialului (care trece pe
            # mijlocul benzii) să rămână o linie separată.
            cy = py(y + h * 0.75)
            x1, x2 = px(x) + 16, px(x + w) - 16
            # Săgeata arată sensul de mers, care e unic pe fiecare bandă.
            if item["direction"] > 0:
                svg.append(line(x1, cy, x2, cy, stroke, 2.0, marker=True, marker_id="arrow-gray"))
            else:
                svg.append(line(x2, cy, x1, cy, stroke, 2.0, marker=True, marker_id="arrow-gray"))
            cx, cy_label = item["caption_at"]
            svg.append(text(px(cx), py(cy_label), item["caption"], 13, "middle", "#31414f"))
    svg.append(text(px(CNC["x"] + CNC["w"] / 2), py(CNC["y"] + CNC["h"] / 2) - 4,
                    "Mașină CNC 3 axe", 14, "middle", "#1d4467", "bold"))
    svg.append(text(px(CNC["x"] + CNC["w"] / 2), py(CNC["y"] + CNC["h"] / 2) + 16,
                    "stare: idle / cutting", 12, "middle", "#315f86"))

    # Brațele: bază, rază de atingere, etichetă cu linie de trimitere și camera de pe flanșă.
    for arm in ARMS:
        fill, stroke = COLORS["arm"]
        r = 0.12 * SCALE
        stage = arm["reach"] * SCALE
        svg.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="none" stroke="%s" '
                   'stroke-width="1.1" stroke-dasharray="4 6" opacity="0.5"/>'
                   % (px(arm["x"]), py(arm["y"]), stage, stroke))
        svg.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="%s" stroke="%s" '
                   'stroke-width="1.6"/>' % (px(arm["x"]), py(arm["y"]), r, fill, stroke))
        lx, ly = arm["label_at"]
        svg.append(text(px(lx), py(ly), arm["label"], 13, arm["anchor"], "#7a4408", "bold"))
        svg.append(text(px(lx), py(ly) + 20, arm["note"], 11, arm["anchor"], "#8a5a1f"))
        # Linia de trimitere pornește la nivelul etichetei, deci trece pe deasupra notei.
        svg.append(line(px(lx + 1.30), py(ly) + 4, px(arm["x"]) - r - 4, py(arm["y"]) + 2,
                        "#a85f13", 1.0, dash="3 3"))
        # Camera proprie a brațului, pe flanșă: prinde piesa, nu judecă finisajul.
        wfill, wstroke = COLORS["camera_wrist"]
        wx, wy = arm["wrist_camera"]
        svg.append(line(px(arm["x"]), py(arm["y"]), px(wx), py(wy), wstroke, 1.0, dash="2 2",
                        opacity=0.8))
        svg.append(camera_glyph(wx, wy, 0.07, wfill, wstroke))

    # Reperul de axe, ca orientarea să nu depindă doar de subtitlu.
    svg.append(line(px(0.25), py(0.25), px(0.85), py(0.25), "#555", 1.4, marker=True,
                    marker_id="arrow-gray"))
    svg.append(text(px(0.9), py(0.25) + 4, "X est", 11, "start", "#555"))
    svg.append(line(px(0.25), py(0.25), px(0.25), py(0.85), "#555", 1.4, marker=True,
                    marker_id="arrow-gray"))
    svg.append(text(px(0.25) + 6, py(0.9), "Y nord", 11, "start", "#555"))
    svg.append('<circle cx="%.1f" cy="%.1f" r="3" fill="#555"/>' % (px(0.25), py(0.25)))
    svg.append(text(px(0.25) - 6, py(0.25) + 14, "0,0", 11, "end", "#555"))

    # Camera de inspecție a calității: singura cameră fixă, separată de brațe.
    for cam in CAMERAS:
        fill, stroke = COLORS["camera"]
        d = cam["size"] * SCALE
        svg.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="none" stroke="%s" '
                   'stroke-width="1.2" stroke-dasharray="2 4" opacity="0.8"/>'
                   % (px(cam["x"]), py(cam["y"]), cam["zone"] * SCALE, stroke))
        svg.append(camera_glyph(cam["x"], cam["y"], cam["size"], fill, stroke))
        lx, ly = cam["label_at"]
        svg.append(line(px(lx) - 6, py(ly) + 4, px(cam["x"]) + d + 2, py(cam["y"]), "#6a3f9b", 1.0,
                        dash="3 3"))
        svg.append(text(px(lx), py(ly), cam["label"], 12, cam["anchor"], "#59308a"))

    # Țintele de prindere.
    for target in TARGETS:
        svg.append('<circle cx="%.1f" cy="%.1f" r="4.5" fill="#1b1b1b" stroke="#ffffff" '
                   'stroke-width="1.4"/>' % (px(target["x"]), py(target["y"])))

    # Cutiile stau dincolo de bandă, pe latura opusă brațului 2: etichetele intră deasupra.
    svg.append(text(px(2.3), py(2.72), "Piese bune (Accepted)", 12, "middle", "#2f5c2a", "bold"))
    svg.append(text(px(2.9), py(2.72), "Rebuts (Trash)", 12, "middle", "#8a2f2f", "bold"))

    # Fluxul materialului.
    svg.append(polyline(FLOW, marker=True))
    for branch in FLOW_BRANCHES:
        svg.append(polyline(branch, marker=True))

    # Cota de gabarit.
    svg.append(line(px(0), py(0) + 46, px(CELL_W), py(0) + 46, "#666", 1.0))
    svg.append(text(px(CELL_W / 2), py(0) + 62, "6,0 m", 12, "middle", "#555"))
    svg.append(line(px(0) - 30, py(0), px(0) - 30, py(CELL_H), "#666", 1.0))
    svg.append(text(px(0) - 38, py(CELL_H / 2), "4,0 m", 12, "middle", "#555"))

    svg.append(build_legend())
    svg.append("</svg>")
    return "\n".join(svg)


def build_legend() -> str:
    x0 = MARGIN_L + PLAN_W + 40
    y = MARGIN_T
    out = [text(x0, y, "Legendă", 15, "start", "#111", "bold")]
    y += 26

    def swatch(fill, stroke, label, note=""):
        nonlocal y
        out.append(rect(x0, y - 11, 20, 14, fill, stroke))
        out.append(text(x0 + 30, y, label, 12.5, "start", "#222"))
        y += 20
        if note:
            out.append(text(x0 + 30, y - 2, note, 11, "start", "#777"))
            y += 14

    swatch(*COLORS["belt"], "Bandă transportoare", "săgeata din interior: sensul de mers")
    swatch(*COLORS["arm"], "Braț robot (Franka Panda)", "cercul punctat: rază de atingere 0,855 m")
    swatch(*COLORS["cnc"], "Mașină CNC, 3 axe", "stare, fără așchiere efectivă")
    swatch(*COLORS["accepted"], "Cutie piese bune", "dincolo de banda 2, pe latura opusă brațului")
    swatch(*COLORS["trash"], "Cutie rebut", "același rând de cutii, lângă cutia de piese bune")
    swatch(*COLORS["camera"], "Cameră de inspecție a calității",
           "separată de brațe; verdictul decide ruta piesei")
    swatch(*COLORS["camera_wrist"], "Cameră pe braț (pe flanșă)",
           "fiecare braț are una, ca să poată ridica piesa")

    y += 6
    out.append('<circle cx="%.1f" cy="%.1f" r="4.5" fill="#1b1b1b" stroke="#fff" '
               'stroke-width="1.4"/>' % (x0 + 10, y - 4))
    out.append(text(x0 + 30, y, "Țintă de prindere", 12.5, "start", "#222"))
    y += 22
    out.append(line(x0, y - 4, x0 + 20, y - 4, "#e07b39", 1.6, dash="5 4", marker=True))
    out.append(text(x0 + 30, y, "Fluxul materialului", 12.5, "start", "#222"))
    y += 22
    out.append(text(x0 + 30, y, "banda 1 → braț 1 → CNC → braț 1 →", 11.5, "start", "#b06024"))
    y += 16
    out.append(text(x0 + 30, y, "banda 2 (inspecție) → braț 2 → cutie", 11.5, "start", "#b06024"))
    y += 30

    out.append(text(x0, y, "Grid: coloanele A-L spre est, rândurile 1-8", 12, "start", "#444"))
    y += 17
    out.append(text(x0, y, "spre nord, o celulă = 0,5 m. Referința unui", 12, "start", "#444"))
    y += 17
    out.append(text(x0, y, "element se citește ca F6 sau G5-J7.", 12, "start", "#444"))
    y += 30
    out.append(text(x0, y, "Coordonatele sunt identice în lumea MuJoCo:", 12, "start", "#444"))
    y += 17
    out.append(text(x0, y, "(x, y) din plan devine (x, y) în scenă, +Z în sus.", 12, "start", "#444"))
    y += 30
    out.append(text(x0, y, "Verificat: fiecare țintă de prindere cade în raza", 12, "start", "#444"))
    y += 17
    out.append(text(x0, y, "de 0,855 m a brațului care o deservește.", 12, "start", "#444"))
    return "".join(out)


def ro(value: str) -> str:
    """Numerele din documentație se scriu cu virgulă zecimală."""
    return value.replace(".", ",")


def coordinate_table() -> str:
    rows = ["| Element | Centru (x, y) | Amprentă (m) | Celule de grid |",
            "| --- | --- | --- | --- |"]
    arms = {arm["key"]: arm for arm in ARMS}
    items = [
        ("Bandă 1 (alimentare)", BELT_IN, "box"),
        ("Braț 1 (Panda #1)", arms["arm1"], "arm"),
        ("Cameră pe brațul 1 (flanșă)", arms["arm1"], "wrist"),
        ("Mașină CNC 3 axe", CNC, "box"),
        ("Bandă 2 (evacuare, un singur sens)", BELT_OUT, "box"),
        ("Braț 2 (Panda #2)", arms["arm2"], "arm"),
        ("Cameră pe brațul 2 (flanșă)", arms["arm2"], "wrist"),
        ("Cutie piese bune (Accepted)", BIN_OK, "box"),
        ("Cutie rebut (Trash)", BIN_NG, "box"),
        ("Cameră de inspecție a calității", CAMERAS[0], "punct"),
    ]
    for label, item, kind in items:
        if kind == "arm":
            cx, cy = item["x"], item["y"]
            span, size = grid_ref(cx, cy), "bază 0,25 Ø"
        elif kind == "wrist":
            cx, cy = item["wrist_camera"]
            span, size = grid_ref(cx, cy), "punct"
        elif kind == "box":
            cx, cy = item["x"] + item["w"] / 2, item["y"] + item["h"] / 2
            span = grid_span(item["x"], item["y"], item["w"], item["h"]).replace("-", "–")
            size = ro("%.1f × %.1f" % (item["w"], item["h"]))
        else:
            cx, cy = item["x"], item["y"]
            span, size = grid_ref(cx, cy), ro("punct, zonă %.2f Ø" % (2 * item["zone"]))
        rows.append("| %s | (%s, %s) | %s | %s |"
                    % (label, ro("%.2f" % cx), ro("%.2f" % cy), size, span))
    return "\n".join(rows)


def reach_table() -> str:
    """Verificarea care contează: fiecare țintă cade în raza brațului care o deservește."""
    rows = ["| Braț | Țintă | Distanță în plan (m) | În raza de 0,855 m |",
            "| --- | --- | --- | --- |"]
    names = {"arm1": "Braț 1", "arm2": "Braț 2"}
    arms = {arm["key"]: arm for arm in ARMS}
    for target in TARGETS:
        arm = arms[target["key"]]
        dist = math.hypot(arm["x"] - target["x"], arm["y"] - target["y"])
        rows.append("| %s | %s | %s | %s |"
                    % (names[target["key"]], target["label"], ro("%.2f" % dist),
                       "da" if dist <= arm["reach"] else "NU"))
    return "\n".join(rows)


def main() -> int:
    here = Path(__file__).resolve().parent
    svg_path = here / "cnc-cell-layout.svg"
    png_path = here / "cnc-cell-layout.png"
    svg_path.write_text(build_svg(), encoding="utf-8")

    if "--table" in sys.argv:
        print(coordinate_table())
        print()
        print(reach_table())
        return 0

    print("scris %s" % svg_path)
    try:
        subprocess.run(
            ["rsvg-convert", "-w", str(WIDTH * 2), "-o", str(png_path), str(svg_path)],
            check=True,
        )
        print("scris %s" % png_path)
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        print("rsvg-convert nu a putut randa PNG-ul (%s); SVG-ul rămâne folosibil" % error)
    print()
    print(coordinate_table())
    print()
    print(reach_table())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
