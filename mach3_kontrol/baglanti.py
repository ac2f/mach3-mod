#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mach3 baglanti katmani
======================
Iki arka uc vardir; ikisi de ayni arayuzu (`TemelBaglanti`) sunar:

* `COMBaglanti`      - Windows'ta Mach3'un OLE/COM otomasyon arayuzu.
                       pywin32 (`pip install pywin32`) gerektirir.
* `SimulasyonBaglanti` - Mach3 olmadan (Linux/Mac dahil) calisan sanal tezgah.
                       Arayuzu, guvenlik kurallarini ve sure tahminini
                       makineye dokunmadan denemek icin kullanilir.

COM kurali: bir COM nesnesi olusturuldugu is parcaciginda kullanilmalidir.
Web sunucusu cok is parcacikli oldugu icin tum COM cagrilari TEK bir ithal
is parcacigina (`_Isci`) kuyrukla iletilir. Boylece "CoInitialize edilmemis
thread" hatalari ve yaris kosullari olusmaz.

Yetenek yoklama: Mach3 surumune gore bazi metotlar (ornegin `LoadFile`)
bulunmayabilir. Baglanti kurulurken hangi metotlarin var oldugu tespit edilir
(`yetenekler()`), arayuz de buna gore davranir - eksik metot sessizce
yutulmaz, kullaniciya bildirilir.
"""

import math
import os
import platform
import queue
import re
import struct
import subprocess
import threading
import time

_WORD_RE = re.compile(r"([A-Za-z])\s*([+-]?\d*\.?\d+)")

# Yoklanacak COM metotlari (ad -> zorunlu mu)
COM_METOTLAR = {
    "GetOEMDRO": True, "SetOEMDRO": True, "Code": True,
    "DoOEMButton": True, "GetOEMLED": False, "IsMoving": False,
    "LoadFile": False, "StopJog": False, "GetButtonState": False,
    "Message": False,
    # Mach3 Reset (E-Stop) durumundayken SetOEMDRO SESSIZCE calismaz;
    # sifirlamanin gercekten olup olmadigini anlamak icin gerekir.
    "IsEstop": False,
}


class Mach3Hata(Exception):
    pass


# ----------------------------------------------------------------------
# Mach3 gercekten acik mi?
# ----------------------------------------------------------------------
def _surec_adi(pid):
    """PID -> EXE dosya adi (or. 'mach3.exe'); bulunamazsa ''."""
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))   # QUERY_LIMITED_INFO
        if not h:
            return ""
        try:
            boyut = ctypes.c_ulong(1024)
            tampon = ctypes.create_unicode_buffer(1024)
            if k32.QueryFullProcessImageNameW(h, 0, tampon,
                                              ctypes.byref(boyut)):
                return os.path.basename(tampon.value)
        finally:
            k32.CloseHandle(h)
    except Exception:                                 # noqa: BLE001
        return ""
    return ""


def _pencere_ara(parca="mach3", surec_adi="mach3.exe", pid=None):
    """Basliginda `parca` gecen pencerenin basligini doner.

    DIKKAT: yalnizca baslik esletmek yanilticidir - "Mach3 ... - Visual
    Studio Code" gibi ALAKASIZ pencereler de esler ve "Mach3 calisiyor"
    sanilir. Bu yuzden pencerenin sahibi surecin adi da dogrulanir.

    `pid` verilirse YALNIZCA o surecin penceresi kabul edilir. Bu, yeni
    baslatilan Mach3'u beklerken sart: aksi halde eskiden kalan baska bir
    Mach3 ornegi "acildi" sanilir ve onun tukenmis fabrikasina baglanmaya
    calisilir.
    """
    if platform.system() != "Windows":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        bulunan = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def _her(hwnd, _):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                tampon = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, tampon, n + 1)
                if parca.lower() in tampon.value.lower():
                    sahip = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(sahip))
                    uygun = (pid is None or sahip.value == int(pid))
                    if uygun and (not surec_adi or
                                  _surec_adi(sahip.value).lower()
                                  == surec_adi.lower()):
                        bulunan.append(tampon.value)
                        return False
            return True

        user32.EnumWindows(_her, 0)
        return bulunan[0] if bulunan else None
    except Exception:                                 # noqa: BLE001
        return None


def _surec_ara(ad="mach3.exe"):
    """Calisan surecler arasinda `ad` varsa satirini doner."""
    if platform.system() != "Windows":
        return None
    import subprocess
    try:
        cikti = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"], capture_output=True,
            text=True, timeout=15,
            creationflags=0x08000000).stdout          # CREATE_NO_WINDOW
    except Exception:                                 # noqa: BLE001
        return None
    for satir in (cikti or "").splitlines():
        if ad.lower() in satir.lower():
            return satir.split(",")[0].strip('" ')
    return None


def mach3_calisiyor():
    """Mach3 acik mi? Aciksa tanimlayici bir metin, degilse None.

    Once pencere basligina, sonra surec adina bakar (biri yeterli).
    """
    return _pencere_ara("mach3") or _surec_ara("mach3.exe")


# Ust seviye COM nesnesi (Mach4.Document) yalnizca bir "kapi"dir;
# GetOEMDRO / Code gibi betik metotlari ondan alinan AYRI bir nesnededir
# (Mach3'te GetScriptDispatch, dispid 3). Bilinen erisimci adlari:
BETIK_ADLARI = ("GetScriptDispatch", "GetScript", "ScriptDispatch",
                "GetDispatch", "Script", "Application", "Document")


# ----------------------------------------------------------------------
# Dusuk seviye COM erisimi
# ----------------------------------------------------------------------
# NEDEN: Mach3'un COM nesneleri tip kutuphanesi (TypeLib) KAYITLI DEGIL ve
# IDispatch::GetTypeInfoCount 0 doner. pywin32'nin dinamik sarmalayicisi bu
# durumda uye adlarini cozemez: `hasattr(o, "GetOEMDRO")` False verir,
# `o.GetScriptDispatch()` "Member not found" atar, `o.IsMoving()` ise uyeyi
# ozellik sanip "'int' object is not callable" hatasi verir.
# GetIDsOfNames + Invoke ile dogrudan cagirmak bunlarin hepsini asar.
def _ole(nesne):
    """CDispatch ya da PyIDispatch farketmeksizin ham IDispatch dondurur."""
    return getattr(nesne, "_oleobj_", nesne)


def _com_mu(nesne):
    """Gercek bir COM nesnesi mi (yoksa sahte/duck-typed bir nesne mi)?"""
    return hasattr(_ole(nesne), "GetIDsOfNames")


def _dispid(nesne, ad):
    """Uyenin dispid'i; yoksa None."""
    try:
        return _ole(nesne).GetIDsOfNames(ad)
    except Exception:                                 # noqa: BLE001
        return None


def uye_var(nesne, ad):
    """`hasattr` yerine kullanilir (yukaridaki nedenle).

    COM olmayan nesnelerde (testler, simulasyon) `hasattr`a duser.
    """
    if _com_mu(nesne):
        return _dispid(nesne, ad) is not None
    try:
        return hasattr(nesne, ad)
    except Exception:                                 # noqa: BLE001
        return False


def betik_nesnesi(nesne):
    """Betik metotlarini tasiyan nesneyi bulur.

    Doner: (ham_IDispatch, kullanilan_ad). Ust nesnede zaten GetOEMDRO varsa
    dogrudan onu doner.
    """
    if nesne is None:
        return None, ""

    # COM olmayan nesneler (testler, sahte nesneler): eski getattr yolu.
    if not _com_mu(nesne):
        if uye_var(nesne, "GetOEMDRO"):
            return nesne, ""
        for ad in BETIK_ADLARI:
            try:
                uye = getattr(nesne, ad)
                alt = uye() if callable(uye) else uye
            except Exception:                         # noqa: BLE001
                continue
            if alt is None or alt is nesne:
                continue
            if uye_var(alt, "GetOEMDRO"):
                return alt, ad
        return nesne, ""

    import pythoncom
    ole = _ole(nesne)
    if _dispid(ole, "GetOEMDRO") is not None:
        return ole, ""
    for ad in BETIK_ADLARI:
        did = _dispid(ole, ad)
        if did is None:
            continue
        for bayrak in (pythoncom.DISPATCH_METHOD,
                       pythoncom.DISPATCH_PROPERTYGET):
            try:
                alt = ole.Invoke(did, 0, bayrak, True)
            except Exception:                         # noqa: BLE001
                continue
            if alt is None:
                continue
            try:
                alt_ole = alt.QueryInterface(pythoncom.IID_IDispatch)
            except Exception:                         # noqa: BLE001
                continue
            if _dispid(alt_ole, "GetOEMDRO") is not None:
                return alt_ole, ad
    return ole, ""


class _Mach3Nesne:
    """Betik nesnesini dispid uzerinden cagiran ince sarmalayici.

    Disaridan bakildiginda sanki `o.GetOEMDRO(800)` diye normal bir COM
    nesnesiymis gibi kullanilir; iceride her cagri GetIDsOfNames ile bulunan
    dispid uzerinden Invoke edilir.
    """

    def __init__(self, ole):
        self._ole = ole
        self.dispid = {}
        for ad in COM_METOTLAR:
            did = _dispid(ole, ad)
            if did is not None:
                self.dispid[ad] = did

    def var(self, ad):
        return ad in self.dispid

    def _c(self, ad, *args):
        import pythoncom
        did = self.dispid.get(ad)
        if did is None:
            raise Mach3Hata(f"Mach3 arayuzunde '{ad}' metodu yok")
        return self._ole.Invoke(did, 0, pythoncom.DISPATCH_METHOD, True, *args)

    # Mach3 betik API'si
    def GetOEMDRO(self, kod):
        return self._c("GetOEMDRO", int(kod))

    def SetOEMDRO(self, kod, deger):
        return self._c("SetOEMDRO", int(kod), float(deger))

    def GetOEMLED(self, kod):
        return self._c("GetOEMLED", int(kod))

    def DoOEMButton(self, kod):
        return self._c("DoOEMButton", int(kod))

    def Code(self, satir):
        return self._c("Code", str(satir))

    def LoadFile(self, yol):
        return self._c("LoadFile", str(yol))

    def IsMoving(self):
        return self._c("IsMoving")

    def StopJog(self):
        return self._c("StopJog")

    def Message(self, metin):
        return self._c("Message", str(metin))

    def IsEstop(self):
        return self._c("IsEstop")


# ----------------------------------------------------------------------
# Mach3'u COM otomasyonu icin baslatma
# ----------------------------------------------------------------------
# ONEMLI (bu projede uzun sure "baglanti yok" sorununun asil sebebi):
#
#   1. Mach3 COM sinif fabrikasini YALNIZCA `/Automation` (ya da COM'un
#      kendi ekledigi `-Embedding`) anahtariyla baslatildiginda kaydeder.
#      Kullanicinin elle (cift tiklayarak) actigi Mach3'e HICBIR sekilde
#      baglanilamaz: Running Object Table'da kaydi yoktur, sinif fabrikasi
#      da yoktur. Bu yuzden "Mach3 acik, ama baglanti kurulamiyor".
#
#   2. Mach3 lisans dosyasini (Mach1Lic.dat) CALISMA DIZININDEN okur. COM
#      sunucuyu kendisi baslattiginda calisma dizini C:\Windows\System32
#      olur ve Mach3 "Corrupt install, no license file found. Aborting."
#      diyerek kapanir; COM da 120 sn sonra 'Server execution failed' verir.
#      Bu yuzden Mach3'u BIZ, calisma dizini kurulum klasoru olacak sekilde
#      baslatiriz.
#
#   3. Profil verilmezse Mach3 profil secim penceresinde bekler ve yine
#      sinif fabrikasini kaydetmez; bu yuzden `/p <profil>` gecilir.
#
#   4. Kaydedilen sinif fabrikasi TEK KULLANIMLIKTIR (REGCLS_SINGLEUSE):
#      ilk istemci baglandiktan sonra iptal olur. Yani her Mach3 acilisina
#      yalnizca BIR istemci baglanabilir; yeniden baglanmak icin Mach3'un
#      yeniden baslatilmasi gerekir.
def _kayitli_exe(progid):
    """ProgID -> LocalServer32 EXE yolu (32-bit kayit defteri gorunumu)."""
    if platform.system() != "Windows":
        return ""
    try:
        import winreg
    except ImportError:
        return ""
    for gorunum in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        erisim = winreg.KEY_READ | gorunum
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                rf"SOFTWARE\Classes\{progid}\CLSID",
                                0, erisim) as k:
                clsid = winreg.QueryValue(k, "")
            with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    rf"SOFTWARE\Classes\CLSID\{clsid}\LocalServer32",
                    0, erisim) as k:
                komut = winreg.QueryValue(k, "")
        except OSError:
            continue
        yol = (komut or "").strip()
        if yol.startswith('"'):
            yol = yol[1:].split('"', 1)[0]
        else:
            yol = yol.split(" /")[0].split(" -")[0].strip()
        if yol and os.path.isfile(yol):
            return yol
    return ""


def _sinif_yok(e):
    """Hata 'ProgID kayitli degil' (0x800401F3) mi?"""
    m = str(e)
    return "invalid class string" in m.lower() or "-2147221005" in m


def _kayit_yok_mesaji(progid):
    """Mach3'un COM kaydi bulunamadiginda gosterilecek aciklama."""
    if struct.calcsize("P") * 8 == 64:
        return ("Mach3'un COM kaydi bulunamadi. En olasi sebep 32/64 bit "
                "uyusmazligi: Mach3 32-bit'tir, kullandiginiz Python 64-bit. "
                "32-bit Python kurun. Ayrinti: python main.py --tani")
    return (f"Mach3'un COM kaydi bulunamadi (ProgID '{progid}'). Mach3'u bir "
            "kez yonetici olarak calistirmak kaydi tazeler. "
            "Ayrinti: python main.py --tani")


def mach3_exe_bul(ayar=None):
    """Mach3.exe'nin tam yolu: ayar -> kayit defteri -> yaygin konumlar."""
    ayar = ayar or {}
    elle = (ayar.get("mach3_exe") or "").strip()
    if elle:
        return elle if os.path.isfile(elle) else ""
    yol = _kayitli_exe(ayar.get("progid") or "Mach4.Document")
    if yol:
        return yol
    for aday in (r"C:\Mach3\Mach3.exe", r"C:\Program Files\Mach3\Mach3.exe",
                 r"C:\Program Files (x86)\Mach3\Mach3.exe"):
        if os.path.isfile(aday):
            return aday
    return ""


def mach3_otomasyon_baslat(exe, profil="", bekle=90.0, progid="Mach4.Document",
                           gunluk=None):
    """Mach3'u COM otomasyonuna hazir sekilde baslatir ve nesneyi dondurur.

    Calisma dizini EXE'nin klasoru yapilir (lisans dosyasi icin sart).

    Acilis beklenirken Dispatch ile YOKLAMA YAPILMAZ: Mach3 henuz sinif
    fabrikasini kaydetmemisken Dispatch, COM'un ikinci bir Mach3 kopyasini
    (yanlis calisma dizininde) baslatmasina yol acar ve 120 saniyelik COM
    zaman asimina takilir. Bunun yerine Mach3'un penceresi gorunene kadar
    beklenir, sonra tek sefer baglanilir.
    """
    import win32com.client
    if not exe or not os.path.isfile(exe):
        raise Mach3Hata(
            "Mach3.exe bulunamadi. Ayarlar > Baglanti > 'mach3_exe' alanina "
            "tam yolu yazin (or. C:\\Mach3\\Mach3.exe).")
    komut = [exe]
    if profil:
        komut += ["/p", profil]
    komut.append("/Automation")
    if gunluk is not None:
        gunluk.append("baslatiliyor: " + " ".join(komut))
    try:
        surec = subprocess.Popen(komut, cwd=os.path.dirname(exe) or None)
    except OSError as e:
        raise Mach3Hata(f"Mach3 baslatilamadi: {e}")

    bitis = time.time() + bekle
    acildi = False
    while time.time() < bitis:
        if surec.poll() is not None:
            # Baslattigimiz kopya cikti. Iki ayri sebep, iki ayri cozum:
            if mach3_calisiyor():
                raise Mach3Hata(
                    "Zaten calisan baska bir Mach3 ornegi var; Mach3 tek "
                    "ornek oldugu icin yeni kopya hemen kapandi. Calisan "
                    "Mach3'u tamamen kapatip tekrar deneyin.")
            raise Mach3Hata(
                "Mach3 acilir acilmaz kapandi. En olasi sebep lisans "
                "dosyasinin (Mach1Lic.dat) bulunamamasi ya da profil "
                f"'{profil or '(yok)'}' adinin yanlis olmasidir. Mach3'u "
                "elle acip calistigini dogrulayin.")
        # YALNIZCA kendi baslattigimiz surecin penceresini kabul et; yoksa
        # eskiden kalan bir Mach3'un tukenmis fabrikasina baglanmaya
        # calisiriz.
        if _pencere_ara("mach3", "mach3.exe", pid=surec.pid):
            acildi = True
            break
        time.sleep(0.5)
    if not acildi:
        raise Mach3Hata(
            f"Mach3 {bekle:.0f} sn icinde acilmadi. Profil secimi ya da bir "
            "uyari penceresi bekliyor olabilir.")

    time.sleep(1.5)                       # sinif fabrikasi kaydi icin pay
    son = None
    for _ in range(3):
        try:
            return win32com.client.Dispatch(progid)
        except Exception as e:                        # noqa: BLE001
            son = e
            time.sleep(1.0)
    raise Mach3Hata(
        "Mach3 acildi ama COM arayuzune baglanilamadi. Mach3'un bu acilisina "
        f"baska bir program baglanmis olabilir. [{son}]")


def _kelimeler(satir):
    s = re.sub(r"\(.*?\)", "", satir).split(";", 1)[0]
    return {h.upper(): float(v) for h, v in _WORD_RE.findall(s)}


# ======================================================================
# Ortak arayuz
# ======================================================================
class TemelBaglanti:
    tur = "temel"

    def __init__(self, ayar=None):
        self.ayar = ayar or {}
        self.bagli = False
        self.son_hata = ""
        self._yetenek = {}

    # -- yasam dongusu ---------------------------------------------------
    def ac(self):
        raise NotImplementedError

    def kapat(self):
        self.bagli = False

    def yetenekler(self):
        return dict(self._yetenek)

    def destekler(self, ad):
        return bool(self._yetenek.get(ad))

    # -- temel islemler --------------------------------------------------
    def dro(self, kod):
        raise NotImplementedError

    def dro_yaz(self, kod, deger):
        raise NotImplementedError

    def led(self, kod):
        raise NotImplementedError

    def dugme(self, kod):
        raise NotImplementedError

    def kod(self, satir):
        """MDI satiri gonderir (Mach3 `Code`)."""
        raise NotImplementedError

    def dosya_yukle(self, yol):
        raise NotImplementedError

    def hareket_ediyor(self):
        raise NotImplementedError

    def jog_durdur(self):
        raise NotImplementedError

    def estop(self):
        """Mach3 Reset/E-Stop durumunda mi? Bilinmiyorsa None."""
        return None

    def referansli(self):
        """Makine koordinati tezgahin sifirina gore mi?

        Gercek Mach3'te referans switch'i olup olmadigini disaridan
        guvenilir sekilde anlayamayiz; bu yuzden False doneriz ve operator
        tezgah referansini kendisi alir. Simulasyonda makine koordinati
        tanim geregi tezgah sifirindan baslar.
        """
        return False


# ======================================================================
# COM (gercek Mach3)
# ======================================================================
class _Isci(threading.Thread):
    """COM nesnesinin sahibi olan tek is parcacigi. Tum cagrilar buradan."""

    def __init__(self, kur):
        super().__init__(daemon=True, name="mach3-com")
        self._kur = kur                 # () -> COM nesnesi
        self._kuyruk = queue.Queue()
        self._dur = threading.Event()
        self.nesne = None
        self.hazir = threading.Event()
        self.hata = ""

    def run(self):
        import pythoncom
        pythoncom.CoInitialize()
        try:
            self.nesne = self._kur()
        except Exception as e:                        # noqa: BLE001
            # Mach3Hata zaten kullaniciya donuk bir aciklama; tur adiyla
            # tekrar onune eklemeyiz.
            self.hata = (str(e) if isinstance(e, Mach3Hata)
                         else f"{type(e).__name__}: {e}")
        finally:
            self.hazir.set()

        while not self._dur.is_set():
            try:
                is_ = self._kuyruk.get(timeout=0.2)
            except queue.Empty:
                continue
            fn, args, kutu, olay = is_
            try:
                kutu.append(fn(self.nesne, *args))
            except Exception as e:                    # noqa: BLE001
                kutu.append(Mach3Hata(f"{type(e).__name__}: {e}"))
            finally:
                olay.set()
        try:
            pythoncom.CoUninitialize()
        except Exception:                             # noqa: BLE001
            pass

    def cagir(self, fn, *args, zaman_asimi=5.0):
        if not self.is_alive():
            raise Mach3Hata("COM is parcacigi calismiyor")
        kutu, olay = [], threading.Event()
        self._kuyruk.put((fn, args, kutu, olay))
        if not olay.wait(zaman_asimi):
            raise Mach3Hata("Mach3 yanit vermedi (zaman asimi)")
        sonuc = kutu[0]
        if isinstance(sonuc, Exception):
            raise sonuc
        return sonuc

    def kapat(self):
        self._dur.set()


class COMBaglanti(TemelBaglanti):
    tur = "com"

    def __init__(self, ayar=None):
        super().__init__(ayar)
        self.progid = (self.ayar.get("progid") or "Mach4.Document")
        self.yedekler = list(self.ayar.get("yedek_progid") or [])
        # Elle acilan Mach3'e baglanmak MUMKUN DEGIL (yukaridaki uzun nota
        # bakin), bu yuzden varsayilan olarak Mach3'u biz baslatiriz.
        self.mach3_baslat = bool(self.ayar.get("mach3_baslat", True))
        self.profil = (self.ayar.get("profil") or "").strip()
        self.betik_yolu = ""
        self._isci = None

    # -- yasam dongusu ---------------------------------------------------
    def ac(self):
        if platform.system() != "Windows":
            raise Mach3Hata("COM baglantisi yalnizca Windows'ta calisir "
                            "(simulasyon modunu kullanin).")
        try:
            import win32com.client  # noqa: F401
        except ImportError:
            raise Mach3Hata("pywin32 kurulu degil: pip install pywin32")

        adaylar = [self.progid] + self.yedekler
        hatalar = []
        self.betik_yolu = ""

        def _betik(o):
            """Betik metotlari ayri bir nesnededir; ona gecip sarmala."""
            alt, ad = betik_nesnesi(o)
            if ad:
                self.betik_yolu = ad
                hatalar.append(f"betik nesnesi: {ad}()")
            sarmal = _Mach3Nesne(alt)
            if not sarmal.var("GetOEMDRO"):
                raise Mach3Hata(
                    "Mach3'un COM nesnesine ulasildi ama betik metotlari "
                    "(GetOEMDRO) bulunamadi. Ayrinti: python main.py --tani")
            return sarmal

        def _kur():
            import win32com.client
            # ONEMLI SIRA: Dispatch YALNIZCA Mach3 calisiyorken denenir.
            # Mach3 kapaliyken Dispatch, COM'un Mach3'u yanlis calisma
            # dizininde baslatmasina yol acar; Mach3 lisans dosyasini
            # bulamayip kapanir ve cagri 120 sn askida kalir.
            if mach3_calisiyor():
                # Yedek ProgID'ler zaten kayitli olmayabilir; "kayit yok"
                # sonucuna ancak ADAYLARIN HEPSI oyle derse variriz.
                hepsi_kayit_yok = True
                for ad in adaylar:
                    try:
                        return _betik(win32com.client.Dispatch(ad))
                    except Mach3Hata:
                        raise
                    except Exception as e:            # noqa: BLE001
                        hatalar.append(f"{ad} (Dispatch): {e}")
                        if not _sinif_yok(e):
                            hepsi_kayit_yok = False
                if hepsi_kayit_yok:
                    raise Mach3Hata(_kayit_yok_mesaji(self.progid))
                raise Mach3Hata(
                    "Mach3 acik ama COM arayuzu kullanilamiyor. Mach3 "
                    "otomasyon arayuzunu YALNIZCA '/Automation' anahtariyla "
                    "baslatildiginda acar; elle (cift tiklayarak) acilan "
                    "Mach3'e baglanilamaz. Ayrica her Mach3 acilisina "
                    "yalnizca bir istemci baglanabilir. COZUM: Mach3'u "
                    "tamamen kapatin ve bu programi yeniden baslatin; "
                    "Mach3'u kendisi dogru sekilde acacaktir.")

            # Mach3 kapali: izin varsa BIZ baslatalim (dogru calisma dizini,
            # profil ve /Automation ile).
            if not self.mach3_baslat:
                raise Mach3Hata(
                    "Mach3 calismiyor ve otomatik baslatma kapali. "
                    "Ayarlar > Baglanti > 'mach3_baslat' secenegini acin "
                    "(ya da --mach3-baslat ile calistirin).")
            exe = mach3_exe_bul(self.ayar)
            if not exe:
                raise Mach3Hata(
                    "Mach3.exe bulunamadi (kayit defterinde de yok). "
                    "Ayarlar > Baglanti > 'mach3_exe' alanina tam yolu "
                    "yazin, or. C:\\Mach3\\Mach3.exe")
            hatalar.append(f"Mach3 baslatiliyor: {exe}")
            return _betik(mach3_otomasyon_baslat(
                exe, self.profil, progid=self.progid, gunluk=hatalar))

        # Mach3'u kendimiz baslatacaksak acilis uzun surer (profil yukleme,
        # ekran cizimi); 15 sn yetmiyordu.
        bekle = float(self.ayar.get("acilis_zaman_asimi_sn", 120))
        self._isci = _Isci(_kur)
        self._isci.start()
        self._isci.hazir.wait(bekle)
        if self._isci.nesne is None:
            if not self._isci.hazir.is_set():
                self.son_hata = (f"Mach3 {bekle:.0f} sn icinde hazir olmadi "
                                 "(profil secimi ya da bir uyari penceresi "
                                 "bekliyor olabilir)")
            else:
                self.son_hata = (self._isci.hata or "; ".join(hatalar)
                                 or "Mach3 COM nesnesi olusturulamadi")
            self._isci.kapat()
            self._isci = None
            raise Mach3Hata(f"Mach3'e baglanilamadi: {self.son_hata}")

        # DIKKAT: `hasattr` bu nesnelerde calismaz (tip bilgisi yok);
        # yetenek yoklamasi dispid uzerinden yapilir.
        self._yetenek = {ad: self._isci.cagir(
            lambda o, a=ad: o.var(a)) for ad in COM_METOTLAR}
        eksik = [a for a, zorunlu in COM_METOTLAR.items()
                 if zorunlu and not self._yetenek.get(a)]
        if eksik:
            self.kapat()
            raise Mach3Hata("Mach3 arayuzunde zorunlu metotlar yok: "
                            + ", ".join(eksik))
        self.bagli = True
        return True

    def kapat(self):
        self.bagli = False
        if self._isci:
            self._isci.kapat()
            self._isci = None

    # -- islemler --------------------------------------------------------
    def dro(self, kod):
        return float(self._isci.cagir(lambda o, k: o.GetOEMDRO(int(k)), kod))

    def dro_yaz(self, kod, deger):
        self._isci.cagir(lambda o, k, d: o.SetOEMDRO(int(k), float(d)),
                         kod, deger)

    def led(self, kod):
        if not self.destekler("GetOEMLED"):
            return None
        return bool(self._isci.cagir(lambda o, k: o.GetOEMLED(int(k)), kod))

    def dugme(self, kod):
        self._isci.cagir(lambda o, k: o.DoOEMButton(int(k)), kod)

    def kod(self, satir):
        self._isci.cagir(lambda o, s: o.Code(s), str(satir))

    def dosya_yukle(self, yol):
        if not self.destekler("LoadFile"):
            raise Mach3Hata(
                "Bu Mach3 surumunde LoadFile yok; dosyayi Mach3'te elle acin.")
        self._isci.cagir(lambda o, y: o.LoadFile(y), str(yol), zaman_asimi=30)

    def hareket_ediyor(self):
        if not self.destekler("IsMoving"):
            return None
        return bool(self._isci.cagir(lambda o: o.IsMoving()))

    def jog_durdur(self):
        if self.destekler("StopJog"):
            self._isci.cagir(lambda o: o.StopJog())
            return True
        return False

    def estop(self):
        if not self.destekler("IsEstop"):
            return None
        try:
            return bool(self._isci.cagir(lambda o: o.IsEstop()))
        except Exception:                             # noqa: BLE001
            return None


# ======================================================================
# Simulasyon (Mach3 olmadan)
# ======================================================================
class SimulasyonBaglanti(TemelBaglanti):
    """Basit ama gercekci bir sanal tezgah.

    * G0/G1/G2/G3 hareketlerini besleme hizinda ilerletir (50 Hz tik).
    * G90/G91, G4 (bekleme), M3/M5 ve F/S modal degerlerini takip eder.
    * Feed hold / stop / jog durdur davranislari gercek makinedeki gibidir.
    """

    tur = "simulasyon"

    def __init__(self, ayar=None, makine=None):
        super().__init__(ayar)
        mak = makine or {}
        self.hizli = mak.get("hizli_hiz", {"X": 8000.0, "Y": 8000.0, "Z": 3000.0})
        self.konum = {"X": 0.0, "Y": 0.0, "Z": 0.0, "A": 0.0}     # makine
        self.ofset = {"X": 0.0, "Y": 0.0, "Z": 0.0, "A": 0.0}     # is = makine - ofset
        self.feed = 1000.0
        self.devir = 0.0
        self.spindle = False
        self.mutlak = True
        self.duraklatildi = False
        self._kuyruk = []          # bekleyen hareketler
        self._kilit = threading.RLock()
        self._dur = threading.Event()
        self._is = None
        self.yuklu_dosya = ""
        self._program_satirlari = []
        self._bekle_bitis = 0.0
        self._yetenek = {ad: True for ad in COM_METOTLAR}

    # -- yasam dongusu ---------------------------------------------------
    def ac(self):
        self.bagli = True
        self._dur.clear()
        self._is = threading.Thread(target=self._dongu, daemon=True,
                                    name="mach3-sim")
        self._is.start()
        return True

    def kapat(self):
        self.bagli = False
        self._dur.set()

    # -- hareket motoru --------------------------------------------------
    def _dongu(self):
        son = time.time()
        while not self._dur.is_set():
            time.sleep(0.02)
            simdi = time.time()
            dt, son = simdi - son, simdi
            with self._kilit:
                if self.duraklatildi or not self._kuyruk:
                    continue
                if simdi < self._bekle_bitis:
                    continue
                hedef, feed = self._kuyruk[0]
                kalan = {e: hedef[e] - self.konum[e] for e in hedef}
                uzak = math.sqrt(sum(v * v for v in kalan.values()))
                if uzak < 1e-6:
                    self._kuyruk.pop(0)
                    continue
                adim = max(feed, 1.0) / 60.0 * dt          # mm/dk -> mm/s
                if adim >= uzak:
                    for e in hedef:
                        self.konum[e] = hedef[e]
                    self._kuyruk.pop(0)
                else:
                    k = adim / uzak
                    for e in hedef:
                        self.konum[e] += kalan[e] * k

    def _hedef_ekle(self, hedef, feed):
        with self._kilit:
            self._kuyruk.append((hedef, feed))

    # -- islemler --------------------------------------------------------
    def _eksen_kod(self, kod):
        """DRO kodundan (eksen, makine_mi) cozer."""
        harita = {800: ("X", False), 801: ("Y", False), 802: ("Z", False),
                  803: ("A", False), 83: ("X", True), 84: ("Y", True),
                  85: ("Z", True), 86: ("A", True)}
        return harita.get(int(kod))

    def dro(self, kod):
        kod = int(kod)
        e = self._eksen_kod(kod)
        if e:
            eksen, makine = e
            with self._kilit:
                return round(self.konum[eksen] -
                             (0.0 if makine else self.ofset[eksen]), 4)
        if kod == 818:
            return self.feed
        if kod in (821, 822):
            return 100.0
        if kod == 812:
            return self.devir
        if kod == 39:
            return self.devir if self.spindle else 0.0
        return 0.0

    def dro_yaz(self, kod, deger):
        e = self._eksen_kod(int(kod))
        if e:
            eksen, makine = e
            with self._kilit:
                if makine:
                    self.konum[eksen] = float(deger)
                else:
                    # is koordinati `deger` olacak sekilde ofseti kaydir
                    self.ofset[eksen] = self.konum[eksen] - float(deger)
            return
        if int(kod) == 818:
            self.feed = float(deger)
        elif int(kod) == 812:
            self.devir = float(deger)

    def led(self, kod):
        return False

    def dugme(self, kod):
        kod = int(kod)
        if kod == 1001:                     # feed hold
            self.duraklatildi = True
        elif kod == 1000:                   # cycle start
            self.duraklatildi = False
            self._programi_kuyrukla()       # yuklu dosya varsa calistir
        elif kod == 1002:                   # stop
            self.duraklatildi = False
            with self._kilit:
                self._kuyruk.clear()
        elif kod in (131, 132, 133, 134):   # zero X/Y/Z/A (is sifiri)
            eksen = {131: "X", 132: "Y", 133: "Z", 134: "A"}[kod]
            with self._kilit:
                self.ofset[eksen] = self.konum[eksen]
        elif kod in (22, 23, 24, 25, 1024):  # Ref (MAKINE sifiri)
            # Home switch'i olmayan kurulum taklit edilir: eksen hareket
            # etmez, makine koordinati bulunulan noktada sifirlanir. Is
            # ofseti makineye gore sakli oldugu icin is DRO'su da kayar -
            # Mach3'teki davranisin aynisi.
            eksenler = ({22: ["X"], 23: ["Y"], 24: ["Z"], 25: ["A"]}
                        .get(kod, ["X", "Y", "Z", "A"]))
            with self._kilit:
                for eksen in eksenler:
                    self.ofset[eksen] -= self.konum[eksen]
                    self.konum[eksen] = 0.0

    def kod(self, satir):
        """Cok satirli MDI destegi (satir satir yorumlanir)."""
        for parca in str(satir).replace("\r", "").split("\n"):
            self._satir_isle(parca)

    def _satir_isle(self, satir):
        w = _kelimeler(satir)
        if not w:
            return
        if "G" in w:
            g = w["G"]
            if g == 90:
                self.mutlak = True
            elif g == 91:
                self.mutlak = False
            elif g == 4:
                sn = w.get("P", 0.0)
                with self._kilit:
                    self._bekle_bitis = time.time() + (
                        sn if sn < 100 else sn / 1000.0)
                return
        if "M" in w:
            m = int(w["M"])
            if m in (3, 4):
                self.spindle = True
                if "S" in w:
                    self.devir = w["S"]
            elif m == 5:
                self.spindle = False
        if "F" in w:
            self.feed = w["F"]
        if "S" in w:
            self.devir = w["S"]

        eksenli = [e for e in ("X", "Y", "Z", "A") if e in w]
        if not eksenli:
            return
        g = w.get("G")
        hizli = (g == 0)
        with self._kilit:
            hedef = dict(self.konum)
            for e in eksenli:
                if self.mutlak:
                    hedef[e] = w[e] + self.ofset[e]
                else:
                    hedef[e] = self.konum[e] + w[e]
        feed = (min(self.hizli.get(e, 6000.0) for e in eksenli)
                if hizli else self.feed)
        self._hedef_ekle(hedef, feed)

    def dosya_yukle(self, yol):
        """Dosyayi yukler ve satirlarini cycle start icin hazir tutar.

        Onceden yalnizca dosya ADI saklaniyordu; bu yuzden simulasyonda
        "program calistir" hicbir sey hareket ettirmiyordu ve sure/ilerleme
        gibi olculer yalnizca gercek makinede denenebiliyordu. Artik
        simulasyon programi gercekten isliyor.
        """
        self.yuklu_dosya = str(yol)
        self._program_satirlari = []
        try:
            with open(yol, "r", encoding="utf-8", errors="replace") as f:
                self._program_satirlari = [s for s in f.read().splitlines()
                                           if s.strip()]
        except OSError:
            pass

    def _programi_kuyrukla(self):
        satirlar = getattr(self, "_program_satirlari", None)
        if not satirlar:
            return False
        for s in satirlar:
            self._satir_isle(s)
        self._program_satirlari = []      # tek seferlik
        return True

    def hareket_ediyor(self):
        with self._kilit:
            return bool(self._kuyruk) and not self.duraklatildi

    def jog_durdur(self):
        with self._kilit:
            self._kuyruk.clear()
        return True

    def estop(self):
        return False                      # sanal tezgah her zaman hazir

    def referansli(self):
        # Sanal tezgahin makine koordinati 0'dan baslar ve tezgah sifiriyla
        # ayni yerdedir; ayrica referans almaya gerek yoktur.
        return True


# ======================================================================
# Fabrika
# ======================================================================
def baglanti_olustur(ayarlar):
    """Ayarlara gore uygun arka ucu secer."""
    b_ayar = ayarlar["baglanti"]
    tur = (b_ayar.get("tur") or "otomatik").lower()
    if tur == "otomatik":
        tur = "com" if platform.system() == "Windows" else "simulasyon"
    if tur == "com":
        return COMBaglanti(b_ayar)
    return SimulasyonBaglanti(b_ayar, ayarlar["makine"])
