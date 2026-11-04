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
from . import kalibrasyon as KAL
from . import izleyici as IZL
from . import jog as JOG
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
                    "hareket": None, "estop": None, "satir": None,
                    "gecen": None, "kuyruk": None, "yavas_jog": None}
        self._son_yoklama = 0.0
        self._yokla_dur = threading.Event()
        self._yokla_is = None
        self._kmt_kilit = threading.RLock()

        # Mach3'un LED durumlari (son yoklama) ve G-code birimi
        self._led = {}
        self._birim = None                 # "mm" | "inch" | None (bilinmiyor)
        self._hata_say = 0                 # ardisik yoklama hatasi
        self._run_goruldu = False

        # jog: Mach3 modelini (kip, Slow Jog %, Step) uygulayan motor
        self.bekci = GUV.KalpAtisiBekcisi(
            self._bekci_durdur,
            zaman_asimi_ms=ayarlar["guvenlik"].get("jog_kalp_atisi_ms", 400),
            en_fazla_sn=ayarlar["guvenlik"].get("max_surekli_jog_sn", 30.0))
        self.jog = JOG.JogMotoru(self)

        # klavye kilidi + odak bekcisi
        kl = ayarlar["klavye"]
        self.klavye = KK.KlavyeKilidi(
            izinli=kl.get("izinli_tuslar"),
            olay=lambda tur, ad, vk: None,
            panik=lambda: self.acil_durdur("klavye ESC"))
        self.odak = KK.OdakBekcisi(
            baslik_parcasi=kl.get("odak_pencere_baslik", "Mach3"),
            durdur=lambda sebep: self.durdur_yumusak(sebep),
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

        # kumpasla kalibrasyon oturumu (bkz. kalibrasyon.py)
        self.kalib = None
        self.kalib_son = None
        self._kalib_is = None

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
            self._hata_say = 0
            self._kurulum_kodlari()
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
                self._hata_say = 0
            except Exception as e:                    # noqa: BLE001
                # Tek bir COM aksamasi (ornegin Mach3 bir an mesgul) baglantiyi
                # KALICI koparmamali; ardisik hatalar sayilir.
                self._hata_say += 1
                self.gunluk.ekle("hata", f"Yoklama basarisiz "
                                         f"({self._hata_say}/3): {e}")
                if self._hata_say >= 3:
                    self.baglanma_hatasi = str(e)
                    if self.baglanti:
                        self.baglanti.bagli = False
                    self.jog.durdur("baglanti koptu")

    # Yoklamada okunan LED'ler (kodlar.led anahtarlari)
    _LED_ADLARI = ("estop", "inch", "metrik", "bosta", "calisiyor",
                   "duraklatildi", "makine_koord", "cv", "jog_adim",
                   "jog_surekli", "yumusak_limit", "limit_gecersiz")
    _DRO_EK = ("feed", "fro", "devir", "satir", "gecen", "kuyruk",
               "yavas_jog")

    def _kurulum_kodlari(self):
        """Mach3 kurulum klasorundeki Buttons/DROs/Leds.txt ile OEM kodlarini
        karsilastirir (farkli surumde kodlar kendiliginden uyarlanir)."""
        try:
            exe = BAG.mach3_exe_bul(self.ayarlar["baglanti"])
        except Exception:                             # noqa: BLE001
            exe = ""
        if not exe or self.baglanti is None or self.baglanti.tur != "com":
            return
        rapor = self.kodlar.kurulumdan_oku(os.path.dirname(exe))
        self.kodlar.uygula(self.ayarlar.get("oem_ezme"))
        if rapor["farklar"]:
            self.gunluk.ekle(
                "uyari", f"Mach3 OEM listeleri gomulu listeden farkli "
                         f"({len(rapor['farklar'])} girdi); kodlar kurulumdan "
                         "alindi")

    def _oku(self, tam=True):
        """Mach3'u TEK turda okur. tam=False: jog dongusu icin hafif okuma."""
        b = self.baglanti
        K = self.kodlar
        eksenler = list(self.ayarlar["makine"].get("eksenler", ["X", "Y", "Z"]))
        adlar = {}                               # ad -> kod
        for e in eksenler:
            adlar[f"i_{e}"] = K.eksen_dro(e)
            adlar[f"m_{e}"] = K.eksen_dro(e, makine=True)
            if tam:
                adlar[f"o_{e}"] = K.eksen_ofset_dro(e)
        if tam:
            for ad in self._DRO_EK:
                adlar[ad] = K.dro.get(ad)
        dro_kodlari = sorted({k for k in adlar.values() if k is not None})
        led_adlari = ([a for a in self._LED_ADLARI if a in K.led]
                      if tam else [])
        led_kodlari = [K.led[a] for a in led_adlari]
        f = getattr(b, "paket_oku", None)
        if f is not None:
            paket = f(dro_kodlari, led_kodlari)
        else:                                     # sade/sahte baglantilar
            paket = BAG.TemelBaglanti.paket_oku(b, dro_kodlari, led_kodlari)
        dro = paket["dro"]
        led = {a: paket["led"].get(K.led[a]) for a in led_adlari}
        zorunlu = self.ayarlar["guvenlik"].get("referans_zorunlu", True)
        with self._kmt_kilit:
            makine_gosteriyor = bool(led.get("makine_koord"))
            for e in eksenler:
                i = dro.get(adlar.get(f"i_{e}"))
                m = dro.get(adlar.get(f"m_{e}"))
                of = dro.get(adlar.get(f"o_{e}")) if tam else None
                if i is not None and makine_gosteriyor and m is not None \
                        and of is not None:
                    # DRO 800.. "Machine Coordinates" gostergesi acikken
                    # MAKINE koordinati verir; is koordinati = makine - ofset
                    i = m - of
                if i is not None:
                    self._konum[e] = i
                if m is not None:
                    self._makine[e] = m
                    r = self.referans.get(e)
                    if r is not None:
                        self._tezgah[e] = m - r
                    elif zorunlu:
                        # Referans yok ve zorunlu: tezgah koordinati
                        # BILINMIYOR. 0 varsaymak yanlis guven verir;
                        # None birakilir, guvenlik katmani reddeder.
                        self._tezgah[e] = None
                    else:
                        # Operator "uyar ama izin ver" dedi: eski
                        # (KORUMASIZ) davranis - ham makine okumasi.
                        self._tezgah[e] = m
            if tam:
                for ad in self._DRO_EK:
                    self._ek[ad] = dro.get(adlar.get(ad))
                self._led = led
                if led.get("metrik"):
                    self._birim = "mm"
                elif led.get("inch"):
                    self._birim = "inch"
                else:
                    self._birim = None
            self._ek["hareket"] = paket.get("hareket")
            estop = paket.get("estop")
            if estop is None and tam:
                estop = led.get("estop")
            self._ek["estop"] = estop
        self._son_yoklama = time.time()
        if tam and self.takip.aktif:
            self.takip.guncelle(dict(self._konum), self._ek.get("hareket"),
                                self._ek.get("fro"))
            self._bitis_yokla()

    def oku_simdi(self):
        """Jog dongusu icin: taze konum anlik goruntusu (hafif okuma)."""
        self._oku(tam=False)
        with self._kmt_kilit:
            return {"konum": dict(self._konum), "tezgah": dict(self._tezgah),
                    "makine": dict(self._makine),
                    "hareket": self._ek.get("hareket"),
                    "estop": self._ek.get("estop")}

    def led_degeri(self, ad):
        """Son yoklamadaki LED durumu (bilinmiyorsa None)."""
        return self._led.get(ad)

    # ------------------------------------------------------------------
    # Hareket oncesi ortak kontrol: Reset, referans, BIRIM
    # ------------------------------------------------------------------
    def hareket_hazirla(self, eksenler=()):
        """Her hareket komutundan once: Reset'te degil miyiz, referans var mi,
        Mach3 mm kipinde mi? (Program mm'de calisir.)"""
        self._gerekli()
        self._oku(tam=True)
        if self._ek.get("estop"):
            raise GUV.GuvenlikHatasi(
                "Mach3 RESET (E-Stop) durumunda; hareket yok. Once Mach3'te "
                "(ya da buradaki Reset dugmesiyle) Reset'i kaldirin.")
        if eksenler:
            self._referans_gerekli(eksenler)
        self.birim_garanti()

    def birim_garanti(self):
        """Mach3 G20 (inc) kipindaysa G21'e gecirir; gecmezse hareketi REDDEDER.

        Neden: uygulama her sayiyi mm sanir. Mach3 inc kipindayken 'G1 X10'
        10 INC (254 mm) hareket ettirir ve yumusak limit denetimi bunu
        goremez. Bu PC'deki Mach3 tam olarak boyle (Standard Meas.) aciliyor.
        """
        if not self.ayarlar["guvenlik"].get("birim_zorla_mm", True):
            return
        if self._birim in (None, "mm"):
            return
        b = self._gerekli()
        self.gunluk.ekle("guvenlik", "Mach3 G20 (inc) kipinde; program mm'de "
                                     "calisir, G21 gonderiliyor")
        b.kod("G21")
        for _ in range(20):
            time.sleep(0.1)
            self._oku(tam=True)
            if self._birim == "mm":
                return
        raise GUV.GuvenlikHatasi(
            "Mach3 inc (G20) kipinde ve G21'e gecmedi; hareket reddedildi. "
            "Mach3'te MDI'a G21 yazip Enter'a basin.")

    def _bitis_yokla(self):
        """Program bitti mi? Mach3 bunu bildirmedigi icin hareketten anlariz.

        `IsMoving` uzun sure False kalirsa (ve feed hold'da degilsek) is
        bitmis sayilir. Duraklama sirasinda sayac ilerlemez.
        """
        if not self.calisiyor or self.takip.duraklatildi:
            self._bitis_bekle = 0
            return
        hz = max(float(self.ayarlar["baglanti"].get("yoklama_hz", 10)), 1.0)
        run = self._led.get("calisiyor")
        if run is not None:
            # Mach3'un "Run" LED'i: heuristik beklemeye gerek yok. LED'in
            # yukselmesi icin kisa bir pay taninir.
            if run:
                self._run_goruldu = True
                self._bitis_bekle = 0
                return
            gecen = time.time() - (self.son_calistirma or {}).get("zaman", 0)
            if not self._run_goruldu and gecen < 3.0:
                return
            if self._ek.get("hareket"):
                self._bitis_bekle = 0
                return
            self._bitis_bekle += 1
            if self._bitis_bekle < max(int(hz * 0.5), 2):
                return
        else:
            if self._ek.get("hareket"):
                self._bitis_bekle = 0
                return
            self._bitis_bekle += 1
            if self._bitis_bekle < int(hz * 6):    # ~6 sn hareketsizlik
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
            "sinir_is": self.sinir_is(),
            "yoklama_yasi": (round(time.time() - self._son_yoklama, 2)
                             if self._son_yoklama else None),
            "jog": self.jog.durum(),
            # Mach3'un LED'leri (Reset, Idle, Run, Pause, CV, jog kipi ...)
            "led": dict(self._led),
            "birim": self._birim,
            "satir": self._ek.get("satir"),
            "gecen_sn": self._ek.get("gecen"),
            "kuyruk": self._ek.get("kuyruk"),
            "yavas_jog": self._ek.get("yavas_jog"),
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
            # Yalnizca ozet: tam durum /api/kalibrasyon ile alinir (bkz.
            # kalibrasyon_ozet notu).
            "kalibrasyon": self.kalibrasyon_ozet(),
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
    def _acil_dugme(self):
        b = self.baglanti
        return getattr(b, "dugme_acil", b.dugme)

    def durdur_yumusak(self, sebep="operator"):
        """Jog'u kes + Feed Hold + Stop. Reset'e BASMAZ.

        Kalp atisi kaybi, odak kaybi gibi 'normal' durdurmalar icindir: bir
        tusu birakmak Mach3'u Reset'e sokmamali.
        """
        self.jog.durdur(sebep)
        sonuc = {"sebep": sebep, "adimlar": []}
        b = self.baglanti
        if not b or not b.bagli:
            sonuc["adimlar"].append("baglanti yok")
            self.gunluk.ekle("acil", f"DURDUR ({sebep}) - baglanti yok")
            return sonuc
        acil = self._acil_dugme()
        adimlar = []
        a = self.jog._aktif
        if a and a.get("surucu") == "mach3":
            for no in range(3):
                adimlar.append((f"jog_kapat{no}",
                                lambda n=no: b.jog_kapat(n)))
        adimlar += [
            ("feed_hold", lambda: acil(self.kodlar.buton["feed_hold"])),
            ("stop", lambda: acil(self.kodlar.buton["stop"]))]
        for ad, islem in adimlar:
            try:
                islem()
                sonuc["adimlar"].append(ad)
            except Exception as e:                    # noqa: BLE001
                sonuc["adimlar"].append(f"{ad} HATA: {e}")
        if self.takip.aktif:
            self._program_bitti(f"acil durdur: {sebep}")
        self.calisiyor = False
        return sonuc

    def acil_durdur(self, sebep="operator"):
        """ACIL DURDUR: jog + Feed Hold + Stop, sonra Mach3'un Reset'i.

        Mach3'te Esc = Reset; Reset hareketi keser ve cikislari kapatir.
        DIKKAT: Reset bir TERSLEME dugmesidir - zaten Reset'teyken basmak
        Reset'i KALDIRIRDI. Bu yuzden yalnizca Mach3 Reset'te DEGILKEN ve
        durum acikca biliniyorsa basilir.
        """
        sonuc = self.durdur_yumusak(sebep)
        b = self.baglanti
        if (b and b.bagli
                and self.ayarlar["guvenlik"].get("acil_reset", True)):
            try:
                zaten = b.estop()
            except Exception:                         # noqa: BLE001
                zaten = None
            if zaten is False:
                try:
                    self._acil_dugme()(self.kodlar.buton["reset"])
                    sonuc["adimlar"].append("reset")
                except Exception as e:                # noqa: BLE001
                    sonuc["adimlar"].append(f"reset HATA: {e}")
            elif zaten is True:
                sonuc["adimlar"].append("reset (zaten Reset'te)")
        self.gunluk.ekle("acil", f"ACIL DURDUR ({sebep})", sonuc)
        return sonuc

    def feed_hold(self):
        b = self._gerekli()
        self._acil_dugme()(self.kodlar.buton["feed_hold"])
        self.takip.duraklat("feed hold")
        self.gunluk.ekle("kontrol", "Feed hold")
        return True

    def devam(self):
        b = self._gerekli()
        if self._ek.get("estop"):
            raise GUV.GuvenlikHatasi(
                "Mach3 RESET durumunda; Cycle Start calismaz. Once Reset'i "
                "kaldirin.")
        b.dugme(self.kodlar.buton["cycle_start"])
        self.takip.devam_et("cycle start")
        self.gunluk.ekle("kontrol", "Devam (cycle start)")
        return True

    def durdur(self):
        self._gerekli()
        self._acil_dugme()(self.kodlar.buton["stop"])
        self._program_bitti("operator durdurdu")
        self.gunluk.ekle("kontrol", "Durdur")
        return True

    def _bekci_durdur(self, sebep):
        self.durdur_yumusak(f"bekci: {sebep}")

    # ------------------------------------------------------------------
    # Mach3 ekranindaki dugmeler
    # ------------------------------------------------------------------
    def reset_dugmesi(self, istemci=None):
        """Mach3 'Reset' dugmesi (E-Stop'u acar/kapar)."""
        self.kilit.dogrula(istemci)
        b = self._gerekli()
        b.dugme(self.kodlar.buton["reset"])
        self.gunluk.ekle("kontrol", "Reset dugmesine basildi")
        for _ in range(8):
            time.sleep(0.1)
            self._oku(tam=True)
            if self._ek.get("estop") is not None:
                break
        return {"estop": self._ek.get("estop")}

    def rewind(self, istemci=None):
        self.kilit.dogrula(istemci)
        b = self._gerekli()
        if self.calisiyor:
            raise GUV.GuvenlikHatasi("Program calisirken Rewind yapilamaz")
        b.dugme(self.kodlar.buton["rewind"])
        self.gunluk.ekle("kontrol", "Rewind")
        return True

    def tek_blok(self, istemci=None):
        self.kilit.dogrula(istemci)
        b = self._gerekli()
        b.dugme(self.kodlar.buton["tek_blok"])
        self.gunluk.ekle("kontrol", "Single Block degistirildi")
        return True

    def iletisim_komutu(self, satir):
        """Yardimci: hareket gerektirmeyen (mil/sogutma) tek MDI satiri."""
        self._gerekli()
        if self._ek.get("estop"):
            raise GUV.GuvenlikHatasi(
                "Mach3 RESET durumunda; once Reset'i kaldirin.")
        self._mdi(satir)

    def mil(self, islem, devir=None, istemci=None):
        """Mil: 'cw' | 'ccw' | 'dur' (+ istege bagli devir)."""
        self.kilit.dogrula(istemci)
        if self.calisiyor:
            raise GUV.GuvenlikHatasi(
                "Program calisirken mil elle degistirilemez")
        if islem == "dur":
            satir = "M5"
        elif islem in ("cw", "ccw"):
            satir = "M3" if islem == "cw" else "M4"
            if devir is not None:
                devir = float(devir)
                if not (0 < devir <= 100000):
                    raise GUV.GuvenlikHatasi("Devir 0..100000 araliginda olmali")
                satir += f" S{devir:.0f}"
        else:
            raise GUV.GuvenlikHatasi(f"Bilinmeyen mil islemi: {islem}")
        self.iletisim_komutu(satir)
        self.gunluk.ekle("kontrol", f"Mil: {satir}")
        return {"satir": satir}

    def sogutma(self, islem, istemci=None):
        """Sogutma: 'sel' (M8) | 'sis' (M7) | 'kapat' (M9)."""
        self.kilit.dogrula(istemci)
        satir = {"sel": "M8", "sis": "M7", "kapat": "M9"}.get(islem)
        if satir is None:
            raise GUV.GuvenlikHatasi(f"Bilinmeyen sogutma islemi: {islem}")
        self.iletisim_komutu(satir)
        self.gunluk.ekle("kontrol", f"Sogutma: {satir}")
        return {"satir": satir}

    # ==================================================================
    # Hareket
    # ==================================================================
    def _mdi(self, *satirlar):
        b = self._gerekli()
        for s in satirlar:
            b.kod(s)

    def jog_adim(self, eksen, mesafe, feed=None, hizli=None, istemci=None):
        """Bagil (artimli) tek adim: 'X ekseninde sola 25 mm git'."""
        self.kilit.dogrula(istemci)
        eksen = eksen.upper()
        self.hareket_hazirla([eksen])
        feed = float(feed) if feed else self.jog.hiz_mm_dk(eksen, bool(hizli))
        feed = min(feed, float(self.ayarlar["guvenlik"]["max_jog_feed"]))
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
        self.hareket_hazirla(
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
        self.hareket_hazirla(["Z"])
        gz = float(self.ayarlar["guvenlik"].get("guvenli_z", 20.0))
        self._mdi("G90", f"G0 Z{gz:.3f}")
        self.gunluk.ekle("hareket", f"Guvenli yukseklige cikildi (Z{gz})")
        return {"z": gz}

    # -- jog (Mach3 modeli: kip, Slow Jog %, Step, Jog ON/OFF; bkz. jog.py) --
    @property
    def jog_bilgi(self):
        return self.jog.durum()

    def jog_feed(self, hizli=False, eksen="X"):
        """Yavas (Slow Jog %) / hizli (Shift) jog beslemesi, mm/dk."""
        return self.jog.hiz_mm_dk(eksen, hizli)

    def jog_basla(self, eksen, yon, feed=None, hizli=None, istemci=None):
        """Jog tusuna basildi: Step kipinde bir adim, Continuous'ta birakilana
        kadar hareket (Mach3'teki gibi tek giris noktasi)."""
        return self.jog.basla(eksen, yon, feed, hizli, istemci)

    def jog_hiz(self, feed=None, hizli=None, istemci=None):
        return self.jog.hiz(hizli, feed, istemci)

    def jog_kalp(self, istemci=None):
        """Istemci 'hala basiliyim' der. Kesilirse hareket yavaslayip durur."""
        return self.jog.kalp(istemci)

    def jog_bitir(self, istemci=None):
        """Jog tusu birakildi."""
        self.jog.birak()
        self.jog.bekle(2.5)
        return True

    def jog_ayarla(self, istemci=None, **kw):
        self.kilit.dogrula(istemci)
        return self.jog.ayarla(**kw)

    def jog_dogrula(self, eksenler=("X", "Y", "Z"), istemci=None):
        return self.jog.yerel_dogrula(eksenler, istemci)

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
    # Home noktalari (adlandirilmis is sifirlari)
    # ==================================================================
    # Her "Home", bir malzemenin/isin SIFIR NOKTASIDIR ve takma adla saklanir
    # ("Levha A", "18 mm MDF - sag ust" ...). Tezgahin farkli yerlerindeki
    # farkli malzemeler icin istediginiz kadar Home tanimlayip tek tikla
    # gecis yapabilirsiniz.
    #
    # KOORDINAT: Home, iş sifirinin TEZGAH koordinatini saklar (tezgah =
    # makine_okumasi - referans). Makine koordinati her Mach3 acilisinda
    # kaydigi icin ona gore saklamak, ertesi gun ayni Home'un baska bir
    # fiziksel noktaya dusmesi demektir; tezgah koordinati ise referans
    # yeniden alindiginda AYNI fiziksel noktayi gosterir.
    #
    #   uygula:   iş = tezgah - home_sifiri      (DRO'ya bu yazilir)
    #   kaydet:   home_sifiri = tezgah - iş      (bulunulan noktadan)
    #
    # Eski surumde `is_sifiri_makine` adiyla saklanan kayitlar aslinda
    # ofset() (tezgah - iş) degerleridir; ayni anlamla okunur ve arayuzde
    # "eski kayit" diye isaretlenir. (Eski `profil_uygula` bu degeri MAKINE
    # okumasindan cikariyordu: referans 0 degilse yanlis sifir yazardi.)
    HOME_EKSENLER = ("X", "Y", "Z")
    HOME_TOLERANS = 0.011                 # mm: "su an aktif" sayilma payi

    def _home_listesi(self):
        return list((self.ayarlar.get("profiller") or {}).get("liste", []))

    @staticmethod
    def _home_tezgah(kayit):
        """Kaydin is sifiri TEZGAH koordinati ({eksen: mm})."""
        t = kayit.get("tezgah")
        eski = not (isinstance(t, dict) and t)
        if eski:
            t = kayit.get("is_sifiri_makine") or {}
        out = {}
        for e, v in t.items():
            if e in EKSENLER and v is not None:
                try:
                    out[e] = float(v)
                except (TypeError, ValueError):
                    pass
        return out, eski

    def _home_bul(self, ad):
        hedef = (ad or "").strip().lower()
        for p in self._home_listesi():
            if (p.get("ad") or "").strip().lower() == hedef:
                return p
        return None

    def _mevcut_sifir(self):
        """Su anki is sifirinin tezgah koordinati ({eksen: mm}); bilinmeyen
        eksen yoktur."""
        out = {}
        for e in self.HOME_EKSENLER:
            i, t = self._konum.get(e), self._tezgah.get(e)
            if i is not None and t is not None:
                out[e] = round(t - i, 4)
        return out

    def homeler(self):
        """Arayuz icin: kayitlar + hangisinin SU AN gecerli oldugu.

        `uyumlu`, saklanan sifir ile Mach3'un gercek sifiri ayni mi diye
        bakar; sifiri elle degistirdiyseniz eski ismin "aktif" kalmasini
        onler.
        """
        p = self.ayarlar.get("profiller") or {}
        mevcut = self._mevcut_sifir()
        liste = []
        for r in p.get("liste", []):
            tz, eski = self._home_tezgah(r)
            ortak = [e for e in tz if e in mevcut]
            uyumlu = None
            fark = {}
            if tz and ortak:
                fark = {e: round(mevcut[e] - tz[e], 3) for e in ortak}
                uyumlu = (len(ortak) == len(tz) and
                          all(abs(v) <= self.HOME_TOLERANS
                              for v in fark.values()))
            liste.append({
                "ad": r.get("ad", ""),
                "tezgah": {e: round(v, 3) for e, v in tz.items()},
                "malzeme": r.get("malzeme") or "",
                "not": r.get("not") or "",
                "takim_boyu": r.get("takim_boyu"),
                "guvenli_z": r.get("guvenli_z"),
                "zaman": r.get("zaman"),
                "eski": eski,
                "uyumlu": uyumlu,
                "fark": fark,
            })
        kayitli = p.get("aktif", "")
        gercek = ""
        for r in liste:
            if r["ad"] == kayitli and r["uyumlu"]:
                gercek = kayitli
                break
        else:
            for r in liste:
                if r["uyumlu"]:
                    gercek = r["ad"]
                    break
        return {"aktif": kayitli, "aktif_gercek": gercek, "liste": liste,
                "mevcut": {e: round(v, 3) for e, v in mevcut.items()}}

    # eski ad (arayuz/testler)
    def profiller(self):
        return self.homeler()

    def _profil_bul(self, ad):
        return self._home_bul(ad)

    # -- dogrulama yardimcilari -------------------------------------------
    @staticmethod
    def _home_ad(ad):
        ad = " ".join(str(ad or "").split())            # bosluklari sadelestir
        if not ad:
            raise GUV.GuvenlikHatasi("Home takma adi bos olamaz")
        if len(ad) > 60:
            raise GUV.GuvenlikHatasi("Home takma adi en fazla 60 karakter")
        return ad

    @staticmethod
    def _home_sayilari(tezgah):
        """{"X": 12.5, "Y": None} -> {"X": 12.5}; sayi olmayani reddeder."""
        out = {}
        for e, v in (tezgah or {}).items():
            e = str(e).upper()
            if e not in Denetleyici.HOME_EKSENLER:
                raise GUV.GuvenlikHatasi(f"Bilinmeyen eksen: {e}")
            if v is None or v == "":
                continue
            try:
                f = float(v)
            except (TypeError, ValueError):
                raise GUV.GuvenlikHatasi(f"{e} koordinati sayi olmali")
            if f != f or f in (float("inf"), float("-inf")):
                raise GUV.GuvenlikHatasi(f"{e} koordinati sonlu bir sayi olmali")
            if abs(f) > 1e6:
                raise GUV.GuvenlikHatasi(f"{e} koordinati makul degil: {f:g}")
            out[e] = round(f, 4)
        return out

    @staticmethod
    def _home_sayi_veya_none(v, ad):
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            raise GUV.GuvenlikHatasi(f"{ad} sayi olmali")

    def _home_uyarilari(self, tezgah):
        """Home'un tezgah araligi disinda kalip kalmadigi (engellemez, uyarir)."""
        uyar = []
        if not self.guvenlik.limitler_aktif():
            return uyar
        for e, v in tezgah.items():
            lim = self.guvenlik.limit(e)
            if lim and (v < float(lim["min"]) - 1e-6 or
                        v > float(lim["max"]) + 1e-6):
                uyar.append(f"{e} sifiri ({v:g}) tezgah araliginin "
                            f"({float(lim['min']):g}..{float(lim['max']):g}) "
                            "disinda; Home'u kontrol edin")
        return uyar

    def _home_yaz(self, liste, aktif=None):
        liste = sorted(liste, key=lambda p: (p.get("ad") or "").lower())
        yama = {"liste": liste}
        if aktif is not None:
            yama["aktif"] = aktif
        self.ayarlar.guncelle({"profiller": yama})

    # -- kaydet ------------------------------------------------------------
    def home_kaydet(self, ad, tezgah=None, eksenler=None, takim_boyu=None,
                    malzeme=None, not_=None, guvenli_z=None,
                    uzerine_yaz=False, istemci=None):
        """Yeni bir Home kaydeder.

        `tezgah` verilirse ({"X": 500, "Z": -18}) o koordinatlar saklanir
        (elle tanimlama; baglanti gerekmez). Verilmezse BULUNULAN is sifiri
        okunur. Ayni ad varsa `uzerine_yaz` olmadan reddedilir.
        """
        self.kilit.dogrula(istemci)
        ad = self._home_ad(ad)
        var = self._home_bul(ad)
        if var is not None and not uzerine_yaz:
            raise GUV.GuvenlikHatasi(
                f"'{var['ad']}' adinda bir Home zaten var. Uzerine yazmak icin "
                "onaylayin ya da baska bir ad verin.")
        if tezgah:
            sayilar = self._home_sayilari(tezgah)
            kaynak = "elle"
        else:
            self._gerekli()
            self._oku(tam=True)
            istenen = [e.upper() for e in (eksenler or self.HOME_EKSENLER)
                       if e.upper() in self.HOME_EKSENLER]
            sayilar, eksik = {}, []
            for e in istenen:
                i, t = self._konum.get(e), self._tezgah.get(e)
                if self.referans.get(e) is None or i is None or t is None:
                    eksik.append(e)
                else:
                    sayilar[e] = round(t - i, 4)
            if eksik:
                raise GUV.GuvenlikHatasi(
                    "Home kaydedilemedi: " + ", ".join(eksik) + " icin tezgah "
                    "referansi alinmadi. Home, tezgah koordinatiyla saklanir; "
                    "once referans alin (ya da koordinatlari elle girin).")
            kaynak = "mevcut"
        if not sayilar:
            raise GUV.GuvenlikHatasi("Home icin en az bir eksen koordinati gerekli")
        eski = var or {}
        kayit = {
            "ad": ad,
            "tezgah": sayilar,
            "malzeme": (malzeme if malzeme is not None
                        else eski.get("malzeme", "")) or "",
            "not": (not_ if not_ is not None else eski.get("not", "")) or "",
            "takim_boyu": (self._home_sayi_veya_none(takim_boyu, "Takim boyu")
                           if takim_boyu is not None
                           else eski.get("takim_boyu")),
            "guvenli_z": (self._home_sayi_veya_none(guvenli_z, "Guvenli Z")
                          if guvenli_z is not None else eski.get("guvenli_z")),
            "zaman": time.time(),
        }
        liste = [p for p in self._home_listesi()
                 if (p.get("ad") or "").strip().lower() != ad.lower()]
        liste.append(kayit)
        self._home_yaz(liste, aktif=ad if kaynak == "mevcut" else None)
        self.gunluk.ekle(
            "home", f"Home {'guncellendi' if var else 'kaydedildi'}: {ad} "
                    f"({kaynak}) {sayilar}")
        return {"ad": ad, "home": kayit, "uyarilar": self._home_uyarilari(sayilar),
                "homeler": self.homeler()}

    # -- duzenle / yeniden adlandir ---------------------------------------
    def home_duzenle(self, ad, istemci=None, **alanlar):
        """Var olan bir Home'un adini, koordinatlarini ya da notlarini degistirir.

        alanlar: yeni_ad, tezgah ({"X": 12} - bos/None eksen SILER), malzeme,
        not, takim_boyu, guvenli_z.
        """
        self.kilit.dogrula(istemci)
        kayit = self._home_bul(ad)
        if kayit is None:
            raise GUV.GuvenlikHatasi(f"Home bulunamadi: {ad}")
        yeni = dict(kayit)
        eski_ad = kayit["ad"]
        if alanlar.get("yeni_ad") not in (None, ""):
            yeni_ad = self._home_ad(alanlar["yeni_ad"])
            baska = self._home_bul(yeni_ad)
            if baska is not None and baska is not kayit and \
                    (baska.get("ad") or "").lower() != eski_ad.lower():
                raise GUV.GuvenlikHatasi(
                    f"'{baska['ad']}' adinda baska bir Home zaten var")
            yeni["ad"] = yeni_ad
        if alanlar.get("tezgah") is not None:
            tz, _ = self._home_tezgah(kayit)
            for e, v in (alanlar["tezgah"] or {}).items():
                e = str(e).upper()
                if e not in self.HOME_EKSENLER:
                    raise GUV.GuvenlikHatasi(f"Bilinmeyen eksen: {e}")
                if v is None or v == "":
                    tz.pop(e, None)               # boslugu = bu eksen saklanmaz
                else:
                    tz.update(self._home_sayilari({e: v}))
            if not tz:
                raise GUV.GuvenlikHatasi("Home en az bir eksen koordinati tasimali")
            yeni["tezgah"] = {e: round(v, 4) for e, v in tz.items()}
            yeni.pop("is_sifiri_makine", None)    # eski alan artik gecersiz
        if "malzeme" in alanlar and alanlar["malzeme"] is not None:
            yeni["malzeme"] = str(alanlar["malzeme"])[:80]
        if "not" in alanlar and alanlar["not"] is not None:
            yeni["not"] = str(alanlar["not"])[:200]
        if "takim_boyu" in alanlar:
            yeni["takim_boyu"] = self._home_sayi_veya_none(
                alanlar["takim_boyu"], "Takim boyu")
        if "guvenli_z" in alanlar:
            yeni["guvenli_z"] = self._home_sayi_veya_none(
                alanlar["guvenli_z"], "Guvenli Z")
        yeni["zaman"] = time.time()
        liste = [p for p in self._home_listesi() if p is not kayit
                 and (p.get("ad") or "") != eski_ad]
        liste.append(yeni)
        aktif = (self.ayarlar.get("profiller") or {}).get("aktif", "")
        self._home_yaz(liste, aktif=yeni["ad"] if aktif == eski_ad else None)
        self.gunluk.ekle("home", f"Home duzenlendi: {eski_ad}"
                         + (f" -> {yeni['ad']}" if yeni["ad"] != eski_ad else ""))
        tz, _ = self._home_tezgah(yeni)
        return {"ad": yeni["ad"], "home": yeni,
                "uyarilar": self._home_uyarilari(tz),
                "homeler": self.homeler()}

    # -- uygula -------------------------------------------------------------
    def home_uygula(self, ad, eksenler=None, git=False, istemci=None):
        """Home'u gecerli is sifiri yapar (DRO'ya yazar).

        iş = tezgah - home_sifiri. Her eksen geri okunarak DOGRULANIR; Mach3
        Reset'teyse sessizce yok sayilan yazma acik hata olur. `git=True`
        ise sifira (X0 Y0) guvenli Z ile gidilir.
        """
        self.kilit.dogrula(istemci)
        kayit = self._home_bul(ad)
        if kayit is None:
            raise GUV.GuvenlikHatasi(f"Home bulunamadi: {ad}")
        ad = kayit["ad"]
        self._gerekli()
        self._oku(tam=True)
        if self._ek.get("estop"):
            raise GUV.GuvenlikHatasi(
                "Mach3 RESET (E-Stop) durumunda; Home uygulanamadi (DRO yazma "
                "bu durumda sessizce yok sayilir). Once Reset'i kaldirin.")
        self._dururken("Home uygulama")
        tz, eski = self._home_tezgah(kayit)
        istenen = [e.upper() for e in (eksenler or tz.keys())]
        yazilan, atlanan, referanssiz = {}, [], []
        for e in istenen:
            if e not in tz:
                atlanan.append(e)
                continue
            t = self._tezgah.get(e)
            if self.referans.get(e) is None or t is None:
                referanssiz.append(e)
                continue
            deger = t - tz[e]
            # Her eksen geri okunarak dogrulanir (Reset'te sessiz basarisizlik)
            self._sifir_yaz_dogrula(e, deger)
            yazilan[e] = round(deger, 4)
        if referanssiz and not yazilan:
            raise GUV.GuvenlikHatasi(
                "Home uygulanamadi: " + ", ".join(referanssiz) + " icin tezgah "
                "referansi alinmadi. Home tezgah koordinatiyla saklandigi icin "
                "once referans alin.")
        if not yazilan:
            raise GUV.GuvenlikHatasi(
                f"Home uygulanamadi: hicbir eksen yazilamadi ({atlanan}).")
        if kayit.get("guvenli_z") is not None:
            self.ayarlar.guncelle(
                {"guvenlik": {"guvenli_z": float(kayit["guvenli_z"])}})
        self.ayarlar.guncelle({"profiller": {"aktif": ad}})
        self.gunluk.ekle("home", f"Home uygulandi: {ad} -> {yazilan}")
        sonuc = {"ad": ad, "yazilan": yazilan, "profil": kayit, "eski": eski,
                 "referanssiz": referanssiz, "git": False}
        if git:
            try:
                self.sifira_git(("X", "Y"), istemci=istemci)
                sonuc["git"] = True
            except GUV.GuvenlikHatasi as e:
                # Home uygulandi; yalnizca gitme reddedildi - bunu gizleme
                sonuc["git_hata"] = str(e)
                sonuc["sebepler"] = list(e.sebepler)
        sonuc["homeler"] = self.homeler()
        return sonuc

    # -- sil ----------------------------------------------------------------
    def home_sil(self, ad, istemci=None):
        self.kilit.dogrula(istemci)
        kayit = self._home_bul(ad)
        if kayit is None:
            raise GUV.GuvenlikHatasi(f"Home bulunamadi: {ad}")
        p = self.ayarlar.get("profiller") or {}
        liste = [x for x in p.get("liste", []) if x is not kayit
                 and x.get("ad") != kayit["ad"]]
        aktif = "" if p.get("aktif") == kayit["ad"] else p.get("aktif", "")
        self._home_yaz(liste, aktif=aktif)
        self.gunluk.ekle("home", f"Home silindi: {kayit['ad']}")
        return {"ok": True, "homeler": self.homeler()}

    # -- eski adlar (geriye uyumluluk) -----------------------------------------
    def profil_kaydet(self, ad, takim_boyu=None, not_=None, istemci=None):
        r = self.home_kaydet(ad, takim_boyu=takim_boyu, not_=not_,
                             uzerine_yaz=True, istemci=istemci)
        return r["home"]

    def profil_uygula(self, ad, eksenler=("X", "Y", "Z"), istemci=None):
        return self.home_uygula(ad, eksenler, False, istemci)

    def profil_sil(self, ad, istemci=None):
        return self.home_sil(ad, istemci)

    # ==================================================================
    # Kalibrasyon (dijital kumpasla)
    # ==================================================================
    # Mach3'un COM arayuzunde "Steps per unit" ve "Backlash" degerlerini
    # OKUYAN YA DA YAZAN BIR CAGRI YOKTUR; bu ayarlar Mach3'un profil
    # dosyasinda durur ve yalnizca Mach3'un kendi pencerelerinden
    # degistirilebilir. Bu yuzden buradaki is bolusumu sudur:
    #
    #   program  -> hareketi yapar, olcumu toplar, yeni degeri hesaplar,
    #               Mach3'te HANGI KUTUYA NE yazilacagini soyler
    #   operator -> degeri Mach3'e yazar ve "girdim" der
    #
    # Her kalibrasyon hareketi normal jog gibi guvenlik katmanindan gecer:
    # yumusak limit, tezgah tabani, adim/besleme siniri ve referans zorunlu.
    def kalibrasyon_ayari(self):
        k = dict(self.ayarlar.get("kalibrasyon") or {})
        k.setdefault("adim_basi", {})
        k.setdefault("bosluk", {})
        k.setdefault("olcum_mesafe", 100.0)
        k.setdefault("olcum_feed", 300.0)
        k.setdefault("on_yaklasma", 5.0)
        k.setdefault("tekrar", 3)
        k.setdefault("kumpas_cozunurluk", 0.01)
        k.setdefault("gecmis", [])
        return k

    def kalibrasyon_durum(self):
        k = self.kalibrasyon_ayari()
        return {
            "ayar": {a: k[a] for a in (
                "adim_basi", "bosluk", "olcum_mesafe", "olcum_feed",
                "on_yaklasma", "tekrar", "kumpas_cozunurluk")},
            "gecmis": k.get("gecmis", [])[-20:],
            "oturum": self.kalib.durum() if self.kalib else None,
            "son_sonuc": self.kalib_son,
            # Arayuz, olcum mesafesinin jog sinirina sigip sigmadigini
            # kullaniciya ONCEDEN gosterebilsin.
            "max_jog_adim": self.ayarlar["guvenlik"].get("max_jog_adim"),
            "max_jog_feed": self.ayarlar["guvenlik"].get("max_jog_feed"),
        }

    def kalibrasyon_ozet(self):
        """Olay akisina giren KUCUK ozet.

        NEDEN: tam durum (olcum sonucu + Mach3 talimati + gecmis) birkac
        kilobayttir; ~8 Hz'de surekli yollamak telefondaki baglantiyi bosuna
        doldurur. Akista yalnizca sihirbazin canli kalmasi icin gerekenler
        gider; arayuz burada bir degisiklik gorunce tam durumu ayrica ister.
        """
        o = self.kalib
        k = self.kalibrasyon_ayari()
        return {
            # Ayar degisirse (baska bir istemci kaydetmis olabilir) arayuz
            # tazelensin diye kucuk bir imza tasinir.
            "ayar_ozet": [k.get("adim_basi"), k.get("bosluk")],
            "sonuc_var": bool(self.kalib_son or (o and o.olcumler)),
            "oturum": None if not o else {
                "aktif": o.asama != KAL.BITTI,
                "eksen": o.eksen, "tur": o.tur, "asama": o.asama,
                "mesgul": o.mesgul, "yonerge": o.yonerge(),
                "komut_mm": o.komut_mm, "yon": o.yon,
                "on_yaklasma": o.on_yaklasma,
                "tekrar": o.tekrar, "alinan": len(o.olcumler),
                "olcumler": [round(x["olculen"], 4) for x in o.olcumler],
                "hata": o.hata,
            },
        }

    def _kalib_oturum(self):
        if not self.kalib:
            raise GUV.GuvenlikHatasi("Acik bir kalibrasyon oturumu yok")
        if self.kalib.mesgul:
            raise GUV.GuvenlikHatasi(
                "Kalibrasyon hareketi surerken islem yapilamaz; eksenin "
                "durmasini bekleyin.")
        return self.kalib

    def kalibrasyon_basla(self, eksen, tur="olcek", mesafe=None, feed=None,
                          yon=1, on_yaklasma=None, tekrar=None,
                          adim_basi=None, istemci=None):
        self.kilit.dogrula(istemci)
        self._gerekli()
        self._dururken("Kalibrasyon")
        if self.kalib and self.kalib.mesgul:
            raise GUV.GuvenlikHatasi(
                "Onceki kalibrasyon hareketi hala suruyor.")
        k = self.kalibrasyon_ayari()
        eksen = str(eksen).upper()
        self._referans_gerekli([eksen])
        if adim_basi is None:
            adim_basi = (k.get("adim_basi") or {}).get(eksen)
        try:
            o = KAL.Oturum(
                eksen, tur,
                komut_mm=(k["olcum_mesafe"] if mesafe is None else mesafe),
                feed=(k["olcum_feed"] if feed is None else feed),
                yon=yon,
                on_yaklasma=(k["on_yaklasma"] if on_yaklasma is None
                             else on_yaklasma),
                tekrar=(k["tekrar"] if tekrar is None else tekrar),
                adim_basi=adim_basi,
                cozunurluk=k["kumpas_cozunurluk"])
        except ValueError as e:
            raise GUV.GuvenlikHatasi(str(e))

        # En uzun tek hareket, jog adim sinirina sigmali. Bunu burada
        # soylemek, olcumun ortasinda "adim cok buyuk" hatasi almaktan
        # (ve makineyi yarim yolda birakmaktan) iyidir.
        en_uzun = max(abs(m) for ad in ("bosluk_al", "olc", "geri")
                      for _, m, _ in o.hareket_plani(ad))
        max_adim = float(self.ayarlar["guvenlik"].get("max_jog_adim", 100.0))
        if en_uzun > max_adim + 1e-9:
            raise GUV.GuvenlikHatasi(
                f"Kalibrasyon icin gereken en uzun hareket {en_uzun:.1f} mm, "
                f"izin verilen en buyuk jog adimi {max_adim:.1f} mm. Olcum "
                "mesafesini kucultun ya da Guvenlik sekmesinden 'En buyuk "
                "jog adimi' degerini yukseltin.")
        max_feed = float(self.ayarlar["guvenlik"].get("max_jog_feed", 6000.0))
        if o.feed > max_feed + 1e-9:
            raise GUV.GuvenlikHatasi(
                f"Olcum beslemesi {o.feed:.0f} mm/dk, ust sinir "
                f"{max_feed:.0f} mm/dk.")

        self.kalib = o
        self.kalib_son = None
        self.gunluk.ekle(
            "kalibrasyon",
            f"Oturum basladi: {eksen} / {tur} / {o.komut_mm:g} mm "
            f"@F{o.feed:.0f} x{o.tekrar}")
        return self.kalibrasyon_durum()

    # Hangi adim hangi asamada yapilabilir (yanlis sirada hareket, olcumu
    # sessizce bozar - bu yuzden acikca reddedilir).
    _KALIB_IZIN = {"bosluk_al": (KAL.HAZIR,),
                   "olc": (KAL.OKUMA_A,),
                   "geri": (KAL.TEKRAR,)}

    def kalibrasyon_adim(self, ad, istemci=None):
        """Kalibrasyon hareketini baslatir (arka planda yurur)."""
        self.kilit.dogrula(istemci)
        o = self._kalib_oturum()
        izin = self._KALIB_IZIN.get(ad)
        if izin is None:
            raise GUV.GuvenlikHatasi(f"Bilinmeyen kalibrasyon adimi: {ad}")
        if o.asama not in izin:
            raise GUV.GuvenlikHatasi(
                f"'{ad}' bu asamada yapilamaz (su anki asama: {o.asama}). "
                "Ekrandaki yonergeyi izleyin.")
        self._gerekli()
        self._dururken("Kalibrasyon hareketi")
        plan = o.hareket_plani(ad)
        o.hata = ""
        o.mesgul = True
        o.asama = KAL.HAREKET
        self._kalib_is = threading.Thread(
            target=self._kalibrasyon_yurut, args=(o, ad, plan, istemci),
            daemon=True, name="kalibrasyon")
        self._kalib_is.start()
        return self.kalibrasyon_durum()

    def _kalibrasyon_yurut(self, o, ad, plan, istemci):
        sonraki = {"bosluk_al": KAL.OKUMA_A, "olc": KAL.OKUMA_B,
                   "geri": KAL.OKUMA_A}[ad]
        try:
            for eksen, mesafe, feed in plan:
                self.jog_adim(eksen, mesafe, feed, istemci=istemci)
                self._hareket_bitmesini_bekle(
                    abs(mesafe) / max(feed, 1.0) * 60.0)
            o.asama = sonraki
        except Exception as e:                        # noqa: BLE001
            # Cok adimli bir plan yarida kalmis olabilir; konum artik
            # bilinmiyor sayilir. Guvenli olan, boslugu bastan almaktir.
            # Sebepler olmadan "Jog reddedildi" demek operatoru cozumsuz
            # birakir; hangi limitin asildigi yaziya eklenir.
            sebepler = [s for s in getattr(e, "sebepler", []) if s != str(e)]
            o.hata = str(e) + (" — " + "; ".join(sebepler) if sebepler else "")
            o.asama = KAL.HAZIR
            self.gunluk.ekle("hata", f"Kalibrasyon hareketi durdu: {e}")
        finally:
            o.mesgul = False

    def _hareket_bitmesini_bekle(self, tahmini_sn, ek_sn=20.0):
        """Eksen duruncaya kadar bekler.

        NEDEN: kumpas okumasi, eksen tam durmadan alinirsa anlamsizdir.
        `IsMoving` desteklenmiyorsa (bazi Mach3 surumleri) hareketin
        hesaplanan suresi kadar beklenir - tahmin, hic beklememekten iyidir.
        """
        time.sleep(0.35)                    # hareketin baslamasina pay
        b = self.baglanti
        try:
            h = b.hareket_ediyor() if b else None
        except Exception:                             # noqa: BLE001
            h = None
        if h is None:
            time.sleep(max(float(tahmini_sn), 0.0) + 1.5)
            return "sure"
        bitis = time.time() + float(tahmini_sn) + ek_sn
        durgun = 0
        while time.time() < bitis:
            try:
                h = b.hareket_ediyor()
            except Exception:                         # noqa: BLE001
                h = None
            # IsMoving hareketin basinda birkac kare titreyebilir; ust uste
            # uc "durgun" okumasi aranir.
            durgun = 0 if h else durgun + 1
            if durgun >= 3:
                return "hareket"
            time.sleep(0.1)
        raise GUV.GuvenlikHatasi(
            "Kalibrasyon hareketi beklenen surede bitmedi; eksen hala "
            "hareket ediyor gorunuyor. Olcum iptal edildi.")

    def kalibrasyon_bekle(self, zaman_asimi=60.0):
        """Suren kalibrasyon hareketinin bitmesini bekler (test/API icin)."""
        if self._kalib_is and self._kalib_is.is_alive():
            self._kalib_is.join(timeout=zaman_asimi)
        return self.kalibrasyon_durum()

    def kalibrasyon_olcum(self, olculen=None, a=None, b=None, istemci=None):
        """Kumpas okumasini kaydeder (mesafe ya da iki okuma)."""
        self.kilit.dogrula(istemci)
        o = self._kalib_oturum()
        if o.asama != KAL.OKUMA_B:
            raise GUV.GuvenlikHatasi(
                "Once olcum hareketini yapin; okuma ancak hareket bittikten "
                "sonra girilebilir.")
        try:
            kayit = o.ekle_olcum(olculen, a, b)
        except ValueError as e:
            raise GUV.GuvenlikHatasi(str(e))
        self.gunluk.ekle(
            "kalibrasyon",
            f"Olcum {len(o.olcumler)}/{o.tekrar}: {o.eksen} "
            f"{kayit['olculen']:.3f} mm (komut {o.komut_mm:g} mm)")
        if o.asama == KAL.BITTI:
            self.kalib_son = o.sonuc()
            self._kalibrasyon_sonucu_gunlukle()
        return self.kalibrasyon_durum()

    def kalibrasyon_bitir(self, istemci=None):
        """Kalan tekrarlari beklemeden sonuca gecer."""
        self.kilit.dogrula(istemci)
        o = self._kalib_oturum()
        if not o.olcumler:
            raise GUV.GuvenlikHatasi("Henuz hicbir olcum alinmadi")
        o.bitir()
        self.kalib_son = o.sonuc()
        self._kalibrasyon_sonucu_gunlukle()
        return self.kalibrasyon_durum()

    def _kalibrasyon_sonucu_gunlukle(self):
        s = self.kalib_son or {}
        if s.get("tur") == "bosluk":
            self.gunluk.ekle(
                "kalibrasyon",
                f"SONUC {s['eksen']} bosluk = {s['bosluk_mm']:.4f} mm "
                f"({s['olcum_sayisi']} olcum)")
        else:
            self.gunluk.ekle(
                "kalibrasyon",
                f"SONUC {s.get('eksen')} sapma {s.get('hata_mm'):+.4f} mm / "
                f"{s.get('komut_mm')} mm · carpan {s.get('carpan')} · "
                f"yeni adim/mm {s.get('yeni_adim_basi')}")

    def kalibrasyon_iptal(self, istemci=None):
        self.kilit.dogrula(istemci)
        if self.kalib and self.kalib.mesgul:
            raise GUV.GuvenlikHatasi(
                "Hareket surerken oturum kapatilamaz; once ACIL DURDUR.")
        self.kalib = None
        self.gunluk.ekle("kalibrasyon", "Oturum iptal edildi")
        return self.kalibrasyon_durum()

    def kalibrasyon_uygula(self, deger=None, istemci=None):
        """Operator degeri Mach3'e yazdi: kaydet, gecmise isle, uyar.

        Adim sayisi degistiginde Mach3'un MAKINE koordinati da olcek
        degistirir; onceki tezgah referansi artik yanlistir ve MUTLAKA
        dusurulur (yoksa yumusak limitler eski olcekten korur).
        """
        self.kilit.dogrula(istemci)
        s = self.kalib_son or (self.kalib.sonuc() if self.kalib else None)
        if not s:
            raise GUV.GuvenlikHatasi("Uygulanacak bir kalibrasyon sonucu yok")
        eksen = s["eksen"]
        k = self.kalibrasyon_ayari()
        uyarilar = []

        if s["tur"] == "bosluk":
            bosluk = dict(k.get("bosluk") or {})
            bosluk[eksen] = s["bosluk_mm"]
            self.ayarlar.guncelle({"kalibrasyon": {"bosluk": bosluk}})
            kayit_deger = s["bosluk_mm"]
        elif s["tur"] == "dogrulama":
            kayit_deger = None
        else:
            yeni = s.get("yeni_adim_basi") if deger is None else deger
            if yeni is None:
                raise GUV.GuvenlikHatasi(
                    "Yeni adim sayisi hesaplanamadi (mevcut 'Steps per' "
                    "degeri girilmemis). Mach3'e yazdiginiz degeri "
                    "'deger' alanina girin ya da mevcut adim sayisini "
                    "kaydedip olcumu tekrarlayin.")
            try:
                yeni = float(yeni)
            except (TypeError, ValueError):
                raise GUV.GuvenlikHatasi("Adim sayisi sayi olmali")
            if yeni <= 0:
                raise GUV.GuvenlikHatasi("Adim sayisi sifirdan buyuk olmali")
            adim = dict(k.get("adim_basi") or {})
            adim[eksen] = round(yeni, 4)
            self.ayarlar.guncelle({"kalibrasyon": {"adim_basi": adim}})
            kayit_deger = round(yeni, 4)
            # Olcek degisti: makine koordinati da degisti.
            self.referans[eksen] = None
            self._tezgah[eksen] = None
            self.referans_zaman = None
            uyarilar.append(
                f"{eksen} ekseninin tezgah referansi dusuruldu: adim sayisi "
                "degisince makine koordinatinin olcegi de degisir. Yeniden "
                "referans alin.")
            uyarilar.append(
                "Yumusak limitleri ve tezgah tabanini bu eksende yeniden "
                "dogrulayin; eski degerler eski olcege gore olculmustu.")
            uyarilar.append(
                "Degisikligin gercekten islendigini gormek icin ayni eksende "
                "'Dogrulama' olcumu yapin.")

        gecmis = list(k.get("gecmis") or [])
        gecmis.append({
            "zaman": time.time(), "eksen": eksen, "tur": s["tur"],
            "komut_mm": s.get("komut_mm"), "olculen_ort": s.get("olculen_ort"),
            "hata_mm": s.get("hata_mm"), "carpan": s.get("carpan"),
            "eski": s.get("eski_adim_basi"), "yeni": kayit_deger,
            "olcum_sayisi": s.get("olcum_sayisi"),
        })
        self.ayarlar.guncelle({"kalibrasyon": {"gecmis": gecmis[-100:]}})
        self.gunluk.ekle(
            "kalibrasyon",
            f"UYGULANDI: {eksen} / {s['tur']} -> {kayit_deger} "
            "(Mach3'e operator tarafindan yazildi)")
        self.kalib = None
        return {"ok": True, "uyarilar": uyarilar,
                "kalibrasyon": self.kalibrasyon_durum()}

    def kalibrasyon_ayar_kaydet(self, yama, istemci=None):
        """Mach3'ten okunan guncel degerleri ve olcum tercihlerini kaydeder."""
        self.kilit.dogrula(istemci)
        temiz = {}
        for alan in ("adim_basi", "bosluk"):
            if alan not in (yama or {}):
                continue
            d = {}
            for e, v in (yama[alan] or {}).items():
                e = str(e).upper()
                if e not in EKSENLER:
                    continue
                if v in (None, ""):
                    d[e] = None
                    continue
                try:
                    f = float(v)
                except (TypeError, ValueError):
                    raise GUV.GuvenlikHatasi(f"{alan}.{e} sayi olmali")
                if f < 0:
                    raise GUV.GuvenlikHatasi(f"{alan}.{e} negatif olamaz")
                d[e] = f
            temiz[alan] = d
        for alan in ("olcum_mesafe", "olcum_feed", "on_yaklasma",
                     "kumpas_cozunurluk"):
            if alan in (yama or {}):
                try:
                    f = float(yama[alan])
                except (TypeError, ValueError):
                    raise GUV.GuvenlikHatasi(f"{alan} sayi olmali")
                if f <= 0:
                    raise GUV.GuvenlikHatasi(f"{alan} sifirdan buyuk olmali")
                temiz[alan] = f
        if "tekrar" in (yama or {}):
            temiz["tekrar"] = max(1, min(int(yama["tekrar"]), 20))
        if temiz:
            self.ayarlar.guncelle({"kalibrasyon": temiz})
            self.gunluk.ekle("kalibrasyon",
                             "Kalibrasyon ayarlari kaydedildi: "
                             + ", ".join(sorted(temiz)))
        return self.kalibrasyon_durum()

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
        # Mach3 Reset'teyken SetOEMDRO'yu SESSIZCE yok sayar: geri oku.
        okunan = None
        for _ in range(6):
            time.sleep(0.1)
            try:
                okunan = b.dro(self.kodlar.dro["fro"])
            except Exception:                         # noqa: BLE001
                okunan = None
            if okunan is not None and abs(okunan - yeni) <= 0.5:
                break
        if okunan is not None and abs(okunan - yeni) > 0.5:
            raise GUV.GuvenlikHatasi(
                f"Besleme override %{yeni:.0f} yazilamadi (Mach3 %{okunan:.0f} "
                "gosteriyor)." + (" Mach3 RESET durumunda olabilir."
                                  if self._ek.get("estop") else ""))
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
        self.hareket_hazirla(["X", "Y", "Z"])
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

    def _vektorler(self):
        """Program icin vektor siralamasi (ayar degisince yeniden hesaplanir)."""
        gz = float(self.ayarlar["guvenlik"].get("guvenli_z", 20.0))
        anahtar = (id(self.program), gz)
        onbellek = getattr(self, "_vektor_onbellek", None)
        if onbellek and onbellek[0] == anahtar:
            return onbellek[1]
        v = self.program.vektorler(gz)
        self._vektor_onbellek = (anahtar, v)
        return v

    def sinir_is(self):
        """Tezgah sinirlari (yumusak limitler) IS koordinatinda.

        Onizleme, takim yolunun tezgahin her kenarina ne kadar yaklastigini
        gostermek icin bunu kullanir. Is sifiri degistikce degisir:
        is = tezgah - ofset. Referans/konum bilinmeyen eksen yoktur.
        """
        out = {}
        if self.guvenlik.limitler_aktif():
            for e in ("X", "Y", "Z"):
                lim = self.guvenlik.limit(e)
                aralik = self.guvenlik.limit_araligi(e)
                t, i = self._tezgah.get(e), self._konum.get(e)
                if not lim or not aralik or t is None or i is None:
                    continue
                o = t - i                    # tezgah = is + o
                out[e] = [round(aralik[0] - o, 3), round(aralik[1] - o, 3)]
        return {"aralik": out,
                "pay": float(self.ayarlar["guvenlik"].get("limit_payi", 0.0)),
                "dogrulandi": bool(self.ayarlar["guvenlik"]
                                   .get("limitler_dogrulandi"))}

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
            # Onizleme bilgi seridi: kesim alani, tum hareket zarfi ve
            # is sifirina (X0 Y0) gore konum
            "olcu": self.program.olculer(),
            # Guvenli Z'ye her yukselisin kapattigi kesim parcalari (siralari)
            "vektor": self._vektorler(),
        }

    def program_satirlari(self, bas=0, adet=3000):
        """Mach3'e yuklu dosyanin ham satirlari (G-Code penceresi).

        Mach3'un 'Current Line' DRO'su (816) YUKLU dosyaya gore sayar: bloktan
        baslatmada bu, orijinal dosya degil uretilen yeniden baslatma
        dosyasidir; bu yuzden calistirilan dosya gosterilir.
        """
        yol = ((self.son_calistirma or {}).get("dosya")
               if self.calisiyor else None) or self.program_yol
        if not yol or not os.path.isfile(yol):
            return {"yol": "", "ad": "", "satirlar": [], "toplam": 0,
                    "bas": 0}
        with open(yol, "r", encoding="utf-8", errors="replace") as f:
            tum = f.read().splitlines()      # Mach3 bos satirlari da sayar
        bas = max(0, int(bas))
        adet = max(1, min(int(adet), 20000))
        return {"yol": yol, "ad": os.path.basename(yol), "toplam": len(tum),
                "bas": bas, "satirlar": tum[bas:bas + adet]}

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
        self.hareket_hazirla()

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
        # Mach3 dosyayi yuklerken Cycle Start'a basmak yarim programi calistirir
        time.sleep(0.2)
        for _ in range(100):                          # en fazla ~10 sn
            try:
                yukleniyor = self.baglanti.yukleniyor()
            except Exception:                         # noqa: BLE001
                yukleniyor = None
            if not yukleniyor:
                break
            time.sleep(0.1)
        self.baglanti.dugme(self.kodlar.buton["cycle_start"])
        self.calisiyor = True
        self._run_goruldu = False
        self._bitis_bekle = 0
        self.son_calistirma = {"dosya": calisacak, "blok": b,
                               "zaman": time.time(),
                               "sure": denetim.get("sure")}
        # Is takibini baslat: gecen sure, ilerleme ve blok sureleri buradan
        self.takip.basla(
            self.program_yol, b, self.blok_sureler, denetim.get("sure"),
            konum=dict(self._konum),
            profil=self.homeler().get("aktif_gercek", ""),
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
