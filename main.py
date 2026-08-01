#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mach3-Kontrol
=============
Mach3'e baglanan, guvenlik onlemli, mobil uyumlu web kontrol arayuzu.

Kullanim:
    python main.py                   # web arayuzunu baslat
    python main.py --simulasyon      # Mach3 olmadan dene
    python main.py --sure kesim.tap  # yalnizca kesim suresi tahmini
    python main.py --help

Ayrintilar icin README.md.
"""

from mach3_kontrol.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
