#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Güvenli Z vektörleri ve tezgâh sınırı (önizleme için)."""

import math
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mach3_kontrol import (ayarlar as AY, denetleyici as DEN,   # noqa: E402
                           gcode as GC)


def _prog(satirlar):
    return GC.Program(satirlar=satirlar)


IKI_KESIM = [
    "G21", "G90", "G0 Z20",
    "G0 X10 Y10", "G1 Z-5 F600", "G1 X60 Y10 F1200", "G1 X60 Y60", "G0 Z20",
    "G0 X100 Y100", "G1 Z-5 F600", "G1 X150 Y100 F1200", "G0 Z20",
    "M5", "M30",
]


def test_her_guvenli_z_yukselisi_bir_vektordur():
    r = _prog(IKI_KESIM).vektorler()
    v = r["vektorler"]
    assert r["guvenli_z"] == 20.0 and r["kaynak"] == "dosya"
    assert [x["no"] for x in v] == [1, 2]
    assert v[0]["bas"] == [10.0, 10.0] and v[0]["son"] == [60.0, 60.0]
    assert v[1]["bas"] == [100.0, 100.0] and v[1]["son"] == [150.0, 100.0]
    assert v[0]["z_min"] == -5.0
    assert v[0]["bbox"] == [10.0, 10.0, 60.0, 60.0]
    # uzunluk: dalis 25 + 50 + 50 (3B)
    assert abs(v[0]["uzunluk"] - (25 + 50 + 50)) < 0.01
    assert abs(v[1]["uzunluk"] - (25 + 50)) < 0.01


def test_ilk_hareket_yonu_ok_icin_verilir():
    v = _prog(IKI_KESIM).vektorler()["vektorler"]
    assert v[0]["yon"] == [1.0, 0.0]                  # ilk XY hareketi +X
    assert v[1]["yon"] == [1.0, 0.0]


def test_blokta_birden_cok_yukselis_ayri_vektorlerdir():
    """Blok kurali (X-Y'li G0) 1 blok der; vektor kurali 2 vektor."""
    satir = ["G21", "G90", "G0 Z20", "G0 X0 Y0",
             "G1 Z-3 F500", "G1 X30 Y0 F900", "G0 Z20",          # 1. kesim
             "G1 X40 Y0 F900",                                    # G0 degil: XY yine de ust
             "G1 Z-3 F500", "G1 X70 Y0 F900", "G0 Z20",          # 2. kesim
             "M30"]
    p = _prog(satir)
    assert len(p.bloklar) == 1
    v = p.vektorler()["vektorler"]
    assert len(v) == 2
    assert [x["blok"] for x in v] == [0, 0]
    assert v[0]["son"] == [30.0, 0.0] and v[1]["bas"] == [40.0, 0.0]


def test_kismi_yukselis_vektoru_bolmez():
    """Z5'e kisa yukselis (ornegin cok pasolu kesim) guvenli Z degildir."""
    satir = ["G21", "G90", "G0 Z20", "G0 X0 Y0",
             "G1 Z-2 F500", "G1 X10 F900",
             "G0 Z5", "G0 X0",                        # kisa kopru, 20'ye cikmadi
             "G1 Z-4 F500", "G1 X10 F900",
             "G0 Z20", "M30"]
    r = _prog(satir).vektorler()
    assert r["guvenli_z"] == 20.0                       # ilk G0 Z hedefi
    assert len(r["vektorler"]) == 1
    assert r["vektorler"][0]["z_min"] == -4.0


def test_guvenli_z_dosyada_yoksa_ayardan_alinir():
    satir = ["G21", "G90", "G1 Z-2 F500", "G1 X10 F900", "G1 Z15 F500",
             "G1 X0 F900", "G1 Z-2 F500", "G1 X10 F900"]
    r = _prog(satir).vektorler(guvenli_z=10.0)
    assert r["kaynak"] == "ayar" and r["guvenli_z"] == 10.0
    assert len(r["vektorler"]) == 2                     # Z15 >= 10 iki kesimi boler


def test_dosya_asagida_biterse_son_vektor_kapanir():
    satir = ["G21", "G90", "G0 Z20", "G0 X0 Y0", "G1 Z-5 F500", "G1 X25 F900"]
    v = _prog(satir).vektorler()["vektorler"]
    assert len(v) == 1 and v[0]["son"] == [25.0, 0.0]


def test_yukselirken_xy_de_degisiyorsa_vektor_hareketin_basladigi_noktada_biter():
    satir = ["G21", "G90", "G0 Z20", "G0 X0 Y0", "G1 Z-5 F500", "G1 X20 F900",
             "G0 X50 Y50 Z20", "M30"]
    v = _prog(satir).vektorler()["vektorler"]
    assert v[0]["son"] == [20.0, 0.0]


def test_yay_uzunlugu_ve_sinir_kutusu_yay_gecislerini_icerir():
    satir = ["G21", "G90", "G0 Z20", "G0 X10 Y0", "G1 Z-1 F300",
             "G3 X-10 Y0 I-10 J0 F600",                   # yarim daire, r=10
             "G0 Z20", "M30"]
    v = _prog(satir).vektorler()["vektorler"][0]
    assert abs(v["uzunluk"] - (21 + math.pi * 10)) < 0.2          # dalis 21 + yarim daire
    assert v["bbox"][3] > 9.5                             # yayin tepe noktasi (Y~10)


def test_artimli_dosyada_vektor_yok_uyari_var():
    r = _prog(["G91", "G0 Z20", "G1 X10"]).vektorler()
    assert r["vektorler"] == [] and "G91" in r["uyari"]


def test_hareketsiz_dosya():
    r = _prog(["G21", "(yorum)", "M30"]).vektorler()
    assert r["vektorler"] == [] and r["uyari"]


def test_ornek_dosya_blok_sayisiyla_uyumlu():
    yol = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "ornekler", "ornek_kesim.tap")
    if not os.path.exists(yol):
        return
    p = GC.Program(yol)
    v = p.vektorler(20.0)["vektorler"]
    assert len(v) == len(p.bloklar)
    assert [x["blok"] for x in v] == list(range(len(p.bloklar)))


# --------------------------------------------------- denetleyici entegrasyonu
def _den(**guv):
    d = tempfile.mkdtemp()
    a = AY.Ayarlar(os.path.join(d, "ayarlar.json"))
    g = {"yumusak_limit": {"X": {"min": 0.0, "max": 2100.0},
                           "Y": {"min": 0.0, "max": 4050.0},
                           "Z": {"min": -150.0, "max": 100.0}},
         "limit_payi": 1.0, "limitler_dogrulandi": True}
    g.update(guv)
    a.guncelle({"baglanti": {"tur": "simulasyon", "yoklama_hz": 30},
                "guvenlik": g})
    den = DEN.Denetleyici(a)
    den.baglan()
    time.sleep(0.15)
    return den, d


def test_program_bilgi_vektorleri_tasir():
    den, d = _den()
    try:
        yol = os.path.join(d, "p.tap")
        with open(yol, "w") as f:
            f.write("\n".join(IKI_KESIM))
        den.program_yukle(yol)
        b = den.program_bilgi()
        assert b["vektor"]["guvenli_z"] == 20.0
        assert [v["no"] for v in b["vektor"]["vektorler"]] == [1, 2]
        # onbellek: ayni nesne
        assert den.program_bilgi()["vektor"] is b["vektor"]
    finally:
        den.kapat()


def test_sinir_is_tezgah_sinirini_is_koordinatina_cevirir():
    den, _ = _den()
    try:
        # kafa tezgahta X=500, Y=300; is sifiri burasi
        den.referans_al(("X", "Y", "Z"), degerler={"X": 500.0, "Y": 300.0, "Z": 0.0})
        den.dro_ayarla("X", 0.0)
        den.dro_ayarla("Y", 0.0)
        s = den.sinir_is()
        # kullanilabilir tezgah (pay 1): X 1..2099 -> is = tezgah - 500
        assert s["aralik"]["X"] == [1.0 - 500.0, 2099.0 - 500.0]
        assert s["aralik"]["Y"] == [1.0 - 300.0, 4049.0 - 300.0]
        assert s["pay"] == 1.0 and s["dogrulandi"] is True
        # is sifiri degisince sinirlar da kayar
        den.dro_ayarla("X", 100.0)                       # is X sifiri 100 geride
        assert den.sinir_is()["aralik"]["X"][0] == 1.0 - 400.0
        assert "sinir_is" in den.durum()
    finally:
        den.kapat()


def test_sinir_is_referanssizsa_eksen_yok():
    den, _ = _den()
    try:
        den.referans_birak()
        assert den.sinir_is()["aralik"] == {}
        den.ayarlar.guncelle({"guvenlik": {"yumusak_limit_zorunlu": False}})
        den.referans_al(("X", "Y", "Z"))
        assert den.sinir_is()["aralik"] == {}            # limitler kapali
    finally:
        den.kapat()


if __name__ == "__main__":
    for ad, fn in sorted(list(globals().items())):
        if ad.startswith("test_"):
            fn()
            print("ok", ad)
    print("Vektor testleri gecti.")
