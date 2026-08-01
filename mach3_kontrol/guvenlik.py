#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Guvenlik katmani
================
Bu modulun tek isi vardir: **makineye giden her komutu, gitmeden once
reddedebilmek.** Denetleyici hicbir hareketi buradan gecirmeden gondermez.

Denetimler
----------
* Yumusak limit    - hedef konum calisma hacminin disina cikiyor mu (pay ile).
* Adim/besleme     - tek jog adimi ve besleme ust sinirlari.
* Dalis derinligi  - elle Z inisi en derin izin verilen degeri asiyor mu.
* Guvenli Z        - XY tasima oncesi takim guvenli yukseklikte mi.
* Program kapsami  - dosyanin TUM hareketleri, mevcut is sifirina gore
                     calisma hacmine sigiyor mu (program baslamadan once).
* Kalp atisi       - surekli jog sirasinda istemci sessiz kalirsa hareket
                     otomatik durur (mobil baglanti koparsa makine kacmaz).
* Kontrol kilidi   - ayni anda yalnizca TEK istemci komut verebilir.
* Olay gunlugu     - gonderilen her komut zaman damgasiyla kaydedilir.

UYARI: Bu katman yazilimdir; yazilim cokebilir, ag kopabilir, Windows
donabilir. **Tek gercek guvenlik cihazi donanim acil-stop butonu ve limit
switchleridir.** Bu program onlarin yerine gecmez.
"""

import threading
import time
from collections import deque

EKSENLER = ("X", "Y", "Z", "A")


class GuvenlikHatasi(Exception):
    """Komut guvenlik denetiminden gecemedi."""

    def __init__(self, mesaj, sebepler=None):
        super().__init__(mesaj)
        self.sebepler = sebepler or [mesaj]


# ----------------------------------------------------------------------
class Gunluk:
    """Son N olayin halka tamponu (kaza sonrasi 'ne oldu' sorusu icin)."""

    def __init__(self, boyut=500, dosya=None):
        self.olaylar = deque(maxlen=boyut)
        self.dosya = dosya
        self._sira = 0
        self._kilit = threading.Lock()

    def ekle(self, tur, mesaj, ek=None):
        with self._kilit:
            self._sira += 1
            kayit = {"id": self._sira, "t": time.time(), "tur": tur,
                     "mesaj": mesaj, "ek": ek or {}}
            self.olaylar.append(kayit)
            if self.dosya:
                try:
                    with open(self.dosya, "a", encoding="utf-8") as f:
                        zaman = time.strftime("%Y-%m-%d %H:%M:%S",
                                              time.localtime(kayit["t"]))
                        f.write(f"{zaman}\t{tur}\t{mesaj}\n")
                except OSError:
                    pass
        return kayit

    def liste(self, adet=100, sonra=0):
        """`sonra`dan buyuk id'li olaylar (arayuze artimli aktarim icin)."""
        with self._kilit:
            olaylar = [o for o in self.olaylar if o["id"] > sonra]
        return olaylar[-adet:]


# ----------------------------------------------------------------------
class KalpAtisiBekcisi:
    """Surekli jog / uzaktan kontrol icin olu adam butonu.

    Istemci `dokun()` cagirmayi birakirsa (sayfa kapandi, telefon uykuya
    gecti, wifi koptu) `dur_geri_cagri` tetiklenir ve hareket durdurulur.
    """

    def __init__(self, dur_geri_cagri, zaman_asimi_ms=400, en_fazla_sn=30.0):
        self.dur = dur_geri_cagri
        self.zaman_asimi = zaman_asimi_ms / 1000.0
        self.en_fazla_sn = en_fazla_sn
        self._son = 0.0
        self._basla = 0.0
        self._aktif = False
        self._olay = threading.Event()
        self._is = None
        self._kilit = threading.Lock()

    def baslat(self):
        with self._kilit:
            simdi = time.time()
            self._son = self._basla = simdi
            self._aktif = True
            self._olay.clear()
            if not (self._is and self._is.is_alive()):
                self._is = threading.Thread(target=self._dongu, daemon=True,
                                            name="jog-bekcisi")
                self._is.start()

    def dokun(self):
        with self._kilit:
            self._son = time.time()
            return self._aktif

    def bitir(self):
        with self._kilit:
            self._aktif = False
        self._olay.set()

    @property
    def aktif(self):
        return self._aktif

    def _dongu(self):
        while not self._olay.wait(0.05):
            with self._kilit:
                if not self._aktif:
                    continue
                simdi = time.time()
                sessiz = simdi - self._son > self.zaman_asimi
                uzun = simdi - self._basla > self.en_fazla_sn
                if not (sessiz or uzun):
                    continue
                self._aktif = False
                sebep = ("istemci kalp atisi kesildi" if sessiz
                         else "surekli jog sure siniri")
            try:
                self.dur(sebep)
            except Exception:                       # noqa: BLE001
                pass


# ----------------------------------------------------------------------
class KontrolKilidi:
    """Ayni anda tek istemcinin komut vermesini saglar.

    Iki kisi (biri bilgisayardan, biri telefondan) ayni anda jog yapamaz;
    kontrol sahibi belli sure sessiz kalirsa kilit kendiliginden acilir.
    """

    def __init__(self, zaman_asimi_sn=90.0):
        self.zaman_asimi = zaman_asimi_sn
        self.sahip = None
        self.ad = ""
        self.son_islem = 0.0
        self._kilit = threading.Lock()

    def durum(self):
        with self._kilit:
            self._sure_dolduysa_birak()
            return {"sahip": self.sahip, "ad": self.ad,
                    "kalan_sn": (round(self.zaman_asimi -
                                       (time.time() - self.son_islem), 1)
                                 if self.sahip else None)}

    def _sure_dolduysa_birak(self):
        if self.sahip and time.time() - self.son_islem > self.zaman_asimi:
            self.sahip = None
            self.ad = ""

    def al(self, istemci, ad="", zorla=False):
        with self._kilit:
            self._sure_dolduysa_birak()
            if self.sahip and self.sahip != istemci and not zorla:
                return False
            self.sahip = istemci
            self.ad = ad
            self.son_islem = time.time()
            return True

    def birak(self, istemci):
        with self._kilit:
            if self.sahip == istemci:
                self.sahip = None
                self.ad = ""
                return True
            return False

    def dogrula(self, istemci):
        """Komut vermeye yetkili mi? Yetkiliyse zaman asimi tazelenir."""
        with self._kilit:
            self._sure_dolduysa_birak()
            if self.sahip is None:
                self.sahip = istemci
                self.son_islem = time.time()
                return True
            if self.sahip != istemci:
                raise GuvenlikHatasi(
                    "Kontrol baska bir istemcide. Once 'Kontrolu al' deyin.")
            self.son_islem = time.time()
            return True


# ----------------------------------------------------------------------
class Guvenlik:
    """Ayarlardaki `guvenlik` bolumunu uygulayan denetim motoru."""

    def __init__(self, ayarlar, gunluk=None):
        self.ayarlar = ayarlar
        self.gunluk = gunluk or Gunluk()

    @property
    def g(self):
        return self.ayarlar["guvenlik"]

    # -- yardimcilar ------------------------------------------------------
    def limit(self, eksen):
        return (self.g.get("yumusak_limit") or {}).get(eksen.upper())

    def limit_araligi(self, eksen):
        lim = self.limit(eksen)
        if not lim:
            return None
        pay = float(self.g.get("limit_payi", 0.0))
        return (float(lim["min"]) + pay, float(lim["max"]) - pay)

    def limitler_aktif(self):
        return bool(self.g.get("yumusak_limit_zorunlu", True))

    # -- hedef denetimi ---------------------------------------------------
    def hedef_dogrula(self, hedefler, etiket="hareket"):
        """hedefler: {"X": makine_hedefi, ...} -> ihlal listesi."""
        sebepler = []
        if not self.limitler_aktif():
            return sebepler
        for eksen, deger in hedefler.items():
            aralik = self.limit_araligi(eksen)
            if not aralik or deger is None:
                continue
            alt, ust = aralik
            if deger < alt - 1e-6:
                sebepler.append(
                    f"{etiket}: {eksen} hedefi {deger:.2f} mm, alt yumusak "
                    f"limit {alt:.2f} mm")
            elif deger > ust + 1e-6:
                sebepler.append(
                    f"{etiket}: {eksen} hedefi {deger:.2f} mm, ust yumusak "
                    f"limit {ust:.2f} mm")
        return sebepler

    def jog_dogrula(self, eksen, mesafe, feed, makine_konum, is_konum=None):
        """Bagil jog denetimi. Hata varsa GuvenlikHatasi firlatir."""
        eksen = eksen.upper()
        sebepler = []
        if eksen not in EKSENLER:
            sebepler.append(f"Bilinmeyen eksen: {eksen}")
        max_adim = float(self.g.get("max_jog_adim", 100.0))
        if abs(mesafe) > max_adim + 1e-9:
            sebepler.append(
                f"Adim {abs(mesafe):.1f} mm, izin verilen en buyuk adim "
                f"{max_adim:.1f} mm")
        max_feed = float(self.g.get("max_jog_feed", 6000.0))
        if feed and feed > max_feed + 1e-9:
            sebepler.append(f"Besleme {feed:.0f} mm/dk, ust sinir "
                            f"{max_feed:.0f} mm/dk")
        mevcut = (makine_konum or {}).get(eksen)
        if mevcut is not None:
            hedef = mevcut + mesafe
            sebepler += self.hedef_dogrula({eksen: hedef}, "Jog")
        # Elle Z inisi derinlik siniri (is koordinatinda degerlendirilir)
        if eksen == "Z" and mesafe < 0:
            derin = self.g.get("max_dalis_derinlik")
            z_is = (is_konum or {}).get("Z")
            if derin is not None and z_is is not None:
                if z_is + mesafe < float(derin) - 1e-6:
                    sebepler.append(
                        f"Z inisi {z_is + mesafe:.2f} mm, en derin izin "
                        f"verilen {float(derin):.2f} mm")
        if sebepler:
            raise GuvenlikHatasi("Jog reddedildi", sebepler)
        return True

    def mutlak_dogrula(self, hedefler, makine_konum, is_konum, ofset):
        """Mutlak (is koordinati) hedef denetimi.

        ofset: makine = is + ofset
        """
        sebepler = []
        makine_hedef = {}
        for eksen, deger in hedefler.items():
            if deger is None:
                continue
            eksen = eksen.upper()
            makine_hedef[eksen] = deger + (ofset or {}).get(eksen, 0.0)
        sebepler += self.hedef_dogrula(makine_hedef, "Konumlama")
        z_hedef = hedefler.get("Z")
        derin = self.g.get("max_dalis_derinlik")
        if z_hedef is not None and derin is not None and z_hedef < float(derin):
            sebepler.append(f"Z hedefi {z_hedef:.2f} mm, en derin izin verilen "
                            f"{float(derin):.2f} mm")
        if sebepler:
            raise GuvenlikHatasi("Hareket reddedildi", sebepler)
        return True

    def guvenli_z_gerekli_mi(self, hedefler, is_konum):
        """XY hareketi var ve takim malzemenin icindeyse True."""
        if not self.g.get("guvenli_z_zorunlu", True):
            return False
        xy_var = any(hedefler.get(e) is not None for e in ("X", "Y"))
        z = (is_konum or {}).get("Z")
        if not xy_var or z is None:
            return False
        return z < float(self.g.get("guvenli_z", 20.0)) - 1e-6

    # -- program denetimi -------------------------------------------------
    def program_dogrula(self, program, is_konum, ofset, baslangic_blok=0):
        """Program baslamadan once tum hareketleri onden tarar.

        Doner: {"gecti": bool, "hatalar": [...], "uyarilar": [...],
                "kapsam": {...}}
        """
        from . import gcode as GC

        hatalar, uyarilar = [], []
        if program.artimli:
            hatalar.append(
                "Dosyada G91 (artimli mod) var; konum kaymasi riski nedeniyle "
                "guvenlik denetimi yapilamiyor.")
        satirlar = list(program.header)
        for b in program.bloklar[baslangic_blok:]:
            satirlar += b
        satirlar += program.footer

        # is koordinatinda kapsam (yaylarin gercek uc noktalari dahil)
        x = y = z = 0.0
        if is_konum:
            x = is_konum.get("X") or 0.0
            y = is_konum.get("Y") or 0.0
            z = is_konum.get("Z") or 0.0
        kapsam = GC.kapsam(satirlar, (x, y, z))

        if self.limitler_aktif():
            for eksen, (mn, mx) in kapsam.items():
                if mn is None:
                    continue
                aralik = self.limit_araligi(eksen)
                if not aralik:
                    continue
                alt, ust = aralik
                o = (ofset or {}).get(eksen, 0.0)
                if mn + o < alt - 1e-6:
                    hatalar.append(
                        f"{eksen}: program {mn:.1f} mm'ye kadar iniyor "
                        f"(makine {mn + o:.1f}), yumusak limit {alt:.1f}")
                if mx + o > ust + 1e-6:
                    hatalar.append(
                        f"{eksen}: program {mx:.1f} mm'ye kadar cikiyor "
                        f"(makine {mx + o:.1f}), yumusak limit {ust:.1f}")

        derin = self.g.get("max_dalis_derinlik")
        if derin is not None and kapsam["Z"][0] is not None:
            if kapsam["Z"][0] < float(derin) - 1e-6:
                uyarilar.append(
                    f"Program Z{kapsam['Z'][0]:.2f} mm'ye iniyor; elle inis "
                    f"siniriniz {float(derin):.2f} mm. (Program icin "
                    f"engellenmez, kontrol edin.)")
        if not self.g.get("limitler_dogrulandi", False):
            uyarilar.append(
                "Yumusak limitler henuz dogrulanmadi (Ayarlar > Guvenlik). "
                "Varsayilan degerler makinenizle uyusmayabilir.")
        if program.uyarilar:
            uyarilar += program.uyarilar
        return {"gecti": not hatalar, "hatalar": hatalar,
                "uyarilar": uyarilar,
                "kapsam": {e: [None if v is None else round(v, 3) for v in k]
                           for e, k in kapsam.items()}}

    def satir_dogrula(self, satirlar, is_konum, ofset, etiket="MDI"):
        """Elle girilen satirlarin (MDI) hedeflerini limitlere gore denetler."""
        from . import gcode as GC

        k = GC.kapsam(satirlar, (is_konum.get("X") or 0.0,
                                 is_konum.get("Y") or 0.0,
                                 is_konum.get("Z") or 0.0))
        sebepler = []
        for eksen, (mn, mx) in k.items():
            for v in (mn, mx):
                if v is not None:
                    sebepler += self.hedef_dogrula(
                        {eksen: v + (ofset or {}).get(eksen, 0.0)}, etiket)
        derin = self.g.get("max_dalis_derinlik")
        if derin is not None and k["Z"][0] is not None and \
                k["Z"][0] < float(derin) - 1e-6:
            sebepler.append(f"{etiket}: Z{k['Z'][0]:.2f} mm, en derin izin "
                            f"verilen {float(derin):.2f} mm")
        return sorted(set(sebepler))

    # -- durum ------------------------------------------------------------
    def ozet(self):
        g = self.g
        return {
            "limitler_dogrulandi": bool(g.get("limitler_dogrulandi")),
            "yumusak_limit": g.get("yumusak_limit"),
            "yumusak_limit_zorunlu": bool(g.get("yumusak_limit_zorunlu")),
            "max_jog_adim": g.get("max_jog_adim"),
            "max_jog_feed": g.get("max_jog_feed"),
            "guvenli_z": g.get("guvenli_z"),
            "max_dalis_derinlik": g.get("max_dalis_derinlik"),
            "max_surekli_jog_sn": g.get("max_surekli_jog_sn"),
            "home_zorunlu": bool(g.get("home_zorunlu")),
        }
