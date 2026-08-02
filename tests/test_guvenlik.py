#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Guvenlik katmani testleri: limit reddi, kalp atisi, kontrol kilidi."""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mach3_kontrol import ayarlar as AY, gcode as GC, guvenlik as GUV  # noqa: E402


def _ayar():
    yol = os.path.join(tempfile.mkdtemp(), "ayarlar.json")
    a = AY.Ayarlar(yol)
    a.guncelle({"guvenlik": {
        "yumusak_limit": {"X": {"min": 0.0, "max": 1000.0},
                          "Y": {"min": 0.0, "max": 500.0},
                          "Z": {"min": -100.0, "max": 0.0}},
        "limit_payi": 1.0, "max_jog_adim": 50.0, "max_jog_feed": 3000.0,
        "max_dalis_derinlik": -30.0}})
    return a


def test_jog_limit_disina_reddedilir():
    g = GUV.Guvenlik(_ayar())
    makine = {"X": 995.0, "Y": 100.0, "Z": -5.0}
    is_k = {"X": 0.0, "Y": 0.0, "Z": -5.0}
    g.jog_dogrula("X", -10.0, 1000.0, makine, is_k)          # sorun yok
    try:
        g.jog_dogrula("X", 10.0, 1000.0, makine, is_k)       # 1005 > 999
        raise AssertionError("limit disi jog reddedilmeliydi")
    except GUV.GuvenlikHatasi as e:
        assert any("ust yumusak limit" in s for s in e.sebepler), e.sebepler


def test_buyuk_adim_ve_feed_reddedilir():
    g = GUV.Guvenlik(_ayar())
    makine = {"X": 100.0, "Y": 100.0, "Z": 0.0}
    try:
        g.jog_dogrula("X", 80.0, 1000.0, makine, makine)
        raise AssertionError("buyuk adim reddedilmeliydi")
    except GUV.GuvenlikHatasi as e:
        assert any("en buyuk adim" in s for s in e.sebepler)
    try:
        g.jog_dogrula("X", 10.0, 9000.0, makine, makine)
        raise AssertionError("yuksek besleme reddedilmeliydi")
    except GUV.GuvenlikHatasi as e:
        assert any("ust sinir" in s for s in e.sebepler)


def test_dalis_derinligi_siniri():
    g = GUV.Guvenlik(_ayar())
    makine = {"X": 100.0, "Y": 100.0, "Z": -25.0}
    is_k = {"X": 100.0, "Y": 100.0, "Z": -25.0}
    try:
        g.jog_dogrula("Z", -10.0, 500.0, makine, is_k)       # -35 < -30
        raise AssertionError("derin inis reddedilmeliydi")
    except GUV.GuvenlikHatasi as e:
        # Mesaj artik "IS sifirina gore" oldugunu acikca soyluyor; tezgahi
        # koruyan denetim ayri (makine_z_taban, bkz. test_taban_*).
        assert any("elle inis siniri" in s for s in e.sebepler), e.sebepler


def test_program_kapsami_limitle_karsilastirilir():
    a = _ayar()
    g = GUV.Guvenlik(a)
    p = GC.Program(satirlar=["G21", "G90", "G0 X10 Y10", "G1 Z-5 F600",
                             "G1 X900 Y400 F1000", "G0 Z20"])
    is_k = {"X": 0.0, "Y": 0.0, "Z": 0.0}
    # is sifiri malzeme ustunde: makine Z'si is Z'sinden 50 mm asagida
    ofset = {"X": 0.0, "Y": 0.0, "Z": -50.0}
    sonuc = g.program_dogrula(p, is_k, ofset)
    assert sonuc["gecti"], sonuc["hatalar"]
    assert sonuc["kapsam"]["X"] == [10.0, 900.0]

    # is sifiri 200 mm sagda ise ayni program limit disina tasar
    sonuc2 = g.program_dogrula(p, is_k, dict(ofset, X=200.0))
    assert not sonuc2["gecti"]
    assert any("X" in h for h in sonuc2["hatalar"])


def test_kalp_atisi_bekcisi_durdurur():
    durdu = []
    b = GUV.KalpAtisiBekcisi(lambda s: durdu.append(s), zaman_asimi_ms=120,
                             en_fazla_sn=5.0)
    b.baslat()
    for _ in range(4):                       # duzenli kalp atisi -> devam
        time.sleep(0.05)
        assert b.dokun()
    assert not durdu
    time.sleep(0.45)                         # sessizlik -> otomatik durdurma
    assert durdu and "kalp atisi" in durdu[0]
    assert not b.aktif


def test_surekli_jog_sure_siniri():
    durdu = []
    b = GUV.KalpAtisiBekcisi(lambda s: durdu.append(s), zaman_asimi_ms=500,
                             en_fazla_sn=0.3)
    b.baslat()
    for _ in range(10):
        time.sleep(0.05)
        b.dokun()
    assert durdu and "sure siniri" in durdu[0]


def test_kontrol_kilidi_tek_istemci():
    k = GUV.KontrolKilidi(zaman_asimi_sn=0.3)
    assert k.al("telefon")
    assert not k.al("masaustu")              # baskasi kilitli
    k.dogrula("telefon")
    try:
        k.dogrula("masaustu")
        raise AssertionError("ikinci istemci komut verememeli")
    except GUV.GuvenlikHatasi:
        pass
    time.sleep(0.45)                         # sessiz kalan sahibi birakir
    assert k.al("masaustu")


def test_gunluk_artimli():
    g = GUV.Gunluk()
    g.ekle("test", "bir")
    g.ekle("test", "iki")
    hepsi = g.liste()
    assert [o["mesaj"] for o in hepsi] == ["bir", "iki"]
    assert [o["mesaj"] for o in g.liste(sonra=hepsi[0]["id"])] == ["iki"]


if __name__ == "__main__":
    for ad, fn in sorted(list(globals().items())):
        if ad.startswith("test_"):
            fn()
            print("ok", ad)
    print("Guvenlik testleri gecti.")
