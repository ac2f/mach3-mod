#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kalibrasyon testleri
====================
Kumpasla adim sayisi (Steps per unit) ve bosluk (backlash) olcumu.

Bu ozellikte iki sinif hata olumcul olur:

1. **Ters formul.** Eksen komuttan FAZLA gidiyorsa adim sayisi FAZLADIR ve
   kucultulmelidir. Ters cevirmek hatayi iki katina cikarir; bu yuzden yon
   ayrica test edilir.
2. **Sessizce kabul edilen sacma olcum.** Kumpas kaymis, operator yanlis
   kutuya yazmis olabilir. 100 mm komuta 43 mm olcum "kalibrasyon" degildir;
   reddedilmelidir.

Ayrica kalibrasyon hareketleri de normal jog gibi guvenlik katmanindan
gecmelidir (referans, yumusak limit, adim siniri).
"""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mach3_kontrol import (ayarlar as AY, denetleyici as DEN,   # noqa: E402
                           guvenlik as GUV, kalibrasyon as KAL)


class _Baglanti:
    """Hareket etmeyen sahte tezgah: gonderilen satirlar kaydedilir."""

    tur = "sahte"
    bagli = True

    def __init__(self):
        self.d = {800: 0.0, 801: 0.0, 802: 0.0,
                  83: 500.0, 84: 500.0, 85: -20.0,
                  818: 0.0, 821: 100.0, 39: 0.0}
        self.gonderilen = []
        self.hareketli = False

    def dro(self, kod):
        return self.d.get(int(kod), 0.0)

    def dro_yaz(self, kod, deger):
        self.d[int(kod)] = float(deger)

    def kod(self, satir):
        self.gonderilen.append(satir)

    def dugme(self, kod):
        pass

    def estop(self):
        return False

    def hareket_ediyor(self):
        return self.hareketli

    def yetenekler(self):
        return {}

    def jog_durdur(self):
        return True

    def kapat(self):
        pass


def _den(hizli=True):
    d = tempfile.mkdtemp()
    a = AY.Ayarlar(os.path.join(d, "ayarlar.json"))
    a.guncelle({"guvenlik": {
        "yumusak_limit": {"X": {"min": 0.0, "max": 2000.0},
                          "Y": {"min": 0.0, "max": 2000.0},
                          "Z": {"min": -200.0, "max": 100.0}},
        "yumusak_limit_zorunlu": True, "limitler_dogrulandi": True,
        "limit_payi": 1.0, "max_jog_adim": 150.0, "max_jog_feed": 6000.0,
        "makine_z_taban": None, "max_dalis_derinlik": -150.0}})
    den = DEN.Denetleyici(a)
    den.baglanti = _Baglanti()
    den._oku()
    den.referans_al(("X", "Y", "Z"), degerler={"X": 500.0, "Y": 500.0,
                                               "Z": 50.0})
    if hizli:
        # Gercek bekleme mantigi ayri test ediliyor; akis testleri onu
        # beklemesin.
        den._hareket_bitmesini_bekle = lambda *a, **k: "test"
    return den


def _olcum_dongusu(den, olcumler):
    """Oturumu bastan sona surer; her tur icin verilen okumayi girer."""
    for i, v in enumerate(olcumler):
        den.kalibrasyon_adim("bosluk_al" if i == 0 else "geri")
        den.kalibrasyon_bekle()
        den.kalibrasyon_adim("olc")
        den.kalibrasyon_bekle()
        den.kalibrasyon_olcum(b=v)


# ==================================================================
# Matematik
# ==================================================================
def test_fazla_giden_eksende_adim_sayisi_kuculur():
    """100 mm komuta 100.5 mm gidiyorsa adim sayisi FAZLADIR."""
    yeni = KAL.yeni_adim_basi(1600.0, 100.0, 100.5)
    assert yeni < 1600.0
    assert abs(yeni - 1600.0 * 100.0 / 100.5) < 1e-9


def test_az_giden_eksende_adim_sayisi_buyur():
    yeni = KAL.yeni_adim_basi(1600.0, 100.0, 99.5)
    assert yeni > 1600.0


def test_yeni_deger_gercekten_duzeltir():
    """Yeni adim sayisiyla ayni komut tam mesafeyi vermeli.

    Mach3 `adim x komut` darbe gonderir; gercek yol
    `gonderilen_darbe / gercek_adim`dir.
    """
    gercek = 1594.0                     # makinenin gercek adim/mm degeri
    mach3 = 1600.0                      # Mach3'te yazan (yanlis) deger
    olculen = 100.0 * mach3 / gercek    # bu ayarla 100 mm komutta gidilen yol
    yeni = KAL.yeni_adim_basi(mach3, 100.0, olculen)
    assert abs(yeni - gercek) < 1e-6


def test_eski_deger_yoksa_carpan_verilir():
    assert KAL.yeni_adim_basi(None, 100.0, 100.2) is None
    assert abs(KAL.carpan(100.0, 100.2) - 100.0 / 100.2) < 1e-12


def test_sifir_olcum_reddedilir():
    for fn in (lambda: KAL.yeni_adim_basi(1600.0, 100.0, 0.0),
               lambda: KAL.carpan(100.0, -5.0)):
        try:
            fn()
            raise AssertionError("sifir/negatif olcum reddedilmeliydi")
        except ValueError:
            pass


def test_tolerans_uzun_olcumde_buyur():
    assert KAL.tolerans(200.0, 0.01) > KAL.tolerans(10.0, 0.01)
    # kisa mesafede taban, kumpas cozunurlugunun 2 kati
    assert abs(KAL.tolerans(10.0, 0.01) - 0.02) < 1e-12
    # 100 mm'de 200 ppm = 0.02 mm (kumpas tabaniyla ayni)
    assert abs(KAL.tolerans(100.0, 0.01) - 0.02) < 1e-12


# ==================================================================
# Oturum: hareket plani
# ==================================================================
def test_bosluk_olcum_yonunde_toplanir():
    """Once TERS yone, sonra olcum yonune: bosluk olcege karismaz."""
    o = KAL.Oturum("X", "olcek", komut_mm=100.0, on_yaklasma=5.0, yon=1)
    plan = o.hareket_plani("bosluk_al")
    assert [round(m, 6) for _, m, _ in plan] == [-5.0, 5.0]


def test_olcek_olcumu_tek_yonde_gider():
    o = KAL.Oturum("X", "olcek", komut_mm=100.0, yon=-1)
    plan = o.hareket_plani("olc")
    assert len(plan) == 1 and plan[0][1] == -100.0


def test_bosluk_olcumu_ayni_noktaya_ters_yonden_gelir():
    o = KAL.Oturum("Y", "bosluk", on_yaklasma=4.0, yon=1)
    plan = o.hareket_plani("olc")
    assert [round(m, 6) for _, m, _ in plan] == [4.0, -4.0]
    # Net yer degistirme sifir: ayni fiziksel noktaya donulur
    assert abs(sum(m for _, m, _ in plan)) < 1e-9


def test_tekrar_basa_donunce_bosluk_yeniden_alinir():
    o = KAL.Oturum("X", "olcek", komut_mm=100.0, on_yaklasma=5.0, yon=1)
    plan = o.hareket_plani("geri")
    assert [round(m, 6) for _, m, _ in plan] == [-105.0, 5.0]
    assert abs(sum(m for _, m, _ in plan) + 100.0) < 1e-9   # basa donuldu


# ==================================================================
# Oturum: olcum kabulu
# ==================================================================
def test_sacma_olcum_reddedilir():
    o = KAL.Oturum("X", "olcek", komut_mm=100.0)
    for deger in (43.0, 260.0):
        try:
            o.ekle_olcum(b=deger)
            raise AssertionError(f"{deger} mm kabul edilmemeliydi")
        except ValueError as e:
            assert "fazla buyuk" in str(e).lower(), str(e)
    assert o.olcumler == []


def test_iki_okuma_farki_kullanilir():
    o = KAL.Oturum("X", "olcek", komut_mm=100.0)
    o.ekle_olcum(a=12.345, b=112.470)
    assert abs(o.olcumler[0]["olculen"] - 100.125) < 1e-9


def test_bosluk_yaklasmadan_buyuk_olamaz():
    o = KAL.Oturum("X", "bosluk", on_yaklasma=5.0)
    try:
        o.ekle_olcum(b=6.0)
        raise AssertionError("yaklasmadan buyuk bosluk reddedilmeliydi")
    except ValueError as e:
        assert "buyuk olamaz" in str(e)


def test_tekrar_bitince_asama_biter():
    o = KAL.Oturum("X", "olcek", komut_mm=100.0, tekrar=2)
    o.ekle_olcum(b=100.1)
    assert o.asama == KAL.TEKRAR
    o.ekle_olcum(b=100.12)
    assert o.asama == KAL.BITTI


def test_dagilan_olcumler_guvenilmez_isaretlenir():
    o = KAL.Oturum("X", "olcek", komut_mm=100.0, tekrar=3, cozunurluk=0.01)
    for v in (100.10, 100.30, 100.02):
        o.ekle_olcum(b=v)
    s = o.sonuc()
    assert s["guvenilir"] is False
    assert any("yayilim" in u.lower() for u in s["uyarilar"])


def test_tutarli_olcumler_guvenilir():
    o = KAL.Oturum("X", "olcek", komut_mm=100.0, tekrar=3, adim_basi=1600.0)
    for v in (100.12, 100.13, 100.12):
        o.ekle_olcum(b=v)
    s = o.sonuc()
    assert s["guvenilir"] is True
    assert s["yeni_adim_basi"] < 1600.0


# ==================================================================
# Mach3'e girilecek degerler
# ==================================================================
def test_talimat_steps_per_kutusunu_gosterir():
    o = KAL.Oturum("X", "olcek", komut_mm=100.0, tekrar=1, adim_basi=1600.0)
    o.ekle_olcum(b=100.5)
    s = o.sonuc()
    t = s["talimat"][0]
    assert "Motor Tuning" in t["pencere"]
    assert t["yer"] == "X Axis"
    alan = t["alanlar"][0]
    assert alan["ad"] == "Steps per"
    assert alan["eski"] == 1600.0
    assert abs(alan["yeni"] - 1600.0 * 100.0 / 100.5) < 0.001
    assert any("SAVE AXIS SETTINGS" in n for n in t["notlar"])
    assert "Steps per" in s["talimat_metni"]


def test_eski_deger_yoksa_talimat_carpani_soyler():
    o = KAL.Oturum("Y", "olcek", komut_mm=100.0, tekrar=1)
    o.ekle_olcum(b=100.5)
    s = o.sonuc()
    assert s["talimat"][0]["alanlar"][0]["yeni"] is None
    assert any("carpip" in n for n in s["talimat"][0]["notlar"])


def test_bosluk_talimati_backlash_penceresini_gosterir():
    o = KAL.Oturum("Z", "bosluk", on_yaklasma=5.0, tekrar=1)
    o.ekle_olcum(b=0.043)
    s = o.sonuc()
    t = s["talimat"][0]
    assert "Backlash" in t["pencere"]
    assert t["alanlar"][0]["yeni"] == 0.043
    assert any(a["ad"] == "Backlash Enabled" for a in t["alanlar"])


# ==================================================================
# Denetleyici: guvenlik
# ==================================================================
def test_referanssiz_kalibrasyon_baslamaz():
    den = _den()
    den.referans_birak()
    try:
        den.kalibrasyon_basla("X", "olcek")
        raise AssertionError("referanssiz kalibrasyon engellenmeliydi")
    except GUV.GuvenlikHatasi as e:
        assert "referans" in str(e).lower()


def test_hareket_sirasinda_kalibrasyon_baslamaz():
    den = _den()
    den.baglanti.hareketli = True
    try:
        den.kalibrasyon_basla("X", "olcek")
        raise AssertionError("hareket sirasinda engellenmeliydi")
    except GUV.GuvenlikHatasi as e:
        assert "hareket" in str(e).lower()


def test_jog_sinirini_asan_mesafe_onceden_reddedilir():
    """Olcumun ortasinda degil, BASLARKEN soylenmeli."""
    den = _den()
    try:
        den.kalibrasyon_basla("X", "olcek", mesafe=149.0, on_yaklasma=5.0)
        raise AssertionError("jog sinirini asan olcum engellenmeliydi")
    except GUV.GuvenlikHatasi as e:
        assert "jog adimi" in str(e), str(e)


def test_besleme_siniri_asilamaz():
    den = _den()
    try:
        den.kalibrasyon_basla("X", "olcek", feed=9000.0)
        raise AssertionError("besleme siniri asilmamaliydi")
    except GUV.GuvenlikHatasi as e:
        assert "besleme" in str(e).lower()


def test_yanlis_sirada_hareket_reddedilir():
    den = _den()
    den.kalibrasyon_basla("X", "olcek", mesafe=100.0)
    try:
        den.kalibrasyon_adim("olc")      # once bosluk alinmali
        raise AssertionError("sirasiz adim engellenmeliydi")
    except GUV.GuvenlikHatasi as e:
        assert "asamada yapilamaz" in str(e)


def test_hareket_yapmadan_olcum_girilemez():
    den = _den()
    den.kalibrasyon_basla("X", "olcek", mesafe=100.0)
    try:
        den.kalibrasyon_olcum(b=100.1)
        raise AssertionError("hareketsiz olcum engellenmeliydi")
    except GUV.GuvenlikHatasi as e:
        assert "once olcum hareketini" in str(e).lower()


def test_limit_disina_cikan_kalibrasyon_hareketi_durur():
    """Kafa tezgah kenarindayken olcum hareketi guvenlikten gecmemeli."""
    den = _den()
    den.referans_al(("X",), degerler={"X": 1950.0})    # ust limit 2000
    den.kalibrasyon_basla("X", "olcek", mesafe=100.0, yon=1)
    den.kalibrasyon_adim("bosluk_al")                  # 1945 → 1950: gecer
    den.kalibrasyon_bekle()
    assert den.kalibrasyon_durum()["oturum"]["asama"] == KAL.OKUMA_A
    den.kalibrasyon_adim("olc")                        # 2050: tezgah disi
    den.kalibrasyon_bekle()
    o = den.kalibrasyon_durum()["oturum"]
    assert o["asama"] == KAL.HAZIR          # bastan alinmali
    # Sebep de yaziyor olmali; yoksa operator neden durdugunu bilemez
    assert "yumusak limit" in o["hata"].lower(), o["hata"]


# ==================================================================
# Denetleyici: uctan uca akis
# ==================================================================
def test_olcek_akisi_dogru_g_kod_gonderir():
    den = _den()
    den.kalibrasyon_basla("X", "olcek", mesafe=100.0, feed=300.0, tekrar=1,
                          on_yaklasma=5.0, adim_basi=1600.0)
    den.baglanti.gonderilen.clear()
    den.kalibrasyon_adim("bosluk_al")
    den.kalibrasyon_bekle()
    assert den.kalibrasyon_durum()["oturum"]["asama"] == KAL.OKUMA_A
    hareket = [s for s in den.baglanti.gonderilen if s.startswith("G1")]
    assert hareket == ["G1 X-5.0000 F300.0", "G1 X5.0000 F300.0"]

    den.baglanti.gonderilen.clear()
    den.kalibrasyon_adim("olc")
    den.kalibrasyon_bekle()
    assert den.kalibrasyon_durum()["oturum"]["asama"] == KAL.OKUMA_B
    assert [s for s in den.baglanti.gonderilen if s.startswith("G1")] == \
        ["G1 X100.0000 F300.0"]
    # Bagil mod acilip kapaniyor: kalici G91 birakmak felaket olur
    assert den.baglanti.gonderilen[0] == "G91"
    assert den.baglanti.gonderilen[-1] == "G90"

    den.kalibrasyon_olcum(b=100.5)
    s = den.kalibrasyon_durum()["son_sonuc"]
    assert s["olcum_sayisi"] == 1
    assert abs(s["hata_mm"] - 0.5) < 1e-9
    assert abs(s["yeni_adim_basi"] - 1600.0 * 100.0 / 100.5) < 0.001


def test_uc_tekrarli_akis_ortalama_alir():
    den = _den()
    den.kalibrasyon_basla("X", "olcek", mesafe=100.0, tekrar=3,
                          adim_basi=1600.0)
    _olcum_dongusu(den, [100.10, 100.12, 100.11])
    s = den.kalibrasyon_durum()["son_sonuc"]
    assert s["olcum_sayisi"] == 3
    assert abs(s["olculen_ort"] - 100.11) < 1e-6
    assert s["guvenilir"] is True


def test_bosluk_akisi():
    den = _den()
    den.kalibrasyon_basla("Y", "bosluk", on_yaklasma=5.0, tekrar=1)
    den.kalibrasyon_adim("bosluk_al")
    den.kalibrasyon_bekle()
    den.baglanti.gonderilen.clear()
    den.kalibrasyon_adim("olc")
    den.kalibrasyon_bekle()
    assert [s for s in den.baglanti.gonderilen if s.startswith("G1")] == \
        ["G1 Y5.0000 F300.0", "G1 Y-5.0000 F300.0"]
    den.kalibrasyon_olcum(b=0.045)
    s = den.kalibrasyon_durum()["son_sonuc"]
    assert s["tur"] == "bosluk"
    assert abs(s["bosluk_mm"] - 0.045) < 1e-9


def test_erken_bitirme_alinan_olcumlerle_sonuc_verir():
    den = _den()
    den.kalibrasyon_basla("X", "olcek", mesafe=100.0, tekrar=5,
                          adim_basi=1600.0)
    den.kalibrasyon_adim("bosluk_al")
    den.kalibrasyon_bekle()
    den.kalibrasyon_adim("olc")
    den.kalibrasyon_bekle()
    den.kalibrasyon_olcum(b=100.2)
    den.kalibrasyon_bitir()
    s = den.kalibrasyon_durum()["son_sonuc"]
    assert s["olcum_sayisi"] == 1
    assert any("tek olcum" in u.lower() for u in s["uyarilar"])


# ==================================================================
# Uygulama: kayit + referansin dusmesi
# ==================================================================
def test_uygulama_yeni_degeri_kaydeder_ve_referansi_dusurur():
    """Adim sayisi degisince makine koordinatinin OLCEGI degisir.

    Eski referansla olculen yumusak limitler artik yanlis yeri gosterir;
    referans MUTLAKA dusmelidir.
    """
    den = _den()
    den.kalibrasyon_basla("X", "olcek", mesafe=100.0, tekrar=1,
                          adim_basi=1600.0)
    _olcum_dongusu(den, [100.5])
    r = den.kalibrasyon_uygula()
    assert den.referans["X"] is None
    assert den._tezgah["X"] is None
    assert any("referans" in u.lower() for u in r["uyarilar"])
    kayit = den.ayarlar["kalibrasyon"]["adim_basi"]["X"]
    assert abs(kayit - 1600.0 * 100.0 / 100.5) < 0.001
    # Referanssiz kaldigimiz icin hareket de engellenir (fail-closed)
    try:
        den.jog_adim("X", 1.0)
        raise AssertionError("referanssiz jog engellenmeliydi")
    except GUV.GuvenlikHatasi:
        pass


def test_bosluk_uygulamasi_referansi_dusurmez():
    """Bosluk telafisi olcegi degistirmez; referans gecerli kalir."""
    den = _den()
    den.kalibrasyon_basla("Y", "bosluk", on_yaklasma=5.0, tekrar=1)
    den.kalibrasyon_adim("bosluk_al")
    den.kalibrasyon_bekle()
    den.kalibrasyon_adim("olc")
    den.kalibrasyon_bekle()
    den.kalibrasyon_olcum(b=0.05)
    den.kalibrasyon_uygula()
    assert den.referans["Y"] is not None
    assert den.ayarlar["kalibrasyon"]["bosluk"]["Y"] == 0.05


def test_eski_deger_bilinmiyorsa_uygulama_deger_ister():
    den = _den()
    den.kalibrasyon_basla("X", "olcek", mesafe=100.0, tekrar=1)
    _olcum_dongusu(den, [100.5])
    try:
        den.kalibrasyon_uygula()
        raise AssertionError("deger olmadan uygulanmamaliydi")
    except GUV.GuvenlikHatasi as e:
        assert "steps per" in str(e).lower()
    den.kalibrasyon_uygula(deger=1592.04)
    assert den.ayarlar["kalibrasyon"]["adim_basi"]["X"] == 1592.04


def test_gecmis_kaydedilir():
    den = _den()
    den.kalibrasyon_basla("X", "olcek", mesafe=100.0, tekrar=1,
                          adim_basi=1600.0)
    _olcum_dongusu(den, [100.5])
    den.kalibrasyon_uygula()
    g = den.ayarlar["kalibrasyon"]["gecmis"]
    assert len(g) == 1 and g[0]["eksen"] == "X" and g[0]["tur"] == "olcek"


def test_ayar_kaydi_dogrular():
    den = _den()
    den.kalibrasyon_ayar_kaydet({"adim_basi": {"X": 1600.0, "Z": None},
                                 "olcum_mesafe": 80.0, "tekrar": 99})
    k = den.ayarlar["kalibrasyon"]
    assert k["adim_basi"]["X"] == 1600.0
    assert k["adim_basi"]["Z"] is None
    assert k["olcum_mesafe"] == 80.0
    assert k["tekrar"] == 20                    # ust sinira kirpilir
    for yama in ({"adim_basi": {"X": "abc"}}, {"olcum_mesafe": 0},
                 {"adim_basi": {"Y": -5}}):
        try:
            den.kalibrasyon_ayar_kaydet(yama)
            raise AssertionError(f"gecersiz yama kabul edildi: {yama}")
        except GUV.GuvenlikHatasi:
            pass


# ==================================================================
# Hareketin bitmesini bekleme
# ==================================================================
def test_hareket_bitene_kadar_beklenir():
    den = _den(hizli=False)
    den.baglanti.hareketli = True
    import threading
    threading.Timer(0.6, lambda: setattr(den.baglanti, "hareketli",
                                         False)).start()
    t0 = time.time()
    assert den._hareket_bitmesini_bekle(0.2) == "hareket"
    assert time.time() - t0 >= 0.6


def test_ismoving_desteklenmiyorsa_sureye_guvenilir():
    den = _den(hizli=False)
    den.baglanti.hareket_ediyor = lambda: None
    t0 = time.time()
    assert den._hareket_bitmesini_bekle(0.3) == "sure"
    assert time.time() - t0 >= 1.8          # 0.35 + 0.3 + 1.5 pay


def test_bitmeyen_hareket_zaman_asimina_ugrar():
    den = _den(hizli=False)
    den.baglanti.hareketli = True
    try:
        den._hareket_bitmesini_bekle(0.1, ek_sn=0.3)
        raise AssertionError("zaman asimi beklenirdi")
    except GUV.GuvenlikHatasi as e:
        assert "bitmedi" in str(e)


if __name__ == "__main__":
    for ad, fn in sorted(list(globals().items())):
        if ad.startswith("test_"):
            fn()
            print("ok", ad)
    print("Kalibrasyon testleri gecti.")
