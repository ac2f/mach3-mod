#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Baglanti teshisi
================
"Mach3 acik ama arayuz baglanti yok diyor" durumunda nerede takildigini
adim adim gosterir:

    python main.py --tani

Zinciri bastan sona dener ve her halkayi ayri ayri raporlar:

    1. Isletim sistemi / Python (32-64 bit) / yonetici yetkisi
    2. pywin32 kurulu mu
    3. Mach3 gercekten calisiyor mu (pencere + surec)
    4. Windows kayit defterinde hangi Mach ProgID'leri var  <-- en kritigi
    5. Her ProgID icin GetActiveObject ve Dispatch denemesi
    6. Baglanti kurulduysa: metot yoklamasi + DRO okuma

Ciktiyi oldugu gibi paylasabilirsiniz; makineye ozel bir bilgi icermez.
"""

import ctypes
import platform
import struct
import sys

from . import baglanti as BAG

CIZGI = "-" * 66


def _yaz(baslik):
    print()
    print(baslik)
    print(CIZGI)


def _durum(ok, mesaj):
    print(f"  [{'OK ' if ok else 'HATA'}] {mesaj}")


def _yonetici_mi():
    if platform.system() != "Windows":
        return None
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:                                 # noqa: BLE001
        return None


def _progid_ara():
    """HKEY_CLASSES_ROOT altinda icinde 'mach' gecen ProgID'leri listeler.

    Dogru ProgID'yi bulmanin en hizli yolu budur: Mach3 kurulumu kendini
    buraya kaydeder.
    """
    if platform.system() != "Windows":
        return []
    try:
        import winreg
    except ImportError:
        return []
    bulunan = []
    try:
        kok = winreg.ConnectRegistry(None, winreg.HKEY_CLASSES_ROOT)
        i = 0
        while True:
            try:
                ad = winreg.EnumKey(kok, i)
            except OSError:
                break
            i += 1
            if "mach" not in ad.lower() or "." not in ad:
                continue
            clsid = ""
            try:
                with winreg.OpenKey(kok, ad + r"\CLSID") as k:
                    clsid = winreg.QueryValue(k, "")
            except OSError:
                pass
            bulunan.append((ad, clsid))
    except Exception as e:                            # noqa: BLE001
        print(f"  (kayit defteri taranamadi: {e})")
    return bulunan


def calistir(ayarlar=None):
    from . import ayarlar as AY
    ayarlar = ayarlar or AY.Ayarlar()
    b_ayar = ayarlar["baglanti"]

    print()
    print("=" * 66)
    print("  Mach3-Kontrol - baglanti teshisi")
    print("=" * 66)

    # ---------------------------------------------------------------- 1
    _yaz("1) Sistem")
    print(f"  Isletim sistemi : {platform.system()} {platform.release()}")
    print(f"  Python          : {sys.version.split()[0]} "
          f"({struct.calcsize('P') * 8} bit)")
    yon = _yonetici_mi()
    print(f"  Yonetici yetkisi: "
          f"{'EVET' if yon else 'hayir' if yon is False else 'bilinmiyor'}")
    if platform.system() != "Windows":
        _durum(False, "Windows degil: gercek Mach3 baglantisi kurulamaz. "
                      "Yalnizca --simulasyon calisir.")
        print()
        return 1

    # ---------------------------------------------------------------- 2
    _yaz("2) pywin32")
    try:
        import win32com.client                        # noqa: F401
        import win32api
        _durum(True, f"kurulu (build {win32api.GetFileVersionInfo.__module__})")
    except ImportError as e:
        _durum(False, f"kurulu DEGIL ({e}) -> 'pip install pywin32'")
        return 1

    # ---------------------------------------------------------------- 3
    _yaz("3) Mach3 calisiyor mu?")
    pencere = BAG._pencere_ara("mach3")
    surec = BAG._surec_ara("mach3.exe")
    _durum(bool(pencere), f"pencere: {pencere or 'bulunamadi'}")
    _durum(bool(surec), f"surec  : {surec or 'bulunamadi'}")
    if not (pencere or surec):
        print("  -> Mach3 kapali gorunuyor. Once Mach3'u acin.")
        print("     (Mach3 acik ve yonetici olarak calisiyorsa, bu programi")
        print("      da yonetici olarak calistirin; aksi halde surec/pencere")
        print("      listesinde gorunmeyebilir.)")

    # ---------------------------------------------------------------- 4
    _yaz("4) Kayitli Mach ProgID'leri (HKEY_CLASSES_ROOT)")
    progidler = _progid_ara()
    if progidler:
        for ad, clsid in progidler:
            print(f"  {ad:<28} {clsid}")
        print()
        print("  Ayarlardaki ProgID : " + str(b_ayar.get("progid")))
        adlar = [a for a, _ in progidler]
        if b_ayar.get("progid") not in adlar:
            print("  -> DIKKAT: ayarlardaki ProgID bu listede YOK.")
            print("     Yukaridakilerden birini Ayarlar > Baglanti'ya yazin.")
    else:
        _durum(False, "Mach ile ilgili ProgID bulunamadi.")
        print("     Mach3'un COM sunucusu kayitli olmayabilir. Mach3'u bir")
        print("     kez YONETICI olarak calistirmak genelde kaydi tamamlar.")

    # ---------------------------------------------------------------- 5
    _yaz("5) Baglanti denemeleri")
    import win32com.client
    adaylar = []
    for a in ([b_ayar.get("progid")] + list(b_ayar.get("yedek_progid") or [])
              + [ad for ad, _ in progidler]):
        if a and a not in adaylar:
            adaylar.append(a)

    nesne = None
    kazanan = ""
    for ad in adaylar:
        try:
            nesne = win32com.client.GetActiveObject(ad)
            kazanan = f"{ad} (GetActiveObject)"
            _durum(True, f"{ad:<28} GetActiveObject basarili")
            break
        except Exception as e:                        # noqa: BLE001
            _durum(False, f"{ad:<28} GetActiveObject: {_kisa(e)}")
    if nesne is None:
        print("  (GetActiveObject basarisiz olabilir - cogu COM sunucusu")
        print("   Running Object Table'a kaydolmaz; bu tek basina sorun")
        print("   degildir. Simdi Dispatch deneniyor.)")
        for ad in adaylar:
            try:
                nesne = win32com.client.Dispatch(ad)
                kazanan = f"{ad} (Dispatch)"
                _durum(True, f"{ad:<28} Dispatch basarili")
                break
            except Exception as e:                    # noqa: BLE001
                _durum(False, f"{ad:<28} Dispatch: {_kisa(e)}")

    if nesne is None:
        _yaz("SONUC")
        print("  Mach3'un COM arayuzune ULASILAMADI.")
        print()
        print("  Sirasiyla deneyin:")
        print("   1. Mach3 ile bu programi AYNI yetkide calistirin")
        print("      (Mach3 yoneticiyse: cmd'yi 'Yonetici olarak calistir').")
        print("   2. Yukaridaki 4. adimda listelenen ProgID'yi")
        print("      Ayarlar > Baglanti alanina yazin.")
        print("   3. Mach3'u bir kez yonetici olarak acip kapatin")
        print("      (COM sunucu kaydini tazeler).")
        print("   4. Bu ciktinin tamamini paylasin.")
        return 2

    # ---------------------------------------------------------------- 6
    _yaz(f"6) Baglanti kuruldu: {kazanan}")
    for ad in BAG.COM_METOTLAR:
        _durum(hasattr(nesne, ad), f"metot {ad}")

    print()
    print("  DRO okuma denemesi (is koordinatlari):")
    kod = {"X": 800, "Y": 801, "Z": 802}
    okundu = False
    for eksen, k in kod.items():
        try:
            deger = nesne.GetOEMDRO(k)
            print(f"    {eksen} (DRO {k}) = {deger}")
            okundu = True
        except Exception as e:                        # noqa: BLE001
            print(f"    {eksen} (DRO {k}) OKUNAMADI: {_kisa(e)}")

    _yaz("SONUC")
    if okundu:
        print("  Baglanti CALISIYOR.")
        print("  Yukaridaki degerler Mach3 ekranindaki DRO ile ayni mi?")
        print("   * Ayniysa: arayuzde Ayarlar > Baglanti > 'Mach3 (COM)'")
        print("     secip Baglan deyin.")
        print("   * Farkliysa: OEM DRO kodlariniz farkli. Ayarlar > OEM")
        print("     kodlari panelinden duzeltin.")
    else:
        print("  COM nesnesine ulasildi ama DRO okunamadi.")
        print("  GetOEMDRO metodu farkli isimde olabilir; ciktiyi paylasin.")
    print()
    return 0


def _kisa(e):
    m = str(e).replace("\n", " ")
    return m[:110] + ("…" if len(m) > 110 else "")
