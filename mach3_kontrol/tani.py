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
import re
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


# Yalnizca GERCEKTEN Mach ailesine ait ProgID'ler: "Mach3.Document",
# "Mach4.Document" gibi. "IAS.SdoMachine" veya
# "MicrosoftEdgeUpdate.CoreMachineClass" gibi icinde "machine" gecen alakasiz
# Windows siniflari ELENIR (yoksa yanlislikla onlara baglaniriz).
_PROGID_RE = re.compile(r"^mach\d*\.", re.I)


def _progid_ara(gorunum=None):
    """Kayit defterindeki Mach ProgID'lerini listeler.

    gorunum: 32 | 64 | None(=surecin kendi gorunumu)

    NEDEN ONEMLI: 32-bit bir uygulamanin (Mach3 32-bit'tir) COM kaydi
    `HKLM\\Software\\Classes\\Wow6432Node` altina yazilir. 64-bit bir Python
    sureci kayit defterinin 64-bit gorunumune baktigi icin bu kaydi
    **goremez** ve "Invalid class string" hatasi alir. Bu yuzden her iki
    gorunum de ayri ayri taranir.
    """
    if platform.system() != "Windows":
        return []
    try:
        import winreg
    except ImportError:
        return []
    erisim = winreg.KEY_READ
    if gorunum == 32:
        erisim |= winreg.KEY_WOW64_32KEY
    elif gorunum == 64:
        erisim |= winreg.KEY_WOW64_64KEY

    bulunan = []
    for kok, alt in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Classes"),
                     (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Classes")):
        try:
            with winreg.OpenKey(kok, alt, 0, erisim) as k:
                i = 0
                while True:
                    try:
                        ad = winreg.EnumKey(k, i)
                    except OSError:
                        break
                    i += 1
                    if not _PROGID_RE.match(ad):
                        continue
                    clsid = ""
                    try:
                        with winreg.OpenKey(k, ad + r"\CLSID", 0, erisim) as c:
                            clsid = winreg.QueryValue(c, "")
                    except OSError:
                        pass
                    if (ad, clsid) not in bulunan:
                        bulunan.append((ad, clsid))
        except OSError:
            continue
    return bulunan


def _surec_32bit_mi(pid):
    """Verilen surec 64-bit Windows uzerinde 32-bit mi calisiyor?"""
    try:
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))   # QUERY_LIMITED_INFO
        if not h:
            return None
        try:
            wow = ctypes.c_int()
            if not k32.IsWow64Process(h, ctypes.byref(wow)):
                return None
            return bool(wow.value)
        finally:
            k32.CloseHandle(h)
    except Exception:                                 # noqa: BLE001
        return None


def _mach3_pid():
    """Calisan Mach3.exe surecinin PID'i."""
    if platform.system() != "Windows":
        return None
    import subprocess
    try:
        cikti = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"], capture_output=True,
            text=True, timeout=15, creationflags=0x08000000).stdout
    except Exception:                                 # noqa: BLE001
        return None
    for satir in (cikti or "").splitlines():
        if "mach3.exe" in satir.lower():
            parca = [p.strip('" ') for p in satir.split('","')]
            if len(parca) > 1 and parca[1].isdigit():
                return int(parca[1])
    return None


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
    pid = _mach3_pid()
    mach3_32 = _surec_32bit_mi(pid) if pid else None
    if pid:
        print(f"  Mach3 PID {pid} · "
              f"{'32 bit' if mach3_32 else '64 bit' if mach3_32 is False else 'bit genisligi okunamadi'}")
    if not (pencere or surec):
        print("  -> Mach3 kapali gorunuyor. Once Mach3'u acin.")
        print("     (Mach3 acik ve yonetici olarak calisiyorsa, bu programi")
        print("      da yonetici olarak calistirin; aksi halde surec/pencere")
        print("      listesinde gorunmeyebilir.)")

    # ---------------------------------------------------------------- 4
    py_64 = struct.calcsize("P") * 8 == 64
    _yaz("4) Kayitli Mach ProgID'leri (her iki kayit defteri gorunumu)")
    p32 = _progid_ara(32)
    p64 = _progid_ara(64)
    for etiket, liste in (("32-bit gorunum", p32), ("64-bit gorunum", p64)):
        if liste:
            for ad, clsid in liste:
                print(f"  [{etiket}] {ad:<22} {clsid}")
        else:
            print(f"  [{etiket}] Mach ProgID yok")
    print()
    print("  Ayarlardaki ProgID : " + str(b_ayar.get("progid")))

    # En kritik teshis: 32-bit kayit + 64-bit Python = goremez
    bit_uyusmazligi = bool(p32) and not p64 and py_64
    if bit_uyusmazligi:
        print()
        print("  >>> SORUN BULUNDU: Mach3'un COM kaydi YALNIZCA 32-bit")
        print("      gorunumde. Calistirdiginiz Python 64-bit oldugu icin bu")
        print("      kaydi goremiyor -> 'Invalid class string'.")
    elif not p32 and not p64:
        print()
        print("  >>> Mach3'un COM sunucusu HIC kayitli degil.")

    # ---------------------------------------------------------------- 5
    _yaz("5) Baglanti denemeleri")
    import win32com.client
    # SADECE gercek Mach ProgID'leri denenir. (Onceki surumde 'machine'
    # gecen alakasiz Windows siniflari da deneniyordu ve bunlardan birine
    # baglanip yanlis 'basarili' raporu uretiliyordu.)
    adaylar = []
    for a in ([b_ayar.get("progid")] + list(b_ayar.get("yedek_progid") or [])
              + [ad for ad, _ in p32 + p64]):
        if a and _PROGID_RE.match(a) and a not in adaylar:
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
        if bit_uyusmazligi or (py_64 and (pencere or surec) and not p64):
            print("  EN OLASI SEBEP: 32/64 bit uyusmazligi.")
            print("  Mach3 32-bit bir programdir; COM kaydi 32-bit kayit")
            print("  defteri gorunumundedir. 64-bit Python bunu goremez.")
            print()
            print("  COZUM - 32-bit Python kurun (yan yana durabilirler):")
            print("   1. python.org/downloads/windows -> 'Windows installer")
            print("      (32-bit)' surumunu indirin (or. 3.12).")
            print("   2. Kurulumda 'Add to PATH' YERINE ozel dizin secin,")
            print("      or. C:\\Python312-32")
            print("   3. C:\\Python312-32\\python.exe -m pip install pywin32")
            print("   4. C:\\Python312-32\\python.exe main.py --tani")
            print("      (once teshis, sonra: ... main.py)")
        else:
            print("  Sirasiyla deneyin:")
            print("   1. Mach3 ile bu programi AYNI yetkide calistirin")
            print("      (Mach3 yoneticiyse: cmd'yi 'Yonetici olarak calistir').")
            print("   2. Mach3'u bir kez yonetici olarak acip kapatin")
            print("      (COM sunucu kaydini tazeler).")
            print("   3. 4. adimda ProgID hic gorunmuyorsa Mach3 kurulumu")
            print("      COM sunucusunu kaydetmemis olabilir; Mach3'u")
            print("      yeniden kurmayi deneyin.")
        print()
        print("  Bu ciktinin tamamini paylasabilirsiniz.")
        return 2

    # ---------------------------------------------------------------- 6
    _yaz(f"6) Baglanti kuruldu: {kazanan}")
    metotlar = {ad: hasattr(nesne, ad) for ad in BAG.COM_METOTLAR}
    for ad, var in metotlar.items():
        _durum(var, f"metot {ad}")
    if not metotlar.get("GetOEMDRO"):
        _yaz("SONUC")
        print("  Baglanilan COM nesnesi Mach3 DEGIL (GetOEMDRO metodu yok).")
        print(f"  '{kazanan}' baska bir uygulamaya ait olabilir.")
        print("  Ayarlar > Baglanti'daki ProgID'yi kontrol edin.")
        return 2

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
