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

VARSAYILAN = {
    "surum": 1,

    "baglanti": {
        # "otomatik" -> Windows'ta COM, digerinde simulasyon
        # "com" | "simulasyon"
        "tur": "otomatik",
        "progid": "Mach4.Document",     # Mach3'un OLE sunucu adi (evet, "Mach4")
        "yedek_progid": ["Mach3.Document"],
        "yoklama_hz": 10,               # DRO okuma sikligi (Hz)
        "yeniden_baglan_sn": 3.0,
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
        # Makinenizin gercek calisma hacmi (MAKINE koordinati, mm).
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
        "max_dalis_derinlik": -60.0,    # bundan asagi elle Z inisine izin verme

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
    },

    "web": {
        "host": "0.0.0.0",              # mobilden erisim icin
        "port": 8010,
        "tarayici_ac": False,
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
                self.veri = _birlestir(VARSAYILAN, json.load(f))
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
