#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Is takibi ve metrikler
======================
Bir programin calismasi boyunca "ne kadar surdu, nerede kaldi, hangi blok ne
kadar cekti" sorularina cevap uretir. Program bitince bu veriler rapor olarak
disari aktarilir (bkz. `rapor.py`).

ILERLEME NASIL OLCULUYOR?
-------------------------
Mach3'un COM betik arayuzunde **calisan satiri veren bir fonksiyon yoktur**
(GetCurrentLine ve benzerleri bu surumde bulunmuyor - makinede yoklandi).
Bu yuzden ilerleme, DRO'dan okunan konumla **kat edilen yoldan** olculur:

    her yoklamada  d = |konum(t) - konum(t-1)|   (X,Y,Z uzayinda)
    birikimli yol  L = sum(d)

Programin her blogunun yol uzunlugu (`kesim_mm + hizli_mm`) sure tahmininden
zaten biliniyor. Birikimli yol, blok sinirlarinin kumulatif uzunluklariyla
karsilastirilarak "su an hangi bloktayiz" bulunur; blok degisince tamamlanan
blogun GERCEK suresi kaydedilir.

Bu yontem satir geri bildirimine ihtiyac duymaz ve duraklamalardan, besleme
override degisikliklerinden etkilenmez. Sinirlari durustce soylemek gerekirse:
tekrar eden (ayni yerden gecen) yollarda ve elle mudahale edilen (jog)
durumlarda blok eslestirmesi kayabilir; toplam sure ve toplam yol her zaman
dogrudur.
"""

import math
import time

from . import sure as SURE

EKSENLER = ("X", "Y", "Z")


def _uzaklik(a, b):
    """Iki konum sozlugu arasindaki oklid mesafesi (eksik eksen = 0)."""
    if not a or not b:
        return 0.0
    toplam = 0.0
    for e in EKSENLER:
        av, bv = a.get(e), b.get(e)
        if av is None or bv is None:
            continue
        toplam += (av - bv) ** 2
    return math.sqrt(toplam)


class IsTakip:
    """Tek bir program calistirmasinin olculeri."""

    # Iki yoklama arasindaki konum farki bu hizdan daha hizli olamaz; daha
    # buyugu gercek hareket degil, DRO'nun sifirlanmasi/koordinat sistemi
    # degisimidir ve yol olarak SAYILMAZ.
    #
    # Sabit bir mm siniri KULLANILMAZ: yoklama bir anligina takilirsa (ag,
    # Windows) iki okuma arasi 1-2 sn olabilir ve gercek bir hizli hareket
    # yanlislikla elenir. Bu yuzden sinir gecen sureye gore hesaplanir.
    # 300 mm/s = 18.000 mm/dk; hicbir ahsap routerin hizli hizi bunu asmaz.
    EN_YUKSEK_HIZ_MM_SN = 300.0
    EN_AZ_SICRAMA_MM = 25.0

    def __init__(self):
        self.temizle()

    def temizle(self):
        self.aktif = False
        self.bitti = False
        self.dosya = ""
        self.ad = ""
        self.baslangic_blok = 0
        self.baslangic_t = 0.0
        self.bitis_t = 0.0
        self.son_t = 0.0
        self.duraklama_sn = 0.0
        self._duraklama_basi = 0.0
        self.duraklatildi = False
        self.duraklama_sayisi = 0
        self.yol_mm = 0.0
        self.toplam_yol_mm = 0.0
        self.tahmini_sn = 0.0
        self._son_konum = None
        self.bloklar = []          # [{"no", "tahmin_sn", "yol_mm", ...}]
        self._blok_sinir = []      # kumulatif yol (mm) blok sonlari
        self.simdiki_blok = 0
        self.olaylar = []          # [{"t", "tur", "mesaj"}]
        self.fro_gecmis = []       # [{"t", "fro"}]
        self.son_fro = None
        self.profil = ""
        self.taban = None
        self.baslangic_konum = {}
        self.bitis_sebep = ""

    # ------------------------------------------------------------------
    def basla(self, dosya, blok, blok_sureler, tahmin, konum=None,
              profil="", taban=None):
        """Yeni bir calistirma baslat.

        blok_sureler: SURE.blok_tahminleri ciktisi (her blogun tahmini +
                      kesim_mm/hizli_mm bilgisi)
        tahmin:       SURE.program_tahmini ciktisi (secili bloktan sonu)
        """
        import os
        self.temizle()
        self.aktif = True
        self.dosya = dosya or ""
        self.ad = os.path.basename(self.dosya)
        self.baslangic_blok = int(blok or 0)
        self.baslangic_t = self.son_t = time.time()
        self.tahmini_sn = float((tahmin or {}).get("toplam_sn") or 0.0)
        self.profil = profil or ""
        self.taban = taban
        self.baslangic_konum = dict(konum or {})
        self._son_konum = dict(konum or {})

        birikim = 0.0
        for i, b in enumerate(blok_sureler or []):
            if i < self.baslangic_blok:
                continue
            yol = float(b.get("kesim_mm", 0.0)) + float(b.get("hizli_mm", 0.0))
            birikim += yol
            self.bloklar.append({
                "no": i + 1,
                "tahmin_sn": float(b.get("toplam_sn") or 0.0),
                "yol_mm": round(yol, 1),
                "kesim_mm": float(b.get("kesim_mm", 0.0)),
                "gercek_sn": None,
                "basladi_t": self.baslangic_t if i == self.baslangic_blok
                else None,
                "bitti_t": None,
            })
            self._blok_sinir.append(birikim)
        self.toplam_yol_mm = round(birikim, 1)
        self.simdiki_blok = 0
        self.ekle("baslangic", f"{self.ad} - blok {self.baslangic_blok + 1}'den "
                               f"baslatildi")
        return self.ozet()

    # ------------------------------------------------------------------
    def guncelle(self, konum, hareket=None, fro=None):
        """Yoklama dongusunden cagrilir (~10 Hz)."""
        if not self.aktif:
            return
        simdi = time.time()
        dt = max(simdi - (self.son_t or simdi), 0.0)
        self.son_t = simdi

        if fro is not None and fro != self.son_fro:
            self.son_fro = fro
            self.fro_gecmis.append({"t": simdi, "fro": fro})

        d = _uzaklik(konum, self._son_konum)
        sinir = max(self.EN_YUKSEK_HIZ_MM_SN * dt, self.EN_AZ_SICRAMA_MM)
        if d > sinir:
            d = 0.0                       # DRO sicramasi - yok say
        self._son_konum = dict(konum or {})
        if d <= 0.0:
            return
        self.yol_mm += d
        self._blok_ilerlet(simdi)

    def _blok_ilerlet(self, simdi):
        """Birikimli yola gore blok sinirlarini gec."""
        while (self.simdiki_blok < len(self._blok_sinir) - 1 and
               self.yol_mm >= self._blok_sinir[self.simdiki_blok]):
            b = self.bloklar[self.simdiki_blok]
            b["bitti_t"] = simdi
            if b.get("basladi_t"):
                b["gercek_sn"] = round(simdi - b["basladi_t"], 1)
            self.simdiki_blok += 1
            self.bloklar[self.simdiki_blok]["basladi_t"] = simdi

    # ------------------------------------------------------------------
    def duraklat(self, sebep="feed hold"):
        if not self.aktif or self.duraklatildi:
            return
        self.duraklatildi = True
        self.duraklama_sayisi += 1
        self._duraklama_basi = time.time()
        self.ekle("duraklama", sebep)

    def devam_et(self, sebep="devam"):
        if not self.aktif or not self.duraklatildi:
            return
        self.duraklatildi = False
        if self._duraklama_basi:
            self.duraklama_sn += time.time() - self._duraklama_basi
        self._duraklama_basi = 0.0
        self.ekle("devam", sebep)

    def ekle(self, tur, mesaj):
        self.olaylar.append({"t": time.time(), "tur": tur, "mesaj": mesaj})

    def bitir(self, sebep="tamamlandi"):
        if not self.aktif:
            return self.ozet()
        if self.duraklatildi:
            self.devam_et("bitis")
        simdi = time.time()
        b = self.bloklar[self.simdiki_blok] if \
            self.simdiki_blok < len(self.bloklar) else None
        if b and b.get("basladi_t") and b.get("gercek_sn") is None:
            b["bitti_t"] = simdi
            b["gercek_sn"] = round(simdi - b["basladi_t"], 1)
        self.aktif = False
        self.bitti = True
        self.bitis_t = simdi
        self.bitis_sebep = sebep
        self.ekle("bitis", sebep)
        return self.ozet()

    # ------------------------------------------------------------------
    @property
    def gecen_sn(self):
        if not self.baslangic_t:
            return 0.0
        son = self.bitis_t or time.time()
        return max(son - self.baslangic_t, 0.0)

    @property
    def calisan_sn(self):
        """Duraklamalar dusulmus sure."""
        durak = self.duraklama_sn
        if self.duraklatildi and self._duraklama_basi:
            durak += time.time() - self._duraklama_basi
        return max(self.gecen_sn - durak, 0.0)

    def ilerleme(self):
        """0..1 arasi; yol bilinmiyorsa None."""
        if self.toplam_yol_mm <= 0:
            return None
        return max(0.0, min(self.yol_mm / self.toplam_yol_mm, 1.0))

    def kalan_sn(self):
        """Gercek hiza gore kalan sure; yeterli veri yoksa tahmine duser."""
        p = self.ilerleme()
        if p is None:
            return None
        if self.bitti:
            return 0.0
        # En az %2 ilerleme ve 10 sn calisma olmadan hiz tahmini guvenilmez.
        if p > 0.02 and self.calisan_sn > 10:
            return round(self.calisan_sn * (1 - p) / p, 1)
        kalan_oran = 1 - p
        return round(self.tahmini_sn * kalan_oran, 1)

    def ozet(self):
        p = self.ilerleme()
        kalan = self.kalan_sn()
        return {
            "aktif": self.aktif,
            "bitti": self.bitti,
            "dosya": self.dosya,
            "ad": self.ad,
            "baslangic_blok": self.baslangic_blok,
            "baslangic_t": self.baslangic_t,
            "bitis_t": self.bitis_t,
            "bitis_sebep": self.bitis_sebep,
            "gecen_sn": round(self.gecen_sn, 1),
            "gecen_metin": SURE.sure_metni(self.gecen_sn),
            "calisan_sn": round(self.calisan_sn, 1),
            "duraklama_sn": round(self.duraklama_sn, 1),
            "duraklama_sayisi": self.duraklama_sayisi,
            "duraklatildi": self.duraklatildi,
            "tahmini_sn": self.tahmini_sn,
            "tahmini_metin": SURE.sure_metni(self.tahmini_sn),
            "kalan_sn": kalan,
            "kalan_metin": SURE.sure_metni(kalan) if kalan is not None else None,
            "ilerleme": None if p is None else round(p, 4),
            "yol_mm": round(self.yol_mm, 1),
            "toplam_yol_mm": self.toplam_yol_mm,
            "simdiki_blok": (self.bloklar[self.simdiki_blok]["no"]
                             if self.simdiki_blok < len(self.bloklar) else None),
            "blok_sayisi": len(self.bloklar),
            "profil": self.profil,
            "taban": self.taban,
            "fro_degisim": len(self.fro_gecmis),
        }

    def rapor_verisi(self):
        """Disa aktarma icin tam veri (bkz. rapor.py)."""
        d = self.ozet()
        d["bloklar"] = [dict(b) for b in self.bloklar]
        d["olaylar"] = list(self.olaylar)
        d["fro_gecmis"] = list(self.fro_gecmis)
        d["baslangic_konum"] = dict(self.baslangic_konum)
        # sapma: gercek / tahmini (kalibrasyon icin faydali)
        if self.tahmini_sn > 0 and self.bitti:
            d["sapma_orani"] = round(self.gecen_sn / self.tahmini_sn, 3)
        else:
            d["sapma_orani"] = None
        return d
