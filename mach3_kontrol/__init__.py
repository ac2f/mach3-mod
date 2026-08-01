#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mach3_kontrol
=============
Mach3'e baglanan, guvenlik onlemli, web tabanli (mobil uyumlu) CNC kontrol
arayuzu.

Ana parcalar:
    ayarlar        - yapilandirma (JSON)
    kodlar         - Mach3 OEM DRO/LED/dugme kod haritasi
    baglanti       - COM (gercek Mach3) + simulasyon arka uclari
    guvenlik       - limit/adim/derinlik denetimi, kalp atisi, kontrol kilidi
    klavye_kilidi  - Windows dusuk seviye klavye hooku + odak bekcisi
    gcode          - blok ayristirma, sira gosterimi, bloktan baslatma
    sure           - kesim suresi tahmini
    denetleyici    - hepsini birlestiren beyin
    webapp         - bagimliliksiz web sunucusu + arayuz
"""

__version__ = "1.0.0"

from . import (ayarlar, kodlar, baglanti, guvenlik, klavye_kilidi,  # noqa: F401
               gcode, sure, denetleyici, webapp, cli)

__all__ = ["ayarlar", "kodlar", "baglanti", "guvenlik", "klavye_kilidi",
           "gcode", "sure", "denetleyici", "webapp", "cli", "__version__"]
