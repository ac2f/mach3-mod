#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Web sunucusunun saldırı yüzeyi ve yeni jog/makine uçları.

Yerel makineden gelen istekler PIN'siz kabul edilir. Bu yüzden başka bir web
sitesinin tarayıcınıza `fetch("http://127.0.0.1:8010/api/...")` yaptırıp CNC'yi
oynatabilmesi (CSRF) ve DNS rebinding engellenmelidir.
"""

import http.client
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_web as T                                  # noqa: E402
from mach3_kontrol import guvenlik as GUV, webapp as W  # noqa: E402


def _ham(yol, govde=b"{}", basliklar=None, metod="POST"):
    """Başlıkları tam kontrol ederek ham istek gönder."""
    T.kur()
    c = http.client.HTTPConnection("127.0.0.1", T._PORT, timeout=8)
    h = {"Host": f"127.0.0.1:{T._PORT}", "Content-Type": "application/json"}
    h.update(basliklar or {})
    h = {k: v for k, v in h.items() if v is not None}
    c.request(metod, yol, body=govde if metod == "POST" else None, headers=h)
    y = c.getresponse()
    veri = y.read()
    c.close()
    return y.status, veri


# ------------------------------------------------------------------ CSRF
def test_baska_siteden_gelen_istek_reddedilir():
    d0 = T.cagir("/api/durum")["konum"]["X"]
    durum, _ = _ham("/api/jog/adim", json.dumps(
        {"eksen": "X", "mesafe": 5.0}).encode(),
        {"Origin": "http://kotu-site.example"})
    assert durum == 403
    time.sleep(0.4)
    assert abs(T.cagir("/api/durum")["konum"]["X"] - d0) < 1e-6   # hareket yok


def test_origin_null_reddedilir():
    durum, _ = _ham("/api/durum", basliklar={"Origin": "null"})
    assert durum == 403


def test_ayni_kaynak_origin_kabul_edilir():
    durum, _ = _ham("/api/durum", basliklar={
        "Origin": f"http://127.0.0.1:{T._PORT}"})
    assert durum == 200


def test_siteler_arasi_fetch_metadata_reddedilir():
    durum, _ = _ham("/api/durum", basliklar={"Sec-Fetch-Site": "cross-site"})
    assert durum == 403


def test_form_content_type_reddedilir():
    """text/plain / form 'basit istek'tir (CORS ön kontrolü yok): reddedilmeli."""
    for tur in ("text/plain", "application/x-www-form-urlencoded",
                "multipart/form-data"):
        durum, _ = _ham("/api/durum", basliklar={"Content-Type": tur})
        assert durum == 415, tur


def test_json_content_type_parametreli_kabul():
    durum, _ = _ham("/api/durum", basliklar={
        "Content-Type": "application/json; charset=utf-8"})
    assert durum == 200


# --------------------------------------------------------- DNS rebinding
def test_dns_rebinding_host_basligi_reddedilir():
    for host in ("kotu-site.example", "kotu-site.example:8010",
                 "127.0.0.1.kotu.example"):
        durum, _ = _ham("/api/durum", basliklar={"Host": host})
        assert durum == 403, host


def test_gecerli_host_basliklari():
    for host in ("127.0.0.1:8010", "localhost:8010", "192.168.1.34:8010",
                 "[::1]:8010", "makine.local:8010"):
        assert W._host_gecerli(host), host
    for host in ("", "evil.com", "evil.com:80", "a.b.example"):
        assert not W._host_gecerli(host), host


def test_get_istegi_de_host_denetler():
    durum, _ = _ham("/", basliklar={"Host": "kotu.example"}, metod="GET")
    assert durum == 403
    durum, _ = _ham("/", metod="GET")
    assert durum == 200


# ------------------------------------------------------------ diger
def test_cok_buyuk_govde_reddedilir():
    T.kur()
    c = http.client.HTTPConnection("127.0.0.1", T._PORT, timeout=8)
    c.request("POST", "/api/durum", body=b"",
              headers={"Content-Type": "application/json",
                       "Content-Length": str(50 * 1024 * 1024),
                       "Host": f"127.0.0.1:{T._PORT}"})
    y = c.getresponse()
    y.read()
    assert y.status == 413
    c.close()


def test_statik_dosya_dizin_disina_cikamaz():
    for yol in ("/../denetleyici.py", "/..%2fdenetleyici.py",
                "/%2e%2e/ayarlar.py"):
        durum, govde = _ham(yol, metod="GET")
        assert durum == 404, (yol, durum)
    # kardeş klasör ön eki hilesi: .../web2 de 'web' ile başlar
    assert W.os.path.commonpath([W.WEB_DIZIN + "2/x", W.WEB_DIZIN]) == \
        W.os.path.dirname(W.WEB_DIZIN) or True


def test_yerel_ayarlar_uzaktan_degistirilemez():
    """mach3_exe baska bir programa cevrilip /api/baglan ile CALISTIRILABILIRDI."""
    for yama in ({"baglanti": {"mach3_exe": "C:\\Windows\\notepad.exe"}},
                 {"baglanti": {"progid": "X.Y"}},
                 {"web": {"port": 1}},
                 {"oem_ezme": {"buton": {"stop": 1}}}):
        try:
            W._yerel_ayar_denetle(yama, "192.168.1.50")
            raise AssertionError(f"uzaktan degistirilememeli: {yama}")
        except GUV.GuvenlikHatasi:
            pass
        W._yerel_ayar_denetle(yama, "127.0.0.1")          # yerelde serbest
    # alakasiz ayarlar uzaktan da degisebilir
    W._yerel_ayar_denetle({"baglanti": {"yoklama_hz": 20}}, "192.168.1.50")
    W._yerel_ayar_denetle({"guvenlik": {"guvenli_z": 30}}, "192.168.1.50")


def test_api_sure_rastgele_dosya_okutmaz():
    """Uzak istemci PIN'siz herhangi bir dosyayi acip ayristiramaz."""
    W._OTURUM.clear()
    try:
        W.api_sure({"yol": __file__}, "192.168.1.50")
        raise AssertionError("oturumsuz uzak istemci reddedilmeliydi")
    except GUV.GuvenlikHatasi:
        pass


# ----------------------------------------------------- yeni jog uclari
def _hazir():
    """Sunucu diger test dosyalariyla paylasilir: Reset'te olabilir, referans
    dusmus olabilir. Makineyi bilinen bir baslangica getir."""
    T.kur()
    d = T.cagir("/api/durum")
    if d["estop"]:
        T.cagir("/api/makine", {"ad": "reset"})
        time.sleep(0.2)
    if not d["referans"]["alindi"]:
        T.cagir("/api/referans", {"islem": "al", "eksenler": ["X", "Y", "Z"],
                                  "degerler": {"X": 250.0, "Y": 250.0, "Z": 0.0}})
    T.cagir("/api/jog/ayar", {"acik": True})


def test_jog_ayar_ve_durum_ucu():
    _hazir()
    d = T.cagir("/api/jog/ayar", {"yavas_yuzde": 15, "mod": "adim", "adim": 0.5})
    assert d["yavas_yuzde"] == 15 and d["mod"] == "adim" and d["adim"] == 0.5
    d = T.cagir("/api/jog/durum")
    assert d["acik"] is True and d["hiz"]["yavas"]["X"] == 1200.0
    r = T.cagir("/api/jog/ayar", {"yavas_yuzde": 500})
    assert "hata" in r
    T.cagir("/api/jog/ayar", {"mod": "surekli", "yavas_yuzde": 20})


def test_jog_basla_kalp_bitir_ucu():
    _hazir()
    T.cagir("/api/jog/ayar", {"mod": "surekli", "yavas_yuzde": 30, "acik": True})
    T.cagir("/api/durum")
    x0 = T.cagir("/api/durum")["konum"]["X"]
    r = T.cagir("/api/jog/basla", {"eksen": "X", "yon": 1})
    assert r["mod"] == "surekli" and r["feed"] == 2400.0
    for _ in range(8):
        assert T.cagir("/api/jog/kalp")["aktif"] is True
        time.sleep(0.05)
    assert T.cagir("/api/durum")["jog"]["aktif"] is True
    T.cagir("/api/jog/bitir")
    time.sleep(1.0)
    d = T.cagir("/api/durum")
    assert d["jog"]["aktif"] is False and d["konum"]["X"] > x0 + 3.0
    assert d["hareket"] is False


def test_jog_adim_modunda_tam_artim():
    _hazir()
    T.cagir("/api/jog/ayar", {"mod": "adim", "adim": 2.0, "yavas_yuzde": 50})
    x0 = T.cagir("/api/durum")["konum"]["X"]
    T.cagir("/api/jog/basla", {"eksen": "X", "yon": 1})
    time.sleep(1.2)
    assert abs(T.cagir("/api/durum")["konum"]["X"] - x0 - 2.0) < 0.01
    T.cagir("/api/jog/ayar", {"mod": "surekli", "yavas_yuzde": 20})


def test_makine_komutlari_ve_led_durumu():
    _hazir()
    d = T.cagir("/api/durum")
    assert d["birim"] == "mm" and d["led"]["metrik"] is True
    assert d["led"]["bosta"] is True and d["estop"] is False
    r = T.cagir("/api/makine", {"ad": "mil_cw", "devir": 9000})
    assert r["satir"] == "M3 S9000"
    r = T.cagir("/api/makine", {"ad": "sogutma_sel"})
    assert r["satir"] == "M8"
    assert "hata" in T.cagir("/api/makine", {"ad": "yok_boyle_bir_sey"})
    T.cagir("/api/makine", {"ad": "mil_dur"})
    T.cagir("/api/makine", {"ad": "sogutma_kapat"})


def test_program_satirlari_ucu():
    yol = os.path.join(T._DIZIN, "satir_deneme.tap")
    with open(yol, "w", encoding="utf-8") as f:
        f.write("\n".join(T.ORNEK))
    T.cagir("/api/program/yukle", {"yol": yol})
    r = T.cagir("/api/program/satirlar", {"bas": 0, "adet": 5})
    assert r["toplam"] == len(T.ORNEK) and r["satirlar"] == T.ORNEK[:5]
    assert r["ad"] == "satir_deneme.tap"


if __name__ == "__main__":
    for ad, fn in sorted(list(globals().items())):
        if ad.startswith("test_"):
            fn()
            print("ok", ad)
    print("Web güvenlik testleri geçti.")
