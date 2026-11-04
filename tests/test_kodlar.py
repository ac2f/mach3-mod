#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OEM kod haritası testleri.

Kodlar tahmin değil, Mach3 kurulumundaki DROs.txt / Buttons.txt / Leds.txt
sıralarından türer (kod = taban + sıra). Burada hem bu kural hem de gerçek bir
Mach3'te canlı doğrulanan değerler kilitlenir.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mach3_kontrol import kodlar as K  # noqa: E402

GERCEK_MACH3 = r"C:\Mach3"


def test_dro_kodlari_sira_artı_800():
    k = K.KodHaritasi()
    assert k.dro["x"] == 800 and k.dro["y"] == 801 and k.dro["z"] == 802
    assert k.dro["satir"] == 816          # Current Line
    assert k.dro["devir"] == 817          # Spindle Speed (eski kodda 812 yanlıştı)
    assert k.dro["feed"] == 818
    assert k.dro["fro"] == 821
    assert k.dro["jog_adim"] == 828       # canlı Mach3: GetOEMDRO(828) = JogInc
    assert k.dro["ofset_x"] == 830


def test_makine_koordinati_83_84_85():
    k = K.KodHaritasi()
    assert [k.eksen_dro(e, makine=True) for e in "XYZ"] == [83, 84, 85]


def test_dugme_kodlari_sira_artı_1000():
    k = K.KodHaritasi()
    assert k.buton["cycle_start"] == 1000
    assert k.buton["feed_hold"] == 1001
    assert k.buton["rewind"] == 1002
    assert k.buton["stop"] == 1003          # eski kodda 1002 idi = Rewind!
    assert k.buton["reset"] == 1021
    assert (k.buton["ref_x"], k.buton["ref_y"], k.buton["ref_z"]) == \
        (1022, 1023, 1024)                  # eski kodda 22/23/24 idi
    assert k.buton["sifirla_x"] == 1008


def test_led_kodlari_sira_artı_800():
    k = K.KodHaritasi()
    assert k.led["estop"] == 800            # eski kodda 825 idi (= Probe)
    assert k.led["inch"] == 801 and k.led["metrik"] == 802
    assert k.led["bosta"] == 803 and k.led["calisiyor"] == 804
    assert k.led["ref_x"] == 807
    assert k.led["makine_koord"] == 816
    assert k.led["cv"] == 817
    assert k.led["jog_adim"] == 819


def test_ezme_uygulanir_ve_silinince_geri_gelir():
    k = K.KodHaritasi({"buton": {"stop": 1234}, "dro": {"fro": 999}})
    assert k.buton["stop"] == 1234 and k.dro["fro"] == 999
    k.uygula({})
    assert k.buton["stop"] == 1003 and k.dro["fro"] == 821


def test_bozuk_ezme_yutulur():
    k = K.KodHaritasi({"buton": {"stop": "abc", "bilinmeyen": 5}})
    assert k.buton["stop"] == 1003
    assert "bilinmeyen" not in k.buton


def test_katalog_bicimi():
    kat = K.KodHaritasi().katalog()
    assert set(kat) == {"dro", "buton", "led"}
    s = [o for o in kat["buton"] if o["ad"] == "stop"][0]
    assert s["kod"] == 1003 and s["guven"] == "yuksek"
    assert all({"ad", "kod", "varsayilan", "aciklama", "guven"} <= set(o)
               for g in kat.values() for o in g)


def test_kurulum_dosyalari_kodlari_uyarlar():
    """Farklı bir Mach3 sürümünde liste sırası değişirse kodlar izlemeli."""
    with tempfile.TemporaryDirectory() as d:
        # "Pause" ve "Run" yer değiştirmiş bir Buttons.txt
        liste = list(K.BUTON_LISTESI)
        liste[0], liste[1] = liste[1], liste[0]
        with open(os.path.join(d, "Buttons.txt"), "w", encoding="latin-1") as f:
            f.write("\n".join(liste) + "\n\n")
        k = K.KodHaritasi(kurulum=d)
        assert k.buton["feed_hold"] == 1000 and k.buton["cycle_start"] == 1001
        assert k.kurulum_raporu["farklar"], "fark raporlanmalı"
        assert k.kurulum_raporu["okunan"] == ["Buttons.txt"]


def test_kurulum_dosyasi_yoksa_gomulu_liste_kalir():
    with tempfile.TemporaryDirectory() as d:
        k = K.KodHaritasi(kurulum=d)
        assert k.buton["stop"] == 1003
        assert k.kurulum_raporu["okunan"] == []


def test_gomulu_liste_gercek_mach3_kurulumuyla_ayni():
    """Bu bilgisayarda Mach3 kuruluysa gömülü listeler dosyalarla birebir aynı."""
    if not os.path.isdir(GERCEK_MACH3):
        return
    k = K.KodHaritasi(kurulum=GERCEK_MACH3)
    r = k.kurulum_raporu
    if not r["okunan"]:
        return
    assert r["farklar"] == [], r["farklar"]


if __name__ == "__main__":
    for ad, fn in sorted(list(globals().items())):
        if ad.startswith("test_"):
            fn()
            print("ok", ad)
    print("Kod haritası testleri geçti.")
