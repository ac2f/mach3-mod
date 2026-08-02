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
from . import izleyici as IZL
from . import is_takip as IST
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
        self._makine = {e: None for e in EKSENLER}         # Mach3 ham okumasi
        # tezgah koordinati = makine okumasi - referans (bkz. referans_al)
        self._tezgah = {e: None for e in EKSENLER}
        self.referans = {e: None for e in EKSENLER}
        self.referans_zaman = None
        self._ek = {"feed": None, "fro": None, "devir": None,
                    "hareket": None, "estop": None}
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

        # is takibi (gecen sure, ilerleme, blok sureleri) + son rapor
        self.takip = IST.IsTakip()
        self.son_rapor = None
        self._bitis_bekle = 0        # program bitisini dogrulamak icin sayac

        # yeni G-Code dosyasi izleyicisi
        self.izleyici = IZL.DosyaIzleyici(
            ayarlar, uzantilar=None, gunluk=self.gunluk)
        self._izleme_kok = self.izleyici.kok
        self.izleyici.baslat()

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
        # Izleme kokU degistiyse gecmisi unut, yoksa eski koke ait dosyalar
        # "yeni" gibi gorunur.
        yeni_kok = self.izleyici.kok
        if yeni_kok != getattr(self, "_izleme_kok", yeni_kok):
            self.izleyici.sifirla()
        self._izleme_kok = yeni_kok
        self.izleyici.baslat()
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
        # Mach3 yeniden baslatildiginda makine sifiri kayar; onceki
        # referans artik gecersizdir ve MUTLAKA dusurulmelidir.
        self.referans = {e: None for e in EKSENLER}
        self._tezgah = {e: None for e in EKSENLER}
        self.referans_zaman = None
        # Arka uc "makine koordinatim zaten tezgah sifirinda" diyorsa
        # (simulasyon) referans kendiliginden alinmis sayilir.
        try:
            if self.baglanti.referansli():
                self.referans = {e: 0.0 for e in EKSENLER}
                self.referans_zaman = time.time()
        except Exception:                             # noqa: BLE001
            pass
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
                    r = self.referans.get(e)
                    if r is not None:
                        self._tezgah[e] = self._makine[e] - r
                    elif self.ayarlar["guvenlik"].get("referans_zorunlu",
                                                      True):
                        # Referans yok ve zorunlu: tezgah koordinati
                        # BILINMIYOR. 0 varsaymak yanlis guven verir;
                        # None birakilir, guvenlik katmani reddeder.
                        self._tezgah[e] = None
                    else:
                        # Operator "uyar ama izin ver" dedi: eski
                        # (KORUMASIZ) davranis - ham makine okumasi
                        # kullanilir. Limitler bu halde tezgahin neresini
                        # gosterdigini bilemez.
                        self._tezgah[e] = self._makine[e]
            self._ek["feed"] = b.dro(self.kodlar.dro["feed"])
            self._ek["fro"] = b.dro(self.kodlar.dro["fro"])
            self._ek["devir"] = b.dro(self.kodlar.dro["gercek_rpm"])
            self._ek["hareket"] = b.hareket_ediyor()
            self._ek["estop"] = b.estop()
        self._son_yoklama = time.time()
        if self.takip.aktif:
            self.takip.guncelle(dict(self._konum), self._ek.get("hareket"),
                                self._ek.get("fro"))
            self._bitis_yokla()

    def _bitis_yokla(self):
        """Program bitti mi? Mach3 bunu bildirmedigi icin hareketten anlariz.

        `IsMoving` uzun sure False kalirsa (ve feed hold'da degilsek) is
        bitmis sayilir. Duraklama sirasinda sayac ilerlemez.
        """
        if not self.calisiyor or self.takip.duraklatildi:
            self._bitis_bekle = 0
            return
        if self._ek.get("hareket"):
            self._bitis_bekle = 0
            return
        self._bitis_bekle += 1
        hz = max(float(self.ayarlar["baglanti"].get("yoklama_hz", 10)), 1.0)
        if self._bitis_bekle < int(hz * 6):        # ~6 sn hareketsizlik
            return
        p = self.takip.ilerleme()
        sebep = ("tamamlandi" if p is None or p >= 0.97
                 else f"yarida kaldi (%{p * 100:.0f})")
        self._program_bitti(sebep)

    def _program_bitti(self, sebep):
        self._bitis_bekle = 0
        self.calisiyor = False
        if not self.takip.aktif:
            return
        self.takip.bitir(sebep)
        self.son_rapor = self.takip.rapor_verisi()
        self.gunluk.ekle(
            "program",
            f"BITTI: {self.takip.ad} - {sebep} "
            f"({self.takip.ozet()['gecen_metin']})")

    def ofset(self):
        """tezgah = is + ofset

        Referans alinmamis eksenlerde tezgah koordinati bilinmedigi icin
        ofset de bilinmez; 0 dondurmek yanlis olur ama arayuz sozlugu
        beklediginden 0 verilir. Guvenlik denetimleri bu duruma `_tezgah`
        icindeki None uzerinden bakar (ofsete degil).
        """
        o = {}
        for e in EKSENLER:
            i, t = self._konum.get(e), self._tezgah.get(e)
            o[e] = (t - i) if (i is not None and t is not None) else 0.0
        return o

    # ==================================================================
    # Tezgah referansi
    # ==================================================================
    # NEDEN GEREKLI: referans switch'i olmayan makinelerde Mach3'un makine
    # koordinati tezgahin kosesini degil, Mach3 acildiginda kafanin oldugu
    # rastgele noktayi gosterir. Yumusak limitler bu yuzden tek basina
    # korumaz. Operator kafayi SIFIR KOSESINE park edip referans alir;
    # o andaki makine okumasi tezgah sifiri sayilir.
    def referans_al(self, eksenler=("X", "Y", "Z"), degerler=None,
                    istemci=None):
        """Kafanin SU ANKI fiziksel yerinin tezgah koordinatini bildirir.

        `degerler` verilmezse bulunulan nokta tezgah SIFIRI sayilir (kafa
        sifir kosesindeyken kullanilir). `degerler={"X": 2099}` verilirse
        "su an tezgah X'i 2099" denmis olur; referans buna gore hesaplanir:

            referans = makine_okumasi - bildirilen_tezgah_koordinati

        NEDEN: operatorun kafayi her seferinde sifir kosesine goturmesi
        pratik degil. Mach3'te oldugu gibi bulunulan yerin koordinatini
        yazabilmek gerekiyor; yoksa referans yanlis yerde alinir ve
        yumusak limitler tezgahi korumaz.
        """
        self.kilit.dogrula(istemci)
        self._gerekli()
        self._dururken("Referans alma")
        degerler = {str(k).upper(): v for k, v in (degerler or {}).items()}
        # Bildirilen koordinat limit araligi disindaysa referans tutarsizdir:
        # kabul edilirse makine daha ilk hareketten once kilitlenir ve
        # operator kendini disari cikamaz halde bulur.
        for e, v in degerler.items():
            if v is None or e not in EKSENLER:
                continue
            # PAYSIZ (ham) aralik: pay hareketi kenardan uzak tutmak icindir,
            # kafanin fiziksel olarak tam kenarda durdugunu bildirmek mesru.
            lim = self.guvenlik.limit(e)
            if not lim or not self.guvenlik.limitler_aktif():
                continue
            alt, ust = float(lim["min"]), float(lim["max"])
            if float(v) < alt - 1e-6 or float(v) > ust + 1e-6:
                raise GUV.GuvenlikHatasi(
                    f"{e} icin bildirilen tezgah koordinati {float(v):.2f} "
                    f"mm, tanimli tezgah araliginin ({alt:g}..{ust:g}) "
                    "disinda. Kafanin gercek yerini ya da yumusak limit "
                    "ayarini kontrol edin.")
        alinan, eksik = {}, []
        for e in eksenler:
            e = e.upper()
            if e not in EKSENLER:
                continue
            m = self._makine.get(e)
            if m is None:
                eksik.append(e)
                continue
            t = degerler.get(e)
            t = 0.0 if t is None else float(t)
            self.referans[e] = m - t
            self._tezgah[e] = t
            alinan[e] = round(t, 4)
        if not alinan:
            raise GUV.GuvenlikHatasi(
                "Referans alinamadi: makine koordinati okunamiyor "
                f"({', '.join(eksik) or 'tum eksenler'}). OEM kodlarini "
                "(makine_x/y/z) kontrol edin.")
        self.referans_zaman = time.time()
        self.gunluk.ekle(
            "referans",
            "Tezgah referansi alindi (bulunulan nokta = tezgah): " +
            ", ".join(f"{e}={v} [makine {round(self._makine[e], 4)}]"
                      for e, v in alinan.items()) +
            (f" (okunamayan: {', '.join(eksik)})" if eksik else ""))
        return self.referans_durumu()

    # ------------------------------------------------------------------
    # Mach3'un MAKINE koordinatini bulunulan noktada sifirlama
    # ------------------------------------------------------------------
    # `SetOEMDRO` makine DRO'suna (83/84/85) YAZAMAZ - gercek makinede
    # olculdu, sessizce yok sayilir. Mach3'te makine koordinatini
    # degistirmenin tek yolu Ref islemidir ve yalnizca SIFIRLAYABILIR;
    # istege bagli bir sayi (or. 2099) yazilamaz.
    #
    # DIKKAT: home switch'i TANIMLI eksende Ref, switch'i aramak icin
    # EKSENI HAREKET ETTIRIR. Switch'i olmayan kurulumda ise hic hareket
    # etmeden bulunulan noktayi makine sifiri yapar.
    def makine_sifirla(self, eksenler=("X", "Y", "Z"), istemci=None):
        self.kilit.dogrula(istemci)
        b = self._gerekli()
        self._dururken("Makine sifirlama")
        if self._ek.get("estop"):
            raise GUV.GuvenlikHatasi(
                "Mach3 RESET (E-Stop) durumunda; bu haldeyken Ref islemi "
                "calismaz. Once Mach3'te Reset'i kaldirin.")
        # Fiziksel konum degismedigi icin TEZGAH koordinati da degismemeli;
        # islem sonrasi referans yeni makine okumasina gore tazelenir.
        onceki_tezgah = {e: self._tezgah.get(e) for e in EKSENLER}
        onceki_makine = {e: self._makine.get(e) for e in EKSENLER}

        istenen = [e.upper() for e in eksenler if e.upper() in EKSENLER]
        for e in istenen:
            kod = self.kodlar.buton.get(f"ref_{e.lower()}")
            if kod is None:
                continue
            b.dugme(kod)

        # Ref gercekten isledi mi? Yanlis OEM kodu, kapali Ref, ya da
        # Reset durumunda hicbir sey olmaz ve "sifirlandi" demek yanlis
        # guven verir. Makine DRO'sunu geri okuyup dogruluyoruz.
        olan, olmayan = {}, []
        for e in istenen:
            okunan = None
            for _ in range(20):                       # ~3 sn
                time.sleep(0.15)
                mkod = self.kodlar.eksen_dro(e, makine=True)
                try:
                    okunan = b.dro(mkod) if mkod is not None else None
                except Exception:                     # noqa: BLE001
                    okunan = None
                if okunan is not None and abs(okunan) <= 0.005:
                    break
            if okunan is not None and abs(okunan) <= 0.005:
                olan[e] = round(okunan, 4)
            else:
                olmayan.append(
                    f"{e} (makine DRO {onceki_makine.get(e)} -> {okunan})")

        self._oku()
        # Tezgah koordinatini koru: kafa yerinden kimildamadi.
        for e in istenen:
            if e not in olan:
                continue
            t = onceki_tezgah.get(e)
            self.referans[e] = self._makine.get(e, 0.0) - (t or 0.0)
            self._tezgah[e] = t if t is not None else 0.0
        if olan:
            self.referans_zaman = time.time()
        self.gunluk.ekle(
            "referans",
            "Makine sifiri kuruldu: " +
            (", ".join(f"{e}={v}" for e, v in olan.items()) or "hicbiri") +
            (f" | ISLEMEYEN: {'; '.join(olmayan)}" if olmayan else ""))
        if olmayan and not olan:
            raise GUV.GuvenlikHatasi(
                "Makine sifiri kurulamadi: Ref komutu makine DRO'sunu "
                "degistirmedi (" + "; ".join(olmayan) + "). Muhtemel "
                "sebepler: OEM Ref buton kodu bu kurulumda farkli "
                "(ayarlardan oem_ezme.buton ile duzeltilebilir), Mach3 "
                "Reset durumunda, ya da eksen icin Ref kapali.",
                olmayan)
        return {"sifirlanan": olan, "islemeyen": olmayan,
                "referans": self.referans_durumu()}

    def referans_birak(self, istemci=None):
        self.kilit.dogrula(istemci)
        self.referans = {e: None for e in EKSENLER}
        self._tezgah = {e: None for e in EKSENLER}
        self.referans_zaman = None
        self.gunluk.ekle("referans", "Tezgah referansi dusuruldu")
        return self.referans_durumu()

    def referans_durumu(self):
        gerekli = [e for e in self.ayarlar["makine"].get(
            "eksenler", ["X", "Y", "Z"])]
        eksik = [e for e in gerekli if self.referans.get(e) is None]
        return {
            "zorunlu": bool(self.ayarlar["guvenlik"].get("referans_zorunlu",
                                                         True)),
            "alindi": not eksik,
            "eksik": eksik,
            "referans": {e: self.referans.get(e) for e in EKSENLER},
            "zaman": self.referans_zaman,
        }

    def _dururken(self, islem):
        """Konum kaydeden islemleri hareket halindeyken reddeder.

        NEDEN: referans ve is sifiri "kafa SU AN surada" varsayimina dayanir.
        Eksen hareket ederken okunan DRO daha yazilirken eskir; kaydedilen
        nokta gercek noktadan metrelerce sapabilir ve yumusak limitler
        sessizce yanlis yere kayar.
        """
        # CANLI okuma sart: `_ek["hareket"]` yoklama onbellegidir ve 10 Hz'de
        # 100 ms'ye kadar bayat olabilir. Jog/MDI gonderdikten hemen sonra
        # referans alinirsa onbellek hala "duruyor" der ve koruma kacar.
        hareket = None
        try:
            if self.baglanti:
                hareket = self.baglanti.hareket_ediyor()
        except Exception:                     # noqa: BLE001
            hareket = None
        if hareket is None:
            hareket = self._ek.get("hareket")
        if hareket:
            raise GUV.GuvenlikHatasi(
                f"{islem} hareket sirasinda yapilamaz: okunan konum aninda "
                "eskir ve yanlis noktaya kaydedilir. Eksenler tam durunca "
                "tekrar deneyin.")

    def _referans_gerekli(self, eksenler):
        """Referanssiz eksende hareketi engeller (fail-closed)."""
        if not self.ayarlar["guvenlik"].get("referans_zorunlu", True):
            return
        eksik = [e.upper() for e in eksenler
                 if e and self.referans.get(e.upper()) is None]
        if eksik:
            raise GUV.GuvenlikHatasi(
                "Tezgah referansi alinmadi (" + ", ".join(eksik) + "). "
                "Referans olmadan yumusak limitler tezgahin neresini "
                "gosterdigini bilemez, bu yuzden hareket engellendi. "
                "Kafayi tezgahin SIFIR KOSESINE getirip 'Referans al' "
                "deyin.", [f"{e} ekseni referanssiz" for e in eksik])

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
            "tezgah": {e: self._tezgah.get(e) for e in EKSENLER},
            "referans": self.referans_durumu(),
            "ofset": self.ofset(),
            "feed": self._ek.get("feed"),
            "fro": self._ek.get("fro"),
            "devir": self._ek.get("devir"),
            "hareket": self._ek.get("hareket"),
            # Reset/E-Stop: bu durumdayken DRO yazma (sifirlama) calismaz
            "estop": self._ek.get("estop"),
            "yoklama_yasi": (round(time.time() - self._son_yoklama, 2)
                             if self._son_yoklama else None),
            "jog": dict(self.jog_bilgi),
            "klavye": self.klavye.durum(),
            "odak": self.odak.durum(),
            "kilit": self.kilit.durum(),
            "guvenlik": self.guvenlik.ozet(),
            # Tabana kalan pay: arayuzde surekli gorunur, is sifiri
            # degistikce degismez (makine koordinatindan hesaplanir).
            "taban_payi": self.guvenlik.taban_payi(self._tezgah.get("Z")),
            "takip": self.takip.ozet(),
            "rapor_hazir": bool(self.son_rapor),
            "izleme": self.izleyici.durum(),
            "profiller": self.profiller(),
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
        if self.takip.aktif:
            self._program_bitti(f"acil durdur: {sebep}")
        self.calisiyor = False
        self.gunluk.ekle("acil", f"ACIL DURDUR ({sebep})", sonuc)
        return sonuc

    def feed_hold(self):
        b = self._gerekli()
        b.dugme(self.kodlar.buton["feed_hold"])
        self.takip.duraklat("feed hold")
        self.gunluk.ekle("kontrol", "Feed hold")
        return True

    def devam(self):
        b = self._gerekli()
        b.dugme(self.kodlar.buton["cycle_start"])
        self.takip.devam_et("cycle start")
        self.gunluk.ekle("kontrol", "Devam (cycle start)")
        return True

    def durdur(self):
        b = self._gerekli()
        b.dugme(self.kodlar.buton["stop"])
        self._program_bitti("operator durdurdu")
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
        self._referans_gerekli([eksen])
        feed = float(feed or min(self.ayarlar["guvenlik"]["max_jog_feed"],
                                 1500.0))
        self.guvenlik.jog_dogrula(eksen, float(mesafe), feed,
                                  self._tezgah, self._konum)
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
        self._referans_gerekli(
            [e for e, v in hedefler.items() if v is not None] + ["Z"])
        self.guvenlik.mutlak_dogrula(hedefler, self._tezgah, self._konum,
                                     self.ofset())
        gz = float(self.ayarlar["guvenlik"].get("guvenli_z", 20.0))
        satirlar = ["G90"]
        if guvenli_z_ile and self.guvenlik.guvenli_z_gerekli_mi(hedefler,
                                                                self._konum):
            # Z'yi guvenli yukseklige almadan XY'de gezinmek takimi/parcayi
            # kirar; bu yuzden once yukari.
            self.guvenlik.mutlak_dogrula({"Z": gz}, self._tezgah,
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
        self._referans_gerekli(["Z"])
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
        self._referans_gerekli([eksen])
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
                                          self._tezgah, self._konum)
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
    def _sifir_yaz_dogrula(self, eksen, hedef_is_degeri):
        """Is DRO'suna yazar ve GERCEKTEN yazildigini geri okuyarak dogrular.

        NEDEN: Mach3 **Reset (E-Stop) durumundayken SetOEMDRO'yu sessizce
        yok sayar** - hata dondurmez, DRO da degismez. Dogrulama olmadan
        "sifirlandi" denir, oysa sifir eski yerinde kalir; sonraki hareket
        beklenenden bambaska bir yerde olur. Gercek makinede olculdu.

        Ayni sekilde `G10 L20` yontemi de bazi kurulumlarda etki etmiyor.
        """
        b = self._gerekli()
        kod = self.kodlar.eksen_dro(eksen)
        yontem = (self.ayarlar["makine"].get("sifirlama_yontemi") or "dro")
        if yontem == "g10":
            p = int(self.ayarlar["makine"].get("g10_fikstur", 1))
            b.kod(f"G10 L20 P{p} {eksen}{hedef_is_degeri:.4f}")
        else:
            b.dro_yaz(kod, hedef_is_degeri)

        # Mach3 DRO yazmayi hemen islemez; tek bir kisa beklemeyle okumak
        # YANLIS ALARM uretir (gercek makinede olculdu). Deger tutana kadar
        # kisa araliklarla yoklanir, en fazla ~1.2 sn.
        okunan = None
        for _ in range(8):
            time.sleep(0.15)
            try:
                okunan = b.dro(kod)
            except Exception:             # noqa: BLE001
                okunan = None
            if okunan is not None and abs(okunan - hedef_is_degeri) <= 0.005:
                break
        if okunan is not None and abs(okunan - hedef_is_degeri) > 0.005:
            estop = b.estop()
            if estop:
                raise GUV.GuvenlikHatasi(
                    f"{eksen} sifirlanamadi: Mach3 RESET (E-Stop) durumunda. "
                    "Mach3'te Reset dugmesine basip kirmizi yanip sonmeyi "
                    "durdurun, sonra tekrar deneyin. "
                    "(DRO yazma sessizce yok sayilmisti.)")
            raise GUV.GuvenlikHatasi(
                f"{eksen} sifirlanamadi: DRO {hedef_is_degeri:.4f} olmaliydi "
                f"ama {okunan:.4f} okundu."
                + (f" Sifirlama yontemi '{yontem}' bu kurulumda "
                   "calismiyor olabilir (Ayarlar > Makine)."
                   if yontem == "g10" else ""))
        self._konum[eksen] = hedef_is_degeri
        return okunan

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
        self._dururken("Sifirlama")
        eski = self._konum.get(eksen)
        yeni_dro = -float(ofset)
        self._sifir_yaz_dogrula(eksen, yeni_dro)
        yontem = (self.ayarlar["makine"].get("sifirlama_yontemi") or "dro")
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
        if eksen not in EKSENLER:
            raise GUV.GuvenlikHatasi(f"Bilinmeyen eksen: {eksen}")
        self._dururken("DRO yazma")
        self._sifir_yaz_dogrula(eksen, float(deger))
        self.gunluk.ekle("sifirla", f"{eksen} DRO = {deger}")
        return {"eksen": eksen, "deger": float(deger)}

    def sifira_git(self, eksenler=("X", "Y"), istemci=None):
        """Is sifirina donus (once guvenli Z)."""
        hedef = {e: 0.0 for e in eksenler if e.upper() in EKSENLER}
        return self.git(hedef, guvenli_z_ile=True, istemci=istemci)

    # ==================================================================
    # Is sifiri profilleri
    # ==================================================================
    # Profil, is sifirini MAKINE koordinatinda saklar. Boylece "6 mm bicak /
    # 18 mm MDF" profilini geri cagirdiginizda, DRO'ya hangi sayiyi yazmaniz
    # gerektigi degil, TAM OLARAK AYNI FIZIKSEL YUKSEKLIK geri gelir.
    # Tezgah tabani denetimi bundan bagimsizdir ve her zaman gecerlidir.
    def profiller(self):
        p = self.ayarlar.get("profiller") or {}
        return {"aktif": p.get("aktif", ""), "liste": p.get("liste", [])}

    def _profil_bul(self, ad):
        for p in (self.ayarlar.get("profiller") or {}).get("liste", []):
            if p.get("ad") == ad:
                return p
        return None

    def profil_kaydet(self, ad, takim_boyu=None, not_=None, istemci=None):
        """MEVCUT is sifirini, makine koordinatiyla birlikte profile yazar."""
        self.kilit.dogrula(istemci)
        ad = (ad or "").strip()
        if not ad:
            raise GUV.GuvenlikHatasi("Profil adi bos olamaz")
        if not self.bagli:
            raise GUV.GuvenlikHatasi(
                "Profil kaydetmek icin Mach3 baglantisi gerekir "
                "(makine koordinati okunmali).")
        ofs = self.ofset()
        eksik = [e for e in ("X", "Y", "Z") if self._makine.get(e) is None]
        if eksik:
            raise GUV.GuvenlikHatasi(
                "Makine koordinati okunamiyor: " + ", ".join(eksik) +
                ". OEM kodlarini (makine_x/y/z) kontrol edin.")
        kayit = {
            "ad": ad,
            "takim_boyu": (None if takim_boyu is None else float(takim_boyu)),
            # is sifirinin makine koordinatindaki yeri = ofset
            "is_sifiri_makine": {e: round(ofs.get(e, 0.0), 4)
                                 for e in ("X", "Y", "Z")},
            "guvenli_z": float(self.ayarlar["guvenlik"].get("guvenli_z", 20.0)),
            "not": not_ or "",
            "zaman": time.time(),
        }
        liste = [p for p in (self.ayarlar.get("profiller") or {})
                 .get("liste", []) if p.get("ad") != ad]
        liste.append(kayit)
        liste.sort(key=lambda p: p.get("ad", "").lower())
        self.ayarlar.guncelle({"profiller": {"liste": liste, "aktif": ad}})
        self.gunluk.ekle(
            "profil",
            f"Profil kaydedildi: {ad} (is sifiri makine Z "
            f"{kayit['is_sifiri_makine']['Z']:.3f})")
        return kayit

    def profil_uygula(self, ad, eksenler=("X", "Y", "Z"), istemci=None):
        """Kayitli is sifirini geri yukler.

        Makine konumu okunur, profilin makine-ofsetine gore DRO'ya yazilacak
        is degeri hesaplanir: is = makine - ofset
        """
        self.kilit.dogrula(istemci)
        p = self._profil_bul(ad)
        if not p:
            raise GUV.GuvenlikHatasi(f"Profil bulunamadi: {ad}")
        self._gerekli()
        ofs = p.get("is_sifiri_makine") or {}
        yazilan, atlanan = {}, []
        for e in eksenler:
            e = e.upper()
            if e not in ofs or self._makine.get(e) is None:
                atlanan.append(e)
                continue
            # Her eksen geri okunarak dogrulanir; Mach3 Reset durumundaysa
            # burada acik bir hata alinir (sessizce "yuklendi" denmez).
            deger = self._makine[e] - float(ofs[e])
            self._sifir_yaz_dogrula(e, deger)
            yazilan[e] = round(deger, 4)
        if not yazilan:
            raise GUV.GuvenlikHatasi(
                f"Profil uygulanamadi: hicbir eksen yazilamadi ({atlanan}). "
                "Makine koordinati okunamiyor olabilir.")
        if p.get("guvenli_z") is not None:
            self.ayarlar.guncelle(
                {"guvenlik": {"guvenli_z": float(p["guvenli_z"])}})
        self.ayarlar.guncelle({"profiller": {"aktif": ad}})
        self.gunluk.ekle("profil", f"Profil uygulandi: {ad} -> {yazilan}")
        return {"ad": ad, "yazilan": yazilan, "profil": p}

    def profil_sil(self, ad, istemci=None):
        self.kilit.dogrula(istemci)
        p = self.ayarlar.get("profiller") or {}
        liste = [x for x in p.get("liste", []) if x.get("ad") != ad]
        aktif = "" if p.get("aktif") == ad else p.get("aktif", "")
        self.ayarlar.guncelle({"profiller": {"liste": liste, "aktif": aktif}})
        self.gunluk.ekle("profil", f"Profil silindi: {ad}")
        return {"ok": True}

    # ==================================================================
    # Besleme override (FRO)
    # ==================================================================
    def fro_ayarla(self, deger=None, delta=None, istemci=None):
        """Besleme yuzdesini ayarlar (F10/F11 tuslari bunu kullanir)."""
        self.kilit.dogrula(istemci)
        b = self._gerekli()
        mevcut = self._ek.get("fro")
        if mevcut is None:
            mevcut = 100.0
        yeni = float(deger) if deger is not None else mevcut + float(delta or 0)
        yeni = max(1.0, min(yeni, 250.0))
        b.dro_yaz(self.kodlar.dro["fro"], yeni)
        self._ek["fro"] = yeni
        if self.program:
            self._sure_hesapla(yeni)
        self.gunluk.ekle("kontrol", f"Besleme override %{yeni:.0f}")
        return {"fro": yeni}

    # ==================================================================
    # Rapor
    # ==================================================================
    def rapor_verisi(self, son=False):
        """Aktif isin (ya da `son=True` ise biten isin) rapor verisi."""
        if son or not self.takip.aktif:
            return self.son_rapor or (self.takip.rapor_verisi()
                                      if self.takip.baslangic_t else None)
        return self.takip.rapor_verisi()

    # ==================================================================
    # MDI
    # ==================================================================
    def mdi(self, satir, istemci=None):
        """Elle G-Code. Gondermeden once kapsam denetimi yapilir."""
        self.kilit.dogrula(istemci)
        satirlar = [s for s in str(satir).splitlines() if s.strip()]
        if not satirlar:
            raise GUV.GuvenlikHatasi("Bos MDI satiri")
        self._referans_gerekli(["X", "Y", "Z"])
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
        # Referans yoksa ofset bilinmiyor demektir; kapsam denetimi tezgahin
        # neresine denk geldigini bilemez. Bu bir uyari degil HATA'dir.
        r = self.referans_durumu()
        if r["zorunlu"] and not r["alindi"]:
            sonuc["hatalar"].insert(0, (
                "Tezgah referansi alinmadi (" + ", ".join(r["eksik"]) + "). "
                "Programin tezgahin neresine dusecegi bilinemiyor; kapsam "
                "denetimi anlamsiz. Kafayi sifir kosesine getirip 'Referans "
                "al' deyin."))
            sonuc["gecti"] = False
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
        self._bitis_bekle = 0
        self.son_calistirma = {"dosya": calisacak, "blok": b,
                               "zaman": time.time(),
                               "sure": denetim.get("sure")}
        # Is takibini baslat: gecen sure, ilerleme ve blok sureleri buradan
        self.takip.basla(
            self.program_yol, b, self.blok_sureler, denetim.get("sure"),
            konum=dict(self._konum),
            profil=(self.ayarlar.get("profiller") or {}).get("aktif", ""),
            taban=self.guvenlik.z_taban())
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
