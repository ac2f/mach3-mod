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
import platform
import queue
import re
import threading
import time

_WORD_RE = re.compile(r"([A-Za-z])\s*([+-]?\d*\.?\d+)")

# Yoklanacak COM metotlari (ad -> zorunlu mu)
COM_METOTLAR = {
    "GetOEMDRO": True, "SetOEMDRO": True, "Code": True,
    "DoOEMButton": True, "GetOEMLED": False, "IsMoving": False,
    "LoadFile": False, "StopJog": False, "GetButtonState": False,
    "Message": False,
}


class Mach3Hata(Exception):
    pass


# ----------------------------------------------------------------------
# Mach3 gercekten acik mi?
# ----------------------------------------------------------------------
def _pencere_ara(parca="mach3"):
    """Basliginda `parca` gecen bir pencere varsa basligini doner."""
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
            self.hata = f"{type(e).__name__}: {e}"
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
        # False (varsayilan): yalnizca ZATEN ACIK olan Mach3'e baglan.
        self.mach3_baslat = bool(self.ayar.get("mach3_baslat", False))
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

        def _kur():
            import win32com.client
            # 1) Calisan ornege baglanmayi dene (ROT uzerinden).
            #    NOT: Cogu EXE tabanli COM sunucusu kendini Running Object
            #    Table'a KAYDETMEZ; bu yuzden GetActiveObject, Mach3 acik
            #    olsa bile basarisiz olabilir. Bu bir hata degil, normaldir -
            #    asagida sureç tespitiyle telafi edilir.
            for ad in adaylar:
                try:
                    return win32com.client.GetActiveObject(ad)
                except Exception as e:                # noqa: BLE001
                    hatalar.append(f"{ad} (ROT): {e}")

            # 2) Mach3 gercekten calisiyor mu? Calisiyorsa Dispatch guvenlidir:
            #    Mach3 tek-ornek (single instance) bir sunucudur, yeni kopya
            #    acmaz, var olana baglanir. Calismiyorsa YENI Mach3 baslatma
            #    riski vardir - bu yuzden kullanici acikca izin vermedikce
            #    denemeyiz.
            acik = mach3_calisiyor()
            if not acik and not self.mach3_baslat:
                raise Mach3Hata(
                    "Mach3 calismiyor gibi gorunuyor (ne penceresi ne de "
                    "mach3.exe sureci bulundu). Once Mach3'u acin. "
                    "Mach3 acik oldugu halde bu mesaji aliyorsaniz "
                    "'python main.py --tani' calistirin.")
            if acik:
                hatalar.append(f"calisan Mach3 bulundu: {acik}")
            son = None
            for ad in adaylar:
                try:
                    return win32com.client.Dispatch(ad)
                except Exception as e:                # noqa: BLE001
                    hatalar.append(f"{ad} (Dispatch): {e}")
                    son = e
            raise Mach3Hata(
                "COM nesnesi olusturulamadi. ProgID yanlis olabilir "
                "(Ayarlar > Baglanti) veya Mach3 ile bu program farkli "
                "yetkilerde calisiyordur. Ayrinti icin: python main.py --tani"
                + (f" [{son}]" if son else ""))

        self._isci = _Isci(_kur)
        self._isci.start()
        self._isci.hazir.wait(15)
        if self._isci.nesne is None:
            self.son_hata = (self._isci.hata or "; ".join(hatalar)
                             or "Mach3 COM nesnesi olusturulamadi")
            self._isci.kapat()
            self._isci = None
            raise Mach3Hata(
                f"Mach3'e baglanilamadi. Mach3 acik mi? ({self.son_hata})")

        self._yetenek = {ad: self._isci.cagir(
            lambda o, a=ad: hasattr(o, a)) for ad in COM_METOTLAR}
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
        elif kod == 1002:                   # stop
            self.duraklatildi = False
            with self._kilit:
                self._kuyruk.clear()
        elif kod in (131, 132, 133, 134):   # zero X/Y/Z/A
            eksen = {131: "X", 132: "Y", 133: "Z", 134: "A"}[kod]
            with self._kilit:
                self.ofset[eksen] = self.konum[eksen]

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
        self.yuklu_dosya = str(yol)

    def hareket_ediyor(self):
        with self._kilit:
            return bool(self._kuyruk) and not self.duraklatildi

    def jog_durdur(self):
        with self._kilit:
            self._kuyruk.clear()
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
