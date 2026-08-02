#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mach3 OEM kod haritasi
======================
Mach3'un OLE/COM arayuzu sayisal "OEM kodlari" ile calisir:

    GetOEMDRO(800)      -> X ekseni konumu
    SetOEMDRO(800, -5)  -> X DRO'suna deger yaz
    DoOEMButton(1001)   -> Feed Hold dugmesine bas
    GetOEMLED(825)      -> bir LED'in durumu

Bu kodlar Mach3 surumune ve profile gore DEGISEBILIR. Bu yuzden hepsi burada
tek yerde toplanir ve `ayarlar.json` icinden ezilebilir (arayuzdeki
"OEM kodlari" panelinden de duzenlenebilir).

`guven` alani, kodun ne kadar yaygin/dogrulanmis oldugunu soyler:

    "yuksek" : Mach3 belgelerinde ve yaygin makrolarda standart olan kod.
    "orta"   : yaygin kullanilir ama profile gore degisebilir.
    "dusuk"  : MUTLAKA kendi makinenizde dogrulayin.

DIKKAT: Program, hayati islemleri (durdurma, feed hold) yalnizca OEM
dugmelerine birakmaz; `denetleyici` ayrica MDI (`Code`) ve `StopJog` gibi
yollari da dener. Yine de tek gercek guvenlik cihazi DONANIM acil-stop
butonudur (bkz. README "Guvenlik sinirlari").
"""

# ----------------------------------------------------------------------
# DRO (sayisal gosterge) kodlari
# ----------------------------------------------------------------------
DRO = {
    # Is (work) koordinatlari - jog ve sifirlama bunlari kullanir
    "x":            {"kod": 800, "aciklama": "X ekseni (is koordinati)",   "guven": "yuksek"},
    "y":            {"kod": 801, "aciklama": "Y ekseni (is koordinati)",   "guven": "yuksek"},
    "z":            {"kod": 802, "aciklama": "Z ekseni (is koordinati)",   "guven": "yuksek"},
    "a":            {"kod": 803, "aciklama": "A ekseni (is koordinati)",   "guven": "yuksek"},
    "b":            {"kod": 804, "aciklama": "B ekseni (is koordinati)",   "guven": "orta"},
    "c":            {"kod": 805, "aciklama": "C ekseni (is koordinati)",   "guven": "orta"},

    # Makine (mutlak) koordinatlari - yumusak limit denetimi bunlari kullanir
    "makine_x":     {"kod": 83,  "aciklama": "X makine koordinati",        "guven": "orta"},
    "makine_y":     {"kod": 84,  "aciklama": "Y makine koordinati",        "guven": "orta"},
    "makine_z":     {"kod": 85,  "aciklama": "Z makine koordinati",        "guven": "orta"},
    "makine_a":     {"kod": 86,  "aciklama": "A makine koordinati",        "guven": "dusuk"},

    # Besleme / devir
    "feed":         {"kod": 818, "aciklama": "Besleme (F) DRO",            "guven": "orta"},
    "fro":          {"kod": 821, "aciklama": "Besleme override %",         "guven": "orta"},
    "sro":          {"kod": 822, "aciklama": "Devir override %",           "guven": "dusuk"},
    "spindle_s":    {"kod": 812, "aciklama": "Istenen devir (S)",          "guven": "orta"},
    "gercek_rpm":   {"kod": 39,  "aciklama": "Olculen gercek devir",       "guven": "dusuk"},
}

# ----------------------------------------------------------------------
# OEM dugme kodlari
# ----------------------------------------------------------------------
BUTON = {
    "cycle_start":  {"kod": 1000, "aciklama": "Cycle Start (baslat)",      "guven": "yuksek"},
    "feed_hold":    {"kod": 1001, "aciklama": "Feed Hold (duraklat)",      "guven": "yuksek"},
    "stop":         {"kod": 1002, "aciklama": "Stop (durdur)",             "guven": "yuksek"},
    "rewind":       {"kod": 1003, "aciklama": "Rewind (basa sar)",         "guven": "orta"},
    "tek_blok":     {"kod": 1004, "aciklama": "Single Block ac/kapa",      "guven": "orta"},
    "reset":        {"kod": 1021, "aciklama": "Reset ac/kapa",             "guven": "orta"},
    "ref_hepsi":    {"kod": 1024, "aciklama": "Ref All Home",              "guven": "dusuk"},
    # Eksen basina Ref. Home switch'i TANIMLI DEGILSE Mach3 hic hareket
    # etmeden makine koordinatini bulunulan noktada sifirlar; tanimliysa
    # eksen switch'i ARAMAYA GIDER. Kodlar kurulumdan kuruluma degisebilir,
    # `oem_ezme.buton` ile ezilebilir; denetleyici yazma sonrasi makine
    # DRO'sunu geri okuyup gercekten degisip degismedigini dogrular.
    "ref_x":        {"kod": 22,   "aciklama": "Ref X (makine sifiri)",     "guven": "dusuk"},
    "ref_y":        {"kod": 23,   "aciklama": "Ref Y (makine sifiri)",     "guven": "dusuk"},
    "ref_z":        {"kod": 24,   "aciklama": "Ref Z (makine sifiri)",     "guven": "dusuk"},
    "sifirla_x":    {"kod": 131,  "aciklama": "Zero X",                    "guven": "orta"},
    "sifirla_y":    {"kod": 132,  "aciklama": "Zero Y",                    "guven": "orta"},
    "sifirla_z":    {"kod": 133,  "aciklama": "Zero Z",                    "guven": "orta"},
    "sifirla_a":    {"kod": 134,  "aciklama": "Zero A",                    "guven": "dusuk"},
    "spindle_cw":   {"kod": 112,  "aciklama": "Spindle CW ac/kapa",        "guven": "dusuk"},
}

# ----------------------------------------------------------------------
# OEM LED kodlari
# ----------------------------------------------------------------------
LED = {
    "estop":        {"kod": 825, "aciklama": "Acil stop / Reset durumu",   "guven": "dusuk"},
    "referansli_x": {"kod": 807, "aciklama": "X referanslandi (home)",     "guven": "dusuk"},
    "referansli_y": {"kod": 808, "aciklama": "Y referanslandi (home)",     "guven": "dusuk"},
    "referansli_z": {"kod": 809, "aciklama": "Z referanslandi (home)",     "guven": "dusuk"},
}

EKSENLER = ("X", "Y", "Z", "A")


class KodHaritasi:
    """DRO/BUTON/LED kodlarini, kullanici ezmeleriyle birlikte tutar."""

    def __init__(self, ezme=None):
        self.dro = {k: v["kod"] for k, v in DRO.items()}
        self.buton = {k: v["kod"] for k, v in BUTON.items()}
        self.led = {k: v["kod"] for k, v in LED.items()}
        self.uygula(ezme or {})

    def uygula(self, ezme):
        """ezme = {"dro": {"x": 800}, "buton": {...}, "led": {...}}"""
        for alan, hedef in (("dro", self.dro), ("buton", self.buton),
                            ("led", self.led)):
            for ad, kod in (ezme.get(alan) or {}).items():
                if ad in hedef:
                    hedef[ad] = int(kod)

    def eksen_dro(self, eksen, makine=False):
        ad = ("makine_" if makine else "") + eksen.lower()
        return self.dro.get(ad)

    def katalog(self):
        """Arayuzde duzenleme icin: ad, guncel kod, aciklama, guven."""
        def _liste(tanim, guncel):
            return [{"ad": ad, "kod": guncel[ad], "varsayilan": t["kod"],
                     "aciklama": t["aciklama"], "guven": t["guven"]}
                    for ad, t in tanim.items()]
        return {"dro": _liste(DRO, self.dro),
                "buton": _liste(BUTON, self.buton),
                "led": _liste(LED, self.led)}
