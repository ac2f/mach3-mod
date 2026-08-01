#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kesim suresi tahmini testleri."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mach3_kontrol import gcode as GC, sure as SURE      # noqa: E402

MAKINE = {"hizli_hiz": {"X": 6000.0, "Y": 6000.0, "Z": 3000.0},
          "ivme": {"X": 500.0, "Y": 500.0, "Z": 300.0}}


def test_duz_hat_beklenen_sureye_yakin():
    """1000 mm'lik hat, F1000 -> yaklasik 60 sn (+ ivmelenme payi)."""
    satirlar = ["G21", "G90", "G0 X0 Y0", "G1 X1000 F1000"]
    t = SURE.tahmin(satirlar, MAKINE, {"kose_kaybi": 0.0})
    assert 59.5 < t["toplam_sn"] < 61.0, t
    assert abs(t["kesim_mm"] - 1000.0) < 1e-6


def test_kose_kaybi_sureyi_uzatir():
    satirlar = ["G21", "G90", "G0 X0 Y0"] + \
        [f"G1 X{i} F1000" for i in range(1, 60)]
    hizli = SURE.tahmin(satirlar, MAKINE, {"kose_kaybi": 0.0})["toplam_sn"]
    yavas = SURE.tahmin(satirlar, MAKINE, {"kose_kaybi": 1.0})["toplam_sn"]
    assert yavas > hizli * 1.2, (hizli, yavas)


def test_fro_kesim_suresini_olcekler():
    satirlar = ["G21", "G90", "G0 X0 Y0", "G1 X600 F1200"]
    tam = SURE.tahmin(satirlar, MAKINE, {"kose_kaybi": 0.0}, fro=100)
    yarim = SURE.tahmin(satirlar, MAKINE, {"kose_kaybi": 0.0}, fro=50)
    assert abs(yarim["kesim_sn"] - 2 * tam["kesim_sn"]) < 0.3


def test_bekleme_ve_takim_eklenir():
    satirlar = ["G21", "G90", "G0 X0 Y0", "G4 P3", "T2 M6", "M3 S12000"]
    t = SURE.tahmin(satirlar, MAKINE,
                    {"takim_degisim_sn": 20.0, "spindle_bekleme_sn": 5.0})
    assert abs(t["ek_sn"] - 28.0) < 0.01, t          # 3 + 20 + 5


def test_kalan_sure_azalir():
    satirlar = ["G21", "G90", "G0 Z20"]
    for i in range(4):
        satirlar += [f"G0 X{i * 100} Y0", "G1 Z-5 F600",
                     f"G1 X{i * 100 + 50} Y0 F1000", "G0 Z20"]
    p = GC.Program(satirlar=satirlar)
    b = SURE.blok_tahminleri(p, MAKINE, {})
    kalanlar = [x["kalan_sn"] for x in b]
    assert kalanlar == sorted(kalanlar, reverse=True), kalanlar
    assert abs(kalanlar[0] - sum(x["toplam_sn"] for x in b)) < 0.2


def test_sure_katsayisi_kalibre_eder():
    satirlar = ["G21", "G90", "G0 X0 Y0", "G1 X600 F1200"]
    t1 = SURE.tahmin(satirlar, MAKINE, {"sure_katsayi": 1.0})
    t2 = SURE.tahmin(satirlar, MAKINE, {"sure_katsayi": 1.5})
    assert abs(t2["toplam_sn"] - 1.5 * t1["toplam_sn"]) < 0.2


def test_sure_metni():
    assert SURE.sure_metni(45) == "45 sn"
    assert SURE.sure_metni(125) == "2 dk 5 sn"
    assert SURE.sure_metni(3725) == "1 sa 2 dk 5 sn"


if __name__ == "__main__":
    for ad, fn in sorted(list(globals().items())):
        if ad.startswith("test_"):
            fn()
            print("ok", ad)
    print("Sure testleri gecti.")
