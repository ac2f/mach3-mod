#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Yeni G-Code dosyasi izleyicisi
==============================
Tipik kullanim: isler tek bir kok klasorun altinda, tarih/firma/malzeme gibi
ic ice klasorlere yaziliyor:

    D:\\isler\\30-01-2026\\firma-XXX\\001-malzemeX\\001.tap

CAM'den yeni bir dosya cikinca arayuz bunu kendiliginden fark eder ve
"bu dosya acilsin mi?" diye sorar. **Onay verilmeden hicbir dosya
yuklenmez** - yanlislikla baska bir isin dosyasi yuklenmesin diye.

Tasarim notlari
---------------
* `watchdog` gibi bir kutuphane KULLANILMAZ (projenin "ek bagimlilik yok"
  kurali). Periyodik tarama yeterlidir: kesim dosyalari saniyede bir
  degismez.
* Tarama derinligi sinirlidir; kok klasor yanlislikla C:\\ secilirse
  program kilitlenmesin.
* Dosya HALA YAZILIYOR olabilir (CAM kaydediyordur). Bu yuzden yeni gorulen
  dosya once "aday" olur; ancak imzasi (mtime + boyut) iki ardisik taramada
  AYNI kalinca kullaniciya sorulur.
"""

import os
import threading
import time

VARSAYILAN_UZANTI = {".tap", ".nc", ".gcode", ".ngc", ".txt", ".cnc", ".g"}


class DosyaIzleyici:
    """Kok klasoru periyodik tarayip yeni dosyalari biriktirir."""

    def __init__(self, ayarlar, uzantilar=None, gunluk=None):
        self.ayarlar = ayarlar
        self.uzantilar = uzantilar or VARSAYILAN_UZANTI
        self.gunluk = gunluk
        self._gorulen = {}          # yol -> imza (onceki tarama)
        self._aday = {}             # yol -> imza (kararli olmayi bekliyor)
        self._bekleyen = {}         # yol -> kayit (kullaniciya sorulacak)
        self._bildirildi = set()    # bir kez soruldu, tekrar sorma
        self._yoksayilan = set()
        self._ilk_tarama = True
        self._dur = threading.Event()
        self._is = None
        self._kilit = threading.RLock()
        self.son_tarama = 0.0
        self.son_hata = ""
        self.taranan_dosya = 0

    # -- ayarlar --------------------------------------------------------
    @property
    def _ay(self):
        return self.ayarlar.get("izleme") or {}

    @property
    def kok(self):
        k = (self._ay.get("kok") or "").strip()
        return os.path.abspath(os.path.expanduser(k)) if k else ""

    @property
    def aktif(self):
        return bool(self._ay.get("aktif", True)) and bool(self.kok)

    # -- yasam dongusu --------------------------------------------------
    def baslat(self):
        if self._is and self._is.is_alive():
            return False
        self._dur.clear()
        self._is = threading.Thread(target=self._dongu, daemon=True,
                                    name="dosya-izleyici")
        self._is.start()
        return True

    def durdur(self):
        self._dur.set()

    def _dongu(self):
        while not self._dur.is_set():
            aralik = float(self._ay.get("aralik_sn", 5.0) or 5.0)
            if self.aktif:
                try:
                    self.tara()
                    self.son_hata = ""
                except Exception as e:                # noqa: BLE001
                    self.son_hata = str(e)
            self._dur.wait(max(aralik, 1.0))

    # -- tarama ---------------------------------------------------------
    def _dosyalar(self):
        """Kok altindaki G-Code dosyalarini (derinlik sinirli) gezer."""
        kok = self.kok
        if not kok or not os.path.isdir(kok):
            return
        max_derinlik = int(self._ay.get("derinlik", 4) or 4)
        kok_parca = kok.rstrip(os.sep).count(os.sep)
        for dizin, altlar, dosyalar in os.walk(kok):
            if dizin.rstrip(os.sep).count(os.sep) - kok_parca >= max_derinlik:
                altlar[:] = []
            altlar[:] = [a for a in altlar
                         if not a.startswith((".", "~", "$"))]
            for ad in dosyalar:
                if ad.startswith((".", "~", "$")):
                    continue
                if os.path.splitext(ad)[1].lower() in self.uzantilar:
                    yield os.path.join(dizin, ad)

    def _kayit(self, yol, imza, simdi):
        try:
            goreli = os.path.relpath(os.path.dirname(yol), self.kok)
        except ValueError:                            # farkli surucu
            goreli = os.path.dirname(yol)
        return {"yol": yol, "ad": os.path.basename(yol),
                "klasor": os.path.dirname(yol), "goreli": goreli,
                "boyut": imza[1], "zaman": imza[0], "bulundu": simdi}

    def tara(self):
        """Tek tur tarama; kararli hale gelen yeni dosyalari bildirir."""
        esik = float(self._ay.get("yeni_sayilir_dk", 120) or 120) * 60.0
        simdi = time.time()
        simdiki = {}
        for yol in self._dosyalar():
            try:
                st = os.stat(yol)
            except OSError:
                continue
            simdiki[yol] = (st.st_mtime, st.st_size)

        yeni = []
        with self._kilit:
            for yol, imza in simdiki.items():
                if (yol in self._yoksayilan or yol in self._bekleyen
                        or yol in self._bildirildi):
                    continue
                if yol in self._aday:
                    # Imza degismediyse dosya artik yaziliyor degil -> sor.
                    if self._aday[yol] == imza:
                        del self._aday[yol]
                        kayit = self._kayit(yol, imza, simdi)
                        self._bekleyen[yol] = kayit
                        self._bildirildi.add(yol)
                        yeni.append(kayit)
                    else:
                        self._aday[yol] = imza
                    continue
                onceki = self._gorulen.get(yol)
                if onceki is None:
                    # Ilk taramada TUM eski dosyalari sormayiz; yalnizca
                    # yakin zamanda degismis olanlari aday yapariz.
                    if self._ilk_tarama and (simdi - imza[0]) > esik:
                        continue
                    self._aday[yol] = imza
                elif onceki != imza:
                    self._aday[yol] = imza        # yeniden kaydedilmis

            # Silinen dosyalari adaylardan dusur
            for yol in [y for y in self._aday if y not in simdiki]:
                del self._aday[yol]

            self._gorulen = simdiki
            self.taranan_dosya = len(simdiki)
            self.son_tarama = simdi
            self._ilk_tarama = False

        for k in yeni:
            if self.gunluk:
                self.gunluk.ekle("izleme", f"Yeni G-Code dosyasi: {k['ad']}")
        return yeni

    # -- bekleyenler ----------------------------------------------------
    def bekleyen(self):
        with self._kilit:
            return sorted(self._bekleyen.values(),
                          key=lambda k: k["zaman"], reverse=True)

    def yoksay(self, yol=None):
        """Kullanici 'acma' dedi: bir daha sorulmaz."""
        with self._kilit:
            if yol:
                self._bekleyen.pop(yol, None)
                self._yoksayilan.add(yol)
            else:
                self._yoksayilan.update(self._bekleyen)
                self._bekleyen.clear()
        return True

    def onayla(self, yol):
        """Kullanici 'ac' dedi: bekleyenlerden dusur."""
        with self._kilit:
            self._bekleyen.pop(yol, None)
        return True

    def sifirla(self):
        """Kok degisince gecmisi unut."""
        with self._kilit:
            self._gorulen.clear()
            self._aday.clear()
            self._bekleyen.clear()
            self._bildirildi.clear()
            self._yoksayilan.clear()
            self._ilk_tarama = True
        return True

    def durum(self):
        return {
            "aktif": self.aktif,
            "kok": self.kok,
            "son_tarama": self.son_tarama,
            "taranan_dosya": self.taranan_dosya,
            "bekleyen": self.bekleyen(),
            "hata": self.son_hata,
        }
