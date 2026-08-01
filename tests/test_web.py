#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Web sunucusu ucdan uca (yerel istemci) testleri."""

import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mach3_kontrol import ayarlar as AY, denetleyici as DEN, webapp as W  # noqa: E402

ORNEK = ["G21", "G90", "G0 Z20",
         "G0 X10 Y10", "G1 Z-5 F600", "G1 X60 Y10 F1200", "G1 X60 Y60",
         "G1 X10 Y10", "G0 Z20",
         "G0 X100 Y100", "G1 Z-5 F600", "G1 X150 Y100 F1200", "G0 Z20",
         "M5", "M30"]

_SUNUCU = None
_PORT = None
_DIZIN = None


def kur():
    global _SUNUCU, _PORT, _DIZIN
    if _SUNUCU:
        return
    _DIZIN = tempfile.mkdtemp()
    W.AYARLAR = AY.Ayarlar(os.path.join(_DIZIN, "ayarlar.json"))
    W.AYARLAR.guncelle({
        "baglanti": {"tur": "simulasyon", "yoklama_hz": 20},
        "guvenlik": {"yumusak_limit": {"X": {"min": 0.0, "max": 500.0},
                                       "Y": {"min": 0.0, "max": 500.0},
                                       "Z": {"min": -100.0, "max": 100.0}},
                     "limit_payi": 0.0}})
    W.DENETLEYICI = DEN.Denetleyici(W.AYARLAR)
    W.DENETLEYICI.baglan()
    _SUNUCU = ThreadingHTTPServer(("127.0.0.1", 0), W.Isleyici)
    _PORT = _SUNUCU.server_address[1]
    threading.Thread(target=_SUNUCU.serve_forever, daemon=True).start()
    time.sleep(0.2)


def cagir(uc, veri=None):
    kur()
    istek = urllib.request.Request(
        f"http://127.0.0.1:{_PORT}{uc}",
        data=json.dumps(veri or {}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(istek, timeout=8) as y:
        return json.loads(y.read())


def test_arayuz_sunuluyor():
    kur()
    with urllib.request.urlopen(f"http://127.0.0.1:{_PORT}/", timeout=5) as y:
        govde = y.read().decode()
    assert "Mach3-Kontrol" in govde and "app.js" in govde


def test_durum():
    d = cagir("/api/durum")
    assert d["bagli"] and d["tur"] == "simulasyon"
    assert "X" in d["konum"] and "guvenlik" in d


def test_jog_ve_sifirlama():
    cagir("/api/jog/adim", {"eksen": "X", "mesafe": 20.0, "feed": 3000})
    time.sleep(1.2)
    d = cagir("/api/durum")
    assert abs(d["konum"]["X"] - 20.0) < 0.2, d["konum"]
    cagir("/api/sifirla", {"eksen": "X", "ofset": 5.0})
    time.sleep(0.2)
    d = cagir("/api/durum")
    assert abs(d["konum"]["X"] + 5.0) < 0.1, d["konum"]


def test_limit_disi_jog_hata_doner():
    d = cagir("/api/jog/adim", {"eksen": "Y", "mesafe": 900.0, "feed": 1000})
    assert "hata" in d and d["sebepler"]


def test_program_akisi():
    yol = os.path.join(_DIZIN or tempfile.mkdtemp(), "web_deneme.tap")
    kur()
    with open(yol, "w", encoding="utf-8") as f:
        f.write("\n".join(ORNEK))
    p = cagir("/api/program/yukle", {"yol": yol})
    assert len(p["bloklar"]) == 2
    assert p["sure"]["toplam_sn"] > 0
    assert p["bloklar"][0]["komut"].startswith("M")      # SVG yolu

    p2 = cagir("/api/program/blok", {"blok": 1})
    assert p2["secili_blok"] == 1
    d = cagir("/api/program/denetle", {})
    assert d["gecti"], d["hatalar"]
    # onaysiz baslatma reddedilir
    r = cagir("/api/program/baslat", {"blok": 1, "onay": False})
    assert "hata" in r


def test_gozat():
    kur()
    with open(os.path.join(_DIZIN, "gozat_deneme.tap"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(ORNEK))
    d = cagir("/api/gozat", {"klasor": _DIZIN})
    assert d["klasor"] == _DIZIN
    assert any(f["ad"].endswith(".tap") for f in d["dosyalar"])


def test_ayar_kaydetme():
    d = cagir("/api/ayarlar", {"yama": {"guvenlik": {"guvenli_z": 33.0}}})
    assert d["ayarlar"]["guvenlik"]["guvenli_z"] == 33.0
    assert d["ayarlar"]["guvenlik"]["pin"] == "***"     # PIN sizmaz
    assert any(k["ad"] == "x" for k in d["oem"]["dro"])
    cagir("/api/ayarlar", {"yama": {"guvenlik": {"guvenli_z": 20.0}}})


def test_olay_akisi():
    kur()
    with urllib.request.urlopen(f"http://127.0.0.1:{_PORT}/olay",
                                timeout=5) as y:
        satir = y.readline().decode("utf-8", "replace")   # ilk "data: {...}"
    assert satir.startswith("data: ")
    d = json.loads(satir[6:])
    assert "konum" in d and "guvenlik" in d


def test_acil_durdur():
    d = cagir("/api/acil", {"sebep": "test"})
    assert "adimlar" in d and d["sebep"] == "test"


if __name__ == "__main__":
    for ad, fn in sorted(list(globals().items())):
        if ad.startswith("test_"):
            fn()
            print("ok", ad)
    print("Web testleri gecti.")
