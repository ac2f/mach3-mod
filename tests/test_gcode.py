#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""G-Code ayristirma, blok sirasi ve bloktan baslatma testleri."""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mach3_kontrol import gcode as GC          # noqa: E402

ORNEK = [
    "(deneme)", "G21", "G90", "G17", "T3 M6", "S12000 M3", "G0 Z20",
    "G0 X10 Y10", "G1 Z-5 F600", "G1 X50 Y10 F1800", "G1 X50 Y40",
    "G1 X10 Y40", "G1 X10 Y10", "G0 Z20",
    "G0 X100 Y100", "G1 Z-5 F600", "G1 X140 Y100 F1800", "G1 X140 Y130",
    "G0 Z20",
    "G0 X0 Y0", "M5", "M30",
]


def test_blok_ayristirma():
    p = GC.Program(satirlar=ORNEK)
    assert len(p.bloklar) == 2, p.bloklar
    # bastaki hazirlik satirlari header'a, "eve don" footer'a gitmeli
    assert "S12000 M3" in p.header
    assert any("M30" in s for s in p.footer)
    assert GC.blok_bas_xy(p.bloklar[0]) == (10.0, 10.0)
    assert GC.blok_bas_xy(p.bloklar[1]) == (100.0, 100.0)


def test_ozet_ve_satir_araligi():
    p = GC.Program(satirlar=ORNEK)
    o = p.ozet()
    assert [b["no"] for b in o] == [1, 2]
    assert o[0]["bbox"] == [10.0, 10.0, 50.0, 40.0]
    assert o[0]["kesim_uz"] > 130          # 40+30+40+30 = 140 (+ dalis yok)
    bas, son = p.satir_araligi(0)
    assert ORNEK[bas - 1].startswith("G0 X10")
    assert son > bas


def test_modal_durum():
    p = GC.Program(satirlar=ORNEK)
    d = p.modal_baslangic(1)
    assert d["birim"] == "G21" and d["mutlak"] is True
    assert d["spindle"] == "M3" and d["devir"] == 12000.0
    assert d["takim"] == 3.0
    assert d["feed"] == 1800.0             # ilk bloktaki modal F tasinir


def test_bloktan_baslat_guvenli():
    """Ikinci bloktan baslatma: ONCE guvenli Z, sonra modal geri kurma,
    sonra havada hedefe gidis. Kesim satirlari aynen korunur."""
    p = GC.Program(satirlar=ORNEK)
    yeni = p.bloktan_baslat_uret(1, guvenli_z=25.0)

    # ilk hareket mutlaka guvenli Z'ye cikis olmali
    ilk_hareket = next(s for s in yeni
                       if s.startswith(("G0 ", "G1 ")))
    assert ilk_hareket == "G0 Z25.0", ilk_hareket
    assert "G90" in yeni
    assert "T3 M6" in yeni and "S12000.0" in yeni and "M3" in yeni
    # hedef bloga XY ile havada gidilir, oraya varmadan dalis yok
    xy_idx = next(i for i, s in enumerate(yeni) if s.startswith("G0 X100"))
    dalis_idx = next(i for i, s in enumerate(yeni) if "Z-5" in s)
    assert xy_idx < dalis_idx
    # birinci blogun satirlari uretilen programda OLMAMALI
    assert not any("X50 Y10" in s for s in yeni)
    # program sonu korunur
    assert any("M30" in s for s in yeni)


def test_bloktan_baslat_g91_reddedilir():
    p = GC.Program(satirlar=["G91"] + ORNEK)
    assert p.artimli
    try:
        p.bloktan_baslat_uret(0)
        raise AssertionError("G91 dosyada bloktan baslatma engellenmeliydi")
    except ValueError:
        pass


def test_yay_uzunlugu():
    # yaricapi 10 olan tam yarim daire: uzunluk = pi * r
    w = {"I": 10.0, "J": 0.0}
    uz = GC.yay_uzunlugu(-10.0, 0.0, 10.0, 0.0, 0.0, w, 3.0)
    assert abs(uz - math.pi * 10) < 1e-6, uz


def test_yay_kapsami_uc_noktalarla_yetinmez():
    """Bir cemberin X/Y kelimeleri yalnizca iki uc noktayi verir; kapsam ve
    bbox yayin GERCEK sinirlarini icermelidir (yumusak limit denetimi buna
    dayaniyor)."""
    cember = ["G21", "G90", "G0 X90 Y200", "G1 Z-5 F600",
              "G3 X110 Y200 I10 J0 F1000", "G3 X90 Y200 I-10 J0", "G0 Z20"]
    k = GC.kapsam(cember)
    assert abs(k["Y"][0] - 190.0) < 0.2, k     # cember alt noktasi
    assert abs(k["Y"][1] - 210.0) < 0.2, k     # cember ust noktasi
    p = GC.Program(satirlar=cember)
    bbox = p.ozet()[0]["bbox"]
    assert abs(bbox[1] - 190.0) < 0.2 and abs(bbox[3] - 210.0) < 0.2, bbox


def test_kapsam_artimli_ve_inch():
    k = GC.kapsam(["G21", "G90", "G0 X10 Y10", "G91", "G1 X20 Y-5"])
    assert k["X"] == [10.0, 30.0] and k["Y"] == [5.0, 10.0], k


def test_hareket_listesi_inch_cevirir():
    satirlar = ["G20", "G90", "G0 X0 Y0", "G1 X1 Y0 F10"]
    h = GC.hareket_listesi(satirlar)
    kesim = [k for k in h if k["tip"] == "kesim"]
    assert abs(kesim[0]["uzunluk"] - 25.4) < 1e-6
    assert abs(kesim[0]["feed"] - 254.0) < 1e-6


if __name__ == "__main__":
    for ad, fn in sorted(list(globals().items())):
        if ad.startswith("test_"):
            fn()
            print("ok", ad)
    print("G-Code testleri gecti.")
