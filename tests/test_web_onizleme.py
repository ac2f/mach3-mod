#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Önizleme: ayrı pencere sayfası, vektör verisi ve tezgâh sınırı (HTTP)."""

import http.client
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_web as T                                   # noqa: E402

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "mach3_kontrol", "web")


def _get(yol, host=None):
    T.kur()
    c = http.client.HTTPConnection("127.0.0.1", T._PORT, timeout=8)
    c.request("GET", yol, headers={"Host": host or f"127.0.0.1:{T._PORT}"})
    y = c.getresponse()
    govde = y.read()
    c.close()
    return y.status, govde, y.getheader("Content-Type") or ""


def test_onizleme_penceresi_sunulur():
    for yol in ("/onizleme", "/onizleme.html"):
        durum, govde, tur = _get(yol)
        assert durum == 200 and tur.startswith("text/html"), (yol, durum, tur)
        h = govde.decode("utf-8")
        assert 'id="ozKap"' in h and "onizleme.js" in h
        assert "onizleme_pencere.js" in h and 'id="ozAcil"' in h   # ACIL DURDUR var


def test_onizleme_betikleri_sunulur():
    for yol in ("/onizleme.js", "/onizleme_pencere.js", "/mach3.css"):
        durum, govde, tur = _get(yol)
        assert durum == 200 and len(govde) > 500, yol


def test_onizleme_sayfasi_da_host_denetler():
    durum, _, _ = _get("/onizleme", host="kotu-site.example")
    assert durum == 403


def test_program_bilgisinde_vektorler_var():
    T.kur()
    yol = os.path.join(T._DIZIN, "onizleme_deneme.tap")
    with open(yol, "w", encoding="utf-8") as f:
        f.write("\n".join(T.ORNEK))
    p = T.cagir("/api/program/yukle", {"yol": yol})
    v = p["vektor"]
    assert v["guvenli_z"] == 20.0 and v["kaynak"] == "dosya"
    assert [x["no"] for x in v["vektorler"]] == [1, 2]
    assert v["vektorler"][0]["bas"] == [10.0, 10.0]
    assert v["vektorler"][1]["bas"] == [100.0, 100.0]
    # ayni veri /api/program/bilgi ile de gelir (ayri pencere bunu kullanir)
    assert T.cagir("/api/program/bilgi", {})["vektor"]["vektorler"][1]["no"] == 2


def test_durumda_tezgah_siniri_is_koordinatinda():
    T.kur()
    T.cagir("/api/referans", {"islem": "al", "eksenler": ["X", "Y", "Z"],
                              "degerler": {"X": 100.0, "Y": 100.0, "Z": 0.0}})
    for e in "XYZ":
        T.cagir("/api/sifirla", {"eksen": e, "ofset": 0.0})
    time.sleep(0.3)
    s = T.cagir("/api/durum")["sinir_is"]
    # sunucu limitleri 0..500 (X, Y); kafa tezgahta 100 ve is sifiri burasi
    assert s["aralik"]["X"] == [-100.0, 400.0] and s["aralik"]["Y"] == [-100.0, 400.0]
    assert s["pay"] == 0.0


def test_katman_listesi_arayuzde_ve_dosyalarda_tutarli():
    """Çip çubuğu, çizici ve CSS aynı katman kimliklerini kullanmalı."""
    js = open(os.path.join(WEB, "onizleme.js"), encoding="utf-8").read()
    kimlikler = re.findall(r'\{ id: "(\w+)", ad:', js)
    assert len(kimlikler) >= 12 and len(set(kimlikler)) == len(kimlikler)
    for k in ("vektor", "gecis", "kenar", "sinir", "konum"):
        assert k in kimlikler
        assert f"K.{k}" in js or f'al("{k}")' in js, f"{k} katmani cizilmiyor"
    css = open(os.path.join(WEB, "mach3.css"), encoding="utf-8").read()
    for sinif in ("oz-vno", "oz-gecis", "oz-kenar", "oz-sinir", "oz-chip",
                  "oz-sahte-tam"):
        assert "." + sinif in css, f"CSS'te .{sinif} yok"


if __name__ == "__main__":
    for ad, fn in sorted(list(globals().items())):
        if ad.startswith("test_"):
            fn()
            print("ok", ad)
    print("Onizleme testleri gecti.")
