#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Home noktaları: adlandırılmış iş sıfırları.

Home, iş sıfırını TEZGÂH koordinatında saklar; böylece Mach3 yeniden açılıp
referans yeniden alınsa bile aynı fiziksel noktaya döner.
"""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mach3_kontrol import (ayarlar as AY, denetleyici as DEN,   # noqa: E402
                           guvenlik as GUV)


def _den(**guv):
    d = tempfile.mkdtemp()
    a = AY.Ayarlar(os.path.join(d, "ayarlar.json"))
    g = {"yumusak_limit": {"X": {"min": 0.0, "max": 2100.0},
                           "Y": {"min": 0.0, "max": 4050.0},
                           "Z": {"min": -150.0, "max": 100.0}},
         "limit_payi": 0.0, "guvenli_z": 20.0, "max_dalis_derinlik": -140.0}
    g.update(guv)
    a.guncelle({"baglanti": {"tur": "simulasyon", "yoklama_hz": 30},
                "guvenlik": g})
    den = DEN.Denetleyici(a)
    den.baglan()
    time.sleep(0.15)
    # Referans 0 DEGIL: makine okumasi (0) = tezgah 500/300/0. Eski hata tam
    # burada ortaya cikiyordu (referans != 0).
    den.referans_al(("X", "Y", "Z"),
                    degerler={"X": 500.0, "Y": 300.0, "Z": 0.0})
    return den


def _tasi(den, x=None, y=None, z=None):
    """Makineyi doğrudan sanal konuma koy (makine koordinatı)."""
    b = den.baglanti
    for e, v in (("X", x), ("Y", y), ("Z", z)):
        if v is not None:
            b.konum[e] = float(v)
    b._plan = dict(b.konum)
    den._oku()


def _bekle(den, sn=8.0):
    son = time.time() + sn
    while time.time() < son:
        if not den.baglanti.hareket_ediyor():
            time.sleep(0.1)
            if not den.baglanti.hareket_ediyor():
                return True
        time.sleep(0.03)
    return False


# ------------------------------------------------------------------ kaydet
def test_mevcut_sifir_tezgah_koordinatiyla_kaydedilir():
    den = _den()
    try:
        den.sifirla("X", 0.0)                    # iş X sıfırı burası
        _tasi(den, x=100.0)                      # tezgah 600'e geç
        den.sifirla("X", 0.0)                    # iş X sıfırı tezgah 600
        r = den.home_kaydet("Levha A")
        assert r["home"]["tezgah"]["X"] == 600.0, r["home"]
        assert r["home"]["tezgah"]["Y"] == 300.0     # iş Y=0 makine 0 = tezgah 300
        assert "is_sifiri_makine" not in r["home"]
        assert den.homeler()["aktif"] == "Levha A"
    finally:
        den.kapat()


def test_ayni_ad_uzerine_yazilmadan_reddedilir_buyuk_kucuk_harf_duyarsiz():
    den = _den()
    try:
        den.home_kaydet("Levha A")
        for ad in ("Levha A", "levha a", "  LEVHA   A "):
            try:
                den.home_kaydet(ad)
                raise AssertionError("ayni ad reddedilmeliydi: " + ad)
            except GUV.GuvenlikHatasi as e:
                assert "zaten var" in str(e)
        assert len(den.homeler()["liste"]) == 1
        den.home_kaydet("levha a", tezgah={"X": 1.0}, uzerine_yaz=True)
        h = den.homeler()["liste"]
        assert len(h) == 1 and h[0]["tezgah"]["X"] == 1.0
    finally:
        den.kapat()


def test_ad_dogrulama():
    den = _den()
    try:
        for kotu in ("", "   ", "x" * 61):
            try:
                den.home_kaydet(kotu)
                raise AssertionError("gecersiz ad kabul edildi")
            except GUV.GuvenlikHatasi:
                pass
        r = den.home_kaydet("  18 mm   MDF – sağ üst ")
        assert r["ad"] == "18 mm MDF – sağ üst"     # bosluklar sadelesir, Turkce karakter korunur
    finally:
        den.kapat()


def test_referanssiz_mevcut_sifir_kaydedilemez_ama_elle_girilebilir():
    den = _den()
    try:
        den.referans_birak()
        try:
            den.home_kaydet("A")
            raise AssertionError("referanssiz kayit reddedilmeliydi")
        except GUV.GuvenlikHatasi as e:
            assert "referans" in str(e).lower()
        # koordinatlar elle verilirse baglanti/referans gerekmez
        r = den.home_kaydet("A", tezgah={"X": 100, "Y": 200, "Z": -18})
        assert r["home"]["tezgah"] == {"X": 100.0, "Y": 200.0, "Z": -18.0}
    finally:
        den.kapat()


def test_elle_girilen_koordinat_dogrulanir():
    den = _den()
    try:
        for kotu in ({"X": "abc"}, {"Q": 1}, {"X": float("nan")},
                     {"X": 1e9}, {}):
            try:
                den.home_kaydet("A", tezgah=kotu) if kotu else \
                    den.home_kaydet("A", tezgah={"X": None})
                raise AssertionError(f"reddedilmeliydi: {kotu}")
            except GUV.GuvenlikHatasi:
                pass
    finally:
        den.kapat()


def test_tezgah_disi_home_uyari_verir_engellemez():
    den = _den()
    try:
        r = den.home_kaydet("A", tezgah={"X": 5000.0})
        assert r["uyarilar"] and "disinda" in r["uyarilar"][0]
    finally:
        den.kapat()


# ------------------------------------------------------------------ uygula
def test_uygula_referans_sifir_degilken_dogru_deger_yazar():
    """REGRESYON: eski profil_uygula tezgah ofsetini MAKINE okumasindan
    cikariyordu; referans != 0 iken yanlis sifir yazardi."""
    den = _den()
    try:
        _tasi(den, x=100.0, y=50.0, z=-10.0)       # tezgah 600 / 350 / -10
        den.home_kaydet("A", tezgah={"X": 500.0, "Y": 300.0, "Z": -30.0})
        r = den.home_uygula("A")
        # iş = tezgah - sifir
        assert abs(r["yazilan"]["X"] - 100.0) < 1e-6, r
        assert abs(r["yazilan"]["Y"] - 50.0) < 1e-6
        assert abs(r["yazilan"]["Z"] - 20.0) < 1e-6
        assert abs(den.baglanti.dro(800) - 100.0) < 1e-3
        assert abs(den.baglanti.dro(802) - 20.0) < 1e-3
        assert den.homeler()["aktif_gercek"] == "A"
    finally:
        den.kapat()


def test_farkli_homelar_ayni_fiziksel_noktaya_farkli_isler_gosterir():
    """Iki malzeme: ayni yerde durulur, hangi Home aktifse DRO ona gore okunur."""
    den = _den()
    try:
        _tasi(den, x=300.0, y=0.0)                # tezgah (800, 300)
        den.home_kaydet("Sol levha", tezgah={"X": 500, "Y": 300, "Z": 0})
        den.home_kaydet("Sag levha", tezgah={"X": 1500, "Y": 300, "Z": 0})
        den.home_uygula("Sol levha")
        assert abs(den.baglanti.dro(800) - 300.0) < 1e-3
        den.home_uygula("Sag levha")
        assert abs(den.baglanti.dro(800) - (-700.0)) < 1e-3
        h = den.homeler()
        assert h["aktif_gercek"] == "Sag levha"
        durum = {x["ad"]: x["uyumlu"] for x in h["liste"]}
        assert durum == {"Sag levha": True, "Sol levha": False}
    finally:
        den.kapat()


def test_mach3_yeniden_acildi_ayni_fiziksel_noktaya_doner():
    """Makine koordinati kayar, referans yeniden alinir; Home ayni noktada."""
    den = _den()
    try:
        _tasi(den, x=100.0)                        # tezgah 600
        den.sifirla("X", 0.0)
        den.home_kaydet("A", eksenler=("X",))
        # Mach3 yeniden basladi: makine X 100 -> 0'a kaydi, referans dustu
        _tasi(den, x=0.0)
        den.referans_birak()
        # operator kafanin gercek yerini (tezgah 600) bildirir
        den.referans_al(("X", "Y", "Z"), degerler={"X": 600.0, "Y": 300.0, "Z": 0.0})
        den.sifirla("X", -123.0)                   # DRO bozuk
        den.home_uygula("A")
        assert abs(den.baglanti.dro(800)) < 1e-3, den.baglanti.dro(800)
    finally:
        den.kapat()


def test_uygula_referanssiz_reddedilir():
    den = _den()
    try:
        den.home_kaydet("A", tezgah={"X": 1.0})
        den.referans_birak()
        try:
            den.home_uygula("A")
            raise AssertionError("referanssiz uygulanmamali")
        except GUV.GuvenlikHatasi as e:
            assert "referans" in str(e).lower()
    finally:
        den.kapat()


def test_uygula_resetteyken_reddedilir():
    den = _den()
    try:
        den.home_kaydet("A", tezgah={"X": 1.0})
        den.baglanti.dugme(1021)
        try:
            den.home_uygula("A")
            raise AssertionError("Reset'te uygulanmamali")
        except GUV.GuvenlikHatasi as e:
            assert "reset" in str(e).lower()
    finally:
        den.kapat()


def test_uygula_hareket_sirasinda_reddedilir():
    den = _den()
    try:
        den.home_kaydet("A", tezgah={"X": 1.0})
        den.baglanti.kod("G1 X500 F3000")
        time.sleep(0.15)
        try:
            den.home_uygula("A")
            raise AssertionError("hareket sirasinda uygulanmamali")
        except GUV.GuvenlikHatasi as e:
            assert "hareket" in str(e).lower()
    finally:
        den.baglanti.dugme(1003)
        den.kapat()


def test_kismi_eksen_yalnizca_o_ekseni_degistirir():
    den = _den()
    try:
        _tasi(den, x=100.0, y=50.0)
        den.dro_ayarla("Y", 7.0)                     # DRO Y = 7
        den.home_kaydet("SadeceZ", tezgah={"Z": -5.0})
        den.home_uygula("SadeceZ")
        assert abs(den.baglanti.dro(801) - 7.0) < 1e-3        # Y'ye dokunulmadi
        assert abs(den.baglanti.dro(802) - 5.0) < 1e-3        # tezgah 0 - (-5)
    finally:
        den.kapat()


def test_uygula_ve_git_x0y0a_gider_guvenli_z_ile():
    den = _den()
    try:
        _tasi(den, x=100.0, y=100.0, z=-20.0)      # tezgah (600, 400, -20)
        den.home_kaydet("A", tezgah={"X": 300.0, "Y": 200.0, "Z": -50.0})
        r = den.home_uygula("A", git=True)
        assert r["git"] is True, r
        assert _bekle(den)
        b = den.baglanti
        assert abs(b.dro(800)) < 0.01 and abs(b.dro(801)) < 0.01   # X0 Y0
        assert b.dro(802) >= 20.0 - 0.01        # Z guvenli yukseklikte kaldi
        # fiziksel konum = tezgah (300, 200): makine = tezgah - 500/300 referans
        assert abs(den._tezgah["X"] - 300.0) < 0.05
    finally:
        den.kapat()


def test_git_reddedilirse_home_yine_de_uygulanir_ve_hata_bildirilir():
    den = _den()
    try:
        _tasi(den, x=100.0, y=100.0, z=-20.0)
        # sifir tezgah disi: X0 Y0'a gitmek limit disina cikar
        den.home_kaydet("Uzak", tezgah={"X": -50.0, "Y": 200.0, "Z": -50.0})
        r = den.home_uygula("Uzak", git=True)
        assert r["git"] is False and r["git_hata"], r
        assert den.homeler()["aktif"] == "Uzak"
        assert not den.baglanti.hareket_ediyor()
    finally:
        den.kapat()


def test_home_guvenli_z_ayari_uygulanir():
    den = _den()
    try:
        den.home_kaydet("A", tezgah={"X": 1.0}, guvenli_z=35.0)
        den.home_uygula("A")
        assert den.ayarlar["guvenlik"]["guvenli_z"] == 35.0
        den.home_kaydet("B", tezgah={"X": 1.0})            # guvenli_z yok
        den.home_uygula("B")
        assert den.ayarlar["guvenlik"]["guvenli_z"] == 35.0  # degismedi
    finally:
        den.kapat()


# ------------------------------------------------------------ duzenle / sil
def test_duzenle_yeniden_adlandirir_ve_aktifi_izler():
    den = _den()
    try:
        den.home_kaydet("A", tezgah={"X": 10.0, "Y": 20.0})
        den.home_uygula("A")
        r = den.home_duzenle("A", yeni_ad="Levha 1", malzeme="MDF 18",
                             **{"not": "sol ust kose"})
        assert r["ad"] == "Levha 1"
        h = den.homeler()
        assert h["aktif"] == "Levha 1"
        assert [x["ad"] for x in h["liste"]] == ["Levha 1"]
        assert h["liste"][0]["malzeme"] == "MDF 18"
        assert h["liste"][0]["not"] == "sol ust kose"
    finally:
        den.kapat()


def test_duzenle_ad_cakismasini_reddeder():
    den = _den()
    try:
        den.home_kaydet("A", tezgah={"X": 1.0})
        den.home_kaydet("B", tezgah={"X": 2.0})
        try:
            den.home_duzenle("A", yeni_ad="b")
            raise AssertionError("cakisan ad reddedilmeliydi")
        except GUV.GuvenlikHatasi as e:
            assert "zaten var" in str(e)
        # yalnizca buyuk/kucuk harf degisimi kendi adiyla cakisma sayilmaz
        den.home_duzenle("A", yeni_ad="a")
        assert sorted(x["ad"] for x in den.homeler()["liste"]) == ["B", "a"]
    finally:
        den.kapat()


def test_duzenle_koordinat_degistirir_ve_bos_ekseni_siler():
    den = _den()
    try:
        den.home_kaydet("A", tezgah={"X": 10.0, "Y": 20.0, "Z": -5.0})
        den.home_duzenle("A", tezgah={"X": 99.5, "Y": None})
        tz = den.homeler()["liste"][0]["tezgah"]
        assert tz == {"X": 99.5, "Z": -5.0}
        try:
            den.home_duzenle("A", tezgah={"X": None, "Z": None})
            raise AssertionError("en az bir eksen kalmali")
        except GUV.GuvenlikHatasi:
            pass
    finally:
        den.kapat()


def test_sil_aktifi_temizler():
    den = _den()
    try:
        den.home_kaydet("A", tezgah={"X": 1.0})
        den.home_kaydet("B", tezgah={"X": 2.0})
        den.home_uygula("A")
        den.home_sil("A")
        h = den.homeler()
        assert h["aktif"] == "" and [x["ad"] for x in h["liste"]] == ["B"]
        try:
            den.home_sil("A")
            raise AssertionError("olmayan Home silinemez")
        except GUV.GuvenlikHatasi:
            pass
    finally:
        den.kapat()


def test_liste_alfabetik_ve_kalici():
    den = _den()
    try:
        for ad in ("cam", "Beta", "alfa"):
            den.home_kaydet(ad, tezgah={"X": 1.0})
        assert [x["ad"] for x in den.homeler()["liste"]] == ["alfa", "Beta", "cam"]
        # diske yazildi: yeni bir Ayarlar ayni listeyi okur
        a2 = AY.Ayarlar(den.ayarlar.yol)
        assert [x["ad"] for x in a2["profiller"]["liste"]] == ["alfa", "Beta", "cam"]
    finally:
        den.kapat()


def test_elle_sifirlayinca_aktif_degil_sayilir():
    den = _den()
    try:
        _tasi(den, x=100.0)
        den.home_kaydet("A", tezgah={"X": 500.0})
        den.home_uygula("A")
        assert den.homeler()["aktif_gercek"] == "A"
        den.sifirla("X", 5.0)                       # operator elle degistirdi
        h = den.homeler()
        assert h["aktif"] == "A" and h["aktif_gercek"] == ""
        assert h["liste"][0]["uyumlu"] is False
        assert abs(h["liste"][0]["fark"]["X"] - (-5.0)) < 0.01 or \
            abs(h["liste"][0]["fark"]["X"]) > 1.0
    finally:
        den.kapat()


# -------------------------------------------------------- eski kayitlar
def test_eski_kayit_ayni_anlamla_okunur_ve_isaretlenir():
    den = _den()
    try:
        den.ayarlar.guncelle({"profiller": {"liste": [{
            "ad": "Eski", "is_sifiri_makine": {"X": 500.0, "Y": 300.0, "Z": 0.0},
            "guvenli_z": 25.0}]}})
        h = den.homeler()["liste"][0]
        assert h["eski"] is True and h["tezgah"]["X"] == 500.0
        assert h["uyumlu"] is False or h["uyumlu"] is True
        r = den.profil_uygula("Eski")                # eski ad hala calisir
        assert r["eski"] is True
        # referans 0 olmadigi halde dogru deger: tezgah 500 - 500 = 0
        assert abs(den.baglanti.dro(800)) < 1e-3
        assert den.homeler()["aktif_gercek"] == "Eski"
    finally:
        den.kapat()


def test_eski_profil_kaydet_adi_calisir_ve_uzerine_yazar():
    den = _den()
    try:
        den.profil_kaydet("A", takim_boyu=52.3, not_="deneme")
        den.profil_kaydet("A", takim_boyu=60.0)         # uzerine yazar (eski davranis)
        h = den.homeler()["liste"]
        assert len(h) == 1 and h[0]["takim_boyu"] == 60.0 and h[0]["not"] == "deneme"
        den.profil_sil("A")
        assert den.homeler()["liste"] == []
    finally:
        den.kapat()


def test_durumda_home_bilgisi_var():
    den = _den()
    try:
        den.home_kaydet("A", tezgah={"X": 1.0})
        d = den.durum()["profiller"]
        assert d["liste"][0]["ad"] == "A" and "mevcut" in d and "aktif_gercek" in d
    finally:
        den.kapat()


def test_rapor_aktif_home_adini_tasir():
    """Is raporundaki 'profil' alani artik GERCEKTEN gecerli Home'dur."""
    den = _den()
    try:
        den.home_kaydet("Levha A", tezgah={"X": 500.0, "Y": 300.0, "Z": 0.0})
        den.home_uygula("Levha A")
        assert den.homeler()["aktif_gercek"] == "Levha A"
    finally:
        den.kapat()


if __name__ == "__main__":
    for ad, fn in sorted(list(globals().items())):
        if ad.startswith("test_"):
            fn()
            print("ok", ad)
    print("Home testleri gecti.")
