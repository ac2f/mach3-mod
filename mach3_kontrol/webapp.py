#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Web sunucusu (bagimliliksiz)
============================
Yalnizca Python standart kutuphanesi (http.server). Flask/tornado gerekmez;
Mach3 makinesindeki Windows kurulumuna ek kutuphane bulastirmaz.

* `GET  /`         -> arayuz (web/index.html)
* `GET  /olay`     -> Server-Sent Events: DRO + durum akisi (~10 Hz) ve olaylar
* `POST /api/...`  -> komutlar (JSON)

Erisim guvenligi
----------------
Telefondan baglanabilmek icin sunucu 0.0.0.0'da dinler. Bu yuzden:

* **Yerel makineden** (127.0.0.1) gelen istekler dogrudan kabul edilir -
  tezgahin basindaki operator zaten makineye erisebilir.
* **Aginizdaki diger cihazlar** (telefon/tablet) once PIN ile oturum acar
  (`/api/oturum`). PIN sunucu acilirken konsola yazilir.
* Durum okuma serbesttir; **hareket ettiren her komut** oturum belirteci ister.
* Ayni anda yalnizca TEK istemci komut verebilir (kontrol kilidi).

Bu, evinizdeki agda makul bir korumadir; sunucuyu **internete acmayin**.
"""

import json
import math
import os
import secrets
import threading
import time
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import ayarlar as AY
from . import denetleyici as DEN
from . import guvenlik as GUV
from . import gcode as GC
from . import sure as SURE

WEB_DIZIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
GCODE_UZANTI = {".tap", ".nc", ".gcode", ".ngc", ".txt", ".cnc", ".g"}

_OTURUM = {}          # belirtec -> {"ad":..., "ip":..., "zaman":...}
_OTURUM_KILIT = threading.Lock()

AYARLAR = None
DENETLEYICI = None


# ----------------------------------------------------------------------
# Yardimcilar
# ----------------------------------------------------------------------
def _sonlu(o):
    """NaN/Infinity -> None (tarayicidaki JSON.parse bunlari kabul etmez)."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _sonlu(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_sonlu(v) for v in o]
    return o


def _json_govde(sonuc):
    try:
        return json.dumps(sonuc, ensure_ascii=False, allow_nan=False,
                          default=str)
    except ValueError:
        return json.dumps(_sonlu(sonuc), ensure_ascii=False, allow_nan=False,
                          default=str)


def _yerel_mi(ip):
    return ip in ("127.0.0.1", "::1", "localhost")


def _yetki(veri, ip):
    """Komut vermeye yetkili mi? Degilse GuvenlikHatasi."""
    if _yerel_mi(ip):
        return True
    if not AYARLAR["guvenlik"].get("pin_zorunlu", True):
        return True
    belirtec = (veri or {}).get("belirtec", "")
    with _OTURUM_KILIT:
        o = _OTURUM.get(belirtec)
        if not o:
            raise GUV.GuvenlikHatasi(
                "Oturum gecersiz. PIN ile giris yapin.")
        o["zaman"] = time.time()
    return True


def _istemci(veri):
    return (veri or {}).get("istemci") or "bilinmeyen"


# ----------------------------------------------------------------------
# API islemleri
# ----------------------------------------------------------------------
def api_oturum(veri, ip):
    pin = str((veri or {}).get("pin", "")).strip()
    dogru = str(AYARLAR["guvenlik"].get("pin", ""))
    if _yerel_mi(ip):
        pass
    elif not pin or pin != dogru:
        time.sleep(0.7)                     # kaba kuvvet yavaslatma
        DENETLEYICI.gunluk.ekle("guvenlik", f"Hatali PIN denemesi ({ip})")
        return {"hata": "PIN hatali"}
    belirtec = secrets.token_urlsafe(24)
    with _OTURUM_KILIT:
        _OTURUM[belirtec] = {"ip": ip, "zaman": time.time(),
                             "ad": (veri or {}).get("ad", "")}
    DENETLEYICI.gunluk.ekle("guvenlik", f"Oturum acildi ({ip})")
    return {"belirtec": belirtec, "yerel": _yerel_mi(ip)}


def api_durum(veri, ip):
    d = DENETLEYICI.durum()
    d["sunucu"] = {"yerel": _yerel_mi(ip), "surum": SURUM}
    return d


def api_baglan(veri, ip):
    _yetki(veri, ip)
    tur = (veri or {}).get("tur")
    if tur:
        AYARLAR.guncelle({"baglanti": {"tur": tur}})
    try:
        DENETLEYICI.baglan()
    except Exception as e:                            # noqa: BLE001
        return {"hata": str(e), "durum": DENETLEYICI.durum()}
    return {"ok": True, "durum": DENETLEYICI.durum()}


def api_kes(veri, ip):
    _yetki(veri, ip)
    DENETLEYICI.kapat()
    return {"ok": True}


# -- kontrol kilidi ------------------------------------------------------
def api_kontrol_al(veri, ip):
    _yetki(veri, ip)
    ok = DENETLEYICI.kilit.al(_istemci(veri), (veri or {}).get("ad", ""),
                              zorla=bool((veri or {}).get("zorla")))
    return {"ok": ok, "kilit": DENETLEYICI.kilit.durum()}


def api_kontrol_birak(veri, ip):
    _yetki(veri, ip)
    DENETLEYICI.jog_bitir()
    return {"ok": DENETLEYICI.kilit.birak(_istemci(veri)),
            "kilit": DENETLEYICI.kilit.durum()}


# -- durdurma (yetki aranmaz: herkes durdurabilmeli) ---------------------
def api_acil(veri, ip):
    return DENETLEYICI.acil_durdur((veri or {}).get("sebep", "arayuz"))


def api_feedhold(veri, ip):
    return {"ok": DENETLEYICI.feed_hold()}


def api_devam(veri, ip):
    _yetki(veri, ip)
    return {"ok": DENETLEYICI.devam()}


def api_durdur(veri, ip):
    return {"ok": DENETLEYICI.durdur()}


# -- hareket -------------------------------------------------------------
def api_jog_adim(veri, ip):
    _yetki(veri, ip)
    return DENETLEYICI.jog_adim(veri["eksen"], float(veri["mesafe"]),
                                veri.get("feed"), veri.get("hizli"),
                                _istemci(veri))


def api_jog_basla(veri, ip):
    _yetki(veri, ip)
    return DENETLEYICI.jog_basla(veri["eksen"], int(veri.get("yon", 1)),
                                 veri.get("feed"), veri.get("hizli"),
                                 _istemci(veri))


def api_jog_hiz(veri, ip):
    """Tus basiliyken Shift'e basmak: jogu kesmeden beslemeyi degistirir."""
    _yetki(veri, ip)
    return DENETLEYICI.jog_hiz((veri or {}).get("feed"),
                               (veri or {}).get("hizli"), _istemci(veri))


def api_jog_kalp(veri, ip):
    _yetki(veri, ip)
    return DENETLEYICI.jog_kalp(_istemci(veri))


def api_jog_bitir(veri, ip):
    return {"ok": DENETLEYICI.jog_bitir(_istemci(veri))}


def api_git(veri, ip):
    _yetki(veri, ip)
    return DENETLEYICI.git(veri.get("hedef", {}), veri.get("feed"),
                           bool(veri.get("guvenli_z", True)), _istemci(veri))


def api_guvenli_z(veri, ip):
    _yetki(veri, ip)
    return DENETLEYICI.guvenli_yukselt(_istemci(veri))


def api_sifirla(veri, ip):
    _yetki(veri, ip)
    return DENETLEYICI.sifirla(veri["eksen"], float(veri.get("ofset", 0.0)),
                               _istemci(veri))


def api_dro(veri, ip):
    _yetki(veri, ip)
    return DENETLEYICI.dro_ayarla(veri["eksen"], float(veri["deger"]),
                                  _istemci(veri))


def api_sifira_git(veri, ip):
    _yetki(veri, ip)
    eksenler = veri.get("eksenler") or ["X", "Y"]
    return DENETLEYICI.sifira_git(eksenler, _istemci(veri))


# -- tezgah referansi ----------------------------------------------------
def api_referans(veri, ip):
    islem = (veri or {}).get("islem", "durum")
    if islem == "al":
        _yetki(veri, ip)
        v = veri or {}
        # `degerler` verilirse bulunulan noktanin tezgah koordinati odur
        # (orn. {"X": 2099}); verilmezse bulunulan nokta tezgah sifiridir.
        return DENETLEYICI.referans_al(
            v.get("eksenler") or ("X", "Y", "Z"),
            degerler=v.get("degerler"), istemci=_istemci(veri))
    if islem == "birak":
        _yetki(veri, ip)
        return DENETLEYICI.referans_birak(_istemci(veri))
    if islem == "makine_sifirla":
        # Mach3'un MAKINE koordinatini bulunulan noktada sifirlar (Ref).
        _yetki(veri, ip)
        return DENETLEYICI.makine_sifirla(
            (veri or {}).get("eksenler") or ("X", "Y", "Z"), _istemci(veri))
    return DENETLEYICI.referans_durumu()


# -- besleme override ----------------------------------------------------
def api_fro(veri, ip):
    _yetki(veri, ip)
    return DENETLEYICI.fro_ayarla((veri or {}).get("deger"),
                                  (veri or {}).get("delta"), _istemci(veri))


# -- is sifiri profilleri ------------------------------------------------
def api_profil_liste(veri, ip):
    return DENETLEYICI.profiller()


def api_profil_kaydet(veri, ip):
    _yetki(veri, ip)
    return DENETLEYICI.profil_kaydet((veri or {}).get("ad", ""),
                                     (veri or {}).get("takim_boyu"),
                                     (veri or {}).get("not"), _istemci(veri))


def api_profil_uygula(veri, ip):
    _yetki(veri, ip)
    return DENETLEYICI.profil_uygula(
        veri["ad"], (veri or {}).get("eksenler") or ("X", "Y", "Z"),
        _istemci(veri))


def api_profil_sil(veri, ip):
    _yetki(veri, ip)
    return DENETLEYICI.profil_sil(veri["ad"], _istemci(veri))


# -- kalibrasyon (dijital kumpasla) --------------------------------------
# Mach3'un COM arayuzu "Steps per unit"/"Backlash" degerlerini ne okur ne
# yazar; bu uc olcumu yurutur, hesabi yapar ve Mach3'te girilecek degerleri
# dondurur. Son yazma islemi operatordedir.
def api_kalibrasyon(veri, ip):
    v = veri or {}
    islem = v.get("islem", "durum")
    if islem == "durum":
        return DENETLEYICI.kalibrasyon_durum()
    _yetki(veri, ip)
    if islem == "basla":
        return DENETLEYICI.kalibrasyon_basla(
            v.get("eksen", "X"), v.get("tur", "olcek"), v.get("mesafe"),
            v.get("feed"), int(v.get("yon", 1)), v.get("on_yaklasma"),
            v.get("tekrar"), v.get("adim_basi"), _istemci(veri))
    if islem == "adim":
        return DENETLEYICI.kalibrasyon_adim(v.get("adim", ""), _istemci(veri))
    if islem == "olcum":
        return DENETLEYICI.kalibrasyon_olcum(
            v.get("olculen"), v.get("a"), v.get("b"), _istemci(veri))
    if islem == "bitir":
        return DENETLEYICI.kalibrasyon_bitir(_istemci(veri))
    if islem == "iptal":
        return DENETLEYICI.kalibrasyon_iptal(_istemci(veri))
    if islem == "uygula":
        return DENETLEYICI.kalibrasyon_uygula(v.get("deger"), _istemci(veri))
    if islem == "ayar":
        return DENETLEYICI.kalibrasyon_ayar_kaydet(v.get("yama") or {},
                                                   _istemci(veri))
    return {"hata": f"Bilinmeyen kalibrasyon islemi: {islem}"}


# -- dosya izleme --------------------------------------------------------
def api_izleme(veri, ip):
    islem = (veri or {}).get("islem")
    if islem == "yoksay":
        _yetki(veri, ip)
        DENETLEYICI.izleyici.yoksay((veri or {}).get("yol"))
    elif islem == "ac":
        _yetki(veri, ip)
        yol = veri["yol"]
        DENETLEYICI.izleyici.onayla(yol)
        return DENETLEYICI.program_yukle(yol)
    elif islem == "tara":
        DENETLEYICI.izleyici.tara()
    elif islem == "sifirla":
        _yetki(veri, ip)
        DENETLEYICI.izleyici.sifirla()
    return DENETLEYICI.izleyici.durum()


# -- is raporu -----------------------------------------------------------
def api_rapor(veri, ip):
    d = DENETLEYICI.rapor_verisi(bool((veri or {}).get("son")))
    return {"rapor": d} if d else {"hata": "Henuz calistirilmis bir is yok"}


def api_mdi(veri, ip):
    _yetki(veri, ip)
    return DENETLEYICI.mdi(veri.get("satir", ""), _istemci(veri))


# -- program -------------------------------------------------------------
def api_program_yukle(veri, ip):
    _yetki(veri, ip)
    return DENETLEYICI.program_yukle(veri["yol"])


def api_program_bilgi(veri, ip):
    return DENETLEYICI.program_bilgi()


def api_program_blok(veri, ip):
    _yetki(veri, ip)
    DENETLEYICI.blok_sec(int(veri["blok"]))
    return DENETLEYICI.program_bilgi()


def api_program_denetle(veri, ip):
    _yetki(veri, ip)
    return DENETLEYICI.program_denetle(veri.get("blok"))


def api_program_baslat(veri, ip):
    _yetki(veri, ip)
    return DENETLEYICI.program_baslat(veri.get("blok"),
                                      bool(veri.get("onay")), _istemci(veri))


def api_sure(veri, ip):
    """Dosyaya dokunmadan sure tahmini (yuklu program veya verilen yol)."""
    yol = (veri or {}).get("yol")
    if yol:
        p = GC.Program(yol)
    elif DENETLEYICI.program:
        p = DENETLEYICI.program
    else:
        return {"hata": "Program yok"}
    fro = float((veri or {}).get("fro") or 100.0)
    blok = int((veri or {}).get("blok") or 0)
    return {"toplam": SURE.program_tahmini(p, AYARLAR["makine"],
                                           AYARLAR["gcode"], fro, blok),
            "bloklar": SURE.blok_tahminleri(p, AYARLAR["makine"],
                                            AYARLAR["gcode"], fro)}


# -- dosya gezgini -------------------------------------------------------
# HIZ NOTU: eskiden her girdi icin ayri ayri `isdir` + `stat` cagriliyordu;
# ag surucusu (\\sunucu\isler) uzerinde bu, klasor basina yuzlerce gidis-
# donus demektir ve gezgin gozle gorulur sekilde takilir. `os.scandir`
# girdinin turunu ve stat bilgisini isletim sisteminden TEK gecise alir.
def _gozat_oku(klasor):
    klasorler, dosyalar = [], []
    with os.scandir(klasor) as girdiler:
        for g in girdiler:
            if g.name.startswith("."):
                continue
            try:
                if g.is_dir(follow_symlinks=False):
                    st = g.stat(follow_symlinks=False)
                    klasorler.append({"ad": g.name, "yol": g.path,
                                      "zaman": st.st_mtime})
                elif os.path.splitext(g.name)[1].lower() in GCODE_UZANTI:
                    st = g.stat()
                    dosyalar.append({"ad": g.name, "yol": g.path,
                                     "boyut": st.st_size,
                                     "zaman": st.st_mtime})
            except OSError:
                continue                   # erisilemeyen girdiyi atla
    return klasorler, dosyalar


def api_gozat(veri, ip):
    _yetki(veri, ip)
    v = veri or {}
    ay = AYARLAR["gcode"]
    # Klasor hatirlama: kullanici dosya ACMADAN gezip cikabilir; bir dahaki
    # acilista yine ayni yerden devam etmek ister.
    klasor = (v.get("klasor") or ay.get("gozat_klasor")
              or ay.get("son_klasor") or os.path.expanduser("~"))
    klasor = os.path.abspath(os.path.expanduser(klasor))
    if not os.path.isdir(klasor):
        klasor = os.path.expanduser("~")
    try:
        klasorler, dosyalar = _gozat_oku(klasor)
    except PermissionError:
        return {"hata": f"Erisim yok: {klasor}"}
    except OSError as e:
        return {"hata": f"Klasor okunamadi: {e}"}

    siralama = (v.get("siralama") or ay.get("gozat_siralama") or "tarih")
    if siralama == "ad":
        anahtar, ters = (lambda o: o["ad"].lower()), False
    else:
        # En son degisiklik yapilan en ustte: yeni is klasoru hep elinizin
        # altinda olur.
        anahtar, ters = (lambda o: o.get("zaman") or 0.0), True
    klasorler.sort(key=anahtar, reverse=ters)
    dosyalar.sort(key=anahtar, reverse=ters)

    yama = {"gozat_klasor": klasor}
    if v.get("siralama"):
        yama["gozat_siralama"] = siralama
    AYARLAR.guncelle({"gcode": yama})

    ust = os.path.dirname(klasor)
    return {"klasor": klasor, "ust": ust if ust != klasor else None,
            "klasorler": klasorler, "dosyalar": dosyalar,
            "siralama": siralama, "ev": os.path.expanduser("~"),
            "son_klasor": ay.get("son_klasor") or ""}


# -- ayarlar / kodlar / gunluk ------------------------------------------
def api_ayarlar(veri, ip):
    if (veri or {}).get("yama"):
        _yetki(veri, ip)
        AYARLAR.guncelle(veri["yama"])
        DENETLEYICI.ayarlar_yenile()
        DENETLEYICI.gunluk.ekle("ayar", "Ayarlar guncellendi",
                                {"anahtarlar": list(veri["yama"].keys())})
    return {"ayarlar": AYARLAR.guvenli_kopya(),
            "oem": DENETLEYICI.kodlar.katalog()}


def api_kilit(veri, ip):
    _yetki(veri, ip)
    return DENETLEYICI.kilit_degistir((veri or {}).get("ac"))


def api_gunluk(veri, ip):
    return {"olaylar": DENETLEYICI.gunluk.liste(
        int((veri or {}).get("adet", 80)), int((veri or {}).get("sonra", 0)))}


API = {
    "/api/oturum": api_oturum,
    "/api/durum": api_durum,
    "/api/baglan": api_baglan,
    "/api/kes": api_kes,
    "/api/kontrol/al": api_kontrol_al,
    "/api/kontrol/birak": api_kontrol_birak,
    "/api/acil": api_acil,
    "/api/feedhold": api_feedhold,
    "/api/devam": api_devam,
    "/api/durdur": api_durdur,
    "/api/jog/adim": api_jog_adim,
    "/api/jog/basla": api_jog_basla,
    "/api/jog/hiz": api_jog_hiz,
    "/api/jog/kalp": api_jog_kalp,
    "/api/jog/bitir": api_jog_bitir,
    "/api/git": api_git,
    "/api/guvenli_z": api_guvenli_z,
    "/api/sifirla": api_sifirla,
    "/api/dro": api_dro,
    "/api/sifira_git": api_sifira_git,
    "/api/referans": api_referans,
    "/api/fro": api_fro,
    "/api/profil/liste": api_profil_liste,
    "/api/profil/kaydet": api_profil_kaydet,
    "/api/profil/uygula": api_profil_uygula,
    "/api/profil/sil": api_profil_sil,
    "/api/kalibrasyon": api_kalibrasyon,
    "/api/izleme": api_izleme,
    "/api/rapor": api_rapor,
    "/api/mdi": api_mdi,
    "/api/program/yukle": api_program_yukle,
    "/api/program/bilgi": api_program_bilgi,
    "/api/program/blok": api_program_blok,
    "/api/program/denetle": api_program_denetle,
    "/api/program/baslat": api_program_baslat,
    "/api/sure": api_sure,
    "/api/gozat": api_gozat,
    "/api/ayarlar": api_ayarlar,
    "/api/kilit": api_kilit,
    "/api/gunluk": api_gunluk,
}

SURUM = "1.0.0"


# ----------------------------------------------------------------------
# HTTP isleyici
# ----------------------------------------------------------------------
class Isleyici(BaseHTTPRequestHandler):
    server_version = "Mach3Kontrol/" + SURUM

    # HIZ: arayuz saniyede onlarca kucuk POST gonderir (jog kalp atisi, durum,
    # gezgin). HTTP/1.0'da her istek YENI bir TCP baglantisi acar; el sikisma
    # + kapanma gecikmesi jogda gozle gorulur bir "agirlik" yaratiyordu.
    # HTTP/1.1 ile baglanti acik kalir (keep-alive). Bunun sarti her yanitin
    # dogru Content-Length tasimasidir - `_gonder` bunu her zaman yapar,
    # olay akisi ise acikca "Connection: close" der.
    protocol_version = "HTTP/1.1"
    # Nagle, kucuk paketleri birlestirmek icin 40 ms'ye kadar bekletir; jog
    # komutlarinda bu dogrudan tepki gecikmesidir.
    disable_nagle_algorithm = True

    def log_message(self, *a):
        pass

    @property
    def istemci_ip(self):
        return self.client_address[0]

    def _gonder(self, kod, icerik, tur="application/json; charset=utf-8"):
        govde = icerik if isinstance(icerik, bytes) else icerik.encode("utf-8")
        self.send_response(kod)
        self.send_header("Content-Type", tur)
        self.send_header("Content-Length", str(len(govde)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(govde)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # -- GET -------------------------------------------------------------
    def do_GET(self):
        yol = self.path.split("?", 1)[0]
        if yol == "/favicon.ico":
            self.send_response(204)
            # HTTP/1.1 keep-alive: govdesiz yanitta da uzunluk belirtilir
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if yol == "/olay":
            self._olay_akisi()
            return
        if yol.startswith("/indir/rapor."):
            self._rapor_indir(yol.rsplit(".", 1)[-1].lower())
            return
        if yol == "/":
            yol = "/index.html"
        dosya = os.path.abspath(os.path.join(WEB_DIZIN, yol.lstrip("/")))
        if dosya.startswith(WEB_DIZIN) and os.path.isfile(dosya):
            tur = ("text/html; charset=utf-8" if dosya.endswith(".html")
                   else "text/javascript; charset=utf-8" if dosya.endswith(".js")
                   else "text/css; charset=utf-8" if dosya.endswith(".css")
                   else "application/octet-stream")
            with open(dosya, "rb") as f:
                self._gonder(200, f.read(), tur)
        else:
            self._gonder(404, json.dumps({"hata": "bulunamadi"}))

    def _rapor_indir(self, bicim):
        """/indir/rapor.pdf | .csv | .json -> dosya olarak iner."""
        from . import rapor as RAP
        veri = DENETLEYICI.rapor_verisi(son=True)
        if not veri:
            self._gonder(404, json.dumps(
                {"hata": "Henuz calistirilmis bir is yok"}))
            return
        try:
            if bicim == "pdf":
                govde, tur = RAP.pdf_uret(veri), "application/pdf"
            elif bicim == "csv":
                # BOM: Excel'in UTF-8'i dogru acmasi icin
                govde = ("﻿" + RAP.csv_uret(veri)).encode("utf-8")
                tur = "text/csv; charset=utf-8"
            elif bicim == "json":
                govde = RAP.json_uret(veri).encode("utf-8")
                tur = "application/json; charset=utf-8"
            else:
                self._gonder(404, json.dumps({"hata": "bilinmeyen bicim"}))
                return
        except Exception as e:                        # noqa: BLE001
            traceback.print_exc()
            self._gonder(500, json.dumps({"hata": str(e)}))
            return
        ad = RAP.dosya_adi(veri, bicim)
        self.send_response(200)
        self.send_header("Content-Type", tur)
        self.send_header("Content-Length", str(len(govde)))
        self.send_header("Content-Disposition",
                         f'attachment; filename="{ad}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(govde)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _olay_akisi(self):
        """SSE: durum (~10 Hz) + yeni gunluk olaylari."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        son_olay = 0
        try:
            while True:
                d = DENETLEYICI.durum()
                d["sunucu"] = {"yerel": _yerel_mi(self.istemci_ip),
                               "surum": SURUM}
                yeni = DENETLEYICI.gunluk.liste(20, son_olay)
                if yeni:
                    son_olay = yeni[-1]["id"]
                    d["olaylar"] = yeni
                self.wfile.write(
                    ("data: " + _json_govde(d) + "\n\n").encode("utf-8"))
                self.wfile.flush()
                time.sleep(0.12)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    # -- POST ------------------------------------------------------------
    def do_POST(self):
        yol = self.path.split("?", 1)[0]
        fn = API.get(yol)
        if not fn:
            self._gonder(404, json.dumps({"hata": "bilinmeyen uc"}))
            return
        try:
            uzunluk = int(self.headers.get("Content-Length", 0))
            veri = json.loads(self.rfile.read(uzunluk) or b"{}")
            sonuc = fn(veri, self.istemci_ip)
            govde = _json_govde(sonuc if sonuc is not None else {"ok": True})
        except GUV.GuvenlikHatasi as e:
            self._gonder(200, _json_govde({"hata": str(e),
                                           "sebepler": e.sebepler}))
            return
        except Exception as e:                        # noqa: BLE001
            traceback.print_exc()
            self._gonder(200, _json_govde(
                {"hata": f"{type(e).__name__}: {e}"}))
            return
        self._gonder(200, govde)


# ----------------------------------------------------------------------
def _yerel_adres(port):
    """Telefondan girilecek adresi bulmaya calisir."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))                    # paket gonderilmez
        ip = s.getsockname()[0]
        s.close()
        return f"http://{ip}:{port}"
    except OSError:
        return None


def calistir(port=None, host=None, ac=None, ayarlar_yolu=None,
             baglan=True, tur=None, mach3_baslat=None):
    global AYARLAR, DENETLEYICI
    AYARLAR = AY.Ayarlar(ayarlar_yolu)
    if tur:
        # Yalnizca bu calistirma icin (diske yazilmaz) - bkz. cli.main
        AYARLAR.veri["baglanti"]["tur"] = tur
    if mach3_baslat is not None:
        AYARLAR.veri["baglanti"]["mach3_baslat"] = bool(mach3_baslat)
    DENETLEYICI = DEN.Denetleyici(AYARLAR)

    w = AYARLAR["web"]
    port = int(port or w.get("port", 8010))
    host = host or w.get("host", "0.0.0.0")
    ac = w.get("tarayici_ac", False) if ac is None else ac

    if baglan:
        try:
            DENETLEYICI.baglan()
        except Exception as e:                        # noqa: BLE001
            print(f"[uyari] Baglanti kurulamadi: {e}")
            print("        Arayuzden 'Baglan' ile yeniden deneyebilirsiniz.")

    sunucu = ThreadingHTTPServer((host, port), Isleyici)
    yerel = f"http://127.0.0.1:{port}"
    agdan = _yerel_adres(port) if host == "0.0.0.0" else None
    print("=" * 64)
    print(f"  Mach3-Kontrol {SURUM}")
    print(f"  Bu bilgisayardan : {yerel}")
    if agdan:
        print(f"  Telefondan       : {agdan}")
        print(f"  PIN              : {AYARLAR['guvenlik']['pin']}")
    print(f"  Baglanti         : "
          f"{DENETLEYICI.baglanti.tur if DENETLEYICI.baglanti else 'yok'}")
    print("  Durdurmak icin Ctrl+C")
    print("=" * 64)
    if ac:
        threading.Timer(0.8, lambda: webbrowser.open(yerel)).start()
    try:
        sunucu.serve_forever()
    except KeyboardInterrupt:
        print("\nKapatiliyor...")
    finally:
        DENETLEYICI.acil_durdur("sunucu kapaniyor")
        DENETLEYICI.kapat()
        sunucu.shutdown()
