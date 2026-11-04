#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mach3_kontrol
=============
Mach3'e baglanan, guvenlik onlemli, web tabanli (mobil uyumlu) CNC kontrol
arayuzu.

Ana parcalar:
    ayarlar        - yapilandirma (JSON)
    kodlar         - Mach3 OEM DRO/LED/dugme kod haritasi (Mach3'un kendi
                     DROs/Buttons/Leds listelerinden turetilir)
    baglanti       - COM (gercek Mach3): oncelikli kuyruk, toplu okuma
    sanal_makine   - Mach3'u fiziksel olarak taklit eden simulasyon arka ucu
    jog            - Mach3 jog modeli: Cont/Step, Slow Jog %, Jog ON/OFF
    guvenlik       - limit/adim/derinlik denetimi, kalp atisi, kontrol kilidi
    klavye_kilidi  - Windows dusuk seviye klavye hooku + odak bekcisi
    gcode          - blok ayristirma, sira gosterimi, bloktan baslatma
    sure           - kesim suresi tahmini
    kalibrasyon    - kumpasla adim/bosluk olcumu ve Mach3'e girilecek degerler
    denetleyici    - hepsini birlestiren beyin
    webapp         - bagimliliksiz web sunucusu + arayuz
"""

__version__ = "2.0.0"

from . import (ayarlar, kodlar, baglanti, sanal_makine, guvenlik,  # noqa: F401
               klavye_kilidi, gcode, sure, kalibrasyon, jog, denetleyici,
               webapp, cli)

__all__ = ["ayarlar", "kodlar", "baglanti", "sanal_makine", "guvenlik",
           "klavye_kilidi", "gcode", "sure", "kalibrasyon", "jog",
           "denetleyici", "webapp", "cli", "__version__"]
