#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mach3 sanal makinesi (simülasyon arka ucu)
==========================================
Gerçek Mach3'ün davranışını **fiziksel olarak anlamlı** biçimde taklit eder;
arayüzü, güvenlik kurallarını, jog motorunu ve süre tahminini makineye hiç
dokunmadan denemek (ve otomatik test etmek) için kullanılır.

Taklit edilenler
----------------
* **Trapez hız profili**: her eksenin hızlanması (`makine.ivme`) ve azami hızı
  (`makine.hizli_hiz`) vardır. Feed Hold/Stop ani kesme değil, **yavaşlama
  rampasıdır**; tam durmadan önce eksen mesafe katetmeye devam eder.
* **CV (sabit hız) kipi**: aynı doğrultudaki ardışık G1'ler birleşir, farklı
  doğrultuda köşede yavaşlanır. Kuyruğun SONU ise her zaman durarak biter -
  bu yüzden zincirleme jog'un yeterince ileri komut tutması gerekir.
* **Reset (E-Stop)**: Reset'teyken hareket yok, MDI yok, DRO yazma sessizce
  yok sayılır (gerçek Mach3'te ölçülen davranış).
* **Birimler**: G20/G21 ve LED'ler (Standard/Metric Meas.). Gerçek bir Mach3
  G20 kipinde açılabilir; mm varsayan bir istemci bu durumda 25.4 kat fazla
  hareket ettirir. Simülasyon bunu birebir yaşatır.
* **İş / makine koordinatı**, iş ofseti, "Machine Coordinates" göstergesi.
* **Program çalıştırma**: satır satır, geçerli satır (DRO 816), geçen süre,
  Run/Pause/Idle LED'leri, Single Block, Rewind.
* **Mach3'ün kendi jog'u**: `JogOn(eksen, yön)` / `JogOff(eksen)`. Continuous
  ve Step kipleri, Slow Jog Rate %, Step artımı, Jog ON/OFF. ``JogOn``'un yön
  değeri gerçek Mach3'te eksene göre ters olabildiğinden simülasyonda da X
  bilerek TERS kodlanmıştır (`yon_esleme`); istemci bunu öğrenmek zorundadır.
"""

import math
import re
import threading
import time
from collections import deque

from .baglanti import COM_METOTLAR, TemelBaglanti

EKSENLER = ("X", "Y", "Z", "A")
_INCH = 25.4
_WORD_RE = re.compile(r"([A-Za-z])\s*([+-]?\d*\.?\d+)")
_EPS = 1e-9


def _tokenler(satir):
    s = re.sub(r"\(.*?\)", "", str(satir)).split(";", 1)[0]
    return [(h.upper(), float(v)) for h, v in _WORD_RE.findall(s)]


class SimulasyonBaglanti(TemelBaglanti):
    tur = "simulasyon"

    def __init__(self, ayar=None, makine=None, jog=None):
        super().__init__(ayar)
        mak = makine or {}
        hiz = mak.get("hizli_hiz") or {}
        ivme = mak.get("ivme") or {}
        # eksen azami hizi (mm/dk) ve ivmesi (mm/s^2)
        self.vmax = {e: float(hiz.get(e, 3000.0 if e in ("Z", "A") else 8000.0))
                     for e in EKSENLER}
        self.ivme = {e: float(ivme.get(e, 250.0 if e in ("Z", "A") else 400.0))
                     for e in EKSENLER}

        self.konum = {e: 0.0 for e in EKSENLER}          # makine (mm)
        self.ofset = {e: 0.0 for e in EKSENLER}          # is = makine - ofset
        self.reset = bool(self.ayar.get("sim_reset_ile_basla", False))
        self.inch = bool(self.ayar.get("sim_inc_kipi", False))      # G20
        self.mutlak = True
        self.feed = 1000.0                                # mm/dk (dahili)
        self.devir = 0.0
        self.spindle = False
        self.sogutma = False
        self.fro = 100.0
        self.cv = True
        self.g_modu = 80.0
        self.holding = False

        # jog (Mach3'un kendi durumu)
        j = jog or {}
        self.yavas_jog = float(j.get("yavas_yuzde", 20.0))      # %
        self.jog_inc = float(j.get("adim", 1.0))                # gecerli birim
        self.jog_modu = "surekli"                               # | "adim"
        self.jog_acik = True
        self.jog_tampon = 5                                     # IncBuffer
        # JogOn(eksen, yon): hangi deger + yonu verir? X bilerek ters.
        self.yon_esleme = {"X": {1: 1, -1: 0}, "Y": {1: 0, -1: 1},
                           "Z": {1: 0, -1: 1}, "A": {1: 0, -1: 1}}
        self.yumusak_limit_acik = False
        self.limitler = {e: (-1e9, 1e9) for e in EKSENLER}
        self.makine_koord_goster = False

        # hareket planlayici
        self._parcalar = deque()
        self._akt = None
        self._v = 0.0                                    # yol hizi (mm/s)
        self._plan = dict(self.konum)                    # kuyruk sonu (makine)
        self._jog = None                                 # surekli jog
        self._jv = 0.0                                   # jog hizi (isaretli)
        self._hiz_ekseni = {e: 0.0 for e in EKSENLER}    # anlik eksen hizi

        # program
        self.yuklu_dosya = ""
        self._program = []
        self._pi = 0
        self.calisiyor = False
        self.tek_blok = False
        self._cs_bekle = False
        self.satir = 0
        self.gecen = 0.0

        # gozlem (testler icin)
        self.dugmeler = []
        self.kodlar = []

        self._k = threading.RLock()
        self._dur = threading.Event()
        self._is = None
        self._yetenek = {ad: True for ad in COM_METOTLAR}

    # ==================================================================
    # Yasam dongusu
    # ==================================================================
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

    def _dongu(self):
        son = time.perf_counter()
        while not self._dur.is_set():
            time.sleep(0.004)
            simdi = time.perf_counter()
            dt, son = min(simdi - son, 0.05), simdi
            if dt <= 0:
                continue
            with self._k:
                try:
                    self._adim(dt)
                except Exception:                      # noqa: BLE001
                    # sanal makine asla coker; durumu guvenli tarafa cek
                    self._parcalar.clear()
                    self._akt = None
                    self._v = 0.0

    # ==================================================================
    # Birim / koordinat yardimcilari
    # ==================================================================
    @property
    def _k_birim(self):
        """Gecerli birimden mm'ye carpan."""
        return _INCH if self.inch else 1.0

    def _is_koord(self, e):
        return self.konum[e] - self.ofset[e]

    # ==================================================================
    # Hareket motoru
    # ==================================================================
    def _bosta(self):
        return (self._akt is None and not self._parcalar and self._jog is None
                and abs(self._jv) < _EPS and self._v < _EPS)

    def _adim(self, dt):
        if self.reset:
            self._sifirla_hareket()
            return
        if self.calisiyor:
            self.gecen += dt
            self._program_besle()
        self._jog_adim(dt)
        self._planlayici(dt)
        # anlik eksen hizlari (DRO 806-809), mm/dk
        self._hiz_guncelle(dt)
        if self._bosta():
            self._plan = dict(self.konum)
            if self.calisiyor and self._pi >= len(self._program):
                self.calisiyor = False
                self.satir = len(self._program)
                self._satir_no = None

    def _sifirla_hareket(self):
        self._parcalar.clear()
        self._akt = None
        self._v = 0.0
        self._jog = None
        self._jv = 0.0
        self.calisiyor = False
        self.holding = False
        self._plan = dict(self.konum)
        self._hiz_ekseni = {e: 0.0 for e in EKSENLER}

    _onceki = None
    _satir_no = None

    def _hiz_guncelle(self, dt):
        # konum degisiminden eksen hizini kestir
        if self._onceki is None:
            self._onceki = dict(self.konum)
            return
        for e in EKSENLER:
            self._hiz_ekseni[e] = (self.konum[e] - self._onceki[e]) / dt * 60.0
        self._onceki = dict(self.konum)

    # -- surekli jog -----------------------------------------------------
    def _jog_adim(self, dt):
        if self._jog is None and abs(self._jv) < _EPS:
            return
        j = self._jog
        eksen = (j or {}).get("eksen") or self._jog_son_eksen
        a = self.ivme[eksen]
        hedef = j["isaret"] * j["v"] if (j and j["acik"]) else 0.0
        # yumusak limit: durus noktasi limiti asacaksa yavasla
        if self.yumusak_limit_acik and abs(self._jv) > _EPS:
            alt, ust = self.limitler[eksen]
            dur = self.konum[eksen] + self._jv * abs(self._jv) / (2 * a)
            if (self._jv > 0 and dur >= ust) or (self._jv < 0 and dur <= alt):
                hedef = 0.0
        dv = a * dt
        v0 = self._jv
        if v0 < hedef:
            v1 = min(hedef, v0 + dv)
        else:
            v1 = max(hedef, v0 - dv)
        self.konum[eksen] += (v0 + v1) / 2 * dt
        if self.yumusak_limit_acik:
            alt, ust = self.limitler[eksen]
            self.konum[eksen] = min(max(self.konum[eksen], alt), ust)
        self._jv = v1
        if abs(self._jv) < _EPS and (j is None or not j["acik"]):
            self._jv = 0.0
            self._jog = None

    _jog_son_eksen = "X"

    # -- MDI / program parcalari -----------------------------------------
    def _parca_basla(self, p):
        """Aktif parcanin gecis ozelliklerini hesaplar."""
        if p.get("bekle") is not None:
            p["bekle_kalan"] = p["bekle"]
            return True
        bas = dict(self.konum)
        d = {e: p["hedef"][e] - bas[e] for e in EKSENLER if e in p["hedef"]}
        uz = math.sqrt(sum(v * v for v in d.values()))
        if uz < _EPS:
            return False
        u = {e: v / uz for e, v in d.items()}
        p["u"] = u
        p["kalan"] = uz
        a = min(self.ivme[e] / abs(u[e]) for e in u if abs(u[e]) > 1e-9)
        p["a"] = a
        eksen_hiz = min(self.vmax[e] / 60.0 / abs(u[e]) for e in u
                        if abs(u[e]) > 1e-9)
        if p["hizli"]:
            p["vcap"] = eksen_hiz
        else:
            p["vcap"] = min(max(p["feed"], 1.0) / 60.0, eksen_hiz)
        return True

    def _siradaki_hareket(self):
        for p in self._parcalar:
            if p.get("bekle") is None:
                return p
            return None
        return None

    def _planlayici(self, dt):
        if self._jog is not None or abs(self._jv) > _EPS:
            return
        if self._akt is None:
            # Feed Hold sirasinda hala hizliysak yavaslamaya KUYRUKTAKI yolda
            # devam ederiz (gercek planlayici gibi); durmus ve bekliyorsak dur.
            if not self._parcalar or (self.holding and self._v <= _EPS):
                self._v = 0.0
                return
            p = self._parcalar.popleft()
            if p.get("satir"):
                self.satir = p["satir"]            # hareket BASLADI
            if not self._parca_basla(p):
                return
            self._akt = p
        p = self._akt
        if p.get("bekle") is not None:                  # G4
            if self.holding:
                return
            p["bekle_kalan"] -= dt
            if p["bekle_kalan"] <= 0:
                self._akt = None
            return

        a = p["a"]
        vcap = p["vcap"]
        if not p["hizli"] and not p.get("jog"):
            vcap *= max(self.fro, 0.0) / 100.0       # FRO jog'u etkilemez
        kalan = p["kalan"]

        # parca sonundaki hedef hiz (CV birlesmesi)
        v_son = 0.0
        birlesir = False
        sonraki = self._siradaki_hareket()
        if self.cv and sonraki is not None and not p.get("dur"):
            # sonraki parcanin yonu: bu parcanin HEDEFINDEN hesaplanir
            bas = {e: p["hedef"].get(e, self.konum[e]) for e in EKSENLER}
            d2 = {e: sonraki["hedef"][e] - bas[e] for e in sonraki["hedef"]}
            uz2 = math.sqrt(sum(v * v for v in d2.values()))
            if uz2 > _EPS:
                nokta = sum(p["u"].get(e, 0.0) * d2.get(e, 0.0) / uz2
                            for e in EKSENLER)
                birlesir = nokta > 0.999
        if birlesir and not self.holding:
            v_son = vcap

        v = self._v
        v_ist = 0.0 if self.holding else vcap
        fren = max(0.0, (v * v - v_son * v_son) / (2 * a))
        if not self.holding and kalan <= fren + 1e-9:
            v_ist = v_son
        if v < v_ist:
            v1 = min(v_ist, v + a * dt)
        else:
            v1 = max(v_ist, v - a * dt)
        # fren bolgesinde tam profil: durusa ulasirken takilmasin
        if not self.holding and kalan <= fren + 1e-9:
            v1 = max(min(v1, math.sqrt(v_son * v_son + 2 * a * kalan)),
                     min(a * dt, math.sqrt(v_son * v_son + 2 * a * kalan)))
        adim = (v + v1) / 2 * dt
        if adim >= kalan - 1e-9:
            for e, hv in p["hedef"].items():
                self.konum[e] = hv
            # birlesen parcada hiz kesilmez (Feed Hold sirasinda yavaslama da
            # kuyruktaki yolda surer); kose/son parcada durulur
            self._v = min(v1, vcap) if birlesir else 0.0
            self._akt = None
            return
        for e, ue in p["u"].items():
            self.konum[e] += ue * adim
        p["kalan"] = kalan - adim
        self._v = v1

    def _hareket_ekle(self, hedef_makine, feed=None, hizli=False, jog=False):
        """hedef_makine: {eksen: makine mm}. Kuyrugun sonuna ekler."""
        tam = dict(self._plan if self._kuyruk_dolu() else self.konum)
        tam.update(hedef_makine)
        self._parcalar.append({"hedef": tam, "feed": feed or self.feed,
                               "hizli": hizli, "jog": jog,
                               "satir": self._satir_no})
        self._plan = dict(tam)

    def _kuyruk_dolu(self):
        return bool(self._parcalar) or self._akt is not None

    # ==================================================================
    # Program calistirma
    # ==================================================================
    def _program_besle(self):
        while (self.calisiyor and not self.holding and not self._cs_bekle
               and len(self._parcalar) < 3 and self._pi < len(self._program)):
            satir = self._program[self._pi]
            self._pi += 1
            bosta = not self._kuyruk_dolu()
            hareketli = self._satir_isle(satir, self._pi)
            # "Current Line" YURUTULEN satiri gosterir: hareket uretmeyen bir
            # satir ancak kuyruk bosken hemen yurutulmus sayilir.
            if bosta and not hareketli:
                self.satir = self._pi
            if self.tek_blok and hareketli:
                self._cs_bekle = True
        # program sonu: M30/M2 ya da dosya bitti -> bosalinca calisiyor=False

    # ==================================================================
    # G-code yorumlama (MDI ve program ortak)
    # ==================================================================
    def kod(self, satir):
        with self._k:
            self.kodlar.append(str(satir))
            if self.reset:
                return                      # Reset'te MDI yok sayilir
            for parca in str(satir).replace("\r", "").split("\n"):
                self._satir_isle(parca)

    def _satir_isle(self, satir, no=None):
        """Dondurur: bu satir bir hareket/bekleme uretti mi?"""
        self._satir_no = no
        tok = _tokenler(satir)
        if not tok:
            return False
        gs = [v for h, v in tok if h == "G"]
        ms = [int(v) for h, v in tok if h == "M"]
        w = {}
        for h, v in tok:
            if h not in ("G", "M"):
                w[h] = v
        # modal kipler
        if 20 in gs:
            self.inch = True
        if 21 in gs:
            self.inch = False
        if 90 in gs:
            self.mutlak = True
        if 91 in gs:
            self.mutlak = False
        if 64 in gs:
            self.cv = True
        if 61 in gs:
            self.cv = False
        k = self._k_birim
        if "F" in w:
            self.feed = max(w["F"] * k, 0.0)
        if "S" in w:
            self.devir = w["S"]
        for m in ms:
            if m in (3, 4):
                self.spindle = True
            elif m == 5:
                self.spindle = False
            elif m in (7, 8):
                self.sogutma = True
            elif m == 9:
                self.sogutma = False
            elif m in (2, 30):
                self._pi = len(self._program)
        eksenli = [e for e in EKSENLER if e in w]
        # G92 / G10 L20: bulunulan noktanin is koordinatini belirle
        if 92 in gs or (10 in gs and w.get("L") == 20):
            for e in eksenli:
                self.ofset[e] = self._plan_taban(e) - w[e] * k
            return False
        if 4 in gs:
            sn = w.get("P", 0.0)
            self._parcalar.append({"bekle": sn if sn < 100 else sn / 1000.0,
                                   "satir": no})
            return True
        hareket_g = [g for g in gs if g in (0, 1, 2, 3)]
        if hareket_g:
            self.g_modu = float(hareket_g[-1])
        if not eksenli:
            return False
        g = self.g_modu
        taban = self._plan if self._kuyruk_dolu() else self.konum
        hedef = {}
        for e in eksenli:
            if self.mutlak:
                hedef[e] = w[e] * k + self.ofset[e]
            else:
                hedef[e] = taban[e] + w[e] * k
        self._hareket_ekle(hedef, hizli=(g == 0.0))
        return True

    def _plan_taban(self, e):
        return (self._plan if self._kuyruk_dolu() else self.konum)[e]

    # ==================================================================
    # DRO / LED okuma
    # ==================================================================
    _EKSEN_DRO = {800: "X", 801: "Y", 802: "Z", 803: "A"}
    _MAKINE_DRO = {83: "X", 84: "Y", 85: "Z", 86: "A"}
    _HIZ_DRO = {806: "X", 807: "Y", 808: "Z", 809: "A"}
    _OFSET_DRO = {830: "X", 831: "Y", 832: "Z", 833: "A"}

    def dro(self, kod):
        kod = int(kod)
        with self._k:
            k = self._k_birim
            if kod in self._EKSEN_DRO:
                e = self._EKSEN_DRO[kod]
                v = self.konum[e] if self.makine_koord_goster \
                    else self._is_koord(e)
                return round(v / k, 5)
            if kod in self._MAKINE_DRO:
                return round(self.konum[self._MAKINE_DRO[kod]] / k, 5)
            if kod in self._HIZ_DRO:
                return round(self._hiz_ekseni[self._HIZ_DRO[kod]] / k, 3)
            if kod in self._OFSET_DRO:
                return round(self.ofset[self._OFSET_DRO[kod]] / k, 5)
            if kod == 814:
                return round(self.gecen, 2)
            if kod == 816:
                return float(self.satir)
            if kod in (817, 812):
                return self.devir
            if kod == 39:
                return self.devir if self.spindle else 0.0
            if kod == 818:
                return round(self.feed / k, 3)
            if kod == 819:
                return self.g_modu
            if kod == 820:
                return 0.0 if self.mutlak else 1.0
            if kod == 821:
                return self.fro
            if kod == 828:
                return self.jog_inc
            if kod == 829:
                h = (self.yavas_jog / 100.0 * max(self.vmax.values()) / k
                     if (self._jog or abs(self._jv) > _EPS) else 0.0)
                return round(h, 3)
            if kod == 837:
                return float(len(self._parcalar) + (1 if self._akt else 0))
            if kod == 3:
                return self.yavas_jog
            return 0.0

    def dro_yaz(self, kod, deger):
        kod = int(kod)
        deger = float(deger)
        with self._k:
            k = self._k_birim
            # Reset'te SetOEMDRO SESSIZCE yok sayilir (gercek Mach3 davranisi)
            if self.reset:
                return
            if kod in self._EKSEN_DRO:
                e = self._EKSEN_DRO[kod]
                if self.makine_koord_goster:
                    return                  # makine DRO'suna yazilamaz
                self.ofset[e] = self.konum[e] - deger * k
            elif kod == 818:
                self.feed = deger * k
            elif kod == 821:
                self.fro = min(max(deger, 0.0), 300.0)
            elif kod == 817:
                self.devir = deger
            elif kod == 828:
                self.jog_inc = max(deger, 0.0)
            elif kod == 3:
                self.yavas_jog = min(max(deger, 0.0), 100.0)

    _LED = {800: "reset", 801: "inch", 802: "metrik", 803: "bosta",
            804: "run", 805: "pause", 813: "bekleme", 815: "soft",
            816: "makine", 817: "cv", 819: "jog_adim", 820: "jog_surekli",
            827: "limit_ovr"}

    def led(self, kod):
        kod = int(kod)
        with self._k:
            if kod in (807, 808, 809, 810, 811, 812):
                return True                  # home switch'siz makine
            ad = self._LED.get(kod)
            if ad == "reset":
                return self.reset
            if ad == "inch":
                return self.inch
            if ad == "metrik":
                return not self.inch
            if ad == "bosta":
                return (not self.reset and not self.calisiyor
                        and self._bosta())
            if ad == "run":
                return self.calisiyor
            if ad == "pause":
                return self.holding
            if ad == "bekleme":
                return bool(self._akt and self._akt.get("bekle") is not None)
            if ad == "soft":
                return self.yumusak_limit_acik
            if ad == "makine":
                return self.makine_koord_goster
            if ad == "cv":
                return self.cv
            if ad == "jog_adim":
                return self.jog_modu == "adim"
            if ad == "jog_surekli":
                return self.jog_modu != "adim"
            return False

    # ==================================================================
    # Dugmeler
    # ==================================================================
    _REF = {1022: "X", 1023: "Y", 1024: "Z", 1025: "A"}
    _SIFIR = {1008: "X", 1009: "Y", 1010: "Z", 1011: "A"}

    def dugme(self, kod):
        kod = int(kod)
        with self._k:
            self.dugmeler.append(kod)
            if kod == 1021:                         # Reset (toggle)
                self.reset = not self.reset
                if self.reset:
                    self._sifirla_hareket()
                return
            if self.reset:
                return                              # Reset'te digerleri yok
            if kod == 1001:                         # Pause / Feed Hold
                self.holding = True
            elif kod == 1000:                       # Run / Cycle Start
                if self.holding:
                    self.holding = False
                elif self._cs_bekle:
                    self._cs_bekle = False
                elif self._program and not self.calisiyor:
                    if self._pi >= len(self._program):
                        self._pi = 0
                    self.calisiyor = True
                    self.gecen = 0.0 if self._pi == 0 else self.gecen
            elif kod == 1003:                       # Stop
                self._durdur_yavasca()
            elif kod == 1002:                       # Rewind
                if not self.calisiyor:
                    self._pi = 0
                    self.satir = 0
            elif kod == 1004:                       # Single Step
                self.tek_blok = not self.tek_blok
            elif kod in self._SIFIR:
                e = self._SIFIR[kod]
                self.ofset[e] = self.konum[e]
            elif kod == 1007:
                for e in EKSENLER:
                    self.ofset[e] = self.konum[e]
            elif kod in self._REF:
                self._makine_sifir(self._REF[kod])
            elif kod == 1031:                       # Jog Toggle
                self.jog_acik = not self.jog_acik
            elif kod == 1029:
                self.yumusak_limit_acik = not self.yumusak_limit_acik
            elif kod == 1018:
                self.makine_koord_goster = True
            elif kod == 1019:
                self.makine_koord_goster = False
            elif kod == 1014:
                self.fro = 100.0

    def dugme_acil(self, kod):
        self.dugme(kod)

    def _makine_sifir(self, e):
        """Home switch'i olmayan eksen: bulunulan nokta makine sifiri olur.
        Is DRO'su degismez (ofset ayni kadar kayar)."""
        self.ofset[e] -= self.konum[e]
        self.konum[e] = 0.0
        self._plan[e] = 0.0

    def makine_sifir(self, eksen_no):
        with self._k:
            if self.reset:
                return
            self._makine_sifir(EKSENLER[int(eksen_no)])

    def _durdur_yavasca(self):
        """Stop: rampayla durur, kuyruk ve program atilir."""
        self.holding = False
        self.calisiyor = False
        self._cs_bekle = False
        self._parcalar.clear()
        p = self._akt
        if p is not None and p.get("bekle") is None and self._v > _EPS:
            fren = self._v * self._v / (2 * p["a"])
            p["kalan"] = min(p["kalan"], fren)
            p["dur"] = True
            bas = dict(self.konum)
            p["hedef"] = {e: bas[e] + p["u"].get(e, 0.0) * p["kalan"]
                          for e in EKSENLER}
        else:
            self._akt = None
            self._v = 0.0
        if self._jog is not None:
            self._jog["acik"] = False
        self._plan = {e: self.konum[e] for e in EKSENLER}
        if p is not None and "hedef" in p:
            self._plan = dict(p["hedef"])

    # ==================================================================
    # Dosya
    # ==================================================================
    def dosya_yukle(self, yol):
        with self._k:
            self.yuklu_dosya = str(yol)
            self._program = []
            try:
                with open(yol, "r", encoding="utf-8", errors="replace") as f:
                    self._program = f.read().splitlines()
            except OSError:
                pass
            self._pi = 0
            self.satir = 0
            self.calisiyor = False

    def yukleniyor(self):
        return False

    # ==================================================================
    # Durum sorgulari
    # ==================================================================
    def hareket_ediyor(self):
        with self._k:
            if self.reset:
                return False
            if self._v > _EPS or abs(self._jv) > _EPS:
                return True
            if self.holding:
                return False
            if self._akt is not None and self._akt.get("bekle") is None:
                return True
            return any(p.get("bekle") is None for p in self._parcalar)

    def estop(self):
        return self.reset

    def referansli(self):
        # Sanal tezgahin makine koordinati 0'dan baslar ve tezgah sifiriyla
        # ayni yerdedir; ayrica referans almaya gerek yoktur.
        return True

    # ==================================================================
    # Mach3 yerel jog (JogOn / JogOff)
    # ==================================================================
    def _eksen_isaret(self, eksen, yon_degeri):
        for isaret, deger in self.yon_esleme[eksen].items():
            if deger == int(yon_degeri):
                return isaret
        return 1

    def jog_ac(self, eksen_no, yon_degeri):
        with self._k:
            if self.reset or not self.jog_acik or self.calisiyor:
                return
            eksen = EKSENLER[int(eksen_no)]
            isaret = self._eksen_isaret(eksen, yon_degeri)
            v = self.yavas_jog / 100.0 * self.vmax[eksen] / 60.0   # mm/s
            if self.jog_modu == "adim":
                if len(self._parcalar) >= self.jog_tampon:
                    return
                mesafe = self.jog_inc * self._k_birim * isaret
                taban = self._plan_taban(eksen)
                hedef = taban + mesafe
                if self.yumusak_limit_acik:
                    alt, ust = self.limitler[eksen]
                    hedef = min(max(hedef, alt), ust)
                self._hareket_ekle({eksen: hedef},
                                   feed=self.yavas_jog / 100.0
                                   * self.vmax[eksen], jog=True)
                return
            if self._kuyruk_dolu():
                return
            self._jog_son_eksen = eksen
            self._jog = {"eksen": eksen, "isaret": isaret, "v": v,
                         "acik": True}

    def jog_kapat(self, eksen_no):
        with self._k:
            if self._jog is not None and \
                    self._jog["eksen"] == EKSENLER[int(eksen_no)]:
                self._jog["acik"] = False

    # -- test yardimcilari ----------------------------------------------------
    def jog_modu_ayarla(self, mod):
        with self._k:
            self.jog_modu = "adim" if mod == "adim" else "surekli"
