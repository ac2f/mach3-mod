#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jog motoru testleri — Mach3 modeli
==================================
Mach3'te jog: Jog ON/OFF, Continuous / Step kipi, Slow Jog Rate %, Shift =
%100, bırakınca YAVAŞLAYARAK durma, limite yavaşlayarak varma. Bu testler
fiziksel olarak anlamlı sanal makineyle (`sanal_makine`) çalışır; hız,
ivme ve durma mesafesi gerçekten ölçülür.
"""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mach3_kontrol import (ayarlar as AY, denetleyici as DEN,   # noqa: E402
                           guvenlik as GUV)


def _den(jog=None, guvenlik=None, makine=None, sim=None):
    dizin = tempfile.mkdtemp()
    a = AY.Ayarlar(os.path.join(dizin, "ayarlar.json"))
    g = {"yumusak_limit": {"X": {"min": 0.0, "max": 500.0},
                           "Y": {"min": 0.0, "max": 500.0},
                           "Z": {"min": -100.0, "max": 100.0}},
         "yumusak_limit_zorunlu": True, "limitler_dogrulandi": True,
         "limit_payi": 0.0, "max_jog_adim": 100.0, "max_jog_feed": 9000.0,
         "max_dalis_derinlik": -90.0, "makine_z_taban": None,
         "jog_kalp_atisi_ms": 5000, "max_surekli_jog_sn": 30.0}
    g.update(guvenlik or {})
    ba = {"tur": "simulasyon", "yoklama_hz": 30}
    ba.update(sim or {})
    a.guncelle({"baglanti": ba, "guvenlik": g,
                "makine": makine or {},
                "jog": jog or {}})
    den = DEN.Denetleyici(a)
    den.baglan()
    time.sleep(0.15)
    # Kafa tezgahin ortasinda: her iki yonde de bosluk var (gercek kullanimda
    # referans, kafanin tezgahtaki yeri bildirilerek alinir).
    den.referans_al(("X", "Y", "Z"),
                    degerler={"X": 250.0, "Y": 250.0, "Z": 0.0})
    return den


def _bekle(den, sn=6.0):
    """Hareket tamamen bitene kadar bekle."""
    son = time.time() + sn
    while time.time() < son:
        time.sleep(0.03)
        if not den.baglanti.hareket_ediyor():
            time.sleep(0.12)
            if not den.baglanti.hareket_ediyor():
                return True
    return False


def _hareket_et(den, eksen="X", yon=1, sure=0.8, **kw):
    """Tusa bas, `sure` sn tut, birak; eksen durana kadar bekle."""
    den.jog_basla(eksen, yon, **kw)
    t0 = time.time()
    while time.time() - t0 < sure:
        den.jog_kalp()
        time.sleep(0.05)
    den.jog_bitir()
    assert _bekle(den)


def _konum(den, e="X"):
    return den.baglanti.konum[e]


# ==================================================================
# 1) Slow Jog Rate % x eksen azami hizi (Mach3 hiz modeli)
# ==================================================================
def test_hiz_yuzde_carpi_eksen_hizi():
    den = _den(jog={"yavas_yuzde": 25.0, "hizli_yuzde": 100.0},
               makine={"hizli_hiz": {"X": 8000.0, "Y": 6000.0, "Z": 3000.0}})
    try:
        assert abs(den.jog.hiz_mm_dk("X") - 2000.0) < 1e-6
        assert abs(den.jog.hiz_mm_dk("Y") - 1500.0) < 1e-6
        assert abs(den.jog.hiz_mm_dk("Z") - 750.0) < 1e-6
        # Shift = %100 (ust sinir max_jog_feed)
        assert abs(den.jog.hiz_mm_dk("X", hizli=True) - 8000.0) < 1e-6
        den.ayarlar.guncelle({"guvenlik": {"max_jog_feed": 3000.0}})
        assert abs(den.jog.hiz_mm_dk("X", hizli=True) - 3000.0) < 1e-6
    finally:
        den.kapat()


def test_surekli_jog_gercekten_o_hizda_gider():
    """%25 x 8000 = 2000 mm/dk = 33.3 mm/s: 1 sn'de ~30 mm (ivme payi)."""
    den = _den(jog={"yavas_yuzde": 25.0})
    try:
        den.jog_basla("X", 1)
        t0 = time.time()
        while time.time() - t0 < 1.0:
            den.jog_kalp()
            time.sleep(0.03)
        hizlar = den.baglanti.dro(806)         # X Velocity (mm/dk)
        den.jog_bitir()
        assert _bekle(den)
        assert 1700.0 < hizlar < 2100.0, hizlar
        assert 25.0 < _konum(den) < 60.0, _konum(den)
    finally:
        den.kapat()


# ==================================================================
# 2) Birakinca YAVASLAYARAK durma (Mach3 tus birakisi)
# ==================================================================
def test_birakinca_durma_mesafesi_v2_bolu_2a():
    """Durma mesafesi ~ v^2/(2a); anlik kesme ya da kuyruk kadar kayma OLMAZ."""
    den = _den(jog={"yavas_yuzde": 50.0},
               makine={"ivme": {"X": 400.0, "Y": 400.0, "Z": 250.0}})
    try:
        den.jog_basla("X", 1)
        t0 = time.time()
        while time.time() - t0 < 1.0:
            den.jog_kalp()
            time.sleep(0.03)
        # birakma ani
        bas = _konum(den)
        v = 4000.0 / 60.0                       # mm/s
        den.jog_bitir()
        assert _bekle(den)
        kayma = _konum(den) - bas
        beklenen = v * v / (2 * 400.0)          # ~5.6 mm
        # 40 ms'lik tik + COM gecikmesi payi
        assert beklenen * 0.5 < kayma < beklenen + v * 0.15, (kayma, beklenen)
    finally:
        den.kapat()


def test_birakinca_kuyruk_bosalir():
    den = _den(jog={"yavas_yuzde": 100.0, "ileri_sn": 0.5})
    try:
        den.jog_basla("X", 1)
        for _ in range(12):
            den.jog_kalp()
            time.sleep(0.05)
        den.jog_bitir()
        assert _bekle(den)
        assert den.baglanti.dro(837) == 0.0     # kuyruk bos
        assert not den.baglanti.holding         # Feed Hold'da kalmadi
        assert den.baglanti.led(803)            # Idle
    finally:
        den.kapat()


def test_stop_dugmesi_rewind_degil():
    """REGRESYON: eski kodda stop=1002 idi; bu Mach3'te REWIND'dir."""
    den = _den()
    try:
        assert den.kodlar.buton["stop"] == 1003
        assert den.kodlar.buton["rewind"] == 1002
        _hareket_et(den, sure=0.3)
        assert 1003 in den.baglanti.dugmeler
        assert 1002 not in den.baglanti.dugmeler
        assert (den.baglanti.dugmeler.index(1001)
                < den.baglanti.dugmeler.index(1003))      # once Feed Hold
    finally:
        den.kapat()


def test_mutlak_hedef_kalici_g91_birakmaz():
    den = _den()
    try:
        _hareket_et(den, sure=0.4)
        assert den.baglanti.mutlak is True
        assert not any("G91" in k for k in den.baglanti.kodlar)
    finally:
        den.kapat()


# ==================================================================
# 3) Shift: jogu kesmeden hiz degisimi
# ==================================================================
def test_shift_jogu_kesmeden_hizi_degistirir():
    den = _den(jog={"yavas_yuzde": 10.0, "hizli_yuzde": 60.0})
    try:
        den.jog_basla("X", 1)
        t0 = time.time()
        while time.time() - t0 < 0.8:
            den.jog_kalp()
            time.sleep(0.03)
        yavas = abs(den.baglanti.dro(806))
        den.jog_hiz(hizli=True)
        t0 = time.time()
        while time.time() - t0 < 1.0:
            den.jog_kalp()
            time.sleep(0.03)
        hizli = abs(den.baglanti.dro(806))
        assert den.jog_bilgi["aktif"], "Shift jogu kesmemeli"
        den.jog_bitir()
        assert _bekle(den)
        assert yavas < 900.0 and hizli > 3500.0, (yavas, hizli)
    finally:
        den.kapat()


# ==================================================================
# 4) Step kipi: bir basis = tam Step mesafesi
# ==================================================================
def test_step_kipi_tam_artim_kadar_gider():
    den = _den(jog={"mod": "adim", "adim": 2.5, "yavas_yuzde": 40.0})
    try:
        den.jog_basla("X", 1)
        den.jog_bitir()                          # tus birakmak etkisiz
        assert _bekle(den)
        assert abs(_konum(den) - 2.5) < 0.001, _konum(den)
        den.jog_basla("X", -1)
        assert _bekle(den)
        assert abs(_konum(den) - 0.0) < 0.001
    finally:
        den.kapat()


def test_step_kipi_tus_tutmak_tekrar_etmez():
    den = _den(jog={"mod": "adim", "adim": 1.0})
    try:
        den.jog_basla("Y", 1)
        for _ in range(10):                      # "basili tutma"
            den.jog_kalp()
            time.sleep(0.05)
        assert _bekle(den)
        assert abs(_konum(den, "Y") - 1.0) < 0.001
        assert den.jog_bilgi["aktif"] is False   # surekli jog yok
    finally:
        den.kapat()


def test_step_basislari_tampona_alinir_ve_birikir():
    den = _den(jog={"mod": "adim", "adim": 10.0, "yavas_yuzde": 5.0,
                    "adim_kuyrugu": 3})
    try:
        for _ in range(3):
            den.jog_basla("X", 1)                # 3 hizli basis
        r = den.jog_basla("X", 1)                # tampon dolu: yoksayilir
        assert r.get("yoksayildi"), r
        assert _bekle(den, 20.0)
        assert abs(_konum(den) - 30.0) < 0.001, _konum(den)
    finally:
        den.kapat()


def test_step_kipinde_limit_asilamaz():
    den = _den(jog={"mod": "adim", "adim": 10.0})
    try:
        den.referans_al(("X",), degerler={"X": 495.0})   # limit 500
        try:
            den.jog_basla("X", 1)
            raise AssertionError("limit disi step gecmemeliydi")
        except GUV.GuvenlikHatasi as e:
            assert any("limit" in s for s in e.sebepler), e.sebepler
    finally:
        den.kapat()


# ==================================================================
# 5) Jog ON/OFF
# ==================================================================
def test_jog_kapaliyken_hicbir_sey_olmaz():
    den = _den(jog={"acik": False})
    try:
        for mod in ("surekli", "adim"):
            den.jog.ayarla(mod=mod)
            try:
                den.jog_basla("X", 1)
                raise AssertionError("Jog OFF iken jog calismamali")
            except GUV.GuvenlikHatasi as e:
                assert "jog" in str(e).lower() and "kapali" in str(e).lower()
        assert abs(_konum(den)) < 1e-9
    finally:
        den.kapat()


# ==================================================================
# 6) Limite YAVASLAYARAK varma
# ==================================================================
def test_surekli_jog_limite_yavaslayarak_varir_ve_durur():
    den = _den(jog={"yavas_yuzde": 50.0})
    try:
        den.referans_al(("X",), degerler={"X": 470.0})   # 30 mm var
        den.jog_basla("X", 1)
        t0 = time.time()
        while time.time() - t0 < 3.0 and den.jog_bilgi["aktif"]:
            den.jog_kalp()
            time.sleep(0.03)
        assert not den.jog_bilgi["aktif"], "limitte kendiliginden durmali"
        assert _bekle(den)
        den._oku()
        x = den._tezgah["X"]                     # TEZGAH koordinati
        assert 495.0 < x <= 500.0 + 1e-6, x      # limite varip durdu, asmadi
        assert "limit" in den.jog.durum()["son_sebep"]
    finally:
        den.kapat()


def test_limitteyken_basla_hemen_durur_hareket_yok():
    den = _den()
    try:
        den.referans_al(("X",), degerler={"X": 499.8})
        den.jog_basla("X", 1)
        den.jog.bekle(3.0)
        assert abs(_konum(den)) < 1e-3
    finally:
        den.kapat()


def test_z_tabana_yaklasirken_durur():
    den = _den(guvenlik={"makine_z_taban": -50.0}, jog={"yavas_yuzde": 60.0})
    try:
        den.referans_al(("Z",), degerler={"Z": -20.0})
        den.jog_basla("Z", -1)
        t0 = time.time()
        while time.time() - t0 < 4.0 and den.jog_bilgi["aktif"]:
            den.jog_kalp()
            time.sleep(0.03)
        assert _bekle(den)
        z_tez = den._tezgah["Z"]
        assert z_tez >= -50.0 - 1e-6, z_tez       # tabanin altina inmedi
        assert z_tez < -45.0, z_tez
    finally:
        den.kapat()


# ==================================================================
# 7) Guvenlik: kalp atisi, Reset, program, referans, birim
# ==================================================================
def test_kalp_atisi_kesilirse_yavaslayip_durur():
    den = _den(guvenlik={"jog_kalp_atisi_ms": 150})
    try:
        den.jog_basla("X", 1)                    # kalp atisi YOK
        den.jog.bekle(3.0)
        assert den.jog_bilgi["aktif"] is False
        assert _bekle(den)
        assert "kalp" in den.jog.durum()["son_sebep"]
    finally:
        den.kapat()


def test_ilk_kalp_atisina_tolerans_taninir():
    """Istemci yaniti alip zamanlayicisini kurana kadar gecen sure aralikta
    sayilmaz (eskiden ag gecikmesi jogu sebepsiz kesiyordu)."""
    den = _den(guvenlik={"jog_kalp_atisi_ms": 200})
    try:
        den.jog_basla("X", 1)
        time.sleep(0.28)                         # aralik 200 ms'yi asti
        den.jog_kalp()
        assert den.jog_bilgi["aktif"], "ilk atisa tolerans taninmali"
        den.jog_bitir()
    finally:
        den.kapat()


def test_reset_durumunda_jog_reddedilir():
    den = _den(sim={"sim_reset_ile_basla": True})
    try:
        try:
            den.jog_basla("X", 1)
            raise AssertionError("Reset'te jog engellenmeliydi")
        except GUV.GuvenlikHatasi as e:
            assert "reset" in str(e).lower()
    finally:
        den.kapat()


def test_jog_sirasinda_reset_basilirsa_durur():
    den = _den()
    try:
        den.jog_basla("X", 1)
        for _ in range(6):
            den.jog_kalp()
            time.sleep(0.04)
        den.baglanti.dugme(1021)                 # biri Reset'e bastı
        den.jog.bekle(3.0)
        assert den.jog_bilgi["aktif"] is False
        assert "reset" in den.jog.durum()["son_sebep"].lower()
    finally:
        den.kapat()


def test_program_calisirken_jog_reddedilir():
    den = _den()
    try:
        den.calisiyor = True
        try:
            den.jog_basla("X", 1)
            raise AssertionError("program calisirken jog engellenmeliydi")
        except GUV.GuvenlikHatasi as e:
            assert "program" in str(e).lower()
    finally:
        den.calisiyor = False
        den.kapat()


def test_referanssiz_jog_engellenir():
    den = _den()
    try:
        den.referans_birak()
        for mod in ("surekli", "adim"):
            den.jog.ayarla(mod=mod)
            try:
                den.jog_basla("X", 1)
                raise AssertionError("referanssiz jog engellenmeliydi")
            except GUV.GuvenlikHatasi as e:
                assert "referans" in str(e).lower()
    finally:
        den.kapat()


def test_mach3_inc_kipindaysa_g21_zorlanir():
    """REGRESYON (gercek Mach3'te gozlendi: 'Standard Meas.' LED'i yanik).

    G20'de 'G1 X10' 254 mm giderdi. Program mm'de calisir: jog oncesi G21.
    """
    den = _den(jog={"mod": "adim", "adim": 10.0, "yavas_yuzde": 50.0},
               sim={"sim_inc_kipi": True})
    try:
        assert den.baglanti.inch is True
        den.jog_basla("X", 1)
        assert _bekle(den)
        assert den.baglanti.inch is False, "G21 gonderilmeliydi"
        assert abs(den.baglanti.konum["X"] - 10.0) < 0.001, \
            den.baglanti.konum["X"]            # 254 degil 10 mm
    finally:
        den.kapat()


def test_g20_g21_dusmezse_hareket_reddedilir():
    den = _den(sim={"sim_inc_kipi": True})
    try:
        den.baglanti.kod = lambda satir: None    # G21 islemiyor
        try:
            den.jog_basla("X", 1)
            raise AssertionError("inc kipinde hareket engellenmeliydi")
        except GUV.GuvenlikHatasi as e:
            assert "g21" in str(e).lower()
    finally:
        den.kapat()


def test_birim_zorlama_kapatilabilir():
    den = _den(guvenlik={"birim_zorla_mm": False}, sim={"sim_inc_kipi": True},
               jog={"mod": "adim", "adim": 1.0})
    try:
        den.jog_basla("X", 1)
        assert den.baglanti.inch is True        # dokunulmadi
    finally:
        den.kapat()


# ==================================================================
# 8) Ayar dogrulama (Mach3'teki dugmeler)
# ==================================================================
def test_ayarlar_dogrulanir():
    den = _den()
    try:
        for kotu in ({"yavas_yuzde": 0}, {"yavas_yuzde": 101},
                     {"mod": "mpg"}, {"adim": -1}, {"adim": 500},
                     {"surucu": "x"}):
            try:
                den.jog_ayarla(**kotu)
                raise AssertionError(f"reddedilmeliydi: {kotu}")
            except GUV.GuvenlikHatasi:
                pass
        d = den.jog_ayarla(yavas_yuzde=12.5, mod="adim", adim=0.1)
        assert d["yavas_yuzde"] == 12.5 and d["mod"] == "adim"
        assert den.ayarlar["jog"]["adim"] == 0.1
    finally:
        den.kapat()


def test_adim_dongusu_mach3_cycle_jog_step():
    den = _den(jog={"adim": 1.0, "adim_listesi": [10.0, 1.0, 0.1, 0.01]})
    try:
        assert den.jog.adim_sonraki()["adim"] == 0.1
        assert den.jog.adim_sonraki()["adim"] == 0.01
        assert den.jog.adim_sonraki()["adim"] == 10.0     # basa sarar
        assert den.jog.adim_sonraki(-1)["adim"] == 0.01
    finally:
        den.kapat()


def test_mod_degisirken_suren_jog_durur():
    den = _den()
    try:
        den.jog_basla("X", 1)
        den.jog_kalp()
        den.jog_ayarla(mod="adim")
        assert den.jog_bilgi["aktif"] is False
    finally:
        den.kapat()


# ==================================================================
# 9) Mach3'un kendi jog motoru (JogOn / JogOff) + dogrulama
# ==================================================================
def test_yerel_dogrulama_x_ters_kodlamayi_ogrenir():
    """Sanal Mach3'te X'in yon degeri BILEREK ters: dogrulama bunu ogrenmeli."""
    den = _den()
    try:
        r = den.jog_dogrula(("X", "Y", "Z"))
        e = r["esleme"]
        assert e["X"] == {"1": 1, "-1": 0}, e      # X ters
        assert e["Y"] == {"1": 0, "-1": 1}, e
        assert e["Z"] == {"1": 0, "-1": 1}, e
        assert den.ayarlar["jog"]["mach3_dogrulandi"] is True
        # test sonunda Slow Jog Rate geri yazildi ve eksenler yerine dondu
        assert den.baglanti.yavas_jog == 20.0
        assert abs(_konum(den)) < 1.0
    finally:
        den.kapat()


def test_yerel_surucu_dogrulanmadan_kullanilmaz():
    den = _den(jog={"surucu": "mach3"})
    try:
        den.jog_basla("X", 1)
        assert den.jog_bilgi["surucu"] == "artimli"
        den.jog_bitir()
    finally:
        den.kapat()


def test_yerel_surucu_dogrulaninca_mach3_motorunu_kullanir():
    den = _den(jog={"yavas_yuzde": 30.0})
    try:
        den.jog_dogrula(("X",))
        den.jog_ayarla(surucu="mach3")
        den.referans_al(("X",), degerler={"X": 100.0})
        den.baglanti.kodlar.clear()
        den.jog_basla("X", 1)
        assert den.jog_bilgi["surucu"] == "mach3"
        t0 = time.time()
        while time.time() - t0 < 0.8:
            den.jog_kalp()
            time.sleep(0.04)
        v = den.baglanti.dro(806)
        den.jog_bitir()
        assert _bekle(den)
        # Mach3 kendi motoruyla gitti: MDI ile hedef gonderilmedi
        assert not any(k.startswith(("G90 G1", "G1")) for k in
                       den.baglanti.kodlar), den.baglanti.kodlar
        assert 2000.0 < v < 2600.0, v            # %30 x 8000 = 2400
        assert _konum(den) > 10.0
        # artisi pozitif yonde: yon esleme dogru kullanildi
    finally:
        den.kapat()


def test_yerel_surucu_ters_yone_gidiyorsa_keser():
    den = _den(jog={"yavas_yuzde": 30.0})
    try:
        den.jog_dogrula(("X",))
        den.jog_ayarla(surucu="mach3")
        den.referans_al(("X",), degerler={"X": 250.0})
        # gercek makinede esleme degismis gibi: tabloyu boz
        den.ayarlar["jog"]["yon_esleme"]["X"] = {"1": 0, "-1": 1}
        den.jog_basla("X", 1)                    # tablo +1 sanir, makine -1 gider
        den.jog.bekle(4.0)
        assert den.jog_bilgi["aktif"] is False
        assert den.ayarlar["jog"]["mach3_dogrulandi"] is False
        assert "TERS" in " ".join(o["mesaj"] for o in
                                  den.gunluk.liste(50)) or \
            "ters" in den.jog.durum()["son_sebep"].lower()
        assert _konum(den) > -8.0                # birkac mm'de kesildi
    finally:
        den.kapat()


def test_yerel_dogrulama_slow_jog_yazilamazsa_hic_hareket_etmez():
    den = _den()
    try:
        den.baglanti.dro_yaz = lambda kod, deger: None   # yazma yok sayiliyor
        try:
            den.jog_dogrula(("X",))
            raise AssertionError("yazilamayan hizla hareket edilmemeliydi")
        except GUV.GuvenlikHatasi as e:
            assert "hic hareket" in str(e).lower()
        assert abs(_konum(den)) < 1e-9
    finally:
        den.kapat()


def test_yerel_dogrulama_yeterli_bosluk_ister():
    den = _den()
    try:
        den.referans_al(("X",), degerler={"X": 2.0})     # alt limite 2 mm
        try:
            den.jog_dogrula(("X",))
            raise AssertionError("bosluk yoksa test yapilmamali")
        except GUV.GuvenlikHatasi as e:
            assert "bosluk" in str(e).lower()
    finally:
        den.kapat()


def test_ayni_tus_tekrar_istegi_zararsiz_baska_eksen_reddedilir():
    den = _den()
    try:
        r1 = den.jog_basla("X", 1)
        r2 = den.jog_basla("X", 1)               # ag ayni istegi iki kez yolladi
        assert r2["eksen"] == "X" and r2["yon"] == 1
        try:
            den.jog_basla("Y", 1)                # ayni anda ikinci eksen
            raise AssertionError("ikinci eksen reddedilmeliydi")
        except GUV.GuvenlikHatasi as e:
            assert "tek eksen" in str(e)
        assert den.jog_bilgi["eksen"] == "X"
        den.jog_bitir()
    finally:
        den.kapat()


# ==================================================================
# 10) Acil durdur
# ==================================================================
def test_acil_durdur_reset_a_basar_ama_resetteyken_basmaz():
    den = _den()
    try:
        den.acil_durdur("test")
        assert den.baglanti.estop() is True
        n = den.baglanti.dugmeler.count(1021)
        den.acil_durdur("test 2")                # zaten Reset'te
        assert den.baglanti.dugmeler.count(1021) == n, \
            "Reset toggle'dir: tekrar basmak E-Stop'u KALDIRIRDI"
        assert den.baglanti.estop() is True
    finally:
        den.kapat()


def test_yumusak_durdurma_reset_a_basmaz():
    den = _den()
    try:
        den.durdur_yumusak("kalp atisi")
        assert den.baglanti.estop() is False
        assert 1021 not in den.baglanti.dugmeler
    finally:
        den.kapat()


if __name__ == "__main__":
    for ad, fn in sorted(list(globals().items())):
        if ad.startswith("test_"):
            fn()
            print("ok", ad)
    print("Jog testleri gecti.")
