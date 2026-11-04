#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Surekli jog testleri (Mach3 tepkiselligi)
=========================================
Kullanicinin bildirdigi sikayet: "basili tutunca hareket purussuz degil,
biraktigim an durmuyor".

Uc ayri sebep vardi, ucu de burada kilitleniyor:

1. **Adim uzunlugu sabit sinirliydi (5 mm).** 4000 mm/dk'da 5 mm 0.075 sn
   surer, ama zincirdeki bir sonraki adim 0.15 sn sonra gelirdi: eksen her
   adim arasinda DURUYORDU. Artik adim = feed/60 * tik * ortusme, yani her
   adim bir sonrakine kadar yetisir.
2. **Her adim UC COM cagrisi gonderiyordu** ("G91", "G1 ...", "G90"). Her
   cagri bir tur gecikme demektir. Artik G91 basta bir kez, G90 sonda bir
   kez gonderilir; adim basina tek satir kalir.
3. **Birakinca kuyruk bosaltilmiyordu**; eksen kalan adimi tamamlayana
   kadar suruyordu. Artik Feed Hold + Stop ile kuyruk bosaltilir.

Ayrica G90'in her kosulda geri gelmesi HAYATIDIR: G91 acik unutulursa
sonraki mutlak komutlar artimli calisir ve makine bambaska bir yere gider.
"""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mach3_kontrol import (ayarlar as AY, denetleyici as DEN,   # noqa: E402
                           guvenlik as GUV)


class _Baglanti:
    tur = "sahte"
    bagli = True

    def __init__(self):
        self.d = {800: 0.0, 801: 0.0, 802: 0.0,
                  83: 500.0, 84: 500.0, 85: -20.0,
                  818: 0.0, 821: 100.0, 39: 0.0}
        self.gonderilen = []
        self.dugmeler = []

    def dro(self, kod):
        return self.d.get(int(kod), 0.0)

    def dro_yaz(self, kod, deger):
        self.d[int(kod)] = float(deger)

    def kod(self, satir):
        self.gonderilen.append(satir)

    def dugme(self, kod):
        self.dugmeler.append(int(kod))

    def estop(self):
        return False

    def hareket_ediyor(self):
        return False

    def yetenekler(self):
        return {}

    def jog_durdur(self):
        return True

    def kapat(self):
        pass


def _den(**guvenlik):
    d = tempfile.mkdtemp()
    a = AY.Ayarlar(os.path.join(d, "ayarlar.json"))
    g = {"yumusak_limit": {"X": {"min": 0.0, "max": 2000.0},
                           "Y": {"min": 0.0, "max": 2000.0},
                           "Z": {"min": -200.0, "max": 100.0}},
         "yumusak_limit_zorunlu": True, "limitler_dogrulandi": True,
         "limit_payi": 1.0, "max_jog_adim": 100.0, "max_jog_feed": 8000.0,
         "makine_z_taban": None, "max_dalis_derinlik": -150.0,
         # Testte kalp atisi gondermiyoruz; bekci jogu erken kesmesin
         "jog_kalp_atisi_ms": 5000, "max_surekli_jog_sn": 30.0}
    g.update(guvenlik)
    a.guncelle({"guvenlik": g})
    den = DEN.Denetleyici(a)
    den.baglanti = _Baglanti()
    den._oku()
    den.referans_al(("X", "Y", "Z"), degerler={"X": 500.0, "Y": 500.0,
                                               "Z": 50.0})
    return den


def _g1ler(den):
    return [s for s in den.baglanti.gonderilen if s.startswith("G1 ")]


def _mesafe(satir, eksen="X"):
    return abs(float(satir.split(eksen, 1)[1].split(" ", 1)[0]))


def _jog(den, eksen="X", yon=1, sure=0.32, **kw):
    den.jog_basla(eksen, yon, **kw)
    time.sleep(sure)
    den.jog_bitir()


# ==================================================================
# 1) Adim uzunlugu beslemeye gore olceklenir (akicilik)
# ==================================================================
def test_adim_uzunlugu_beslemeyle_buyur():
    """REGRESYON: eskiden adim 5 mm ile siniriydi ve eksen takiliyordu."""
    den = _den(jog_tik_ms=80, jog_ortusme=1.35)
    _jog(den, feed=4000.0)
    g1 = _g1ler(den)
    assert g1, "hic hareket gonderilmedi"
    adim = _mesafe(g1[0])
    beklenen = 4000.0 / 60.0 * 0.08 * 1.35            # ~7.2 mm
    assert abs(adim - beklenen) < 0.01, (adim, beklenen)
    assert adim > 5.0, "eski 5 mm siniri geri gelmis"


def test_adim_bir_sonraki_tike_kadar_yetisir():
    """Ortusme > 1: adimin suresi tik suresinden UZUN olmali.

    Kisa kalirsa eksen adimlar arasinda durur - "tik tik" hissiyati.
    """
    for feed in (300.0, 1200.0, 6000.0):
        den = _den(jog_tik_ms=80, jog_ortusme=1.35)
        _jog(den, feed=feed, sure=0.25)
        adim = _mesafe(_g1ler(den)[0])
        adim_sn = adim / (feed / 60.0)
        assert adim_sn > 0.08, (feed, adim_sn)


def test_adim_max_jog_adimini_asamaz():
    den = _den(max_jog_adim=4.0, jog_tik_ms=80, jog_ortusme=1.35)
    _jog(den, feed=8000.0, sure=0.2)
    assert _mesafe(_g1ler(den)[0]) <= 4.0 + 1e-9


def test_yavas_ve_hizli_beslemeler_ayarlardan_gelir():
    den = _den(jog_yavas_feed=500.0, jog_hizli_feed=3500.0)
    assert den.jog_feed(False) == 500.0
    assert den.jog_feed(True) == 3500.0
    # Ust sinir her zaman uygulanir
    den.ayarlar.guncelle({"guvenlik": {"max_jog_feed": 1000.0}})
    assert den.jog_feed(True) == 1000.0


# ==================================================================
# 2) Adim basina tek MDI satiri
# ==================================================================
def test_g91_bir_kez_gonderilir():
    """REGRESYON: her adimda G91/G1/G90 gonderiliyordu (3 kat COM trafigi)."""
    den = _den(jog_tik_ms=60)
    _jog(den, feed=1200.0, sure=0.3)
    g = den.baglanti.gonderilen
    assert g.count("G91") == 1, g
    assert g[0] == "G91"
    assert len(_g1ler(den)) >= 3, g
    # G1'ler arasinda G90 OLMAMALI: modal kip bir kez kurulur
    ilk_g1 = g.index(next(s for s in g if s.startswith("G1 ")))
    son_g1 = len(g) - 1 - g[::-1].index(next(s for s in reversed(g)
                                             if s.startswith("G1 ")))
    assert "G90" not in g[ilk_g1:son_g1], g


def test_jog_bitince_g90_geri_gelir():
    """G91 acik kalirsa sonraki mutlak komutlar artimli calisir - felaket."""
    den = _den()
    _jog(den, feed=1000.0)
    assert den.baglanti.gonderilen[-1] == "G90", den.baglanti.gonderilen


def test_hareket_reddedilse_bile_g90_geri_gelir():
    """Limit ihlalinde dongu kirilir; modal kip yine de geri alinmali."""
    den = _den()
    den.referans_al(("X",), degerler={"X": 1998.0})    # ust limit 2000
    den.jog_basla("X", 1, feed=1000.0)
    time.sleep(0.3)
    den.jog_bitir()
    g = den.baglanti.gonderilen
    if "G91" in g:                       # ilk adim gectiyse
        assert g[-1] == "G90", g


# ==================================================================
# 3) Birakinca kuyruk bosaltilir (aninda durma)
# ==================================================================
def test_birakinca_feed_hold_ve_stop_gonderilir():
    den = _den()
    _jog(den, feed=2000.0)
    fh = den.kodlar.buton["feed_hold"]
    st = den.kodlar.buton["stop"]
    assert fh in den.baglanti.dugmeler, den.baglanti.dugmeler
    assert st in den.baglanti.dugmeler, den.baglanti.dugmeler
    # Sira onemli: once yumusak yavaslama, sonra kuyrugu at
    assert (den.baglanti.dugmeler.index(fh)
            < den.baglanti.dugmeler.index(st))


def test_durdurma_kapatilabilir():
    den = _den(jog_birakinca_durdur=False)
    _jog(den, feed=2000.0)
    assert den.kodlar.buton["feed_hold"] not in den.baglanti.dugmeler
    assert den.baglanti.gonderilen[-1] == "G90"   # modal kip yine de geri


# ==================================================================
# 4) Canli hiz degisimi (basili tutarken Shift)
# ==================================================================
def test_shift_jogu_kesmeden_hizi_degistirir():
    den = _den(jog_tik_ms=60, jog_yavas_feed=600.0, jog_hizli_feed=4000.0)
    den.jog_basla("X", 1, hizli=False)
    time.sleep(0.15)
    onceki = len(_g1ler(den))
    den.jog_hiz(hizli=True)
    time.sleep(0.2)
    den.jog_bitir()
    g1 = _g1ler(den)
    assert len(g1) > onceki, "hiz degisimi jogu kesmis olmamali"
    assert "F600" in g1[0]
    assert "F4000" in g1[-1], g1
    # Zincir kesilmediyse arada G90/G91 olmaz
    assert den.baglanti.gonderilen.count("G91") == 1


def test_hiz_degisimi_ust_siniri_asamaz():
    den = _den(max_jog_feed=1500.0, jog_hizli_feed=9000.0)
    assert den.jog_hiz(hizli=True)["feed"] == 1500.0


# ==================================================================
# 5) Guvenlik
# ==================================================================
def test_program_calisirken_jog_reddedilir():
    """Jog birakinca Feed Hold/Stop gonderir; bu, calisan programi keserdi."""
    den = _den()
    den.calisiyor = True
    try:
        den.jog_basla("X", 1)
        raise AssertionError("program calisirken jog engellenmeliydi")
    except GUV.GuvenlikHatasi as e:
        assert "program" in str(e).lower()


def test_referanssiz_surekli_jog_engellenir():
    den = _den()
    den.referans_birak()
    try:
        den.jog_basla("X", 1)
        raise AssertionError("referanssiz jog engellenmeliydi")
    except GUV.GuvenlikHatasi as e:
        assert "referans" in str(e).lower()


def test_limit_disina_cikan_zincir_durur():
    """Denetim, OKUNAN degil GONDERILEN konuma gore yapilmali.

    DRO yoklamasi 100 ms geriden gelir; adimlar uzun oldugu icin bu surede
    gonderilen adimlar limiti sessizce asabilirdi. Zincir, taahhut edilen
    konumu kendisi sayar.
    """
    den = _den(jog_tik_ms=60)
    den.referans_al(("X",), degerler={"X": 1990.0})
    _jog(den, feed=3000.0, sure=0.5)
    # Kullanilabilir yol 9 mm (2000 - 1 pay - 1990); tek adim 4.05 mm.
    # Sahte tezgah hic kimildamadigi icin DRO da hic ilerlemez: koruma
    # yalnizca kendi saydigi mesafeyle calisir.
    toplam = sum(_mesafe(s) for s in _g1ler(den))
    assert toplam <= 9.0 + 1e-6, toplam
    assert toplam > 4.0, "hic hareket gonderilmemis"


def test_kalp_atisi_kesilirse_durur():
    den = _den(jog_kalp_atisi_ms=120)
    den.jog_basla("X", 1, feed=1000.0)
    time.sleep(0.6)                       # kalp atisi gonderilmiyor
    assert den.jog_bilgi["aktif"] is False
    assert den.baglanti.gonderilen[-1] == "G90"


if __name__ == "__main__":
    for ad, fn in sorted(list(globals().items())):
        if ad.startswith("test_"):
            fn()
            print("ok", ad)
    print("Jog testleri gecti.")
