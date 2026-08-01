#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Klavye kilidi + odak bekcisi (Windows)
======================================
Cozulen problem
---------------
Mach3'te ok tuslariyla jog yaparken yanlislikla **Windows tusuna**, **Alt**'a
veya **Alt+Tab**'a basilirsa Mach3 odagi kaybeder; tusun birakildigi bilgisi
Mach3'e ULASMAZ ve eksen, odak Mach3'e geri donene kadar hareket etmeye devam
eder. Bu, elin/parmagin takima yaklastigi anda ciddi yaralanma sebebidir.

Iki katmanli cozum
------------------
1. **Kilit (dusuk seviye klavye hooku)** - `SetWindowsHookEx(WH_KEYBOARD_LL)`
   ile TUM klavye, isletim sistemine ulasmadan once yakalanir. Kilit acikken
   yalnizca `izinli` listesindeki tuslar (varsayilan: ok tuslari, PgUp/PgDn,
   Space, Esc) gecer; **Windows, Alt, Tab, Ctrl+Esc ve diger her sey yutulur.**
   Boylece jog sirasinda yanlis tusa basmak Mach3'un odagini kaybettiremez.

2. **Odak bekcisi** - buna ragmen (baska bir program kendini one atarsa)
   Mach3 odagi kaybederse, bekci bunu ~100 ms icinde fark eder, `durdur`
   geri cagrisini tetikler (feed hold + jog durdur) ve istenirse odagi
   Mach3'e geri verir.

SINIRLAR - DURUSTCE
-------------------
* **Ctrl+Alt+Del engellenemez.** Bu kombinasyonu isletim sistemi guvenli
  masaustune ayirir; hicbir uygulama yakalayamaz. Basilirsa odak gider;
  ikinci katman (odak bekcisi) devreye girer.
* Hook, uygulamanin **yonetici** yetkisiyle calismasini gerektirebilir
  (Mach3 yonetici olarak calisiyorsa bu program da oyle calismalidir).
* Windows disinda kilit **calismaz** (`destekleniyor` False doner); arayuz
  bunu acikca gosterir. Tarayici tarafindaki kilit ise her platformda calisir
  ama yalnizca tarayici penceresini korur.
* Bu bir guvenlik cihazi DEGILDIR. Donanim acil-stop butonunun yerine gecmez.
"""

import platform
import threading
import time

WINDOWS = platform.system() == "Windows"

# Sanal tus kodlari (izin listesinde isimle kullanilir)
VK = {
    "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28,
    "PRIOR": 0x21, "NEXT": 0x22, "HOME": 0x24, "END": 0x23,
    "SPACE": 0x20, "ESCAPE": 0x1B, "RETURN": 0x0D, "TAB": 0x09,
    "LWIN": 0x5B, "RWIN": 0x5C, "MENU": 0x12, "CONTROL": 0x11,
    "SHIFT": 0x10, "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73,
    "F5": 0x74, "NUMPAD4": 0x64, "NUMPAD6": 0x66, "NUMPAD8": 0x68,
    "NUMPAD2": 0x62, "NUMPAD9": 0x69, "NUMPAD3": 0x63,
}
VK_AD = {v: k for k, v in VK.items()}

# Kilit acikken hicbir kosulda gecmeyecek tuslar (izin listesine konsa bile)
ASLA_GECME = {VK["LWIN"], VK["RWIN"]}


class KlavyeKilidi:
    """Dusuk seviye klavye hooku. Windows disinda etkisiz (no-op)."""

    def __init__(self, izinli=None, olay=None, panik=None):
        self.izinli_adlar = list(izinli or ["LEFT", "RIGHT", "UP", "DOWN",
                                            "PRIOR", "NEXT", "SPACE",
                                            "ESCAPE"])
        self.olay = olay           # (tur, ad, vk) -> None  (gunluk/istatistik)
        self.panik = panik         # ESC'e basilinca cagrilir
        self.aktif = False
        self.destekleniyor = WINDOWS
        self.son_hata = ""
        self.engellenen = 0
        self.gecen = 0
        self._is = None
        self._hook = None
        self._thread_id = None
        self._dur = threading.Event()
        self._kilit = threading.Lock()

    # -- izin listesi -----------------------------------------------------
    @property
    def izinli_vk(self):
        return {VK[a.upper()] for a in self.izinli_adlar if a.upper() in VK}

    def izinli_ayarla(self, adlar):
        self.izinli_adlar = [a.upper() for a in adlar]

    # -- yasam dongusu ----------------------------------------------------
    def kilitle(self):
        if self.aktif:
            return True
        if not WINDOWS:
            self.son_hata = ("Klavye kilidi yalnizca Windows'ta calisir; "
                             "tarayici kilidi yine de etkin.")
            self.aktif = False
            return False
        with self._kilit:
            self._dur.clear()
            hazir = threading.Event()
            self._is = threading.Thread(target=self._hook_dongusu,
                                        args=(hazir,), daemon=True,
                                        name="klavye-kilidi")
            self._is.start()
            hazir.wait(3.0)
        return self.aktif

    def ac(self):
        """Kilidi kaldirir."""
        self._dur.set()
        if WINDOWS and self._thread_id:
            try:
                import ctypes
                ctypes.windll.user32.PostThreadMessageW(
                    self._thread_id, 0x0012, 0, 0)      # WM_QUIT
            except Exception:                            # noqa: BLE001
                pass
        self.aktif = False
        return True

    def degistir(self):
        return self.ac() if self.aktif else self.kilitle()

    def durum(self):
        return {"aktif": self.aktif, "destekleniyor": self.destekleniyor,
                "izinli": self.izinli_adlar, "engellenen": self.engellenen,
                "gecen": self.gecen, "hata": self.son_hata}

    # -- ic isleyis -------------------------------------------------------
    def _hook_dongusu(self, hazir):
        import ctypes
        from ctypes import wintypes

        WH_KEYBOARD_LL = 13
        WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
        WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD),
                        ("scanCode", wintypes.DWORD),
                        ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_void_p)]

        HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int,
                                      wintypes.WPARAM, wintypes.LPARAM)
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        def _proc(nCode, wParam, lParam):
            if nCode != 0:
                return user32.CallNextHookEx(None, nCode, wParam, lParam)
            bilgi = ctypes.cast(lParam,
                                ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            vk = bilgi.vkCode
            basma = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
            gecsin = vk in self.izinli_vk and vk not in ASLA_GECME
            if gecsin:
                self.gecen += 1
                if basma and vk == VK["ESCAPE"] and self.panik:
                    threading.Thread(target=self._panik, daemon=True).start()
                return user32.CallNextHookEx(None, nCode, wParam, lParam)
            self.engellenen += 1
            if basma and self.olay:
                # Hook proc HIZLI donmeli: is parcacigina devret.
                ad = VK_AD.get(vk, f"VK{vk}")
                threading.Thread(target=self._olay, args=("engellendi", ad, vk),
                                 daemon=True).start()
            return 1                      # tusu yut

        self._c_proc = HOOKPROC(_proc)    # referans tutulmali (GC koruma)
        self._thread_id = kernel32.GetCurrentThreadId()
        self._hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._c_proc, None, 0)
        if not self._hook:
            self.son_hata = (f"Hook kurulamadi (kod {kernel32.GetLastError()}). "
                             "Programi yonetici olarak calistirmayi deneyin.")
            self.aktif = False
            hazir.set()
            return
        self.aktif = True
        self.son_hata = ""
        hazir.set()

        msg = wintypes.MSG()
        while not self._dur.is_set():
            r = user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1)
            if r:
                if msg.message == 0x0012:          # WM_QUIT
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            else:
                time.sleep(0.005)
        user32.UnhookWindowsHookEx(self._hook)
        self._hook = None
        self.aktif = False

    def _olay(self, tur, ad, vk):
        try:
            self.olay(tur, ad, vk)
        except Exception:                            # noqa: BLE001
            pass

    def _panik(self):
        try:
            self.panik()
        except Exception:                            # noqa: BLE001
            pass


# ----------------------------------------------------------------------
class OdakBekcisi:
    """Mach3 penceresi odagi kaybederse hareketi durdurur.

    `sadece_hareketteyken=True` iken yalnizca makine hareket halindeyken
    tetiklenir; boylece Mach3 disinda calisirken bos yere durdurma olmaz.
    """

    def __init__(self, baslik_parcasi="Mach3", durdur=None,
                 hareket_sorgu=None, geri_al=True, periyot=0.1):
        self.baslik = baslik_parcasi
        self.durdur = durdur
        self.hareket_sorgu = hareket_sorgu
        self.geri_al = geri_al
        self.periyot = periyot
        self.aktif = False
        self.destekleniyor = WINDOWS
        self.son_odak = ""
        self.tetikleme = 0
        self._dur = threading.Event()
        self._is = None
        self._hwnd = None

    def baslat(self):
        if not WINDOWS or self.aktif:
            return self.aktif
        self._dur.clear()
        self._is = threading.Thread(target=self._dongu, daemon=True,
                                    name="odak-bekcisi")
        self._is.start()
        self.aktif = True
        return True

    def durdur_bekci(self):
        self._dur.set()
        self.aktif = False

    def durum(self):
        return {"aktif": self.aktif, "destekleniyor": self.destekleniyor,
                "odak": self.son_odak, "tetikleme": self.tetikleme,
                "baslik": self.baslik}

    # -- ic isleyis -------------------------------------------------------
    def _mach3_hwnd(self):
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        bulunan = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def _her_pencere(hwnd, _):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                tampon = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, tampon, n + 1)
                if self.baslik.lower() in tampon.value.lower():
                    bulunan.append(hwnd)
                    return False
            return True

        user32.EnumWindows(_her_pencere, 0)
        return bulunan[0] if bulunan else None

    def _on_pencere_basligi(self):
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        n = user32.GetWindowTextLengthW(hwnd)
        tampon = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, tampon, n + 1)
        return tampon.value

    def _dongu(self):
        import ctypes
        user32 = ctypes.windll.user32
        onceki_mach3 = False
        while not self._dur.wait(self.periyot):
            try:
                baslik = self._on_pencere_basligi()
            except Exception:                        # noqa: BLE001
                continue
            self.son_odak = baslik
            mach3_onde = self.baslik.lower() in (baslik or "").lower()
            if mach3_onde:
                onceki_mach3 = True
                continue
            if not onceki_mach3:
                continue                             # zaten Mach3'te degildik
            onceki_mach3 = False
            hareketli = True
            if self.hareket_sorgu:
                try:
                    hareketli = bool(self.hareket_sorgu())
                except Exception:                    # noqa: BLE001
                    hareketli = True
            if not hareketli:
                continue
            self.tetikleme += 1
            if self.durdur:
                try:
                    self.durdur(f"Mach3 odagi kaybetti ({baslik or '?'})")
                except Exception:                    # noqa: BLE001
                    pass
            if self.geri_al:
                try:
                    if self._hwnd is None:
                        self._hwnd = self._mach3_hwnd()
                    if self._hwnd:
                        user32.SetForegroundWindow(self._hwnd)
                except Exception:                    # noqa: BLE001
                    pass
