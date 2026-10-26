#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Planul celulei CNC, văzut de sus, pe un grid de 0,5 m.

Fluxul materialului, în ordine: banda 1 alimentează dinspre vest, brațul 1 preia piesa și o
încarcă în mașină; după ce mașina termină, tot brațul 1 o descarcă și o așază pe banda 2, care
merge într-un singur sens spre vest. Brațul 2 stă la marginea sudică a benzii 2, iar dulapul pe
familii stă la vest de braț, cu lungimea pe direcția nord-sud: trei rafturi suprapuse, fiecare
despărțit în două compartimente de 0,5 m, câte unul pe familie. Brațul ia piesa de pe bandă și o
lasă în compartimentul familiei ei, apropiindu-se înclinat de pe fața estică. Verdictul — familia
și conformitatea — îl dă camera de pe flanșa brațului 2,
care fotografiază piesa ținută. O cameră fixă nu există: ruta piesei o decide brațul. Piesa
neconformă nu are punct de lucru: rămâne pe bandă până la capătul de vest, de unde cade în lada de
rebut.

Coordonatele de mai jos sunt singura sursă de adevăr pentru așezarea în spațiu: când se scrie
scena MJCF, `simulation/mujoco/cncdt_mj/layout.py` preia aceleași valori. Gridul are coloane A-L
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
    "rack": ("#aed7a8", "#3f7a37"),
    "rack_support": ("#e3e6ea", "#8b949e"),
    "trash": ("#ecaaaa", "#9b3b3b"),
    "camera_wrist": ("#e6dbf6", "#8a63b8"),
}

# direction = +1: banda merge spre est; -1: spre vest. Fiecare bandă are un singur sens.
BELT_IN = dict(name="infeed_conveyor", kind="belt", x=0.4, y=3.1, w=2.4, h=0.4, direction=1,
               caption="Bandă 1: alimentare", caption_at=(1.6, 3.86))
BELT_OUT = dict(name="outfeed_conveyor", kind="belt", x=0.8, y=1.8, w=3.8, h=0.4, direction=-1,
                caption="Bandă 2: evacuare, un singur sens", caption_at=(4.0, 1.68))
# Mașina stă la 0,45 m de banda 2, nu la 0,20 m cât dădea prima variantă: brațul 1 lucrează
# între ele, la încărcare și la descărcare. Cota e limita de sus a mutării, fiindcă universalul
# călătorește cu masa: la +0,30 m ar ajunge la 0,849 m de baza brațului 1, adică 6 mm sub raza de
# 0,855 m, iar la +0,35 m ar ieși din ea.
#
# Gabaritul ei este simbolic: 1,0 × 0,8 m, cu colțul sud-vest în același loc, ca universalul să
# rămână la aceeași țintă. Nu e o reprezentare reală, iar capacul rămâne la 1,90 m.
CNC = dict(name="cnc_3axis", kind="cnc", x=3.4, y=2.65, w=1.0, h=0.8)

# Dulapul pe familii stă la vest de brațul 2, cu lungimea pe direcția nord-sud, și are trei rafturi
# suprapuse, fiecare despărțit în două compartimente: șase locuri, câte unul pentru fiecare
# familie. Este forma dulapului real, nu o masă cu intrări separate.
# Lungimea pe nord-sud pune compartimentele la 90° față de banda 2, deci brațul se rotește ~90° de
# la preluare până la așezare, în loc de ~130° cât ar fi cu dulapul paralel cu banda.
#
# Așezarea nu e liberă, ci iese din măsurătoare. Baza brațului 2 este la (2,70, 1,55), raza de
# atingere este 0,855 m până la flanșă, iar scula atârnă 0,103 m sub ea. Un dulap cu rafturile
# aliniate nu se poate încărca de sus: antebrațul brațului 2 trece prin volumul lui. De aceea fața
# deschisă stă la x = 2,22, la 0,48 m de bază, iar piesa la 2 cm în spatele ei, la x = 2,20; scula
# intră dinspre est, înclinată la 50° în compartimentul sudic și la 30° în cel nordic. Cotele de
# mai jos sunt măsurate în scenă (`make mj-check`, `make mj-demo`): la aceste unghiuri, verigile nu
# ating dulapul.
#
# Dulapul are 0,30 m adâncime spre vest și 1,00 m lungime pe nord-sud; capătul nordic stă la 1,79 m,
# la 1 cm de banda 2. Fața estică este deschisă, iar rafturile au 0,31 m și 0,25 m între ele;
# partea de jos, spatele și lateralele sunt închise, ca să se citească drept dulap. Podeaua de jos
# stă la 0,85 m, nivelul benzii 2.
RACK_COLUMNS = 2
RACK_SHELVES = 3

#: Lățimea nominală a unui compartiment, pe lungimea dulapului: o celulă de grid. Pereții
#: subțiri lasă compartimentul util cu ~0,47 m, destul pentru discul de Ø104 mm.
RACK_ENTRY_M = 0.5

#: Adâncimea dulapului, pe direcția de intrare a sculei: 0,30 m. Fața estică, spre braț, este
#: deschisă, iar cei 0,30 m se întind spre vest, în spatele ei.
RACK_DEPTH_M = 0.30

#: Cota fiecărei podele de raft: nivelul benzii, apoi treptele de 0,31 m și 0,25 m. A treia este
#: aproape de limita de atingere, de aceea a rămas la 1,41 m.
RACK_FLOORS = (0.85, 1.16, 1.41)

#: Cota feței de sus a dulapului: ultimul raft plus încă o treaptă.
RACK_TOP_Z = 1.69

#: Colțul sud-vest al dulapului: spatele închis, la vest, și capătul sudic. Dulapul stă la vest
#: de brațul 2, cu fața deschisă spre est, ca brațul să ajungă la ea rotindu-se 90° de la banda 2.
RACK_X = 1.92
RACK_Y = 0.79

#: Fața deschisă a dulapului, spre braț: marginea estică a carcasei.
RACK_FACE_X = RACK_X + RACK_DEPTH_M

#: Cota la care se așază piesa pe fiecare raft, aproape de fața deschisă: acolo ajunge scula fără
#: să intre cu antebrațul în dulap. Piesa stă la 2 cm în spatele feței, pe direcția vest.
RACK_PICK_X = RACK_FACE_X - 0.02

#: Intrările dulapului, în ordinea de umplere: raftul de jos de la sud la nord, apoi cele de
#: deasupra. Coordonatele `x` și `y` sunt colțul sud-vest al compartimentului, ca la celelalte
#: amprente din plan; cota podelei se scrie aici ca să se poată citi din desen. Toate cele
#: șase compartimente au acum o familie.
RACK_ENTRIES = (
    dict(index=1, shelf=1, column=1, family="wheel_100", x=RACK_X, y=RACK_Y, floor=0.85),
    dict(index=2, shelf=1, column=2, family="gusset_72", x=RACK_X, y=RACK_Y + 0.5, floor=0.85),
    dict(index=3, shelf=2, column=1, family="hex_68", x=RACK_X, y=RACK_Y, floor=1.16),
    dict(index=4, shelf=2, column=2, family="link_68", x=RACK_X, y=RACK_Y + 0.5, floor=1.16),
    dict(index=5, shelf=3, column=1, family="pinion_56", x=RACK_X, y=RACK_Y, floor=1.41),
    dict(index=6, shelf=3, column=2, family="plate_84", x=RACK_X, y=RACK_Y + 0.5, floor=1.41),
)

#: Gabaritul întregului dulap, pentru tabelul de coordonate: reuniunea compartimentelor,
#: calculată, ca desenul și scena să nu se poată despărți de lista de mai sus.
RACK = dict(
    name="rack_families", kind="rack",
    levels=RACK_SHELVES,
    columns=RACK_COLUMNS,
    slots=len(RACK_ENTRIES),
    x=min(item["x"] for item in RACK_ENTRIES),
    y=min(item["y"] for item in RACK_ENTRIES),
    w=RACK_DEPTH_M,
    h=max(item["y"] for item in RACK_ENTRIES) + RACK_ENTRY_M - min(
        item["y"] for item in RACK_ENTRIES),
)

# Numele familiilor, în ordinea de umplere a compartimentelor: raftul de jos de la sud la nord,
# apoi cele de deasupra. Sunt aceleași nume ca în `cnc_cell/parts/gear.py`; un test verifică
# potrivirea, ca planul și generatorul să nu se despartă.
RACK_FAMILIES = (
    "wheel_100", "gusset_72", "hex_68", "link_68", "pinion_56", "plate_84",
)


def rack_entry(index: int) -> dict:
    """Intrarea cu numărul dat, în ordinea de umplere (1 = prima)."""
    if not 1 <= index <= len(RACK_ENTRIES):
        raise KeyError(f"intrare inexistentă în dulap: {index}")
    return RACK_ENTRIES[index - 1]


def rack_entry_center(index: int) -> tuple[float, float]:
    """Punctul de așezare al piesei în compartimentul dat, în plan.

    Nu este centrul geometric al compartimentului: piesa stă aproape de fața deschisă, singurul
    loc în care scula ajunge fără ca antebrațul să intre în dulap.
    """
    item = rack_entry(index)
    return (RACK_PICK_X, item["y"] + RACK_ENTRY_M / 2)


def rack_shelf_entries(shelf: int) -> tuple[dict, ...]:
    """Compartimentele unui raft, de la sud la nord."""
    items = tuple(item for item in RACK_ENTRIES if item["shelf"] == shelf)
    if not items:
        raise KeyError(f"raft inexistent în dulap: {shelf}")
    return items


def rack_shelf_span(shelf: int) -> tuple[float, float]:
    """Felia din lungimea dulapului pe care stă raftul dat (``y0``, ``y1``)."""
    items = rack_shelf_entries(shelf)
    return (min(item["y"] for item in items),
            max(item["y"] for item in items) + RACK_ENTRY_M)


def rack_entry_floor(index: int) -> float:
    """Cota podelei unei intrări, din listă."""
    return float(rack_entry(index)["floor"])

# Lada de rebut stă sub capătul de vest al benzii 2: piesa neconformă rămâne pe bandă, trece de
# linia de oprire a brațului 2 și cade de la capătul benzii în ladă. Gura lăzii acoperă capătul
# benzii, fiindcă piesa nu cade vertical: pleacă de pe platformă cu viteza benzii și ajunge la
# 10–15 cm înăuntru. Marginea ei e sub nivelul benzii (0,62 m), ca piesa să nu se sprijine pe
# perete, iar latura de est nu are perete, ca nimic să nu prindă piesa la intrare.
SCRAP_BIN = dict(name="scrap_bin", kind="trash", x=0.28, y=1.80, w=0.62, h=0.40)

ARMS = [
    dict(key="arm1", name="arm1_cnc", x=2.95, y=2.65, reach=0.855,
         label="Braț 1 (Panda #1)", label_at=(0.55, 2.65), anchor="start",
         note="încarcă și descarcă CNC, așază piesa pe banda 2",
         wrist_camera=(3.3352, 2.4360)),
    dict(key="arm2", name="arm2_sort", x=2.7, y=1.55, reach=0.855,
         label="Braț 2 (Panda #2)", label_at=(0.55, 1.55), anchor="start",
         note="ridică piesa de pe bandă și o lasă în locul familiei",
         wrist_camera=(2.4867, 1.8782)),
]

# Nu există nicio cameră fixă în celulă: verdictul — familia piesei și conformitatea ei — îl dă
# camera de pe flanșa brațului 2, care fotografiază piesa ținută în falci, iar ruta piesei o
# decide brațul, nu un post de inspecție deasupra benzii. Camerele de pe flanșe sunt singurele
# marcate în plan, la punctele lor (`ARMS[*]["wrist_camera"]`).

# Ținte de prindere: punctele pe care brațele trebuie să le atingă. Cota din plan este centrul
# piesei, iar brațul ajunge acolo cu flanșa, nu cu vârful: scula atârnă ~0,10 m sub flanșă, iar
# raza de 0,855 m se măsoară până la flanșă. Locurile de lucru stau deci mai aproape de bazele
# brațelor decât pare din rază, iar diferența se vede în tabelul de atingere (`make cell-layout`).
TARGETS = [
    dict(name="belt1_pick", key="arm1", x=2.7, y=3.3, label="preluare de pe banda 1"),
    dict(name="cnc_chuck", key="arm1", x=3.46, y=3.16, label="încărcare/descărcare CNC"),
    dict(name="belt2_drop", key="arm1", x=3.25, y=2.0, label="așezare pe banda 2"),
    dict(name="belt2_pick", key="arm2", x=2.7, y=2.0, label="preluare de pe banda 2"),
    # Fiecare familie are compartimentul ei: piesa se lasă culcată pe podeaua raftului, aproape de
    # fața deschisă, iar compartimentele se umplu în ordinea din listă — raftul de jos de la sud la
    # nord, apoi cele de deasupra. Toate șase au o familie.
    *(
        dict(name=f"rack_{family}", key="arm2", slot=index,
             x=rack_entry_center(index + 1)[0], y=rack_entry_center(index + 1)[1],
             label=(f"raftul {rack_entry(index + 1)['shelf']}, "
                    f"compartimentul {'AB'[rack_entry(index + 1)['column'] - 1]} ({family})"))
        for index, family in enumerate(RACK_FAMILIES)
    ),
]

#: Intrarea fiecărei familii, după numele țintei de prindere.
RACK_TARGETS = {family: f"rack_{family}" for family in RACK_FAMILIES}

#: Câte intrări are raftul: una pe familie, niciuna goală.
RACK_SLOTS = len(RACK_ENTRIES)

# Fluxul materialului, ca polilinie punctată: bandă 1 -> braț 1 -> CNC -> braț 1 -> bandă 2 ->
# braț 2 -> intrarea familiei. Rebutul nu are ramură de braț: rămâne pe bandă și cade de la
# capătul ei în lada de la vest.
FLOW = [(0.55, 3.3), (2.7, 3.3), (3.5, 2.95), (3.4, 2.0), (2.7, 2.0)]
FLOW_BRANCHES = [
    *([(2.7, 2.0), rack_entry_center(index + 1)] for index in range(len(RACK_FAMILIES))),
    [(2.7, 2.0), (0.75, 2.0)],
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
    for item in (BELT_IN, BELT_OUT, CNC, SCRAP_BIN):
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

    # Dulapul, în proiecție de sus: carcasa, cele două compartimente și despărțitorul dintre ele.
    # Rafturile suprapuse nu se văd separat de sus, deci în celule scriem coloana, iar legenda dă
    # raftul și familia fiecărui compartiment.
    case_fill, case_stroke = COLORS["rack_support"]
    shelf_fill, shelf_stroke = COLORS["rack"]
    svg.append(rect(px(RACK["x"]), py(RACK["y"] + RACK["h"]),
                    RACK["w"] * SCALE, RACK["h"] * SCALE, case_fill, case_stroke))
    for column in (1, 2):
        y0 = RACK["y"] + (column - 1) * RACK_ENTRY_M + 0.025
        svg.append(rect(px(RACK["x"] + 0.005), py(y0 + RACK_ENTRY_M - 0.025),
                        (RACK["w"] - 0.01) * SCALE, (RACK_ENTRY_M - 0.05) * SCALE,
                        shelf_fill, shelf_stroke))
        svg.append(text(px(RACK["x"] + RACK["w"] / 2),
                        py(y0 + RACK_ENTRY_M / 2 - 0.025) + 4, "AB"[column - 1],
                        13, "middle", "#2f5c2a", "bold"))

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

    # Camera de inspecție a calității nu mai există: verdictul îl dă camera de pe flanșa brațului 2,
    # marcată mai sus, lângă brațul ei.

    # Țintele de prindere.
    for target in TARGETS:
        svg.append('<circle cx="%.1f" cy="%.1f" r="4.5" fill="#1b1b1b" stroke="#ffffff" '
                   'stroke-width="1.4"/>' % (px(target["x"]), py(target["y"])))

    # Dulapul și lada de rebut. Rafturile se citesc din legendă, fiindcă într-o fâșie de 34 px
    # încape doar litera coloanei.
    svg.append(text(px(RACK["x"] + RACK["w"] + 0.08), py(RACK["y"] + RACK["h"] / 2),
                    "Dulap pe familii: 3 rafturi × 2 compartimente", 12, "start", "#2f5c2a",
                    "bold"))
    svg.append(text(px(SCRAP_BIN["x"] + SCRAP_BIN["w"] / 2), py(SCRAP_BIN["y"] - 0.06),
                    "Ladă rebut", 12, "middle", "#8a2f2f", "bold"))

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
    swatch(*COLORS["rack"], "Dulap pe familii: 3 rafturi × 2 compartimente",
           "podele la 0,85 / 1,16 / 1,41 m; piesa stă în față, pe raft")
    swatch(*COLORS["rack_support"], "Carcasa dulapului",
           "laterale, spate și capac; fața estică rămâne deschisă")
    swatch(*COLORS["trash"], "Ladă rebut", "sub capătul de vest al benzii 2, fără perete la est")
    swatch(*COLORS["camera_wrist"], "Cameră pe braț (pe flanșă)",
           "brațul 2 fotografiază piesa ținută și decide familia și conformitatea")

    y += 6
    out.append(text(x0, y, "Compartimentele dulapului: raftul de jos întâi,", 12.5, "start",
                    "#222"))
    y += 17
    out.append(text(x0, y, "iar pe raft de la sud la nord (A, apoi B).", 12.5, "start",
                    "#222"))
    y += 18
    for index in range(RACK_SLOTS):
        entry = rack_entry(index + 1)
        family = entry["family"] or "gol"
        out.append(text(x0 + 6, y, "%d. raftul %d, compartimentul %s — %s" % (
            entry["index"], entry["shelf"], "AB"[entry["column"] - 1], family),
            11.5, "start", "#3f7a37"))
        y += 15

    y += 8
    out.append('<circle cx="%.1f" cy="%.1f" r="4.5" fill="#1b1b1b" stroke="#fff" '
               'stroke-width="1.4"/>' % (x0 + 10, y - 4))
    out.append(text(x0 + 30, y, "Țintă de prindere", 12.5, "start", "#222"))
    y += 22
    out.append(line(x0, y - 4, x0 + 20, y - 4, "#e07b39", 1.6, dash="5 4", marker=True))
    out.append(text(x0 + 30, y, "Fluxul materialului", 12.5, "start", "#222"))
    y += 22
    out.append(text(x0 + 30, y, "banda 1 → braț 1 → CNC → braț 1 →", 11.5, "start", "#b06024"))
    y += 16
    out.append(text(x0 + 30, y, "banda 2 → braț 2 → locul familiei din raft", 11.5, "start",
                    "#b06024"))
    y += 16
    out.append(text(x0 + 30, y, "rebut: banda 2 → capătul de vest → ladă", 11.5, "start",
                    "#b06024"))
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
        ("Dulap pe familii (%d rafturi × %d compartimente)" % (
            RACK["levels"], RACK["columns"]), RACK, "box"),
        *[("Raftul %d, compartimentul %s (%s) — podea %s m" % (
            item["shelf"], "AB"[item["column"] - 1], item["family"] or "gol",
            ro("%.2f" % item["floor"])), item, "entry")
          for item in RACK_ENTRIES],
        ("Ladă rebut", SCRAP_BIN, "box"),
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
        elif kind == "entry":
            x0, y0 = float(item["x"]), float(item["y"])
            cx, cy = rack_entry_center(int(item["index"]))
            span = grid_span(x0, y0, RACK_DEPTH_M, RACK_ENTRY_M).replace("-", "–")
            size = ro("%.2f × %.2f" % (RACK_DEPTH_M, RACK_ENTRY_M))
        else:
            cx, cy = item["x"], item["y"]
            span, size = grid_ref(cx, cy), ro("punct, zonă %.2f Ø" % (2 * item["zone"]))
        rows.append("| %s | (%s, %s) | %s | %s |"
                    % (label, ro("%.2f" % cx), ro("%.2f" % cy), size, span))
    return "\n".join(rows)


def reach_table() -> str:
    """Distanța orizontală până la fiecare țintă, ca primă verificare a așezării.

    Tabelul nu ține cont de cotă ori de lungimea sculei: verificarea în spațiu, cu unghiul de
    apropiere al fiecărei ținte, o face `make mj-check` pe scena compilată.
    """
    rows = ["| Braț | Țintă | Distanță orizontală (m) | În raza de 0,855 m |",
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
