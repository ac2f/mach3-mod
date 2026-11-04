#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tezgah tabani (dalma korumasi) testleri
=======================================
Buradaki testlerin varlik sebebi tek bir cumle:

    "Farkli boyda bicak takiyorum, farkli yuksekliklerde sifirliyorum;
     tezgaha dalinmasin."

`max_dalis_derinlik` IS koordinatindadir ve her sifirlamada baska bir
fiziksel yukseklige denk gelir - bu yuzden tezgahi KORUMAZ. Koruma
`makine_z_taban` ile yapilir; asagidaki testler bunun is sifirindan
BAGIMSIZ oldugunu kanitlar.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mach3_kontrol import gcode as GC, guvenlik as GUV   # noqa: E402


class _Ayar(dict):
    def __getitem__(self, k):
        return dict.__getitem__(self, k)


def _ayar(taban=-95.0, zorunlu=True, derinlik=-60.0):
    return _Ayar(guvenlik={
        "yumusak_limit": {"X": {"min": 0.0, "max": 1000.0},
                          "Y": {"min": 0.0, "max": 1000.0},
                          "Z": {"min": -200.0, "max": 50.0}},
        "yumusak_limit_zorunlu": True,
        "limit_payi": 0.0,
        "max_jog_adim": 500.0,
        "max_jog_feed": 6000.0,
        "guvenli_z": 20.0,
        "guvenli_z_zorunlu": True,
        "max_dalis_derinlik": derinlik,
        "makine_z_taban": taban,
        "makine_z_taban_zorunlu": zorunlu,
        "limitler_dogrulandi": True,
    })


# ---------------------------------------------------------------------
# Temel davranis
# ---------------------------------------------------------------------
def test_taban_altina_jog_reddedilir():
    g = GUV.Guvenlik(_ayar(taban=-95.0))
    makine = {"X": 0.0, "Y": 0.0, "Z": -90.0}
    is_k = {"X": 0.0, "Y": 0.0, "Z": 0.0}
    try:
        g.jog_dogrula("Z", -10.0, 300.0, makine, is_k)   # -100 < -95
        raise AssertionError("taban altina inis reddedilmeliydi")
    except GUV.GuvenlikHatasi as e:
        assert any("tezgah tabani" in s for s in e.sebepler), e.sebepler


def test_taban_ustunde_jog_gecer():
    g = GUV.Guvenlik(_ayar(taban=-95.0))
    makine = {"X": 0.0, "Y": 0.0, "Z": -90.0}
    is_k = {"X": 0.0, "Y": 0.0, "Z": 0.0}
    assert g.jog_dogrula("Z", -4.0, 300.0, makine, is_k) is True


def test_taban_kapatilabilir():
    g = GUV.Guvenlik(_ayar(taban=-95.0, zorunlu=False))
    assert g.z_taban() is None
    makine = {"X": 0.0, "Y": 0.0, "Z": -90.0}
    assert g.jog_dogrula("Z", -50.0, 300.0, makine,
                         {"Z": 0.0}) is True


def test_taban_olculmediyse_denetim_yok():
    g = GUV.Guvenlik(_ayar(taban=None))
    assert g.z_taban() is None
    assert g.taban_dogrula(-9999.0, "test") == []


# ---------------------------------------------------------------------
# ASIL MESELE: is sifiri degisse de koruma ayni fiziksel yerde kalir
# ---------------------------------------------------------------------
def test_koruma_is_sifirindan_bagimsizdir():
    """Ayni fiziksel konum, iki farkli is sifiriyla ayni sonucu vermeli."""
    g = GUV.Guvenlik(_ayar(taban=-95.0))
    # Tezgah tablasindan 3 mm yukarida duruyoruz (makine Z = -92).
    makine = {"X": 0.0, "Y": 0.0, "Z": -92.0}

    # Senaryo A: uzun bicak, is sifiri tabla yuzeyinde  -> is Z = 3
    # Senaryo B: kisa bicak, is sifiri 18 mm malzeme ustunde -> is Z = -15
    for is_z in (3.0, -15.0, 50.0, -40.0):
        is_k = {"X": 0.0, "Y": 0.0, "Z": is_z}
        # 5 mm daha inmek her iki durumda da tabani asar (-97 < -95)
        try:
            g.jog_dogrula("Z", -5.0, 300.0, makine, is_k)
            raise AssertionError(f"is Z={is_z} icin reddedilmeliydi")
        except GUV.GuvenlikHatasi as e:
            assert any("tezgah tabani" in s for s in e.sebepler), (is_z,
                                                                   e.sebepler)


def test_eski_derinlik_siniri_tek_basina_korumuyordu():
    """Regresyon: `max_dalis_derinlik` neden yetersiz - belgeleyen test.

    Is sifiri yukarida yapilmissa (or. uzun bicak, sifir tabla yuzeyinde),
    is koordinatindaki -60 sinirina hic yaklasmadan tezgaha dalinabilir.
    Taban denetimi olmasaydi bu hareket GECERDI.
    """
    makine = {"X": 0.0, "Y": 0.0, "Z": -90.0}
    is_k = {"X": 0.0, "Y": 0.0, "Z": -10.0}          # -60 sinirindan uzak

    # Taban KAPALI: eski davranis -> hareket gecer (tezgaha dalar!)
    eski = GUV.Guvenlik(_ayar(taban=None, derinlik=-60.0))
    assert eski.jog_dogrula("Z", -20.0, 300.0, makine, is_k) is True

    # Taban ACIK: ayni hareket reddedilir
    yeni = GUV.Guvenlik(_ayar(taban=-95.0, derinlik=-60.0))
    try:
        yeni.jog_dogrula("Z", -20.0, 300.0, makine, is_k)
        raise AssertionError("taban acikken reddedilmeliydi")
    except GUV.GuvenlikHatasi as e:
        assert any("tezgah tabani" in s for s in e.sebepler)


# ---------------------------------------------------------------------
# Mutlak konumlama / MDI / program
# ---------------------------------------------------------------------
def test_mutlak_hedef_tabana_gore_denetlenir():
    g = GUV.Guvenlik(_ayar(taban=-95.0))
    makine = {"X": 0.0, "Y": 0.0, "Z": -50.0}
    is_k = {"X": 0.0, "Y": 0.0, "Z": 0.0}
    ofset = {"X": 0.0, "Y": 0.0, "Z": -50.0}      # makine = is + ofset
    try:
        g.mutlak_dogrula({"Z": -50.0}, makine, is_k, ofset)   # makine -100
        raise AssertionError("reddedilmeliydi")
    except GUV.GuvenlikHatasi as e:
        assert any("tezgah tabani" in s for s in e.sebepler), e.sebepler
    # -40 is -> makine -90: gecer
    assert g.mutlak_dogrula({"Z": -40.0}, makine, is_k, ofset) is True


def test_mdi_satiri_tabana_gore_denetlenir():
    g = GUV.Guvenlik(_ayar(taban=-95.0))
    is_k = {"X": 0.0, "Y": 0.0, "Z": 0.0}
    ofset = {"X": 0.0, "Y": 0.0, "Z": -80.0}
    sebepler = g.satir_dogrula(["G1 Z-20 F300"], is_k, ofset, "MDI")
    assert any("tezgah tabani" in s for s in sebepler), sebepler


def test_program_tabani_asarsa_HATA_verir():
    """Taban ihlali uyari degil HATA olmali: program calistirilamaz."""
    a = _ayar(taban=-95.0)
    g = GUV.Guvenlik(a)
    p = GC.Program(satirlar=["G21", "G90", "G0 X10 Y10",
                             "G1 Z-20 F600", "G0 Z20"])
    is_k = {"X": 0.0, "Y": 0.0, "Z": 0.0}
    ofset = {"X": 0.0, "Y": 0.0, "Z": -80.0}       # makine = is - 80
    sonuc = g.program_dogrula(p, is_k, ofset, 0)
    assert not sonuc["gecti"], sonuc
    assert any("tezgah tabani" in h for h in sonuc["hatalar"]), sonuc["hatalar"]


def test_program_taban_ustundeyse_gecer():
    g = GUV.Guvenlik(_ayar(taban=-95.0))
    p = GC.Program(satirlar=["G21", "G90", "G0 X10 Y10",
                             "G1 Z-5 F600", "G0 Z20"])
    sonuc = g.program_dogrula(p, {"X": 0.0, "Y": 0.0, "Z": 0.0},
                              {"X": 0.0, "Y": 0.0, "Z": -80.0}, 0)
    assert sonuc["gecti"], sonuc["hatalar"]


# ---------------------------------------------------------------------
# Arayuzde gosterilen pay
# ---------------------------------------------------------------------
def test_taban_payi():
    g = GUV.Guvenlik(_ayar(taban=-95.0))
    assert g.taban_payi(-90.0) == 5.0
    assert g.taban_payi(-95.0) == 0.0
    assert g.taban_payi(-96.0) == -1.0          # asilmis
    assert g.taban_payi(None) is None
    assert GUV.Guvenlik(_ayar(taban=None)).taban_payi(-90.0) is None


def test_ozet_tabani_bildirir():
    o = GUV.Guvenlik(_ayar(taban=-95.0)).ozet()
    assert o["makine_z_taban"] == -95.0
    assert o["makine_z_taban_zorunlu"] is True


if __name__ == "__main__":
    for ad, fn in sorted(list(globals().items())):
        if ad.startswith("test_"):
            fn()
            print("ok", ad)
    print("Tezgah tabani testleri gecti.")
