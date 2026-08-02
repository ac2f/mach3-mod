#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tezgah referansi testleri
=========================
Kullanicinin bildirdigi hata: "X2100 tezgahta gecici X'i 2099 yaptim ama
2100 disina cikabiliyordum."

SEBEP: yumusak limitler MAKINE koordinatina gore denetleniyordu; referans
switch'i olmayan makinede Mach3'un makine sifiri tezgahin kosesi degil,
Mach3 acildiginda kafanin oldugu rastgele noktadir. Makine X 0.677 okurken
limit 0..2100 bu rastgele noktadan olculuyordu.

COZUM: operator kafayi sifir kosesine park edip referans alir;
    tezgah = makine_okumasi - referans
ve tum denetimler tezgah koordinatinda yapilir. Referanssizken hareket
engellenir (fail-closed).
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mach3_kontrol import ayarlar as AY, denetleyici as DEN, guvenlik as GUV


class _Baglanti:
    """Kullanicinin makinesini taklit eder: makine X ~0.677 okur."""

    tur = "sahte"
    bagli = True

    def __init__(self):
        # 800/801/802 = is, 83/84/85 = makine
        self.d = {800: -1.873, 801: -3.442, 802: 0.0,
                  83: 0.677, 84: 0.0, 85: 0.0,
                  818: 0.0, 821: 100.0, 39: 0.0}
        self.gonderilen = []

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
        return False

    def yetenekler(self):
        return {}

    def jog_durdur(self):
        return True

    def kapat(self):
        pass


def _den(referans_zorunlu=True):
    d = tempfile.mkdtemp()
    a = AY.Ayarlar(os.path.join(d, "ayarlar.json"))
    # Kullanicinin gercek tezgahi
    a.guncelle({"guvenlik": {
        "yumusak_limit": {"X": {"min": 0.0, "max": 2100.0},
                          "Y": {"min": 0.0, "max": 4050.0},
                          "Z": {"min": -150.0, "max": 60.0}},
        "yumusak_limit_zorunlu": True, "limitler_dogrulandi": True,
        "limit_payi": 1.0, "referans_zorunlu": referans_zorunlu,
        "max_jog_adim": 500.0, "guvenli_z_zorunlu": False}})
    den = DEN.Denetleyici(a)
    den.baglanti = _Baglanti()
    den._oku()
    return den


# ------------------------------------------------------------------
# Referanssizken hareket ENGELLENIR (fail-closed)
# ------------------------------------------------------------------
def test_referanssiz_jog_engellenir():
    den = _den()
    assert den.referans_durumu()["alindi"] is False
    try:
        den.jog_adim("X", 10.0)
        raise AssertionError("referanssiz jog engellenmeliydi")
    except GUV.GuvenlikHatasi as e:
        assert "referans" in str(e).lower(), str(e)


def test_referanssiz_mdi_engellenir():
    den = _den()
    try:
        den.mdi("G0 X100")
        raise AssertionError("referanssiz MDI engellenmeliydi")
    except GUV.GuvenlikHatasi as e:
        assert "referans" in str(e).lower()


def test_referanssiz_mutlak_hareket_engellenir():
    den = _den()
    try:
        den.git({"X": 100.0})
        raise AssertionError("referanssiz hareket engellenmeliydi")
    except GUV.GuvenlikHatasi as e:
        assert "referans" in str(e).lower()


def test_referans_zorunlu_degilse_engellenmez():
    den = _den(referans_zorunlu=False)
    den.jog_adim("X", 10.0)          # istisna atmamali


# ------------------------------------------------------------------
# ASIL HATA: referans alindiktan sonra tezgah disina cikilamaz
# ------------------------------------------------------------------
def test_referans_alininca_tezgah_kordinati_sifirlanir():
    den = _den()
    den.referans_al(("X", "Y", "Z"))
    assert den.referans_durumu()["alindi"] is True
    assert den.referans["X"] == 0.677          # makine okumasi kaydedildi
    assert abs(den._tezgah["X"]) < 1e-9        # kose = tezgah 0


def test_tezgah_sinirinin_disina_cikilamaz():
    """Kullanicinin bildirdigi senaryonun REGRESYON testi."""
    den = _den()
    den.referans_al(("X", "Y", "Z"))

    # Kafa tezgahin 2095 mm'sinde (makine okumasi = referans + 2095)
    den.baglanti.d[83] = 0.677 + 2095.0
    den._oku()
    assert abs(den._tezgah["X"] - 2095.0) < 1e-6

    # 3 mm daha: 2098 -> sinir 2099 (2100 - 1 pay), gecmeli
    den.jog_adim("X", 3.0)

    # 10 mm daha: 2105 -> tezgah disi, REDDEDILMELI
    try:
        den.jog_adim("X", 10.0)
        raise AssertionError("tezgah disina cikis engellenmeliydi")
    except GUV.GuvenlikHatasi as e:
        assert any("yumusak limit" in s for s in e.sebepler), e.sebepler


def test_is_sifiri_degistirmek_korumayi_bozmaz():
    """Gecici (is) DRO'yu 2099 yapmak limiti etkilememeli.

    Eski hatada is DRO'su 2099 yapilinca kullanici tezgah kenarinda
    sandigi halde denetim makine 0.677'ye bakiyordu ve serbest birakiyordu.
    Artik denetim TEZGAH koordinatina bakar; is DRO'su ne olursa olsun
    fiziksel sinir ayni yerdedir.
    """
    den = _den()
    den.referans_al(("X", "Y", "Z"))

    # Kafa tezgahin basinda (tezgah X = 0), ama is DRO'su 2099 yapilmis
    den.baglanti.d[800] = 2099.0
    den._oku()
    assert abs(den._tezgah["X"]) < 1e-6
    assert den._konum["X"] == 2099.0

    # Tezgahin basinda oldugumuz icin +10 mm SERBEST olmali
    den.jog_adim("X", 10.0)

    # Kafa gercekten kenara gelirse artik engellenir
    den.baglanti.d[83] = 0.677 + 2098.0
    den._oku()
    try:
        den.jog_adim("X", 20.0)
        raise AssertionError("engellenmeliydi")
    except GUV.GuvenlikHatasi:
        pass


def test_Y_ekseni_4050_siniri():
    den = _den()
    den.referans_al(("X", "Y", "Z"))
    den.baglanti.d[84] = 4040.0
    den._oku()
    den.jog_adim("Y", 5.0)                    # 4045 < 4049, gecer
    try:
        den.jog_adim("Y", 20.0)               # 4060 > 4049
        raise AssertionError("Y sinirinin disina cikis engellenmeliydi")
    except GUV.GuvenlikHatasi:
        pass


def test_eksi_yone_de_sinir_var():
    den = _den()
    den.referans_al(("X", "Y", "Z"))
    try:
        den.jog_adim("X", -5.0)               # tezgah -5 < 1
        raise AssertionError("eksi yon engellenmeliydi")
    except GUV.GuvenlikHatasi:
        pass


# ------------------------------------------------------------------
# Referans dusme kosullari
# ------------------------------------------------------------------
def test_referans_elle_dusurulebilir():
    den = _den()
    den.referans_al(("X", "Y", "Z"))
    den.referans_birak()
    assert den.referans_durumu()["alindi"] is False
    assert den._tezgah["X"] is None


def test_program_denetimi_referanssizken_gecmez():
    from mach3_kontrol import gcode as GC
    den = _den()
    den.program = GC.Program(satirlar=["G21", "G90", "G0 X10 Y10",
                                       "G1 Z-5 F600", "G0 Z20"])
    den.program_ozet = den.program.ozet()
    den.blok_sureler = []
    sonuc = den.program_denetle(0)
    assert not sonuc["gecti"]
    assert any("referans" in h.lower() for h in sonuc["hatalar"]), \
        sonuc["hatalar"]


# ------------------------------------------------------------------
# Bulunulan noktanin tezgah koordinatini ELLE bildirme
# ------------------------------------------------------------------
# Kullanicinin ikinci bildirimi: "Manuel bir sekilde X'e 2099 yaziyorum
# ama 2100 disina cikabiliyorum."
# SEBEP: DRO'ya yazilan deger IS koordinatidir, referansi kurmaz. Referans
# ise yalnizca "burasi 0" diyebiliyordu; operator kafa karsi uctayken
# "Referans al" deyince tezgah sifiri YANLIS yere kuruluyor ve onunde
# tekrar 2100 mm yol aciliyordu (olay gunlugu: referans X=4.0 alinmis).
def test_bulunulan_noktanin_koordinati_bildirilebilir():
    den = _den()
    den.referans_al(("X",), degerler={"X": 2099.0})
    assert abs(den._tezgah["X"] - 2099.0) < 1e-9
    # referans = makine_okumasi - bildirilen
    assert abs(den.referans["X"] - (0.677 - 2099.0)) < 1e-9


def test_karsi_uctan_referans_alinca_disari_cikilamaz():
    """Kullanicinin senaryosunun REGRESYON testi."""
    den = _den()
    den.referans_al(("X", "Y", "Z"), degerler={"X": 2099.0})
    den.jog_adim("X", -5.0)                   # 2094 -> iceri, serbest
    try:
        den.jog_adim("X", 5.0)                # 2104 > 2099 (2100 - pay)
        raise AssertionError("2100 disina cikis engellenmeliydi")
    except GUV.GuvenlikHatasi as e:
        assert "limit" in str(e.sebepler).lower(), e.sebepler


def test_is_dro_yazmak_tezgah_referansini_degistirmez():
    """DRO yazmak limitleri kaydirmamali; ikisi ayri kavram."""
    den = _den()
    den.referans_al(("X", "Y", "Z"))          # kose = tezgah 0
    onceki = den.referans["X"]
    den.sifirla("X", 2099.0)
    den._oku()
    assert den.referans["X"] == onceki
    assert abs(den._tezgah["X"]) < 1e-9       # kafa hala kosede


def test_limit_disi_koordinat_bildirimi_reddedilir():
    """Yanlis deger operatoru hareket edemez halde birakmasin."""
    den = _den()
    try:
        den.referans_al(("X",), degerler={"X": 3000.0})
        raise AssertionError("limit disi bildirim reddedilmeliydi")
    except GUV.GuvenlikHatasi as e:
        assert "disinda" in str(e).lower(), str(e)
    assert den.referans["X"] is None          # yarim uygulanmamali


def test_tek_eksen_referansi_digerlerini_bozmaz():
    den = _den()
    den.referans_al(("X", "Y", "Z"))
    den.referans_al(("X",), degerler={"X": 2099.0})
    assert abs(den._tezgah["X"] - 2099.0) < 1e-9
    assert abs(den._tezgah["Y"]) < 1e-9
    assert den.referans_durumu()["alindi"] is True


# ------------------------------------------------------------------
# Mach3'un MAKINE koordinatini sifirlama (Ref)
# ------------------------------------------------------------------
# SetOEMDRO makine DRO'suna yazamaz (gercek makinede olculdu). Tek yol
# Ref'tir ve yalnizca SIFIRLAR; istege bagli bir sayi yazilamaz.
def test_makine_sifirlama_makine_drosunu_sifirlar():
    den = _den()
    den.referans_al(("X", "Y", "Z"), degerler={"X": 2099.0})
    # Ref'i taklit et: makine DRO'su bulunulan noktada sifirlanir
    def _dugme(kod):
        if int(kod) in (22, 23, 24, 1024):
            for k in (83, 84, 85):
                den.baglanti.d[k] = 0.0
    den.baglanti.dugme = _dugme
    s = den.makine_sifirla(("X", "Y", "Z"))
    assert s["islemeyen"] == [], s["islemeyen"]
    assert abs(den._makine["X"]) < 1e-9


def test_makine_sifirlama_tezgah_koordinatini_korur():
    """Kafa kimildamadi; tezgah koordinati da degismemeli."""
    den = _den()
    den.referans_al(("X", "Y", "Z"), degerler={"X": 2099.0})
    den.baglanti.dugme = lambda kod: den.baglanti.d.update({83: 0.0, 84: 0.0,
                                                            85: 0.0})
    den.makine_sifirla(("X", "Y", "Z"))
    assert abs(den._tezgah["X"] - 2099.0) < 1e-6
    # Sinir hala koruyor
    try:
        den.jog_adim("X", 5.0)
        raise AssertionError("2100 disina cikis engellenmeliydi")
    except GUV.GuvenlikHatasi:
        pass


def test_ref_islemezse_sessizce_basarili_denmez():
    """Yanlis OEM kodu / kapali Ref: makine DRO degismez, hata verilmeli."""
    den = _den()
    den.baglanti.dugme = lambda kod: None      # Ref hicbir sey yapmiyor
    try:
        den.makine_sifirla(("X",))
        raise AssertionError("islemeyen Ref bildirilmeliydi")
    except GUV.GuvenlikHatasi as e:
        assert "degistirmedi" in str(e).lower(), str(e)


def test_makine_sifirlama_estop_sirasinda_reddedilir():
    den = _den()
    den.baglanti.estop = lambda: True
    den._oku()
    try:
        den.makine_sifirla(("X",))
        raise AssertionError("RESET sirasinda engellenmeliydi")
    except GUV.GuvenlikHatasi as e:
        assert "reset" in str(e).lower(), str(e)


def test_hareket_sirasinda_referans_alinamaz():
    """Hareket ederken okunan konum eskir; yanlis yere referans kurulmasin."""
    den = _den()
    den.baglanti.hareket_ediyor = lambda: True
    den._oku()
    for cagri in (lambda: den.referans_al(("X",), degerler={"X": 2099.0}),
                  lambda: den.sifirla("X", 0.0),
                  lambda: den.dro_ayarla("X", 5.0)):
        try:
            cagri()
            raise AssertionError("hareket sirasinda engellenmeliydi")
        except GUV.GuvenlikHatasi as e:
            assert "hareket" in str(e).lower(), str(e)
    assert den.referans["X"] is None


if __name__ == "__main__":
    for ad, fn in sorted(list(globals().items())):
        if ad.startswith("test_"):
            fn()
            print("ok", ad)
    print("Referans testleri gecti.")
