#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Denetleyici
===========
Uygulamanin beyni: baglanti + guvenlik + klavye kilidi + program durumu tek
bir nesnede toplanir. Web arayuzu yalnizca buradaki metotlari cagirir.

ALTIN KURAL: Makineye giden HICBIR hareket komutu, `guvenlik` denetiminden
gecmeden gonderilmez. Tek istisna durdurma komutlaridir (acil durdur / feed
hold) - onlar her kosulda, kontrol kilidi kimde olursa olsun calisir.
"""

import os
import tempfile
import threading
import time

from . import baglanti as BAG
from . import gcode as GC
from . import guvenlik as GUV
from . import kodlar as KOD
from . import klavye_kilidi as KK
from . import sure as SURE

EKSENLER = ("X", "Y", "Z", "A")


class Denetleyici:
    def __init__(self, ayarlar):
        self.ayarlar = ayarlar
        self.gunluk = GUV.Gunluk(
            dosya=os.path.join(os.path.dirname(ayarlar.yol), "olaylar.log"))
        self.guvenlik = GUV.Guvenlik(ayarlar, self.gunluk)
        self.kodlar = KOD.KodHaritasi(ayarlar.get("oem_ezme"))
        self.kilit = GUV.KontrolKilidi(
            ayarlar["guvenlik"].get("kontrol_zaman_asimi_sn", 90.0))

        self.baglanti = None
        self.baglanma_hatasi = ""
        self._konum = {e: None for e in EKSENLER}          # is koordinati
        self._makine = {e: None for e in EKSENLER}         # makine koordinati
        self._ek = {"feed": None, "fro": None, "devir": None,
                    "hareket": None}
        self._son_yoklama = 0.0
        self._yokla_dur = threading.Event()
        self._yokla_is = None
        self._kmt_kilit = threading.RLock()

        # jog durumu
        self._jog_dur = threading.Event()
        self._jog_is = None
        self.jog_bilgi = {"aktif": False, "eksen": None, "yon": 0}
        self.bekci = GUV.KalpAtisiBekcisi(
            self._bekci_durdur,
            zaman_asimi_ms=ayarlar["guvenlik"].get("jog_kalp_atisi_ms", 400),
            en_fazla_sn=ayarlar["guvenlik"].get("max_surekli_jog_sn", 30.0))

        # klavye kilidi + odak bekcisi
        kl = ayarlar["klavye"]
        self.klavye = KK.KlavyeKilidi(
            izinli=kl.get("izinli_tuslar"),
            olay=lambda tur, ad, vk: None,
            panik=lambda: self.acil_durdur("klavye ESC"))
        self.odak = KK.OdakBekcisi(
            baslik_parcasi=kl.get("odak_pencere_baslik", "Mach3"),
            durdur=lambda sebep: self.acil_durdur(sebep),
            hareket_sorgu=lambda: self._ek.get("hareket"),
            geri_al=bool(kl.get("odak_geri_al", True)))

        # program durumu
        self.program = None
        self.program_yol = ""
        self.program_ozet = []
        self.program_sure = None
        self.blok_sureler = []
        self.secili_blok = 0
        self.calisiyor = False
        self.son_calistirma = None

    def ayarlar_yenile(self):
        """Ayarlar arayuzden degistiginde, calisan bilesenlere yansitir.

        (Kalp atisi suresi, jog sure siniri, kontrol zaman asimi, izinli
        tuslar ve izlenen pencere basligi yeniden okunur.)"""
        g = self.ayarlar["guvenlik"]
        k = self.ayarlar["klavye"]
        self.bekci.zaman_asimi = float(g.get("jog_kalp_atisi_ms", 400)) / 1000.0
        self.bekci.en_fazla_sn = float(g.get("max_surekli_jog_sn", 30.0))
        self.kilit.zaman_asimi = float(g.get("kontrol_zaman_asimi_sn", 90.0))
        self.kodlar.uygula(self.ayarlar.get("oem_ezme"))
        self.klavye.izinli_ayarla(k.get("izinli_tuslar", []))
        self.odak.baslik = k.get("odak_pencere_baslik", "Mach3")
        self.odak.geri_al = bool(k.get("odak_geri_al", True))
        return True

    # ==================================================================
    # Baglanti
    # ==================================================================
    def baglan(self):
        self.kapat()
        try:
            self.baglanti = BAG.baglanti_olustur(self.ayarlar)
            self.baglanti.ac()
            self.baglanma_hatasi = ""
            self.gunluk.ekle("baglanti",
                             f"Baglanti kuruldu ({self.baglanti.tur})")
        except Exception as e:                        # noqa: BLE001
            self.baglanti = None
            self.baglanma_hatasi = str(e)
            self.gunluk.ekle("hata", f"Baglanti kurulamadi: {e}")
            raise
        self._yokla_baslat()
        if self.ayarlar["klavye"].get("odak_bekcisi", True):
            self.odak.baslat()
        if self.ayarlar["klavye"].get("kilit_acilista"):
            self.klavye.kilitle()
        return True

    def kapat(self):
        self.jog_bitir()
        self._yokla_dur.set()
        self.odak.durdur_bekci()
        if self.baglanti:
            try:
                self.baglanti.kapat()
            except Exception:                         # noqa: BLE001
                pass
        self.baglanti = None

    @property
    def bagli(self):
        return bool(self.baglanti and self.baglanti.bagli)

    def _gerekli(self):
        if not self.bagli:
            raise GUV.GuvenlikHatasi(
                "Mach3 baglantisi yok. Once baglanin.")
        return self.baglanti

    # -- yoklama ---------------------------------------------------------
    def _yokla_baslat(self):
        self._yokla_dur.clear()
        if self._yokla_is and self._yokla_is.is_alive():
            return
        hz = max(float(self.ayarlar["baglanti"].get("yoklama_hz", 10)), 1.0)
        self._yokla_is = threading.Thread(
            target=self._yokla_dongu, args=(1.0 / hz,), daemon=True,
            name="mach3-yoklama")
        self._yokla_is.start()

    def _yokla_dongu(self, periyot):
        while not self._yokla_dur.wait(periyot):
            if not self.bagli:
                continue
            try:
                self._oku()
            except Exception as e:                    # noqa: BLE001
                self.baglanma_hatasi = str(e)
                if self.baglanti:
                    self.baglanti.bagli = False
                self.gunluk.ekle("hata", f"Yoklama basarisiz: {e}")

    def _oku(self):
        b = self.baglanti
        with self._kmt_kilit:
            for e in self.ayarlar["makine"].get("eksenler", ["X", "Y", "Z"]):
                kod = self.kodlar.eksen_dro(e)
                if kod is not None:
                    self._konum[e] = b.dro(kod)
                mkod = self.kodlar.eksen_dro(e, makine=True)
                if mkod is not None:
                    self._makine[e] = b.dro(mkod)
            self._ek["feed"] = b.dro(self.kodlar.dro["feed"])
            self._ek["fro"] = b.dro(self.kodlar.dro["fro"])
            self._ek["devir"] = b.dro(self.kodlar.dro["gercek_rpm"])
            self._ek["hareket"] = b.hareket_ediyor()
        self._son_yoklama = time.time()

    def ofset(self):
        """makine = is + ofset"""
        o = {}
        for e in EKSENLER:
            i, m = self._konum.get(e), self._makine.get(e)
            o[e] = (m - i) if (i is not None and m is not None) else 0.0
        return o

    # ==================================================================
    # Durum
    # ==================================================================
    def durum(self):
        d = {
            "bagli": self.bagli,
            "tur": self.baglanti.tur if self.baglanti else None,
            "hata": self.baglanma_hatasi,
            "konum": {e: self._konum.get(e) for e in EKSENLER},
            "makine": {e: self._makine.get(e) for e in EKSENLER},
            "ofset": self.ofset(),
            "feed": self._ek.get("feed"),
            "fro": self._ek.get("fro"),
            "devir": self._ek.get("devir"),
            "hareket": self._ek.get("hareket"),
            "yoklama_yasi": (round(time.time() - self._son_yoklama, 2)
                             if self._son_yoklama else None),
            "jog": dict(self.jog_bilgi),
            "klavye": self.klavye.durum(),
            "odak": self.odak.durum(),
            "kilit": self.kilit.durum(),
            "guvenlik": self.guvenlik.ozet(),
            "calisiyor": self.calisiyor,
            "program": {
                "yol": self.program_yol,
                "ad": os.path.basename(self.program_yol),
                "blok_sayisi": len(self.program_ozet),
                "secili_blok": self.secili_blok,
                "sure": self.program_sure,
            } if self.program else None,
            "yetenek": self.baglanti.yetenekler() if self.baglanti else {},
        }
        return d

    # ==================================================================
    # Durdurma (her zaman calisir - kontrol kilidi aranmaz)
    # ==================================================================
    def acil_durdur(self, sebep="operator"):
        """Once jog'u kes, sonra Mach3'e feed hold + stop gonder."""
        self._jog_dur.set()
        self.bekci.bitir()
        self.jog_bilgi = {"aktif": False, "eksen": None, "yon": 0}
        sonuc = {"sebep": sebep, "adimlar": []}
        b = self.baglanti
        if not b or not b.bagli:
            sonuc["adimlar"].append("baglanti yok")
            self.gunluk.ekle("acil", f"ACIL DURDUR ({sebep}) - baglanti yok")
            return sonuc
        for ad, islem in (
                ("jog_durdur", lambda: b.jog_durdur()),
                ("feed_hold", lambda: b.dugme(self.kodlar.buton["feed_hold"])),
                ("stop", lambda: b.dugme(self.kodlar.buton["stop"]))):
            try:
                islem()
                sonuc["adimlar"].append(ad)
            except Exception as e:                    # noqa: BLE001
                sonuc["adimlar"].append(f"{ad} HATA: {e}")
        self.calisiyor = False
        self.gunluk.ekle("acil", f"ACIL DURDUR ({sebep})", sonuc)
        return sonuc

    def feed_hold(self):
        b = self._gerekli()
        b.dugme(self.kodlar.buton["feed_hold"])
        self.gunluk.ekle("kontrol", "Feed hold")
        return True

    def devam(self):
        b = self._gerekli()
        b.dugme(self.kodlar.buton["cycle_start"])
        self.gunluk.ekle("kontrol", "Devam (cycle start)")
        return True

    def durdur(self):
        b = self._gerekli()
        b.dugme(self.kodlar.buton["stop"])
        self.calisiyor = False
        self.gunluk.ekle("kontrol", "Durdur")
        return True

    def _bekci_durdur(self, sebep):
        self.acil_durdur(f"bekci: {sebep}")

    # ==================================================================
    # Hareket
    # ==================================================================
    def _mdi(self, *satirlar):
        b = self._gerekli()
        for s in satirlar:
            b.kod(s)

    def jog_adim(self, eksen, mesafe, feed=None, istemci=None):
        """Bagil (artimli) tek adim: 'X ekseninde sola 25 mm git'."""
        self.kilit.dogrula(istemci)
        eksen = eksen.upper()
        feed = float(feed or min(self.ayarlar["guvenlik"]["max_jog_feed"],
                                 1500.0))
        self.guvenlik.jog_dogrula(eksen, float(mesafe), feed,
                                  self._makine, self._konum)
        self._mdi("G91", f"G1 {eksen}{mesafe:.4f} F{feed:.1f}", "G90")
        self.gunluk.ekle("hareket",
                         f"Jog {eksen}{mesafe:+.3f} mm @F{feed:.0f}")
        return {"eksen": eksen, "mesafe": mesafe, "feed": feed}

    def git(self, hedefler, feed=None, guvenli_z_ile=True, istemci=None):
        """Mutlak konumlama (is koordinati). Gerekirse once Z'yi kurtarir."""
        self.kilit.dogrula(istemci)
        hedefler = {k.upper(): (None if v is None else float(v))
                    for k, v in hedefler.items() if k.upper() in EKSENLER}
        if not any(v is not None for v in hedefler.values()):
            raise GUV.GuvenlikHatasi("Hedef verilmedi")
        self.guvenlik.mutlak_dogrula(hedefler, self._makine, self._konum,
                                     self.ofset())
        gz = float(self.ayarlar["guvenlik"].get("guvenli_z", 20.0))
        satirlar = ["G90"]
        if guvenli_z_ile and self.guvenlik.guvenli_z_gerekli_mi(hedefler,
                                                                self._konum):
            # Z'yi guvenli yukseklige almadan XY'de gezinmek takimi/parcayi
            # kirar; bu yuzden once yukari.
            self.guvenlik.mutlak_dogrula({"Z": gz}, self._makine,
                                         self._konum, self.ofset())
            satirlar.append(f"G0 Z{gz:.3f}")
        xy = " ".join(f"{e}{hedefler[e]:.4f}" for e in ("X", "Y", "A")
                      if hedefler.get(e) is not None)
        if xy:
            satirlar.append((f"G1 {xy} F{float(feed):.1f}" if feed
                             else f"G0 {xy}"))
        if hedefler.get("Z") is not None:
            satirlar.append((f"G1 Z{hedefler['Z']:.4f} F{float(feed):.1f}"
                             if feed else f"G0 Z{hedefler['Z']:.4f}"))
        self._mdi(*satirlar)
        self.gunluk.ekle("hareket", "Git " + " ".join(satirlar[1:]))
        return {"satirlar": satirlar}

    def guvenli_yukselt(self, istemci=None):
        self.kilit.dogrula(istemci)
        gz = float(self.ayarlar["guvenlik"].get("guvenli_z", 20.0))
        self._mdi("G90", f"G0 Z{gz:.3f}")
        self.gunluk.ekle("hareket", f"Guvenli yukseklige cikildi (Z{gz})")
        return {"z": gz}

    # -- surekli jog ------------------------------------------------------
    def jog_basla(self, eksen, yon, feed=None, istemci=None):
        """Basili tut - birak jogu.

        Mach3'un COM arayuzunde guvenilir bir 'surekli jog' cagrisi yoktur;
        bu yuzden KISA artimli adimlar zincirlenir. Her adim ayri ayri
        guvenlik denetiminden gecer ve istemcinin kalp atisi kesilirse zincir
        aninda durur (olu adam butonu).
        """
        self.kilit.dogrula(istemci)
        eksen, yon = eksen.upper(), (1 if yon >= 0 else -1)
        feed = float(feed or 1000.0)
        max_feed = float(self.ayarlar["guvenlik"]["max_jog_feed"])
        feed = min(feed, max_feed)
        self.jog_bitir()
        self._jog_dur.clear()
        self.bekci.baslat()
        self.jog_bilgi = {"aktif": True, "eksen": eksen, "yon": yon,
                          "feed": feed}
        self._jog_is = threading.Thread(
            target=self._jog_dongu, args=(eksen, yon, feed), daemon=True,
            name="jog")
        self._jog_is.start()
        self.gunluk.ekle("hareket", f"Surekli jog basladi {eksen}{yon:+d} "
                                    f"@F{feed:.0f}")
        return {"eksen": eksen, "yon": yon, "feed": feed}

    def _jog_dongu(self, eksen, yon, feed):
        periyot = 0.15
        adim = max(min(feed / 60.0 * periyot * 1.4, 5.0), 0.05)
        while not self._jog_dur.is_set() and self.bekci.aktif:
            try:
                self.guvenlik.jog_dogrula(eksen, yon * adim, feed,
                                          self._makine, self._konum)
                self._mdi("G91", f"G1 {eksen}{yon * adim:.4f} F{feed:.1f}",
                          "G90")
            except GUV.GuvenlikHatasi as e:
                self.gunluk.ekle("guvenlik",
                                 f"Jog durduruldu: {'; '.join(e.sebepler)}")
                break
            except Exception as e:                    # noqa: BLE001
                self.gunluk.ekle("hata", f"Jog hatasi: {e}")
                break
            time.sleep(periyot)
        self.jog_bilgi = {"aktif": False, "eksen": None, "yon": 0}
        self.bekci.bitir()
        try:
            if self.bagli:
                self.baglanti.jog_durdur()
        except Exception:                             # noqa: BLE001
            pass

    def jog_kalp(self, istemci=None):
        """Istemci 'hala basiliyim' der. Kesilirse hareket durur."""
        self.kilit.dogrula(istemci)
        return {"aktif": self.bekci.dokun()}

    def jog_bitir(self, istemci=None):
        self._jog_dur.set()
        self.bekci.bitir()
        if self._jog_is and self._jog_is.is_alive():
            self._jog_is.join(timeout=1.0)
        self.jog_bilgi = {"aktif": False, "eksen": None, "yon": 0}
        return True

    # ==================================================================
    # Sifirlama / is sifiri
    # ==================================================================
    def sifirla(self, eksen, ofset=0.0, istemci=None):
        """Yeni is sifirini, MEVCUT konumdan `ofset` mm oteye koyar.

        ofset = 0   -> klasik "burasi sifir"
        ofset = +5  -> yeni sifir, su anki konumun 5 mm SAGINDA (+X yonunde);
                       DRO bu yuzden -5 gosterir (sifira 5 mm var demektir).
        Ayni mantik Y (+ ileri) ve Z (+ yukari) icin de gecerlidir.
        """
        self.kilit.dogrula(istemci)
        eksen = eksen.upper()
        if eksen not in EKSENLER:
            raise GUV.GuvenlikHatasi(f"Bilinmeyen eksen: {eksen}")
        b = self._gerekli()
        yeni_dro = -float(ofset)
        yontem = (self.ayarlar["makine"].get("sifirlama_yontemi") or "dro")
        if yontem == "g10":
            p = int(self.ayarlar["makine"].get("g10_fikstur", 1))
            b.kod(f"G10 L20 P{p} {eksen}{yeni_dro:.4f}")
        else:
            b.dro_yaz(self.kodlar.eksen_dro(eksen), yeni_dro)
        eski = self._konum.get(eksen)
        self._konum[eksen] = yeni_dro
        self.gunluk.ekle(
            "sifirla",
            f"{eksen} sifirlandi: yeni sifir mevcut konumun {ofset:+.3f} mm "
            f"otesinde (DRO {eski} -> {yeni_dro:.3f})")
        return {"eksen": eksen, "ofset": ofset, "dro": yeni_dro,
                "yontem": yontem}

    def dro_ayarla(self, eksen, deger, istemci=None):
        """DRO'ya dogrudan deger yaz ('bu nokta X=120 olsun')."""
        self.kilit.dogrula(istemci)
        eksen = eksen.upper()
        b = self._gerekli()
        b.dro_yaz(self.kodlar.eksen_dro(eksen), float(deger))
        self._konum[eksen] = float(deger)
        self.gunluk.ekle("sifirla", f"{eksen} DRO = {deger}")
        return {"eksen": eksen, "deger": float(deger)}

    def sifira_git(self, eksenler=("X", "Y"), istemci=None):
        """Is sifirina donus (once guvenli Z)."""
        hedef = {e: 0.0 for e in eksenler if e.upper() in EKSENLER}
        return self.git(hedef, guvenli_z_ile=True, istemci=istemci)

    # ==================================================================
    # MDI
    # ==================================================================
    def mdi(self, satir, istemci=None):
        """Elle G-Code. Gondermeden once kapsam denetimi yapilir."""
        self.kilit.dogrula(istemci)
        satirlar = [s for s in str(satir).splitlines() if s.strip()]
        if not satirlar:
            raise GUV.GuvenlikHatasi("Bos MDI satiri")
        if GC.g91_var_mi(satirlar) and len(satirlar) > 1:
            self.gunluk.ekle("guvenlik", "MDI'da G91 var - dikkat")
        # is koordinatinda kapsam -> makine koordinatina cevir -> limit denetimi
        sebepler = self.guvenlik.satir_dogrula(satirlar, self._konum,
                                               self.ofset(), "MDI")
        if sebepler:
            raise GUV.GuvenlikHatasi("MDI reddedildi", sebepler)
        self._mdi(*satirlar)
        self.gunluk.ekle("mdi", " | ".join(satirlar))
        return {"satirlar": satirlar}

    # ==================================================================
    # Program (G-Code dosyasi)
    # ==================================================================
    def program_yukle(self, yol):
        yol = os.path.abspath(os.path.expanduser(yol))
        if not os.path.isfile(yol):
            raise GUV.GuvenlikHatasi(f"Dosya bulunamadi: {yol}")
        p = GC.Program(yol)
        self.program = p
        self.program_yol = yol
        self.program_ozet = p.ozet()
        self.secili_blok = 0
        self._sure_hesapla()
        self.ayarlar.guncelle({"gcode": {"son_klasor": os.path.dirname(yol)}})
        self.gunluk.ekle("program",
                         f"Yuklendi: {os.path.basename(yol)} "
                         f"({len(p.bloklar)} blok)")
        return self.program_bilgi()

    def _sure_hesapla(self, fro=None):
        if not self.program:
            return
        mak = self.ayarlar["makine"]
        ay = self.ayarlar["gcode"]
        f = fro if fro is not None else (self._ek.get("fro") or 100.0)
        self.program_sure = SURE.program_tahmini(self.program, mak, ay, f,
                                                 self.secili_blok)
        self.blok_sureler = SURE.blok_tahminleri(self.program, mak, ay, f)

    def program_bilgi(self):
        if not self.program:
            return None
        for i, o in enumerate(self.program_ozet):
            if i < len(self.blok_sureler):
                o["sure_sn"] = self.blok_sureler[i]["toplam_sn"]
                o["kalan_sn"] = self.blok_sureler[i]["kalan_sn"]
                o["kalan_metin"] = self.blok_sureler[i]["kalan_metin"]
            o["satir_araligi"] = self.program.satir_araligi(i)
        return {
            "yol": self.program_yol,
            "ad": os.path.basename(self.program_yol),
            "birim": self.program.birim,
            "artimli": self.program.artimli,
            "uyarilar": self.program.uyarilar,
            "bloklar": self.program_ozet,
            "secili_blok": self.secili_blok,
            "sure": self.program_sure,
            "header": len(self.program.header),
            "footer": len(self.program.footer),
        }

    def blok_sec(self, index):
        if not self.program:
            raise GUV.GuvenlikHatasi("Once bir program yukleyin")
        index = max(0, min(int(index), len(self.program.bloklar) - 1))
        self.secili_blok = index
        self._sure_hesapla()
        return {"secili_blok": index, "sure": self.program_sure}

    def program_denetle(self, blok=None):
        """Baslatmadan once: limit kapsami, derinlik, uyarilar."""
        if not self.program:
            raise GUV.GuvenlikHatasi("Once bir program yukleyin")
        b = self.secili_blok if blok is None else int(blok)
        sonuc = self.guvenlik.program_dogrula(self.program, self._konum,
                                              self.ofset(), b)
        sonuc["blok"] = b
        sonuc["sure"] = SURE.program_tahmini(
            self.program, self.ayarlar["makine"], self.ayarlar["gcode"],
            self._ek.get("fro") or 100.0, b)
        if not self.bagli:
            sonuc["uyarilar"].append("Mach3 baglantisi yok (yalnizca denetim).")
        return sonuc

    def program_baslat(self, blok=None, onay=False, istemci=None):
        """Secilen bloktan itibaren calistirir.

        blok=0 ise orijinal dosya, aksi halde 'guvenli yeniden baslatma'
        dosyasi uretilip yuklenir (bkz. gcode.bloktan_baslat_uret).
        """
        self.kilit.dogrula(istemci)
        if not self.program:
            raise GUV.GuvenlikHatasi("Once bir program yukleyin")
        b = self.secili_blok if blok is None else int(blok)
        denetim = self.program_denetle(b)
        if not denetim["gecti"]:
            raise GUV.GuvenlikHatasi("Program guvenlik denetiminden gecmedi",
                                     denetim["hatalar"])
        if not onay:
            raise GUV.GuvenlikHatasi(
                "Onay gerekli: baslatmadan once denetim sonucunu onaylayin",
                denetim["hatalar"] + denetim["uyarilar"])
        self._gerekli()

        if b == 0:
            calisacak = self.program_yol
        else:
            gz = float(self.ayarlar["guvenlik"].get("guvenli_z", 20.0))
            dizin = os.path.join(tempfile.gettempdir(), "mach3_kontrol")
            os.makedirs(dizin, exist_ok=True)
            ad = os.path.splitext(os.path.basename(self.program_yol))[0]
            calisacak = os.path.join(dizin, f"{ad}_blok{b + 1}.tap")
            self.program.bloktan_baslat_yaz(
                b, calisacak, guvenli_z=gz,
                spindle_bekleme=self.ayarlar["gcode"].get(
                    "spindle_bekleme_sn", 4.0))

        self.baglanti.dosya_yukle(calisacak)
        time.sleep(0.4)                     # Mach3'un dosyayi acmasini bekle
        self.baglanti.dugme(self.kodlar.buton["cycle_start"])
        self.calisiyor = True
        self.son_calistirma = {"dosya": calisacak, "blok": b,
                               "zaman": time.time(),
                               "sure": denetim.get("sure")}
        self.gunluk.ekle("program",
                         f"BASLATILDI: blok {b + 1}/"
                         f"{len(self.program.bloklar)} -> "
                         f"{os.path.basename(calisacak)}")
        return {"dosya": calisacak, "blok": b, "denetim": denetim}

    # ==================================================================
    # Klavye kilidi
    # ==================================================================
    def kilit_degistir(self, ac=None):
        istenen = (not self.klavye.aktif) if ac is None else bool(ac)
        if istenen:
            ok = self.klavye.kilitle()
        else:
            ok = self.klavye.ac()
        self.gunluk.ekle("klavye",
                         f"Klavye kilidi {'ACILDI' if istenen else 'KAPANDI'}"
                         + ("" if ok else " (basarisiz)"))
        return self.klavye.durum()
