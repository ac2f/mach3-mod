#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mach3 OEM kod haritası
======================
Mach3'ün OLE/COM arayüzü sayısal "OEM kodları" ile çalışır:

    GetOEMDRO(800)      -> X ekseni konumu
    SetOEMDRO(800, -5)  -> X DRO'suna değer yaz
    DoOEMButton(1001)   -> Feed Hold düğmesine bas
    GetOEMLED(800)      -> bir LED'in durumu

KAYNAK (tahmin değil)
---------------------
Mach3 kurulumu üç sıralı liste taşır: ``DROs.txt``, ``Buttons.txt`` ve
``Leds.txt``. Ekran tasarımcısının açılır listeleridir ve **OEM kodu, listedeki
sıradan** gelir:

    DRO   kodu = 800  + sıra     (DROs.txt)
    Düğme kodu = 1000 + sıra     (Buttons.txt)
    LED   kodu = 800  + sıra     (Leds.txt)

Bu kural gerçek bir Mach3 R3.043 ile salt-okunur yoklanarak doğrulandı:
``GetOEMDRO(828)`` = 0.001 (profildeki ``JogInc``), ``GetOEMLED(800)`` = Reset
(``IsEstop`` ile aynı), ``GetOEMDRO(83)`` = ``GetAbsPosition(0)``. ``Help.txt``
de aynı sırayı doğrular (6002 = Rewind, 6003 = Stop).

Daha önceki sürümde ``stop`` için 1002 kullanılıyordu; bu **Rewind** düğmesidir
(gerçek Stop 1003). Ref/Zero kodları da yanlıştı. Burada hepsi listeden
türetilir; farklı bir Mach3 sürümünde kurulum klasöründeki üç dosya okunarak
(`kurulumdan_oku`) kodlar kendiliğinden uyarlanır.

``guven`` alanı:  "yuksek" = canlı Mach3'te doğrulandı ya da listeden birebir
türedi; "orta" = listeden türedi ama etkisi canlı denenmedi; "dusuk" = tahmin.
"""

import os

DRO_TABANI = 800
BUTON_TABANI = 1000
LED_TABANI = 800

# ----------------------------------------------------------------------
# Mach3 R3.043 kurulumundaki listeler (sıra = OEM kodu - taban)
# ----------------------------------------------------------------------
DRO_LISTESI = [
    "X Position", "Y Position", "Z Position", "A Position", "B Position",
    "C Position", "X Velocity", "Y Velocity", "Z Velocity", "A Velocity",
    "B Vecocity", "C Velocity", "OEM Code", "Blended Velocity",
    "Elapsed Time", "Estimate Time", "Current Line", "Spindle Speed",
    "FeedRate", "GMode", "Distance Mode", "FeedOverRide",
    "Current Tool Diam", "Current Tool Length", "Current Tool Number",
    "A Axis Radius", "B Axis Radius", "C Axis Radius", "Jog Increment",
    "Jog Feedrate", "X Offset", "Y Offset", "Z Offset", "A Offset",
    "B Offset", "C Offset", "Length Offset", "Queue Depth",
]

BUTON_LISTESI = [
    "Run", "Pause", "Rewind", "Stop File", "Single Step", "Resume",
    "Edit File", "Zero All Axis", "Zero X", "Zero Y", "Zero Z", "Zero A",
    "Zero B", "Zero C", "Reset Feed OverRide", "Estimate Job",
    "Set Next Line", "Goto Zero's", "Show Machine Coord.",
    "Show Work Coordinate", "Verify Position", "Reset ", "Home X", "Home Y",
    "Home Z", "Home A", "Home B", "Home C", "JoyStick On/Off",
    "SoftLimits Toggle", "Radius Tracking Toggle", "Jog Toggle", "OEM Code",
]

LED_LISTESI = [
    "EStop", "Standard Meas.", "Metric Meas.", "Idle", "Run", "Pause",
    "ToolChg", "X Ref'ed", "Y Ref'ed", "Z Ref'ed", "A Ref'ed", "B Ref'ed",
    "C Ref'ed", "In Dwell", "JoyStick ", "Softlimit", "Machine Coordinates",
    "CV Mode", "Work Offset", "Jog Inremental", "Jog Absolute", "INPUT 1",
    "INPUT 2", "INPUT 3", "INPUT 4", "Probe", "Index", "Limit OVerRide",
    "X++ Limit", "X-- Limit", "X Home switch", "Y++ Limit", "Y-- Limit",
    "Y Home switch", "Z++ Limit", "Z-- Limit", "Z Home switch",
    "A++ Limit", "A-- Limit", "A Home switch", "B++ Limit", "B-- Limit",
    "B Home switch", "C++ Limit", "C-- Limit", "C Home switch", "Enable 1",
    "Enable 2", "Enable 3", "Enable 4", "Enable 5", "Enable 6", "OUTPUT 1",
    "OUTPUT 2", "OUTPUT 3", "Digitize OUT", "OEM Code",
]


def _ad(s):
    return " ".join(str(s).split()).lower()


# ----------------------------------------------------------------------
# Adlandırılmış kodlar: ad -> (Mach3 listesindeki ad | doğrudan kod, açıklama,
# güven). Liste adı verilmişse kod listeden hesaplanır.
# ----------------------------------------------------------------------
# DRO: (kod | Mach3 liste adı, açıklama, güven)
DRO = {
    # İş (work) koordinatı. NOT: Mach3 "Machine Coordinates" göstergesi açıkken
    # 800-805 makine koordinatını döndürebilir; denetleyici LED 816'ya bakar.
    "x": ("X Position", "X ekseni (iş koordinatı)", "yuksek"),
    "y": ("Y Position", "Y ekseni (iş koordinatı)", "yuksek"),
    "z": ("Z Position", "Z ekseni (iş koordinatı)", "yuksek"),
    "a": ("A Position", "A ekseni (iş koordinatı)", "orta"),
    "b": ("B Position", "B ekseni (iş koordinatı)", "orta"),
    "c": ("C Position", "C ekseni (iş koordinatı)", "orta"),

    # Mutlak MAKİNE koordinatı: canlı doğrulandı (GetAbsPosition ile aynı).
    "makine_x": (83, "X makine koordinatı", "yuksek"),
    "makine_y": (84, "Y makine koordinatı", "yuksek"),
    "makine_z": (85, "Z makine koordinatı", "yuksek"),
    "makine_a": (86, "A makine koordinatı", "yuksek"),

    # Eksen hızları (birim/dk)
    "hiz_x": ("X Velocity", "X ekseni anlık hızı", "orta"),
    "hiz_y": ("Y Velocity", "Y ekseni anlık hızı", "orta"),
    "hiz_z": ("Z Velocity", "Z ekseni anlık hızı", "orta"),
    "hiz_a": ("A Velocity", "A ekseni anlık hızı", "orta"),

    # İş ofsetleri: iş = makine - ofset
    "ofset_x": ("X Offset", "X iş ofseti", "yuksek"),
    "ofset_y": ("Y Offset", "Y iş ofseti", "yuksek"),
    "ofset_z": ("Z Offset", "Z iş ofseti", "yuksek"),
    "ofset_a": ("A Offset", "A iş ofseti", "orta"),

    # Program / besleme / devir
    "gecen": ("Elapsed Time", "Programın geçen süresi (sn)", "orta"),
    "tahmin": ("Estimate Time", "Mach3'ün süre tahmini (sn)", "orta"),
    "satir": ("Current Line", "Çalışan G-Code satırı", "orta"),
    "devir": ("Spindle Speed", "İstenen devir (S)", "orta"),
    "feed": ("FeedRate", "Besleme (F) DRO", "yuksek"),
    "gmode": ("GMode", "Aktif G kodu", "orta"),
    "mesafe_kipi": ("Distance Mode", "G90/G91", "orta"),
    "fro": ("FeedOverRide", "Besleme override %", "yuksek"),
    "kuyruk": ("Queue Depth", "Hareket kuyruğu derinliği", "orta"),

    # Jog (Mach3'ün kendi jog durumu)
    "jog_adim": ("Jog Increment", "Mach3 Step jog artımı", "yuksek"),
    "jog_feed": ("Jog Feedrate", "Mach3 jog beslemesi", "orta"),
    # Ekrandaki "Slow Jog Rate" göstergesi (yüzde). Canlı Mach3'te 100 okundu
    # (profildeki JogSpeed=100 ile aynı); yazmanın etkisi donanımda denenmeli.
    "yavas_jog": (3, "Slow Jog Rate (% — Mach3 ekran DRO'su)", "orta"),
}

# Düğme: (Mach3 liste adı, açıklama, güven)
BUTON = {
    "cycle_start": ("Run", "Cycle Start (başlat)", "yuksek"),
    "feed_hold": ("Pause", "Feed Hold (duraklat)", "yuksek"),
    "rewind": ("Rewind", "Rewind (başa sar)", "yuksek"),
    "stop": ("Stop File", "Stop (durdur)", "yuksek"),
    "tek_blok": ("Single Step", "Single Block aç/kapa", "orta"),
    "devam": ("Resume", "Resume", "orta"),
    "sifirla_hepsi": ("Zero All Axis", "Tüm eksenleri sıfırla", "orta"),
    "sifirla_x": ("Zero X", "Zero X", "orta"),
    "sifirla_y": ("Zero Y", "Zero Y", "orta"),
    "sifirla_z": ("Zero Z", "Zero Z", "orta"),
    "sifirla_a": ("Zero A", "Zero A", "orta"),
    "fro_sifirla": ("Reset Feed OverRide", "Besleme override'ı %100 yap", "orta"),
    "sifira_git": ("Goto Zero's", "Sıfır noktalarına git", "orta"),
    "makine_koord": ("Show Machine Coord.", "Makine koordinatını göster", "orta"),
    "is_koord": ("Show Work Coordinate", "İş koordinatını göster", "orta"),
    "reset": ("Reset ", "Reset (E-Stop) aç/kapa", "yuksek"),
    # Home/Ref: home switch tanımlıysa eksen switch'i ARAMAYA GİDER
    "ref_x": ("Home X", "Ref X (makine sıfırı)", "orta"),
    "ref_y": ("Home Y", "Ref Y (makine sıfırı)", "orta"),
    "ref_z": ("Home Z", "Ref Z (makine sıfırı)", "orta"),
    "ref_a": ("Home A", "Ref A (makine sıfırı)", "orta"),
    "yumusak_limit": ("SoftLimits Toggle", "Mach3 yumuşak limit aç/kapa", "orta"),
    "jog_ac_kapa": ("Jog Toggle", "Jog ON/OFF", "orta"),
}

# LED: (Mach3 liste adı, açıklama, güven)
LED = {
    "estop": ("EStop", "Reset / E-Stop durumu", "yuksek"),
    "inch": ("Standard Meas.", "G20 (inç) kipi", "yuksek"),
    "metrik": ("Metric Meas.", "G21 (mm) kipi", "yuksek"),
    "bosta": ("Idle", "Boşta", "orta"),
    "calisiyor": ("Run", "Program çalışıyor", "orta"),
    "duraklatildi": ("Pause", "Feed Hold", "orta"),
    "takim_degisimi": ("ToolChg", "Takım değişimi", "orta"),
    "ref_x": ("X Ref'ed", "X referanslandı (home)", "yuksek"),
    "ref_y": ("Y Ref'ed", "Y referanslandı (home)", "yuksek"),
    "ref_z": ("Z Ref'ed", "Z referanslandı (home)", "yuksek"),
    "bekleme": ("In Dwell", "G4 bekleme", "orta"),
    "joystick": ("JoyStick ", "Joystick açık", "orta"),
    "yumusak_limit": ("Softlimit", "Mach3 yumuşak limit açık", "orta"),
    "makine_koord": ("Machine Coordinates", "DRO makine koordinatı gösteriyor", "yuksek"),
    "cv": ("CV Mode", "Sabit hız (G64) kipi", "yuksek"),
    "is_ofseti": ("Work Offset", "İş ofseti aktif", "orta"),
    "jog_adim": ("Jog Inremental", "Jog kipi: Step (artımlı)", "orta"),
    "jog_surekli": ("Jog Absolute", "Jog kipi: Continuous", "orta"),
    "limit_gecersiz": ("Limit OVerRide", "Limit override açık", "orta"),
}

EKSENLER = ("X", "Y", "Z", "A")


class KodHaritasi:
    """DRO/BUTON/LED kodlarını, kullanıcı ezmeleriyle birlikte tutar."""

    def __init__(self, ezme=None, kurulum=None):
        self.listeler = {"dro": list(DRO_LISTESI), "buton": list(BUTON_LISTESI),
                         "led": list(LED_LISTESI)}
        self.kurulum_raporu = None
        if kurulum:
            self.kurulumdan_oku(kurulum)
        self._hesapla()
        self.ezme = {}
        self.uygula(ezme or {})

    # -- kod hesabı -------------------------------------------------------
    @staticmethod
    def _kod(tanim_degeri, liste, taban):
        """Liste adı -> kod; zaten sayıysa olduğu gibi. Bulunamazsa None."""
        if isinstance(tanim_degeri, int):
            return tanim_degeri
        hedef = _ad(tanim_degeri)
        for i, ad in enumerate(liste):
            if _ad(ad) == hedef:
                return taban + i
        return None

    def _hesapla(self):
        self.varsayilan = {"dro": {}, "buton": {}, "led": {}}
        for alan, tanim, taban in (("dro", DRO, DRO_TABANI),
                                   ("buton", BUTON, BUTON_TABANI),
                                   ("led", LED, LED_TABANI)):
            for ad, (kaynak, _, _) in tanim.items():
                kod = self._kod(kaynak, self.listeler[alan], taban)
                if kod is not None:
                    self.varsayilan[alan][ad] = kod
        self.dro = dict(self.varsayilan["dro"])
        self.buton = dict(self.varsayilan["buton"])
        self.led = dict(self.varsayilan["led"])

    def uygula(self, ezme):
        """ezme = {"dro": {"x": 800}, "buton": {...}, "led": {...}}"""
        self.ezme = ezme or {}
        # önce varsayılana dön: ezmeyi silmek eski değeri geri getirmeli
        self.dro = dict(self.varsayilan["dro"])
        self.buton = dict(self.varsayilan["buton"])
        self.led = dict(self.varsayilan["led"])
        for alan, hedef in (("dro", self.dro), ("buton", self.buton),
                            ("led", self.led)):
            for ad, kod in (self.ezme.get(alan) or {}).items():
                if ad in hedef:
                    try:
                        hedef[ad] = int(kod)
                    except (TypeError, ValueError):
                        pass

    # -- kurulum dosyalarından uyarlama -----------------------------------
    def kurulumdan_oku(self, klasor):
        """Mach3 klasöründeki Buttons/DROs/Leds.txt'yi okur ve kodları uyarlar.

        Dosya yoksa ya da okunamıyorsa gömülü liste kalır. Döndürülen rapor,
        gömülü listeden FARKLI olan girdileri içerir (farklı Mach3 sürümü).
        """
        rapor = {"klasor": klasor, "okunan": [], "farklar": []}
        for alan, dosya in (("dro", "DROs.txt"), ("buton", "Buttons.txt"),
                            ("led", "Leds.txt")):
            yol = os.path.join(klasor, dosya)
            try:
                with open(yol, "r", encoding="latin-1") as f:
                    satirlar = [s.rstrip("\r\n") for s in f.read().split("\n")]
            except OSError:
                continue
            # sondaki boş satırları at; ortadakiler sıra için SAYILIR
            while satirlar and not satirlar[-1].strip():
                satirlar.pop()
            gomulu = self.listeler[alan]
            if satirlar and satirlar != gomulu:
                for i, ad in enumerate(satirlar):
                    g = gomulu[i] if i < len(gomulu) else None
                    if g is None or _ad(g) != _ad(ad):
                        rapor["farklar"].append(
                            {"liste": dosya, "sira": i, "dosyada": ad,
                             "gomulu": g})
                self.listeler[alan] = satirlar
            rapor["okunan"].append(dosya)
        self.kurulum_raporu = rapor
        if getattr(self, "dro", None) is not None:
            self._hesapla()
        return rapor

    # -- kolay erişim -----------------------------------------------------
    def eksen_dro(self, eksen, makine=False):
        ad = ("makine_" if makine else "") + eksen.lower()
        return self.dro.get(ad)

    def eksen_ofset_dro(self, eksen):
        return self.dro.get("ofset_" + eksen.lower())

    def katalog(self):
        """Arayüzde düzenleme için: ad, güncel kod, açıklama, güven."""
        def _liste(tanim, guncel, varsayilan):
            return [{"ad": ad, "kod": guncel.get(ad),
                     "varsayilan": varsayilan.get(ad),
                     "aciklama": t[1], "guven": t[2]}
                    for ad, t in tanim.items() if ad in guncel]
        return {"dro": _liste(DRO, self.dro, self.varsayilan["dro"]),
                "buton": _liste(BUTON, self.buton, self.varsayilan["buton"]),
                "led": _liste(LED, self.led, self.varsayilan["led"])}
