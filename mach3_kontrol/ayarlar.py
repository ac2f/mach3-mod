#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ayarlar
=======
Tum yapilandirma tek bir JSON dosyasinda tutulur (varsayilan:
`~/.mach3_kontrol/ayarlar.json`). Dosya yoksa varsayilanlarla olusturulur.

EN ONEMLI BOLUM `guvenlik`: yumusak limitler, en buyuk jog adimi, en yuksek
besleme, guvenli Z ve en derin dalis. Bunlar "kaza engelleyici" katmanin
girdisidir; **makinenize gore mutlaka ayarlayin**. Varsayilanlar 3000x1500
tabaka router icin ornek degerlerdir ve arayuzde "dogrulanmadi" uyarisi
verilir (`guvenlik.limitler_dogrulandi` = false).
"""

import copy
import json
import os

SURUM = 4

VARSAYILAN = {
    "surum": SURUM,

    "baglanti": {
        # "otomatik" -> Windows'ta COM, digerinde simulasyon
        # "com" | "simulasyon"
        "tur": "otomatik",
        "progid": "Mach4.Document",     # Mach3'un OLE sunucu adi (evet, "Mach4")
        "yedek_progid": ["Mach3.Document"],
        # Mach3 COM arayuzunu yalnizca "/Automation" ile baslatildiginda acar;
        # elle acilan Mach3'e BAGLANILAMAZ. Bu yuzden varsayilan olarak Mach3'u
        # bu program baslatir. Ayrinti: baglanti.py icindeki uzun not.
        "mach3_baslat": True,
        # Bos ise kayit defterindeki LocalServer32 yolundan bulunur.
        "mach3_exe": "",
        # Profil verilmezse Mach3 profil secim penceresinde bekler ve COM
        # arayuzunu hic acmaz. Or. "Mach3Mill", "Mach3Turn", "Plasma".
        "profil": "Mach3Mill",
        "yoklama_hz": 10,               # DRO okuma sikligi (Hz)
        "yeniden_baglan_sn": 3.0,
        # Mach3'u biz baslatiyorsak acilis uzun surebilir (profil yukleme).
        "acilis_zaman_asimi_sn": 120,
    },

    "makine": {
        "birim": "mm",
        "eksenler": ["X", "Y", "Z"],
        "hizli_hiz": {"X": 8000.0, "Y": 8000.0, "Z": 3000.0},   # mm/dk (G0)
        "ivme": {"X": 400.0, "Y": 400.0, "Z": 250.0},           # mm/s^2
        # Sifirlama yontemi: "dro" -> SetOEMDRO (her profilde calisir),
        # "g10" -> G10 L20 (fikstur ofsetine yazar, kalicidir)
        "sifirlama_yontemi": "dro",
        "g10_fikstur": 1,
    },

    "guvenlik": {
        # ---------------------------------------------------------------
        # TEZGAH REFERANSI
        # Referans switch'i olmayan makinelerde Mach3'un "makine koordinati"
        # tezgahin kosesini DEGIL, Mach3 acildiginda kafanin bulundugu
        # rastgele noktayi gosterir. Bu yuzden yumusak limitler tek basina
        # korumaz: 0..2100 araligi rastgele bir noktadan olculur.
        #
        # Cozum: operator kafayi tezgahin SIFIR KOSESINE park edip
        # "Referans al" der; o andaki makine okumasi tezgah sifiri olarak
        # kaydedilir. Tum limitler artik "tezgah koordinatinda" denetlenir:
        #     tezgah = makine_okumasi - referans
        # Referans bellekte tutulur ve Mach3 her yeniden baslatildiginda
        # (makine sifiri kaydigi icin) dusdurulur.
        "referans_zorunlu": True,       # referanssizken hareket engellensin
        # Makinenizin gercek calisma hacmi (TEZGAH koordinati, mm).
        "limitler_dogrulandi": False,
        "yumusak_limit": {
            "X": {"min": 0.0,    "max": 3000.0},
            "Y": {"min": 0.0,    "max": 1500.0},
            "Z": {"min": -150.0, "max": 0.0},
        },
        "yumusak_limit_zorunlu": True,
        "limit_payi": 1.0,              # limite bu kadar mm kala reddet

        "max_jog_adim": 100.0,          # tek seferde en fazla mm
        "max_jog_feed": 6000.0,         # jog beslemesi ust siniri (mm/dk)
        "max_surekli_jog_sn": 30.0,     # surekli jog en fazla bu kadar surer
        "jog_kalp_atisi_ms": 400,       # istemciden bu araliktan seyrek gelirse dur

        "guvenli_z": 20.0,              # XY hareketi oncesi olmasi gereken Z
        "guvenli_z_zorunlu": True,      # XY tasima once Z'yi guvenli yuksekli§e al
        # IS koordinatinda elle inis siniri (is sifirina gore degerlendirilir)
        "max_dalis_derinlik": -60.0,
        # TEZGAH koordinatinda (referansa gore) mutlak Z tabani. Is sifirini
        # nerede yaparsaniz yapin, hangi boyda bicak takarsaniz takin hicbir
        # komut bunun altina inemez -> tezgaha dalma korumasi.
        # null ise denetim yapilmaz.
        # OLCUM: once referans alin, sonra takimi tabla yuzeyine deger
        # konuma indirip arayuzdeki TEZGAH Z degerini okuyun, 1-2 mm pay
        # ekleyip yazin.
        "makine_z_taban": None,
        "makine_z_taban_zorunlu": True,

        "home_zorunlu": False,          # program baslatmadan once referans sart mi
        "onay_gereken": ["program_baslat", "sifirla", "home"],

        # Ag guvenligi: mobilden baglanirken PIN sorulur (bos ise otomatik uretilir)
        "pin": "",
        "pin_zorunlu": True,
        "kontrol_zaman_asimi_sn": 90.0,  # kontrolu alan istemci sessiz kalirsa birak
    },

    "klavye": {
        "kilit_acilista": False,        # program acilinca kilit aktif mi
        "izinli_tuslar": ["LEFT", "RIGHT", "UP", "DOWN", "PRIOR", "NEXT",
                          "SPACE", "ESCAPE"],
        "odak_bekcisi": True,           # Mach3 odagi kaybederse hareketi durdur
        "odak_pencere_baslik": "Mach3",  # izlenecek pencere basligi (icerir)
        "odak_geri_al": True,           # odagi Mach3'e geri ver

        # DRO duzenleme kisayolu (Mach3'teki gibi): true ise Ctrl+X/Y/Z,
        # false ise dogrudan X/Y/Z tusu DRO alanini acar. Yalnizca bir yazi
        # alanina odaklanilmamisken calisir.
        "dro_ctrl_gerekli": True,
        "dro_tuslari": {"X": "x", "Y": "y", "Z": "z"},
        # Besleme yuzdesi (FRO) tuslari ve adimi
        "fro_azalt_tus": "F10",
        "fro_artir_tus": "F11",
        "fro_adim": 5,                  # her basista % kac degissin
    },

    "web": {
        "host": "0.0.0.0",              # mobilden erisim icin
        "port": 8010,
        "tarayici_ac": False,
    },

    # Is sifiri profilleri: her bicak/malzeme icin kayitli Z sifiri.
    # Profil, MAKINE koordinatindaki is sifirini saklar (`is_sifiri_makine`);
    # boylece geri cagirinca ayni fiziksel yukseklige donulur.
    "profiller": {
        "aktif": "",
        "liste": [],
        # ornek kayit:
        # {"ad": "6mm duz freze / MDF 18", "takim_boyu": 52.3,
        #  "is_sifiri_makine": {"X": .., "Y": .., "Z": -78.1},
        #  "guvenli_z": 20.0, "not": ""}
    },

    # Yeni G-Code dosyasi izleme
    "izleme": {
        "aktif": True,
        "kok": "",                      # or. "D:\\isler" (bos ise kapali)
        "derinlik": 4,                   # kac alt klasor asagi taransin
        "aralik_sn": 5.0,
        "yeni_sayilir_dk": 120,          # bu kadar once degismis dosyalar "yeni"
    },

    # ------------------------------------------------------------------
    # KALIBRASYON (dijital kumpasla)
    # Mach3'un COM arayuzu "Steps per unit" ve "Backlash" degerlerini NE
    # OKUR NE DE YAZAR; bunlar yalnizca Mach3'un kendi pencerelerinden
    # degistirilebilir. Burada tutulanlar:
    #   adim_basi - Mach3 > Config > Motor Tuning'deki GUNCEL deger (elle
    #               girilir). Yeni degeri hesaplayabilmek icin gereklidir.
    #   bosluk    - olculen backlash (Mach3 > Config > Backlash'e yazilir)
    # Ikisi de yalnizca KAYIT amaclidir; makineyi bu dosya degil, Mach3'e
    # yazdiginiz degerler kalibre eder.
    "kalibrasyon": {
        "adim_basi": {"X": None, "Y": None, "Z": None},   # steps / mm
        "bosluk": {"X": None, "Y": None, "Z": None},      # mm
        "olcum_mesafe": 100.0,          # kumpas araligina sigmali (mm)
        "olcum_feed": 300.0,            # yavas ve tekrarlanabilir (mm/dk)
        "on_yaklasma": 5.0,             # bosluk alma mesafesi (mm)
        "tekrar": 3,                    # kac olcum ortalanacak
        "kumpas_cozunurluk": 0.01,      # dijital kumpasin adimi (mm)
        "gecmis": [],                   # uygulanan kalibrasyonlarin kaydi
    },

    "gcode": {
        "son_klasor": "",
        "sure_katsayi": 1.0,            # tahmini sureyi kalibre etmek icin
        "takim_degisim_sn": 20.0,
        "spindle_bekleme_sn": 4.0,
    },

    "oem_ezme": {"dro": {}, "buton": {}, "led": {}},
}


def varsayilan_yol():
    return os.path.join(os.path.expanduser("~"), ".mach3_kontrol",
                        "ayarlar.json")


def _gocur(veri):
    """Eski ayar dosyalarini yeni surume tasir. (degisti_mi, veri) doner."""
    surum = veri.get("surum", 1)
    if surum >= SURUM:
        return False, veri
    if surum < 2:
        # Surum 2: Mach3'un COM otomasyon arayuzunu YALNIZCA "/Automation"
        # ile baslatildiginda actigi tespit edildi. Elle acilan Mach3'e
        # baglanmak mumkun olmadigi icin "mach3_baslat: false" calismayan
        # bir secenektir; eski dosyalarda aciliyor.
        veri.setdefault("baglanti", {})["mach3_baslat"] = True
    if surum < 3:
        # Surum 3: tezgah tabani denetimi eklendi. Degeri OTOMATIK
        # DOLDURMUYORUZ - yanlis bir taban, gercek bir tabandan daha
        # tehlikelidir (yanlis guven verir). null birakilir; arayuz
        # "olculmedi" uyarisi gosterir ve kullanicidan olcmesini ister.
        veri.setdefault("guvenlik", {}).setdefault("makine_z_taban", None)
    if surum < 4:
        # Surum 4: kumpasla kalibrasyon bolumu eklendi. Adim sayilarini
        # TAHMIN ETMIYORUZ - yanlis bir "mevcut deger" hesabi da bozar.
        # None birakilir; arayuz Mach3'ten okumanizi ister.
        veri.setdefault("kalibrasyon", {})
    veri["surum"] = SURUM
    return True, veri


def _birlestir(temel, ustu):
    """Ic ice sozlukleri, eksik anahtarlari varsayilandan alacak sekilde
    birlestirir (kullanici dosyasi eski surumden kalmis olabilir)."""
    sonuc = copy.deepcopy(temel)
    for k, v in (ustu or {}).items():
        if isinstance(v, dict) and isinstance(sonuc.get(k), dict):
            sonuc[k] = _birlestir(sonuc[k], v)
        else:
            sonuc[k] = v
    return sonuc


class Ayarlar:
    def __init__(self, yol=None):
        self.yol = yol or varsayilan_yol()
        self.veri = copy.deepcopy(VARSAYILAN)
        self.yukle()

    # -- disk -----------------------------------------------------------
    def yukle(self):
        try:
            with open(self.yol, "r", encoding="utf-8") as f:
                ham = json.load(f)
            gocuruldu, ham = _gocur(ham)
            self.veri = _birlestir(VARSAYILAN, ham)
            if gocuruldu:
                self.kaydet()
        except FileNotFoundError:
            self.kaydet()
        except (ValueError, OSError) as e:
            print(f"[ayarlar] okunamadi ({e}); varsayilanlar kullaniliyor.")
        if not self.veri["guvenlik"].get("pin"):
            self.veri["guvenlik"]["pin"] = self._pin_uret()
            self.kaydet()
        return self.veri

    def kaydet(self):
        try:
            os.makedirs(os.path.dirname(self.yol) or ".", exist_ok=True)
            gecici = self.yol + ".tmp"
            with open(gecici, "w", encoding="utf-8") as f:
                json.dump(self.veri, f, ensure_ascii=False, indent=2)
            os.replace(gecici, self.yol)
        except OSError as e:
            print(f"[ayarlar] kaydedilemedi: {e}")

    @staticmethod
    def _pin_uret():
        import secrets
        return f"{secrets.randbelow(1000000):06d}"

    # -- erisim ---------------------------------------------------------
    def __getitem__(self, k):
        return self.veri[k]

    def get(self, k, vars_=None):
        return self.veri.get(k, vars_)

    def guncelle(self, yama):
        """Kismi guncelleme (ic ice birlestirme) + diske yazma."""
        self.veri = _birlestir(self.veri, yama)
        self.kaydet()
        return self.veri

    def kopya(self):
        return copy.deepcopy(self.veri)

    def guvenli_kopya(self):
        """Arayuze gonderilecek surum: PIN disari sizmaz."""
        d = self.kopya()
        d["guvenlik"]["pin"] = "***" if d["guvenlik"].get("pin") else ""
        return d
