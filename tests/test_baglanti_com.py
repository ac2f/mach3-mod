#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""COM baglanti duzeltmelerinin testleri (Windows disinda da calisir).

Buradaki testler, "Mach3 acik ama baglanti kurulamiyor" sorununun
duzeltilmesi sirasinda ortaya cikan somut hatalari korur.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mach3_kontrol import ayarlar as AY, baglanti as BAG   # noqa: E402


# ---------------------------------------------------------------------
# uye_var: COM nesnelerinde `hasattr` calismaz (tip bilgisi yok), ama
# COM olmayan nesnelerde eski davranis korunmali.
# ---------------------------------------------------------------------
def test_uye_var_com_olmayan_nesnede_hasattr_gibi():
    class Sahte:
        def GetOEMDRO(self, kod):
            return 1.0

    s = Sahte()
    assert BAG.uye_var(s, "GetOEMDRO")
    assert not BAG.uye_var(s, "YokBoyleBirSey")


def test_uye_var_patlayan_ozelligi_yutar():
    class Patlayan:
        @property
        def GetOEMDRO(self):
            raise RuntimeError("COM hatasi")

    # Istisna disari sizmamali; cozulemeyen uye "yok" sayilir.
    assert BAG.uye_var(Patlayan(), "GetOEMDRO") is False


# ---------------------------------------------------------------------
# "Invalid class string" ayirt edilmeli: yedek ProgID kayitli olmadigi
# icin gelen bu hata, asil ProgID'nin hatasini maskelememeli.
# ---------------------------------------------------------------------
def test_sinif_yok_tespiti():
    assert BAG._sinif_yok("(-2147221005, 'Invalid class string', None, None)")
    assert BAG._sinif_yok("Invalid class string")
    assert not BAG._sinif_yok(
        "(-2146959355, 'Server execution failed', None, None)")


# ---------------------------------------------------------------------
# Ayar gocu: "mach3_baslat: false" artik calismayan bir secenek oldugu
# icin eski dosyalarda acilmali.
# ---------------------------------------------------------------------
def test_gocur_mach3_baslat_acar():
    degisti, veri = AY._gocur({"surum": 1,
                               "baglanti": {"mach3_baslat": False}})
    assert degisti
    assert veri["baglanti"]["mach3_baslat"] is True
    assert veri["surum"] == AY.SURUM


def test_gocur_guncel_dosyaya_dokunmaz():
    degisti, veri = AY._gocur({"surum": AY.SURUM,
                               "baglanti": {"mach3_baslat": False}})
    assert not degisti
    assert veri["baglanti"]["mach3_baslat"] is False


def test_eski_ayar_dosyasi_yuklenince_gocurulur():
    with tempfile.TemporaryDirectory() as d:
        yol = os.path.join(d, "ayarlar.json")
        with open(yol, "w", encoding="utf-8") as f:
            json.dump({"surum": 1, "baglanti": {"mach3_baslat": False}}, f)
        a = AY.Ayarlar(yol)
        assert a["baglanti"]["mach3_baslat"] is True
        # diske de yazilmis olmali
        with open(yol, encoding="utf-8") as f:
            assert json.load(f)["baglanti"]["mach3_baslat"] is True


# ---------------------------------------------------------------------
# Mach3.exe yolu: ayarda acikca verilmisse o kullanilir.
# ---------------------------------------------------------------------
def test_mach3_exe_ayardan_okunur():
    with tempfile.TemporaryDirectory() as d:
        sahte = os.path.join(d, "Mach3.exe")
        with open(sahte, "w") as f:
            f.write("")
        assert BAG.mach3_exe_bul({"mach3_exe": sahte}) == sahte


def test_mach3_exe_yok_ise_bos_doner():
    assert BAG.mach3_exe_bul({"mach3_exe": "/olmayan/yol/Mach3.exe"}) == ""


# ---------------------------------------------------------------------
# Varsayilan ayarlar: baglanti bolumunde yeni alanlar bulunmali.
# ---------------------------------------------------------------------
def test_varsayilan_baglanti_alanlari():
    b = AY.VARSAYILAN["baglanti"]
    assert b["mach3_baslat"] is True      # elle acilan Mach3'e baglanilamaz
    assert "profil" in b and b["profil"]  # profilsiz acilis COM'u acmaz
    assert "mach3_exe" in b
    assert b["acilis_zaman_asimi_sn"] >= 60


if __name__ == "__main__":
    for ad, fn in sorted(list(globals().items())):
        if ad.startswith("test_"):
            fn()
            print("ok", ad)
    print("COM baglanti testleri gecti.")
