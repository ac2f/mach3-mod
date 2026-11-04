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


def test_gozat_son_klasoru_hatirlar():
    """Kullanici dosya ACMADAN cikip geri gelse de ayni yerden devam eder."""
    kur()
    alt = os.path.join(_DIZIN, "hatirla")
    os.makedirs(alt, exist_ok=True)
    cagir("/api/gozat", {"klasor": alt})
    d = cagir("/api/gozat", {})              # klasor verilmedi
    assert d["klasor"] == alt, d["klasor"]


def test_gozat_en_yeni_ustte():
    """Varsayilan siralama: en son degisiklik yapilan en ustte."""
    kur()
    kok = os.path.join(_DIZIN, "sira")
    os.makedirs(kok, exist_ok=True)
    simdi = time.time()
    for ad, yas_sn in (("eski.tap", 9000), ("orta.tap", 3600),
                       ("yeni.tap", 10)):
        yol = os.path.join(kok, ad)
        with open(yol, "w", encoding="utf-8") as f:
            f.write("G21\n")
        os.utime(yol, (simdi - yas_sn, simdi - yas_sn))
    for ad, yas_sn in (("eski_klasor", 9000), ("yeni_klasor", 5)):
        yol = os.path.join(kok, ad)
        os.makedirs(yol, exist_ok=True)
        os.utime(yol, (simdi - yas_sn, simdi - yas_sn))

    d = cagir("/api/gozat", {"klasor": kok, "siralama": "tarih"})
    assert [f["ad"] for f in d["dosyalar"]] == ["yeni.tap", "orta.tap",
                                                "eski.tap"]
    assert [k["ad"] for k in d["klasorler"]] == ["yeni_klasor", "eski_klasor"]
    assert all("zaman" in k for k in d["klasorler"])   # arayuz "3 dk once" yazar

    d = cagir("/api/gozat", {"klasor": kok, "siralama": "ad"})
    assert [f["ad"] for f in d["dosyalar"]] == ["eski.tap", "orta.tap",
                                                "yeni.tap"]
    # Secim hatirlanir
    assert cagir("/api/gozat", {"klasor": kok})["siralama"] == "ad"
    cagir("/api/gozat", {"klasor": kok, "siralama": "tarih"})


def test_baglanti_acik_kalir():
    """HTTP/1.1 keep-alive: her kucuk POST icin yeni TCP baglantisi acmak
    jogda gozle gorulur gecikme yaratiyordu."""
    kur()
    import http.client
    c = http.client.HTTPConnection("127.0.0.1", _PORT, timeout=8)
    for _ in range(3):
        c.request("POST", "/api/durum", body=b"{}",
                  headers={"Content-Type": "application/json"})
        y = c.getresponse()
        y.read()
        assert y.status == 200
        assert y.version == 11
        assert (y.getheader("Connection") or "").lower() != "close"
    c.close()


def test_program_olculeri():
    """Onizleme bilgi seridi: kesim alani, en sag/en ust, X0Y0'a uzaklik."""
    yol = os.path.join(_DIZIN or tempfile.mkdtemp(), "olcu_deneme.tap")
    kur()
    with open(yol, "w", encoding="utf-8") as f:
        f.write("\n".join(ORNEK))
    p = cagir("/api/program/yukle", {"yol": yol})
    o = p["olcu"]
    assert o["kesim"]["x"] == [10.0, 150.0]
    assert o["kesim"]["genislik"] == 140.0
    assert o["kesim"]["yukseklik"] == 90.0
    assert o["sifira_gore"]["sag"] == 150.0
    assert o["sifira_gore"]["ust"] == 100.0
    assert o["sifira_gore"]["sol"] == 10.0
    assert o["z"]["min"] == -5.0
    # Bosta (G0) gidisler dahil zarf, kesim alanindan buyuk olmali
    assert o["tum"]["genislik"] >= o["kesim"]["genislik"]


def test_ayar_kaydetme():
    d = cagir("/api/ayarlar", {"yama": {"guvenlik": {"guvenli_z": 33.0}}})
    assert d["ayarlar"]["guvenlik"]["guvenli_z"] == 33.0
    assert d["ayarlar"]["guvenlik"]["pin"] == "***"     # PIN sizmaz
    assert any(k["ad"] == "x" for k in d["oem"]["dro"])
    cagir("/api/ayarlar", {"yama": {"guvenlik": {"guvenli_z": 20.0}}})


def _kalibrasyon_bekle():
    """Kalibrasyon hareketi bitene kadar durumu yoklar."""
    for _ in range(120):
        d = cagir("/api/kalibrasyon", {"islem": "durum"})
        o = d.get("oturum")
        if not o or (not o["mesgul"] and o["asama"] != "hareket"):
            return d
        time.sleep(0.1)
    raise AssertionError("kalibrasyon hareketi bitmedi")


def test_kalibrasyon_akisi():
    """Olc → hesapla → Mach3'e girilecek degeri dondur (simulasyonda)."""
    kur()
    d = cagir("/api/kalibrasyon", {
        "islem": "basla", "eksen": "X", "tur": "olcek", "mesafe": 10.0,
        "feed": 3000.0, "on_yaklasma": 2.0, "tekrar": 1,
        "adim_basi": 1600.0})
    assert d["oturum"]["asama"] == "hazir", d

    cagir("/api/kalibrasyon", {"islem": "adim", "adim": "bosluk_al"})
    d = _kalibrasyon_bekle()
    assert d["oturum"]["asama"] == "okuma_a", d["oturum"]

    # Hareket bitmeden okuma kabul edilmemeli
    cagir("/api/kalibrasyon", {"islem": "adim", "adim": "olc"})
    d = _kalibrasyon_bekle()
    assert d["oturum"]["asama"] == "okuma_b", d["oturum"]

    # Kumpas 10.04 mm okudu: Mach3'e yazilacak yeni adim sayisi hesaplanir
    d = cagir("/api/kalibrasyon", {"islem": "olcum", "a": 0, "b": 10.04})
    s = d["son_sonuc"]
    assert abs(s["hata_mm"] - 0.04) < 1e-6
    assert abs(s["yeni_adim_basi"] - 1600.0 * 10.0 / 10.04) < 0.001
    t = s["talimat"][0]
    assert "Motor Tuning" in t["pencere"] and t["alanlar"][0]["ad"] == "Steps per"

    r = cagir("/api/kalibrasyon", {"islem": "uygula"})
    assert r["ok"] and r["uyarilar"]
    assert r["kalibrasyon"]["ayar"]["adim_basi"]["X"] == s["yeni_adim_basi"]
    # Olay akisina yalnizca OZET gider (talimat/gecmis kilobaytlar tutar)
    ozet = cagir("/api/durum")["kalibrasyon"]
    assert set(ozet) == {"ayar_ozet", "sonuc_var", "oturum"}
    assert ozet["sonuc_var"] is True
    # Olcek degistigi icin referans dusmus olmali
    assert "X" in cagir("/api/durum")["referans"]["eksik"]
    cagir("/api/referans", {"islem": "al", "eksenler": ["X", "Y", "Z"]})


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
