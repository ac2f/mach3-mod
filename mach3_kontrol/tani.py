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
import os
import platform
import re
import struct
import sys
import time

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


def com_hata_aciklama(e):
    """Sik gorulen COM hatalarinin Turkce karsiligi."""
    m = str(e)
    if "-2147221005" in m or "Invalid class string" in m:
        return "ProgID kayitli degil (yanlis ad ya da yanlis bit gorunumu)"
    if "-2147221021" in m or "Operation unavailable" in m:
        return "nesne Running Object Table'da yok (tek basina sorun degil)"
    if "-2146959355" in m or "Server execution failed" in m:
        return ("COM sunucusu baslatilamadi. Mach3'te en sik sebep: Mach3 "
                "ELLE acilmis (otomasyon arayuzunu yalnizca /Automation ile "
                "acar) ya da bu acilisa zaten bir istemci baglanmis. Daha "
                "seyrek: yetki uyusmazligi")
    if "-2147024891" in m or "Access is denied" in m:
        return "erisim reddedildi (yetki uyusmazligi)"
    if "-2147221164" in m or "Class not registered" in m:
        return "sinif kayitli degil"
    if "-2147467262" in m or "No such interface" in m:
        return "bu sinif Automation arayuzu sunmuyor (alakasiz bir sinif)"
    return ""


def _uyeler(nesne):
    """COM nesnesinin GERCEKTEN sundugu metot/ozellik adlari (ITypeInfo)."""
    import pythoncom
    adlar = set()

    def _topla(ti):
        try:
            ta = ti.GetTypeAttr()
        except Exception:                             # noqa: BLE001
            return
        if ta.typekind == pythoncom.TKIND_COCLASS:
            for i in range(ta.cImplTypes):
                try:
                    _topla(ti.GetRefTypeInfo(ti.GetRefTypeOfImplType(i)))
                except Exception:                     # noqa: BLE001
                    pass
            return
        for i in range(ta.cFuncs):
            try:
                fd = ti.GetFuncDesc(i)
                adlar.add(ti.GetNames(fd.memid)[0])
            except Exception:                         # noqa: BLE001
                pass
        for i in range(ta.cVars):
            try:
                vd = ti.GetVarDesc(i)
                adlar.add(ti.GetNames(vd.memid)[0])
            except Exception:                         # noqa: BLE001
                pass
        # dual arayuzlerde asil metotlar miras alinan tipte olabilir
        for i in range(getattr(ta, "cImplTypes", 0)):
            try:
                _topla(ti.GetRefTypeInfo(ti.GetRefTypeOfImplType(i)))
            except Exception:                         # noqa: BLE001
                pass

    try:
        ole = BAG._ole(nesne)          # CDispatch ya da ham PyIDispatch
        if ole.GetTypeInfoCount() > 0:
            _topla(ole.GetTypeInfo())
    except Exception as e:                            # noqa: BLE001
        return [], f"tip bilgisi alinamadi: {_kisa(e)}"
    return sorted(adlar), ""


def _clsid_sunucu(clsid, gorunum):
    """CLSID icin kayitli sunucu EXE/DLL yolu.

    'Server execution failed' hatasinin en somut sebebi buradaki yolun
    yanlis olmasidir (Mach3 baska dizine kurulmus, tasinmis ya da eski bir
    kurulumdan kalma kayit).
    """
    if platform.system() != "Windows" or not clsid:
        return None, ""
    try:
        import winreg
    except ImportError:
        return None, ""
    erisim = winreg.KEY_READ | (winreg.KEY_WOW64_32KEY if gorunum == 32
                                else winreg.KEY_WOW64_64KEY)
    for tur in ("LocalServer32", "InprocServer32"):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                rf"SOFTWARE\Classes\CLSID\{clsid}\{tur}",
                                0, erisim) as k:
                return winreg.QueryValue(k, ""), tur
        except OSError:
            continue
    return None, ""


def _surec_yolu(pid):
    """Calisan surecin tam EXE yolu."""
    if platform.system() != "Windows" or not pid:
        return None
    try:
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))
        if not h:
            return None
        try:
            boyut = ctypes.c_ulong(1024)
            tampon = ctypes.create_unicode_buffer(1024)
            if k32.QueryFullProcessImageNameW(h, 0, tampon,
                                              ctypes.byref(boyut)):
                return tampon.value
        finally:
            k32.CloseHandle(h)
    except Exception:                                 # noqa: BLE001
        return None
    return None


def _yol_temizle(komut):
    """'"C:\\Mach3\\Mach3.exe" /Automation' -> 'C:\\Mach3\\Mach3.exe'"""
    if not komut:
        return ""
    k = komut.strip()
    if k.startswith('"'):
        return k[1:].split('"', 1)[0]
    return k.split(" /")[0].split(" -")[0].strip()


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

    # --- Kayitli sunucu EXE'si gercekten var mi? -----------------------
    calisan_yol = _surec_yolu(pid)
    if calisan_yol:
        print(f"  Calisan Mach3 EXE  : {calisan_yol}")
    yol_sorunu = False
    for ad, clsid in (p32 + p64):
        if not clsid:
            continue
        for gorunum in (32, 64):
            komut, tur = _clsid_sunucu(clsid, gorunum)
            if not komut:
                continue
            exe = _yol_temizle(komut)
            var = os.path.isfile(exe)
            print(f"  {ad} [{gorunum}-bit] {tur}:")
            print(f"    {komut}")
            _durum(var, "dosya var" if var else f"DOSYA YOK -> {exe}")
            if not var:
                yol_sorunu = True
            elif calisan_yol and os.path.normcase(exe) != \
                    os.path.normcase(calisan_yol):
                print("    ! Kayitli yol, CALISAN Mach3'ten farkli "
                      "(iki kurulum olabilir).")
                yol_sorunu = True

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
    # Once Dispatch: Mach3 /Automation ile baslatilmissa sinif fabrikasi
    # kayitlidir ve ANINDA baglanir. (GetActiveObject genelde basarisizdir;
    # Mach3 kendini Running Object Table'a hic kaydetmez.)
    for ad in adaylar:
        try:
            nesne = win32com.client.Dispatch(ad)
            kazanan = f"{ad} (Dispatch)"
            _durum(True, f"{ad:<28} Dispatch basarili")
            break
        except Exception as e:                        # noqa: BLE001
            _durum(False, f"{ad:<28} Dispatch: {_kisa(e)}")
            if com_hata_aciklama(e):
                print(f"         -> {com_hata_aciklama(e)}")

    if nesne is None:
        # Mach3 kapaliysa: dogru sekilde (calisma dizini + profil +
        # /Automation) baslatmayi deneyelim; asil cozum budur.
        if not (pencere or surec):
            exe = BAG.mach3_exe_bul(b_ayar)
            print()
            print(f"  Mach3 kapali. Dogru sekilde baslatiliyor: {exe}")
            print(f"  (calisma dizini = {os.path.dirname(exe) if exe else '?'}, "
                  f"profil = {b_ayar.get('profil') or '(yok)'})")
            try:
                nesne = BAG.mach3_otomasyon_baslat(
                    exe, (b_ayar.get("profil") or "").strip(),
                    progid=b_ayar.get("progid") or "Mach4.Document")
                kazanan = "otomasyon ile baslatildi"
                _durum(True, "Mach3 baslatildi ve COM arayuzu acildi")
            except Exception as e:                    # noqa: BLE001
                _durum(False, f"baslatma basarisiz: {_kisa(e)}")

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
        elif yol_sorunu:
            print("  EN OLASI SEBEP: kayitli sunucu EXE yolu yanlis.")
            print("  Yukarida 'DOSYA YOK' ya da 'farkli' isaretli satira")
            print("  bakin. Mach3 tasinmis/yeniden kurulmus olabilir.")
            print()
            print("  COZUM - kaydi tazeleyin (yonetici cmd):")
            print("   1. cd <Mach3'un GERCEK kurulu oldugu dizin>")
            print("   2. Mach3.exe /regserver")
            print("      (bu switch yoksa Mach3'u ayni dizinden bir kez")
            print("       yonetici olarak calistirmak da kaydi tazeler)")
        elif pencere or surec:
            print("  EN OLASI SEBEP: Mach3 ELLE acilmis.")
            print()
            print("  Mach3 COM otomasyon arayuzunu yalnizca '/Automation'")
            print("  anahtariyla baslatildiginda acar. Cift tiklayarak acilan")
            print("  Mach3 kendini ne Running Object Table'a kaydeder ne de")
            print("  sinif fabrikasi olusturur; bu yuzden ona baglanmak")
            print("  teknik olarak MUMKUN DEGILDIR.")
            print("  Ayrica sinif fabrikasi TEK KULLANIMLIKTIR: her Mach3")
            print("  acilisina yalnizca bir istemci baglanabilir.")
            print()
            print("  COZUM:")
            print("   1. Mach3'u TAMAMEN kapatin (gorev yoneticisinde")
            print("      mach3.exe kalmadigini dogrulayin).")
            print("   2. python main.py   -> Mach3'u kendisi dogru sekilde")
            print("      acar (calisma dizini + profil + /Automation).")
            print("   Mach3'u elle acmaya devam etmek isterseniz kisayolunuza")
            print("   /Automation anahtarini ekleyin, or.:")
            print("      C:\\Mach3\\Mach3.exe /p Mach3Mill /Automation")
        else:
            print("  Mach3 baslatilamadi ya da baslatilinca COM arayuzunu")
            print("  acmadi. En sik iki sebep:")
            print("   * Calisma dizini yanlis -> Mach3 lisans dosyasini")
            print("     (Mach1Lic.dat) bulamayip 'Corrupt install, no license")
            print("     file found' diyerek kapanir. Bu program Mach3'u kendi")
            print("     klasorunden baslatir; elle denerken siz de oyle yapin.")
            print("   * Profil secilmemis -> Mach3 profil penceresinde bekler")
            print("     ve COM arayuzunu hic acmaz. Ayarlar > Baglanti >")
            print("     'profil' alanina profil adini yazin (or. Mach3Mill).")
        print()
        print("  Bu ciktinin tamamini paylasabilirsiniz.")
        return 2

    # ---------------------------------------------------------------- 6
    _yaz(f"6) Baglanti kuruldu: {kazanan}")
    # `hasattr` bu nesnelerde calismaz (TypeLib kayitli degil, tip bilgisi
    # yok); uyeler GetIDsOfNames ile yoklanir.
    metotlar = {ad: BAG.uye_var(nesne, ad) for ad in BAG.COM_METOTLAR}
    for ad, var in metotlar.items():
        _durum(var, f"metot {ad}")
    if not metotlar.get("GetOEMDRO"):
        print("  (Mach3'te bu NORMALDIR: ust nesne yalnizca bir 'kapi'dir,")
        print("   asil metotlar GetScriptDispatch() nesnesindedir - bkz. 7)")

    # ---------------------------------------------------------------- 7
    if not metotlar.get("GetOEMDRO"):
        _yaz("7) Nesne ne sunuyor? (metotlar baska bir nesnede olabilir)")
        uyeler, hata = _uyeler(nesne)
        if hata:
            print(f"  {hata}")
        if uyeler:
            print(f"  {kazanan} uyeleri ({len(uyeler)}):")
            for i in range(0, len(uyeler), 4):
                print("    " + "  ".join(f"{u:<24}" for u in uyeler[i:i + 4]))
        else:
            print("  Nesne tip bilgisi vermiyor (yalnizca gec baglama).")

        print()
        print("  Betik nesnesi araniyor (Mach3'te asil metotlar burada olur):")
        alt, alt_ad = BAG.betik_nesnesi(nesne)
        if alt_ad:
            _durum(True, f"{alt_ad}() bir nesne dondurdu")
            alt_uyeler, alt_hata = _uyeler(alt)
            if alt_uyeler:
                print(f"  {alt_ad}() uyeleri ({len(alt_uyeler)}):")
                for i in range(0, len(alt_uyeler), 4):
                    print("    " + "  ".join(f"{u:<24}"
                                             for u in alt_uyeler[i:i + 4]))
            elif alt_hata:
                print(f"  {alt_hata}")
            if BAG.uye_var(alt, "GetOEMDRO"):
                _durum(True, "GetOEMDRO bu nesnede BULUNDU -> otomatik "
                             "kullanilacak")
                nesne, kazanan = alt, f"{kazanan} -> {alt_ad}()"
                metotlar = {ad: BAG.uye_var(nesne, ad)
                            for ad in BAG.COM_METOTLAR}
        else:
            print("  Bilinen adlarla betik nesnesi bulunamadi.")

    if not metotlar.get("GetOEMDRO"):
        _yaz("SONUC")
        print("  Baglanti kuruldu ama GetOEMDRO bulunamadi.")
        print("  Yukaridaki 7. adimdaki UYE LISTESINI paylasin; dogru")
        print("  metot adlari ona gore ayarlanacak.")
        return 2

    print()
    print("  DRO okuma denemesi (is koordinatlari):")
    sarmal = BAG._Mach3Nesne(nesne)
    kod = {"X": 800, "Y": 801, "Z": 802}
    okundu = False
    for eksen, k in kod.items():
        try:
            deger = sarmal.GetOEMDRO(k)
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
