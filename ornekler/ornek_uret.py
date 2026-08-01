#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Deneme dosyasi uretici
======================
Mach3 olmadan (simulasyon modunda) arayuzu denemek icin kucuk bir kesim
programi uretir: bir tabaka uzerinde 6 dikdortgen + 2 daire.

    python ornekler/ornek_uret.py
    python main.py --simulasyon
    (arayuzde Program sekmesi -> Dosya sec -> ornek_kesim.tap)
"""

import math
import os

GUVENLI_Z = 20.0
KESIM_Z = -12.0
FEED = 2400.0
DALIS = 800.0


def dikdortgen(x, y, en, boy):
    s = [f"G0 X{x:.3f} Y{y:.3f}", f"G1 Z{KESIM_Z:.3f} F{DALIS:.0f}"]
    s.append(f"G1 X{x + en:.3f} Y{y:.3f} F{FEED:.0f}")
    s.append(f"G1 X{x + en:.3f} Y{y + boy:.3f}")
    s.append(f"G1 X{x:.3f} Y{y + boy:.3f}")
    s.append(f"G1 X{x:.3f} Y{y:.3f}")
    s.append(f"G0 Z{GUVENLI_Z:.3f}")
    return s


def daire(cx, cy, r):
    return [
        f"G0 X{cx - r:.3f} Y{cy:.3f}",
        f"G1 Z{KESIM_Z:.3f} F{DALIS:.0f}",
        f"G3 X{cx + r:.3f} Y{cy:.3f} I{r:.3f} J0 F{FEED:.0f}",
        f"G3 X{cx - r:.3f} Y{cy:.3f} I{-r:.3f} J0",
        f"G0 Z{GUVENLI_Z:.3f}",
    ]


def uret(yol):
    satir = ["(Mach3-Kontrol ornek kesim programi)", "G21", "G90", "G17",
             "G40", "T1 M6", "S18000 M3", f"G0 Z{GUVENLI_Z:.3f}"]
    for i in range(6):
        x = 50 + (i % 3) * 320
        y = 50 + (i // 3) * 260
        satir += dikdortgen(x, y, 280, 220)
    satir += daire(250, 620, 90)
    satir += daire(700, 620, 60)
    satir += [f"G0 Z{GUVENLI_Z:.3f}", "M5", "M9", "G0 X0 Y0", "M30"]
    with open(yol, "w", encoding="utf-8") as f:
        f.write("\n".join(satir) + "\n")
    return yol


if __name__ == "__main__":
    hedef = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "ornek_kesim.tap")
    uret(hedef)
    print(f"Uretildi: {hedef}")
    try:
        from mach3_kontrol import gcode as GC, sure as SURE
        p = GC.Program(hedef)
        t = SURE.program_tahmini(p)
        print(f"{len(p.bloklar)} kesim blogu · tahmini sure "
              f"{t['toplam_metin']}")
    except ImportError:
        pass
