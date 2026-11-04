#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Home uç noktaları (HTTP) — arayüzün kullandığı sözleşme."""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_web as T                                   # noqa: E402
from mach3_kontrol import webapp as W, guvenlik as GUV  # noqa: E402


def _hazir():
    T.kur()
    d = T.cagir("/api/durum")
    if d["estop"]:
        T.cagir("/api/makine", {"ad": "reset"})
        time.sleep(0.2)
    T.cagir("/api/referans", {"islem": "al", "eksenler": ["X", "Y", "Z"],
                              "degerler": {"X": 300.0, "Y": 200.0, "Z": 0.0}})
    for h in T.cagir("/api/home/liste")["liste"]:
        T.cagir("/api/home/sil", {"ad": h["ad"]})
    # sunucu diger testlerle paylasilir: is sifirini temiz bir noktaya getir
    # (is DRO = 0 burada)
    for e in "XYZ":
        T.cagir("/api/sifirla", {"eksen": e, "ofset": 0.0})
    time.sleep(0.2)


def test_kaydet_liste_uygula_duzenle_sil_akisi():
    _hazir()
    # iki farkli malzeme
    r = T.cagir("/api/home/kaydet", {"ad": "Levha A", "malzeme": "MDF 18",
                                      "tezgah": {"X": 300, "Y": 200, "Z": 0}})
    assert r["home"]["tezgah"] == {"X": 300.0, "Y": 200.0, "Z": 0.0}, r
    T.cagir("/api/home/kaydet", {"ad": "Levha B", "not": "sag levha",
                                  "tezgah": {"X": 1500, "Y": 200, "Z": -18}})
    h = T.cagir("/api/home/liste")
    assert [x["ad"] for x in h["liste"]] == ["Levha A", "Levha B"]
    assert h["mevcut"] == {"X": 300.0, "Y": 200.0, "Z": 0.0}

    # Levha B'ye gec: iş X = tezgah 300 - 1500
    r = T.cagir("/api/home/uygula", {"ad": "Levha B"})
    assert abs(r["yazilan"]["X"] - (-1200.0)) < 1e-6 and r["git"] is False
    d = T.cagir("/api/durum")
    assert d["profiller"]["aktif_gercek"] == "Levha B"
    assert abs(d["konum"]["X"] - (-1200.0)) < 0.01

    # duzenle + yeniden adlandir
    r = T.cagir("/api/home/duzenle", {"ad": "Levha B", "yeni_ad": "Sag levha",
                                       "tezgah": {"Z": None}})
    assert r["ad"] == "Sag levha"
    h = T.cagir("/api/home/liste")
    b = [x for x in h["liste"] if x["ad"] == "Sag levha"][0]
    assert "Z" not in b["tezgah"] and h["aktif"] == "Sag levha"

    T.cagir("/api/home/sil", {"ad": "Levha A"})
    assert [x["ad"] for x in T.cagir("/api/home/liste")["liste"]] == ["Sag levha"]


def test_ayni_ad_hata_doner_uzerine_yaz_onayiyla_gecer():
    _hazir()
    T.cagir("/api/home/kaydet", {"ad": "A", "tezgah": {"X": 1}})
    r = T.cagir("/api/home/kaydet", {"ad": "a", "tezgah": {"X": 2}})
    assert "hata" in r and "zaten var" in r["hata"]
    r = T.cagir("/api/home/kaydet", {"ad": "a", "tezgah": {"X": 2},
                                      "uzerine_yaz": True})
    assert r["home"]["tezgah"]["X"] == 2.0
    assert len(T.cagir("/api/home/liste")["liste"]) == 1


def test_mevcut_sifir_otomatik_kaydedilir_ve_aktif_sayilir():
    _hazir()
    r = T.cagir("/api/home/kaydet", {"ad": "Simdiki"})      # tezgah verilmedi
    assert r["home"]["tezgah"] == {"X": 300.0, "Y": 200.0, "Z": 0.0}, r
    h = T.cagir("/api/home/liste")
    assert h["aktif_gercek"] == "Simdiki" and h["liste"][0]["uyumlu"] is True


def test_uygula_ve_git():
    _hazir()
    T.cagir("/api/home/kaydet", {"ad": "Uzak", "tezgah": {"X": 100, "Y": 100, "Z": -10}})
    r = T.cagir("/api/home/uygula", {"ad": "Uzak", "git": True})
    assert r["git"] is True, r
    # onbellekli "hareket" bayragi hareket baslamadan False olabilir: konuma bak
    for _ in range(150):
        d = T.cagir("/api/durum")
        if (abs(d["konum"]["X"]) < 0.05 and abs(d["konum"]["Y"]) < 0.05
                and not d["hareket"]):
            break
        time.sleep(0.1)
    assert abs(d["konum"]["X"]) < 0.05 and abs(d["konum"]["Y"]) < 0.05, d["konum"]


def test_hata_yanitlari():
    _hazir()
    for uc, veri in (("/api/home/uygula", {"ad": "yok"}),
                     ("/api/home/duzenle", {"ad": "yok", "malzeme": "x"}),
                     ("/api/home/sil", {"ad": "yok"}),
                     ("/api/home/kaydet", {"ad": ""}),
                     ("/api/home/kaydet", {"ad": "X", "tezgah": {"X": "abc"}})):
        r = T.cagir(uc, veri)
        assert "hata" in r, (uc, r)


def test_eski_profil_uclari_calismaya_devam_eder():
    _hazir()
    r = T.cagir("/api/profil/kaydet", {"ad": "Eski ad", "takim_boyu": 52.3})
    assert r["ad"] == "Eski ad" or r.get("takim_boyu") == 52.3
    assert [x["ad"] for x in T.cagir("/api/profil/liste")["liste"]] == ["Eski ad"]
    r = T.cagir("/api/profil/uygula", {"ad": "Eski ad"})
    assert "yazilan" in r
    T.cagir("/api/profil/sil", {"ad": "Eski ad"})


def test_uzak_istemci_pinsiz_home_degistiremez():
    W._OTURUM.clear()
    for fn, veri in ((W.api_home_kaydet, {"ad": "x", "tezgah": {"X": 1}}),
                     (W.api_home_uygula, {"ad": "x"}),
                     (W.api_home_duzenle, {"ad": "x"}),
                     (W.api_home_sil, {"ad": "x"})):
        try:
            fn(veri, "192.168.1.50")
            raise AssertionError("PIN'siz uzak istemci reddedilmeliydi")
        except GUV.GuvenlikHatasi:
            pass
    # listeleme serbest (durum okuma gibi)
    assert "liste" in W.api_home_liste({}, "192.168.1.50")


if __name__ == "__main__":
    for ad, fn in sorted(list(globals().items())):
        if ad.startswith("test_"):
            fn()
            print("ok", ad)
    print("Web Home testleri gecti.")
