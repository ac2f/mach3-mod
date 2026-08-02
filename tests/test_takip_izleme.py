#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Is takibi, rapor ve dosya izleyici testleri."""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mach3_kontrol import izleyici as IZL          # noqa: E402
from mach3_kontrol import is_takip as IST          # noqa: E402
from mach3_kontrol import rapor as RAP             # noqa: E402


# =====================================================================
# Is takibi
# =====================================================================
def _blok_sureler():
    # her blok 100 mm yol, 10 sn tahmin
    return [{"toplam_sn": 10.0, "kesim_mm": 80.0, "hizli_mm": 20.0}
            for _ in range(3)]


def _takip():
    t = IST.IsTakip()
    t.basla("D:/isler/a.tap", 0, _blok_sureler(), {"toplam_sn": 30.0},
            konum={"X": 0.0, "Y": 0.0, "Z": 0.0}, profil="6mm", taban=-95.0)
    return t


def test_baslangicta_ilerleme_sifir():
    t = _takip()
    assert t.aktif
    assert t.toplam_yol_mm == 300.0
    assert t.ilerleme() == 0.0
    assert t.ozet()["blok_sayisi"] == 3


def _ilerlet(t, hedef_x, adim=10.0):
    """Gercekci kucuk adimlarla X boyunca ilerlet (yoklama benzetimi)."""
    x = t._son_konum.get("X", 0.0)
    while x < hedef_x - 1e-9:
        x = min(x + adim, hedef_x)
        t.guncelle({"X": x, "Y": 0.0, "Z": 0.0})


def test_yol_biriktikce_ilerleme_artar():
    t = _takip()
    _ilerlet(t, 50.0)
    assert abs(t.yol_mm - 50.0) < 1e-6
    assert abs(t.ilerleme() - 50.0 / 300.0) < 1e-6


def test_blok_gecisi_gercek_sure_kaydeder():
    t = _takip()
    _ilerlet(t, 100.0)                             # 1. blok bitti
    assert t.simdiki_blok == 1
    assert t.bloklar[0]["gercek_sn"] is not None
    assert t.bloklar[1]["basladi_t"] is not None
    _ilerlet(t, 200.0)                             # 2. blok bitti
    assert t.simdiki_blok == 2
    assert t.bloklar[1]["gercek_sn"] is not None


def test_dro_sicramasi_ilerlemeyi_bozmaz():
    """DRO sifirlanirsa konum birden degisir; bu 'yol' sayilmamali."""
    t = _takip()
    _ilerlet(t, 10.0)
    onceki = t.yol_mm
    t.guncelle({"X": 5000.0, "Y": 0.0, "Z": 0.0})   # sicrama
    assert t.yol_mm == onceki, "sicrama yol olarak sayilmamaliydi"


def test_yoklama_takilirsa_gercek_hareket_elenmez():
    """Iki okuma arasi uzun surerse buyuk fark MESRUDUR; sayilmali.

    Regresyon: sabit 200 mm'lik siçrama siniri, 2 sn'lik bir yoklama
    duraksamasindan sonraki hizli hareketi yanlislikla eliyordu.
    """
    t = _takip()
    t.son_t = time.time() - 2.0                    # 2 sn once okunmus gibi
    t.guncelle({"X": 150.0, "Y": 0.0, "Z": 0.0})   # 75 mm/s - makul
    assert abs(t.yol_mm - 150.0) < 1e-6


def test_duraklama_calisan_sureden_dusulur():
    t = _takip()
    t.duraklat("feed hold")
    assert t.duraklatildi
    time.sleep(0.05)
    t.devam_et()
    assert not t.duraklatildi
    assert t.duraklama_sayisi == 1
    assert t.duraklama_sn > 0
    assert t.calisan_sn <= t.gecen_sn


def test_bitir_ozet_uretir():
    t = _takip()
    _ilerlet(t, 300.0)
    o = t.bitir("tamamlandi")
    assert o["bitti"] and not o["aktif"]
    assert o["bitis_sebep"] == "tamamlandi"
    assert o["ilerleme"] == 1.0
    assert o["kalan_sn"] == 0.0
    r = t.rapor_verisi()
    assert r["sapma_orani"] is not None
    assert len(r["bloklar"]) == 3


def test_fro_degisimi_kaydedilir():
    t = _takip()
    t.guncelle({"X": 1.0, "Y": 0, "Z": 0}, fro=100)
    t.guncelle({"X": 2.0, "Y": 0, "Z": 0}, fro=80)
    t.guncelle({"X": 3.0, "Y": 0, "Z": 0}, fro=80)
    assert len(t.fro_gecmis) == 2          # yalnizca DEGISIMLER


def test_baslangic_blogu_atlanan_bloklari_saymaz():
    t = IST.IsTakip()
    t.basla("a.tap", 1, _blok_sureler(), {"toplam_sn": 20.0})
    assert t.toplam_yol_mm == 200.0
    assert [b["no"] for b in t.bloklar] == [2, 3]


# =====================================================================
# Rapor
# =====================================================================
def test_rapor_bicimleri_uretilir():
    t = _takip()
    _ilerlet(t, 150.0)
    t.bitir("tamamlandi")
    v = t.rapor_verisi()

    pdf = RAP.pdf_uret(v)
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF")
    assert len(pdf) > 800

    csv = RAP.csv_uret(v)
    assert "Is raporu" in csv and "Blok;" in csv.replace(" ", "")

    js = RAP.json_uret(v)
    assert '"bloklar"' in js

    assert RAP.dosya_adi(v, "pdf").endswith(".pdf")


def test_pdf_turkce_karakteri_ascii_yapar():
    v = {"ad": "ölçü-şablonğ.tap", "bloklar": [], "olaylar": [],
         "baslangic_t": time.time()}
    pdf = RAP.pdf_uret(v)
    assert "ğ".encode("utf-8") not in pdf
    assert b"olcu-sablong" in pdf.replace(b"\\", b"")


# =====================================================================
# Dosya izleyici
# =====================================================================
class _SahteAyar(dict):
    def get(self, k, v=None):
        return dict.get(self, k, v)


def _izleyici(kok, **ek):
    ay = _SahteAyar(izleme={"aktif": True, "kok": kok, "derinlik": 4,
                            "aralik_sn": 1, "yeni_sayilir_dk": 120, **ek})
    return IZL.DosyaIzleyici(ay)


def _yaz(yol, icerik="G0 X0\n"):
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    with open(yol, "w", encoding="utf-8") as f:
        f.write(icerik)


def test_ilk_tarama_eski_dosyalari_sormaz():
    with tempfile.TemporaryDirectory() as d:
        yol = os.path.join(d, "2026-01-30", "firma", "001.tap")
        _yaz(yol)
        os.utime(yol, (time.time() - 86400, time.time() - 86400))  # 1 gun once
        iz = _izleyici(d)
        iz.tara()
        iz.tara()
        assert iz.bekleyen() == []


def test_yeni_dosya_ikinci_taramada_bildirilir():
    """Ilk gorulduğunde 'aday', imzasi sabit kalinca sorulur."""
    with tempfile.TemporaryDirectory() as d:
        iz = _izleyici(d)
        iz.tara()                                  # taban cizgisi
        _yaz(os.path.join(d, "30-01-2026", "firma-X", "001-mdf", "001.tap"))
        assert iz.tara() == []                     # aday, henuz sorulmaz
        yeni = iz.tara()                           # imza sabit -> sor
        assert len(yeni) == 1
        assert yeni[0]["ad"] == "001.tap"
        assert "firma-X" in yeni[0]["goreli"]
        assert len(iz.bekleyen()) == 1


def test_yazilmakta_olan_dosya_beklenir():
    with tempfile.TemporaryDirectory() as d:
        iz = _izleyici(d)
        iz.tara()
        yol = os.path.join(d, "a.tap")
        _yaz(yol, "G0\n")
        iz.tara()                                  # aday
        _yaz(yol, "G0\nG1 X10\nG1 Y10\n")          # hala yaziliyor
        assert iz.tara() == []                     # imza degisti -> bekle
        assert iz.tara()                           # artik sabit -> sor


def test_yoksayilan_tekrar_sorulmaz():
    with tempfile.TemporaryDirectory() as d:
        iz = _izleyici(d)
        iz.tara()
        yol = os.path.join(d, "b.tap")
        _yaz(yol)
        iz.tara(); iz.tara()
        assert len(iz.bekleyen()) == 1
        iz.yoksay(yol)
        assert iz.bekleyen() == []
        iz.tara(); iz.tara()
        assert iz.bekleyen() == [], "yoksayilan dosya tekrar sorulmamali"


def test_ayni_dosya_iki_kez_sorulmaz():
    with tempfile.TemporaryDirectory() as d:
        iz = _izleyici(d)
        iz.tara()
        _yaz(os.path.join(d, "c.tap"))
        iz.tara(); iz.tara()
        iz.onayla(os.path.join(d, "c.tap"))
        assert iz.bekleyen() == []
        iz.tara(); iz.tara()
        assert iz.bekleyen() == []


def test_gcode_disi_uzantilar_yoksayilir():
    with tempfile.TemporaryDirectory() as d:
        iz = _izleyici(d)
        iz.tara()
        _yaz(os.path.join(d, "resim.jpg"))
        _yaz(os.path.join(d, "belge.pdf"))
        iz.tara(); iz.tara()
        assert iz.bekleyen() == []


def test_derinlik_siniri_uygulanir():
    with tempfile.TemporaryDirectory() as d:
        iz = _izleyici(d, derinlik=1)
        iz.tara()
        _yaz(os.path.join(d, "a", "b", "c", "derin.tap"))
        iz.tara(); iz.tara()
        assert iz.bekleyen() == [], "derinlik sinirinin otesi taranmamali"


def test_kok_yoksa_cokmez():
    iz = _izleyici(r"Z:\olmayan\klasor")
    assert iz.tara() == []
    assert iz.durum()["aktif"] is True


def test_kok_bossa_izleme_kapali():
    iz = _izleyici("")
    assert iz.aktif is False


def test_sifirla_gecmisi_unutur():
    with tempfile.TemporaryDirectory() as d:
        iz = _izleyici(d)
        iz.tara()
        _yaz(os.path.join(d, "d.tap"))
        iz.tara(); iz.tara()
        assert len(iz.bekleyen()) == 1
        iz.sifirla()
        assert iz.bekleyen() == []


if __name__ == "__main__":
    for ad, fn in sorted(list(globals().items())):
        if ad.startswith("test_"):
            fn()
            print("ok", ad)
    print("Takip/izleme testleri gecti.")


# =====================================================================
# Sifirlama dogrulamasi (regresyon)
# =====================================================================
# GERCEK MAKINEDE OLCULDU: Mach3 Reset (E-Stop) durumundayken SetOEMDRO
# sessizce yok sayilir - hata dondurmez, DRO da degismez. Dogrulama
# olmasaydi arayuz "sifirlandi" der, sifir eski yerinde kalirdi.
class _SessizBaglanti:
    """DRO yazmayi kabul ediyormus gibi yapip aslinda yok sayan baglanti."""

    tur = "sahte"
    bagli = True

    def __init__(self, estop=True):
        self._estop = estop
        self.degerler = {800: 1.0, 801: 2.0, 802: 3.0,
                         83: 1.0, 84: 2.0, 85: 3.0,
                         818: 0.0, 821: 100.0, 39: 0.0}

    def dro(self, kod):
        return self.degerler.get(int(kod), 0.0)

    def dro_yaz(self, kod, deger):
        if self._estop:
            return                     # SESSIZCE yok say (Mach3 davranisi)
        self.degerler[int(kod)] = float(deger)

    def kod(self, satir):
        pass

    def estop(self):
        return self._estop

    def hareket_ediyor(self):
        return False

    def yetenekler(self):
        return {}

    def kapat(self):
        pass


def _denetleyici(estop=True):
    import tempfile as _tf
    from mach3_kontrol import ayarlar as AY, denetleyici as DEN
    d = _tf.mkdtemp()
    a = AY.Ayarlar(os.path.join(d, "ayarlar.json"))
    den = DEN.Denetleyici(a)
    den.baglanti = _SessizBaglanti(estop)
    den._makine = {"X": 1.0, "Y": 2.0, "Z": 3.0, "A": None}
    den._konum = {"X": 1.0, "Y": 2.0, "Z": 3.0, "A": None}
    return den


def test_estop_sirasinda_sifirlama_hata_verir():
    from mach3_kontrol import guvenlik as GUV
    den = _denetleyici(estop=True)
    try:
        den.sifirla("X", 0.0)
        raise AssertionError("sessiz basarisizlik yakalanmaliydi")
    except GUV.GuvenlikHatasi as e:
        assert "RESET" in str(e), str(e)


def test_estop_sirasinda_dro_yazma_hata_verir():
    from mach3_kontrol import guvenlik as GUV
    den = _denetleyici(estop=True)
    try:
        den.dro_ayarla("Z", 12.5)
        raise AssertionError("sessiz basarisizlik yakalanmaliydi")
    except GUV.GuvenlikHatasi as e:
        assert "RESET" in str(e)


def test_estop_yokken_sifirlama_calisir():
    den = _denetleyici(estop=False)
    r = den.sifirla("X", 0.0)
    assert r["dro"] == 0.0
    assert den._konum["X"] == 0.0


def test_estop_sirasinda_profil_uygulanamaz():
    from mach3_kontrol import guvenlik as GUV
    den = _denetleyici(estop=True)
    # Profil ofsetleri MEVCUT degerlerden farkli olmali; aksi halde yazma
    # yok sayilsa bile DRO zaten dogru degeri gosterir ve dogrulama (haklı
    # olarak) sorun gormez.
    den.ayarlar.guncelle({"profiller": {"liste": [{
        "ad": "test",
        "is_sifiri_makine": {"X": 0.5, "Y": 0.5, "Z": 0.5}}]}})
    try:
        den.profil_uygula("test")
        raise AssertionError("profil sessizce uygulanmis gibi gorunmemeli")
    except GUV.GuvenlikHatasi as e:
        assert "RESET" in str(e) or "uygulanamadi" in str(e)


def test_hedef_zaten_dogruysa_hata_verilmez():
    """Yazma yok sayilsa da DRO zaten hedefteyse sonuc dogrudur."""
    den = _denetleyici(estop=True)
    r = den.dro_ayarla("X", 1.0)          # DRO zaten 1.0
    assert r["deger"] == 1.0
