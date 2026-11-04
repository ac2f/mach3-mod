#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sanal Mach3'ün fiziği: testler ve arayüz buna güvendiği için doğru olmalı."""

import math
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mach3_kontrol.sanal_makine import SimulasyonBaglanti  # noqa: E402

MAKINE = {"hizli_hiz": {"X": 8000.0, "Y": 8000.0, "Z": 3000.0},
          "ivme": {"X": 400.0, "Y": 400.0, "Z": 250.0}}


def _sim(**kw):
    m = SimulasyonBaglanti(kw.pop("ayar", {}), MAKINE, kw.pop("jog", {}))
    m.ac()
    return m


def _bekle(m, sn=10.0):
    son = time.time() + sn
    while time.time() < son:
        if not m.hareket_ediyor():
            time.sleep(0.05)
            if not m.hareket_ediyor():
                return True
        time.sleep(0.01)
    return False


def _trapez(mesafe, v, a):
    if mesafe >= v * v / a:
        return 2 * v / a + (mesafe - v * v / a) / v
    return 2 * math.sqrt(mesafe / a)


def test_trapez_sure_teorikle_uyusur():
    m = _sim()
    try:
        t0 = time.time()
        m.kod("G21 G90 G1 X100 F6000")          # 100 mm/s, a=400
        assert _bekle(m)
        sure = time.time() - t0
        assert abs(sure - _trapez(100.0, 100.0, 400.0)) < 0.12, sure
        assert abs(m.konum["X"] - 100.0) < 1e-6
    finally:
        m.kapat()


def test_kisa_hareket_ucgen_profil():
    m = _sim()
    try:
        t0 = time.time()
        m.kod("G1 X5 F6000")                    # hedef hıza ulaşamaz
        assert _bekle(m)
        assert abs((time.time() - t0) - _trapez(5.0, 100.0, 400.0)) < 0.1
    finally:
        m.kapat()


def test_feed_hold_rampayla_durur_mesafe_v2_2a():
    m = _sim()
    try:
        m.kod("G1 X1000 F6000")
        time.sleep(0.6)                          # hedef hızda
        v = m._v
        assert abs(v - 100.0) < 1.0
        x0 = m.konum["X"]
        m.dugme(1001)
        assert _bekle(m)
        assert abs((m.konum["X"] - x0) - v * v / (2 * 400.0)) < 0.5
        assert m.holding and not m.hareket_ediyor()
        m.dugme(1000)                            # Cycle Start: devam
        time.sleep(0.3)
        assert m.hareket_ediyor()
        m.dugme(1003)
        assert _bekle(m)
        assert m.konum["X"] < 1000.0
    finally:
        m.kapat()


def test_stop_kuyrugu_atar():
    m = _sim()
    try:
        for i in range(1, 6):
            m.kod(f"G1 X{i * 100} F6000")
        time.sleep(0.4)
        m.dugme(1003)
        assert _bekle(m)
        assert m.dro(837) == 0.0
        assert m.konum["X"] < 150.0
    finally:
        m.kapat()


def test_cv_ayni_dogrultuda_birlesir_kosede_yavaslar():
    m = _sim()
    try:
        m.kod("G1 X50 F6000")
        m.kod("G1 X100 F6000")
        time.sleep(0.5)                          # ilk parçanın sonuna yakın
        en_hiz = 0.0
        t0 = time.time()
        while time.time() - t0 < 0.8:
            en_hiz = max(en_hiz, m._v)
            time.sleep(0.005)
        assert en_hiz > 95.0                      # birleşti: 100 mm/s'ye ulaştı
        assert _bekle(m)
        # köşe: dikey dönüş -> parça sonunda durmalı (hız düşer)
        m.kod("G1 X150 F6000")
        m.kod("G1 Y50 F6000")
        dip = 1e9
        t0 = time.time()
        while time.time() - t0 < 1.5:
            if 140 < m.konum["X"] < 150.0 - 1e-6 or abs(m.konum["X"] - 150) < 0.05:
                dip = min(dip, m._v)
            time.sleep(0.003)
        assert dip < 20.0, dip
    finally:
        m.kapat()


def test_reset_hareketi_keser_ve_mdi_yok_sayar():
    m = _sim()
    try:
        m.kod("G1 X500 F6000")
        time.sleep(0.2)
        m.dugme(1021)
        assert m.estop() is True and m.led(800) is True
        assert not m.hareket_ediyor()
        x = m.konum["X"]
        m.kod("G1 X0 F6000")
        time.sleep(0.2)
        assert m.konum["X"] == x                 # Reset'te MDI yok
        m.dro_yaz(800, 123.0)                    # Reset'te DRO yazma yok sayılır
        assert abs(m.dro(800) - x) < 1e-4
        m.dugme(1021)
        assert m.estop() is False
    finally:
        m.kapat()


def test_inc_kipinde_x10_254_mm_gider():
    """Gerçek Mach3'te gözlenen tehlike (G20): istemci mm sanırsa 25.4 kat gider."""
    m = _sim(ayar={"sim_inc_kipi": True})
    try:
        assert m.led(801) is True and m.led(802) is False
        m.kod("G90 G1 X10 F100")
        assert _bekle(m, 60.0)
        assert abs(m.konum["X"] - 254.0) < 1e-6
        # G21 sonrası DRO mm gösterir
        m.kod("G21")
        assert m.led(802) is True
        assert abs(m.dro(83) - 254.0) < 1e-6
    finally:
        m.kapat()


def test_is_ve_makine_koordinati_ofset_makine_gostergesi():
    m = _sim()
    try:
        m.kod("G1 X40 F9000")
        assert _bekle(m)
        m.dugme(1008)                            # Zero X
        assert abs(m.dro(800)) < 1e-9 and abs(m.dro(83) - 40.0) < 1e-9
        assert abs(m.dro(830) - 40.0) < 1e-9     # X Offset
        m.dugme(1018)                            # Show Machine Coord.
        assert abs(m.dro(800) - 40.0) < 1e-9     # DRO artık makine gösteriyor
        assert m.led(816) is True
        m.dugme(1019)
        assert abs(m.dro(800)) < 1e-9
    finally:
        m.kapat()


def test_ref_is_koordinatini_degistirmez():
    m = _sim()
    try:
        m.kod("G1 X40 F9000")
        assert _bekle(m)
        is_once = m.dro(800)
        m.dugme(1022)                            # Home X (switch yok)
        assert abs(m.dro(83)) < 1e-9
        assert abs(m.dro(800) - is_once) < 1e-9
    finally:
        m.kapat()


def test_program_satir_satir_calisir_ve_current_line():
    with tempfile.TemporaryDirectory() as d:
        yol = os.path.join(d, "p.tap")
        with open(yol, "w") as f:
            f.write("G21\nG90\nG1 X50 F6000\nG1 X100 F6000\n\nM30\n")
        m = _sim()
        try:
            m.dosya_yukle(yol)
            assert m.dro(816) == 0
            m.dugme(1000)
            gorulen = set()
            t0 = time.time()
            while time.time() - t0 < 8 and (m.calisiyor or not gorulen):
                gorulen.add(int(m.dro(816)))
                time.sleep(0.01)
            assert {3, 4} <= gorulen, gorulen    # satırlar sırayla yürütüldü
            assert m.led(804) is False or not m.calisiyor
            assert abs(m.konum["X"] - 100.0) < 1e-6
            m.dugme(1002)                        # Rewind
            assert m.dro(816) == 0
        finally:
            m.kapat()


def test_jog_surekli_hiz_yuzde_ve_yavaslama():
    m = _sim(jog={"yavas_yuzde": 25.0})
    try:
        m.jog_ac(0, 1)                           # sim: X için +1 -> değer 1
        time.sleep(0.7)
        assert abs(m._jv - 8000 * 0.25 / 60) < 0.5
        m.jog_kapat(0)
        x0 = m.konum["X"]
        assert _bekle(m)
        v = 8000 * 0.25 / 60
        assert abs((m.konum["X"] - x0) - v * v / 800.0) < 0.5
    finally:
        m.kapat()


def test_jog_x_yon_degeri_ters_kodlu():
    m = _sim(jog={"yavas_yuzde": 30.0})
    try:
        m.jog_ac(0, 0)                           # X için 0 -> eksi yön
        time.sleep(0.3)
        m.jog_kapat(0)
        assert _bekle(m)
        assert m.konum["X"] < -1.0
        m.jog_ac(1, 0)                           # Y için 0 -> artı yön
        time.sleep(0.3)
        m.jog_kapat(1)
        assert _bekle(m)
        assert m.konum["Y"] > 1.0
    finally:
        m.kapat()


def test_jog_step_kipi_artim_kadar():
    m = _sim(jog={"adim": 2.5})
    try:
        m.jog_modu_ayarla("adim")
        assert m.led(819) is True and m.led(820) is False
        m.jog_ac(0, 1)
        m.jog_kapat(0)
        assert _bekle(m)
        assert abs(m.konum["X"] - 2.5) < 1e-6
    finally:
        m.kapat()


def test_jog_kapaliyken_ve_resette_calismaz():
    m = _sim()
    try:
        m.dugme(1031)                            # Jog Toggle -> OFF
        m.jog_ac(0, 1)
        time.sleep(0.2)
        assert abs(m.konum["X"]) < 1e-9
        m.dugme(1031)
        m.dugme(1021)
        m.jog_ac(0, 1)
        time.sleep(0.2)
        assert abs(m.konum["X"]) < 1e-9
    finally:
        m.kapat()


def test_jog_mach3_yumusak_limitinde_durur():
    m = _sim(jog={"yavas_yuzde": 50.0})
    try:
        m.yumusak_limit_acik = True
        m.limitler["X"] = (-100.0, 20.0)
        m.jog_ac(0, 1)
        time.sleep(1.2)
        m.jog_kapat(0)
        assert _bekle(m)
        assert 19.0 < m.konum["X"] <= 20.0 + 1e-6
    finally:
        m.kapat()


def test_paket_oku_tek_turda_hepsini_verir():
    m = _sim()
    try:
        p = m.paket_oku([800, 83, 818, 821], [800, 801, 802])
        assert set(p["dro"]) == {800, 83, 818, 821}
        assert p["led"][802] is True and p["led"][801] is False
        assert p["hareket"] is False and p["estop"] is False
    finally:
        m.kapat()


if __name__ == "__main__":
    for ad, fn in sorted(list(globals().items())):
        if ad.startswith("test_"):
            fn()
            print("ok", ad)
    print("Sanal makine testleri geçti.")
