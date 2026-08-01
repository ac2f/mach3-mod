#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Denetleyici uctan uca testleri (simulasyon arka ucu ile)."""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mach3_kontrol import ayarlar as AY, denetleyici as DEN  # noqa: E402
from mach3_kontrol import guvenlik as GUV                    # noqa: E402

ORNEK = [
    "(deneme)", "G21", "G90", "G0 Z20",
    "G0 X10 Y10", "G1 Z-5 F600", "G1 X60 Y10 F1200", "G1 X60 Y60",
    "G1 X10 Y60", "G1 X10 Y10", "G0 Z20",
    "G0 X100 Y100", "G1 Z-5 F600", "G1 X150 Y100 F1200", "G0 Z20",
    "M5", "M30",
]


def _kur():
    dizin = tempfile.mkdtemp()
    a = AY.Ayarlar(os.path.join(dizin, "ayarlar.json"))
    a.guncelle({
        "baglanti": {"tur": "simulasyon", "yoklama_hz": 30},
        "guvenlik": {
            "yumusak_limit": {"X": {"min": 0.0, "max": 500.0},
                              "Y": {"min": 0.0, "max": 500.0},
                              "Z": {"min": -100.0, "max": 100.0}},
            "limit_payi": 0.0, "max_jog_adim": 100.0,
            "max_dalis_derinlik": -50.0, "guvenli_z": 20.0},
    })
    d = DEN.Denetleyici(a)
    d.baglan()
    time.sleep(0.2)
    return d, dizin


def _bekle(d, sn=3.0):
    """Simulasyon hareketini bitirene kadar bekle."""
    son = time.time() + sn
    while time.time() < son:
        time.sleep(0.05)
        if not d.baglanti.hareket_ediyor():
            time.sleep(0.1)
            if not d.baglanti.hareket_ediyor():
                return True
    return False


def test_jog_adim_hareket_ettirir():
    d, _ = _kur()
    try:
        d.jog_adim("X", 25.0, 3000.0, istemci="test")
        assert _bekle(d)
        assert abs(d._konum["X"] - 25.0) < 0.01, d._konum
        d.jog_adim("X", -10.0, 3000.0, istemci="test")   # "sola 10 mm git"
        assert _bekle(d)
        assert abs(d._konum["X"] - 15.0) < 0.01, d._konum
    finally:
        d.kapat()


def test_limit_disi_jog_reddedilir():
    d, _ = _kur()
    try:
        try:
            d.jog_adim("X", -5.0, 1000.0, istemci="test")   # X0 alt limitte
            raise AssertionError("limit disi jog gecmemeliydi")
        except GUV.GuvenlikHatasi:
            pass
        assert abs(d._konum["X"]) < 0.001                   # hic hareket yok
    finally:
        d.kapat()


def test_sifirlama_ofsetli():
    """'Su anki konumun 5 mm sagi yeni X sifiri olsun' -> DRO -5 gosterir,
    5 mm saga gidince tam sifira gelinir."""
    d, _ = _kur()
    try:
        d.jog_adim("X", 40.0, 3000.0, istemci="test")
        assert _bekle(d)
        d.sifirla("X", 5.0, istemci="test")
        time.sleep(0.15)
        assert abs(d._konum["X"] + 5.0) < 0.01, d._konum
        d.jog_adim("X", 5.0, 3000.0, istemci="test")
        assert _bekle(d)
        assert abs(d._konum["X"]) < 0.01, d._konum
        # makine konumu degismedi, yalnizca is sifiri kaydi
        assert abs(d._makine["X"] - 45.0) < 0.01, d._makine
    finally:
        d.kapat()


def test_sifirlama_z_ve_y():
    d, _ = _kur()
    try:
        d.jog_adim("Y", 30.0, 3000.0, istemci="test")
        assert _bekle(d)
        d.sifirla("Y", -2.0, istemci="test")     # 2 mm gerisi sifir
        time.sleep(0.15)
        assert abs(d._konum["Y"] - 2.0) < 0.01, d._konum
        d.sifirla("Z", 0.2, istemci="test")      # 0.2 mm ustu sifir (kagit payi)
        time.sleep(0.15)
        assert abs(d._konum["Z"] + 0.2) < 0.01, d._konum
    finally:
        d.kapat()


def test_kontrol_kilidi_ikinci_istemciyi_engeller():
    d, _ = _kur()
    try:
        d.jog_adim("X", 5.0, 1000.0, istemci="telefon")
        try:
            d.jog_adim("X", 5.0, 1000.0, istemci="masaustu")
            raise AssertionError("ikinci istemci hareket ettirememeli")
        except GUV.GuvenlikHatasi as e:
            assert "Kontrol" in str(e)
    finally:
        d.kapat()


def test_program_yukle_ve_bloktan_baslat():
    d, dizin = _kur()
    try:
        yol = os.path.join(dizin, "deneme.tap")
        with open(yol, "w", encoding="utf-8") as f:
            f.write("\n".join(ORNEK))
        bilgi = d.program_yukle(yol)
        assert bilgi["bloklar"][0]["no"] == 1
        assert len(bilgi["bloklar"]) == 2
        assert bilgi["sure"]["toplam_sn"] > 0

        d.blok_sec(1)
        assert d.secili_blok == 1
        denetim = d.program_denetle()
        assert denetim["gecti"], denetim["hatalar"]

        # onaysiz baslatma reddedilir
        try:
            d.program_baslat(onay=False, istemci="test")
            raise AssertionError("onaysiz baslatma gecmemeliydi")
        except GUV.GuvenlikHatasi:
            pass

        r = d.program_baslat(onay=True, istemci="test")
        assert os.path.isfile(r["dosya"])
        with open(r["dosya"], encoding="utf-8") as f:
            uretilen = f.read()
        assert "G0 Z20.0" in uretilen           # once guvenli yukseklik
        assert "X60" not in uretilen            # 1. blok calistirilmaz
        assert d.calisiyor
    finally:
        d.kapat()


def test_acil_durdur_jogu_keser():
    d, _ = _kur()
    try:
        d.jog_basla("Y", 1, 2000.0, istemci="test")
        time.sleep(0.3)
        assert d.jog_bilgi["aktif"]
        d.acil_durdur("test")
        time.sleep(0.3)
        assert not d.jog_bilgi["aktif"]
        assert not d.baglanti.hareket_ediyor()
    finally:
        d.kapat()


def test_kalp_atisi_kesilirse_jog_durur():
    d, _ = _kur()
    try:
        d.ayarlar["guvenlik"]["jog_kalp_atisi_ms"] = 200
        d.bekci.zaman_asimi = 0.2
        d.jog_basla("Y", 1, 1500.0, istemci="test")
        time.sleep(0.15)
        d.jog_kalp("test")
        assert d.jog_bilgi["aktif"]
        time.sleep(0.6)                          # kalp atisi yok -> dur
        assert not d.jog_bilgi["aktif"]
    finally:
        d.kapat()


def test_mdi_limit_disi_reddedilir():
    d, _ = _kur()
    try:
        d.mdi("G0 X100 Y100", istemci="test")
        try:
            d.mdi("G0 X900", istemci="test")
            raise AssertionError("limit disi MDI gecmemeliydi")
        except GUV.GuvenlikHatasi as e:
            assert e.sebepler
    finally:
        d.kapat()


if __name__ == "__main__":
    for ad, fn in sorted(list(globals().items())):
        if ad.startswith("test_"):
            fn()
            print("ok", ad)
    print("Denetleyici testleri gecti.")
