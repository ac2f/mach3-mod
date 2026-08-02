#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Is raporu disa aktarma
======================
`is_takip.IsTakip.rapor_verisi()` ciktisini uc bicimde verir:

    * JSON - tum ham veri (baska bir araca aktarmak icin)
    * CSV  - blok tablosu (Excel'de acilir)
    * PDF  - okunabilir rapor

PDF NEDEN ELDEN YAZILDI?
------------------------
Projenin kurali: makinedeki Windows kurulumuna ek kutuphane bulastirma
(bkz. README "Kurulum"). reportlab/fpdf kurmak yerine PDF'in ihtiyac
duyulan kucuk alt kumesi burada uretiliyor: yalnizca duz metin, cizgi ve
dikdortgen. Gomulu yazi tipi yok; PDF'in 14 standart yazi tipinden
Helvetica kullaniliyor - bu yuzden dosya birkac KB kaliyor ve her
goruntuleyicide acilir.

TURKCE KARAKTER: Standart yazi tipleri WinAnsi (CP1252) kodlamasi kullanir;
bu kumede 'g', 'i', 's', 'I' yoktur. Gomulu yazi tipi eklemek dosyayi
yuzlerce KB buyutecegi icin bu karakterler ASCII karsiliklarina cevrilir
(zaten arayuzun ve kaynak kodun tamami bu yazimi kullaniyor).
"""

import json
import time

# ----------------------------------------------------------------------
# PDF uretimi
# ----------------------------------------------------------------------
A4 = (595.28, 841.89)

_CEVRIM = str.maketrans({
    "ğ": "g", "Ğ": "G", "ı": "i", "İ": "I", "ş": "s", "Ş": "S",
    "ç": "c", "Ç": "C", "ö": "o", "Ö": "O", "ü": "u", "Ü": "U",
    "â": "a", "î": "i", "û": "u", "–": "-", "—": "-", "’": "'",
    "“": '"', "”": '"', "•": "-", "·": "-",
})


def _duz(s):
    """Turkce karakterleri WinAnsi'de sorunsuz olanlara cevirir."""
    return str(s).translate(_CEVRIM)


def _kacis(s):
    """PDF metin dizesi kacislari."""
    return (_duz(s).replace("\\", r"\\").replace("(", r"\(")
            .replace(")", r"\)"))


class PDFYazici:
    """Cok sayfali, duz metin + cizgi ureten kucuk PDF yazici."""

    def __init__(self, boyut=A4, kenar=42.0):
        self.g, self.y_boy = boyut
        self.kenar = kenar
        self._sayfalar = []          # her biri komut listesi
        self._komut = []
        self.y = self.y_boy - kenar  # ust kenardan asagi

    # -- sayfa ---------------------------------------------------------
    def yeni_sayfa(self):
        if self._komut:
            self._sayfalar.append(self._komut)
        self._komut = []
        self.y = self.y_boy - self.kenar

    def yer_ac(self, gerekli=14.0):
        """Sayfa sonuna geldiysek yeni sayfaya gec."""
        if self.y - gerekli < self.kenar:
            self.yeni_sayfa()
            return True
        return False

    # -- cizim ---------------------------------------------------------
    def metin(self, x, metin, boyut=9.5, kalin=False, y=None, renk=None):
        yy = self.y if y is None else y
        font = "F2" if kalin else "F1"
        r, gg, b = renk or (0, 0, 0)
        self._komut.append(
            f"q {r:.3f} {gg:.3f} {b:.3f} rg BT /{font} {boyut:.1f} Tf "
            f"1 0 0 1 {x:.2f} {yy:.2f} Tm ({_kacis(metin)}) Tj ET Q")
        return self

    def satir(self, metin, boyut=9.5, kalin=False, x=None, bosluk=13.0,
              renk=None):
        self.yer_ac(bosluk)
        self.metin(self.kenar if x is None else x, metin, boyut, kalin,
                   renk=renk)
        self.y -= bosluk
        return self

    def bosluk(self, h=8.0):
        self.yer_ac(h)
        self.y -= h
        return self

    def cizgi(self, x1, y1, x2, y2, kalinlik=0.6, renk=(0.75, 0.75, 0.75)):
        r, g, b = renk
        self._komut.append(
            f"q {r:.3f} {g:.3f} {b:.3f} RG {kalinlik:.2f} w "
            f"{x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S Q")
        return self

    def ayirac(self):
        self.yer_ac(10)
        self.cizgi(self.kenar, self.y + 4, self.g - self.kenar, self.y + 4)
        self.y -= 10
        return self

    def kutu(self, x, y, w, h, renk=(0.94, 0.95, 0.97)):
        r, g, b = renk
        self._komut.append(f"q {r:.3f} {g:.3f} {b:.3f} rg "
                           f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} re f Q")
        return self

    def tablo_satiri(self, hucreler, genislikler, kalin=False, vurgu=False,
                     bosluk=14.0):
        """hucreler: [str], genislikler: [pt] (sol hizali)."""
        self.yer_ac(bosluk + 2)
        if vurgu:
            self.kutu(self.kenar - 3, self.y - 3.5,
                      sum(genislikler) + 6, bosluk)
        x = self.kenar
        for metin, w in zip(hucreler, genislikler):
            self.metin(x, metin, 9.0, kalin)
            x += w
        self.y -= bosluk
        return self

    # -- uretim --------------------------------------------------------
    def bytes(self):
        if self._komut:
            self._sayfalar.append(self._komut)
            self._komut = []
        if not self._sayfalar:
            self._sayfalar = [[]]

        nesneler = []            # 1'den baslayan nesne govdeleri

        def ekle(govde):
            nesneler.append(govde)
            return len(nesneler)

        # yer tutucular: 1 = Catalog, 2 = Pages
        ekle(b"")
        ekle(b"")
        font1 = ekle(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
                     b"/Encoding /WinAnsiEncoding >>")
        font2 = ekle(b"<< /Type /Font /Subtype /Type1 /BaseFont "
                     b"/Helvetica-Bold /Encoding /WinAnsiEncoding >>")

        sayfa_no = []
        for komutlar in self._sayfalar:
            akis = "\n".join(komutlar).encode("latin-1", "replace")
            icerik = ekle(b"<< /Length " + str(len(akis)).encode() + b" >>\n"
                          b"stream\n" + akis + b"\nendstream")
            sayfa = ekle(
                ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 "
                 f"{self.g:.2f} {self.y_boy:.2f}] /Resources << /Font << "
                 f"/F1 {font1} 0 R /F2 {font2} 0 R >> >> /Contents "
                 f"{icerik} 0 R >>").encode("latin-1"))
            sayfa_no.append(sayfa)

        kids = " ".join(f"{n} 0 R" for n in sayfa_no)
        nesneler[1] = (f"<< /Type /Pages /Kids [{kids}] /Count "
                       f"{len(sayfa_no)} >>").encode("latin-1")
        nesneler[0] = b"<< /Type /Catalog /Pages 2 0 R >>"

        # govde + xref
        cikti = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        konum = []
        for i, govde in enumerate(nesneler, start=1):
            konum.append(len(cikti))
            cikti += f"{i} 0 obj\n".encode() + govde + b"\nendobj\n"
        xref = len(cikti)
        cikti += f"xref\n0 {len(nesneler) + 1}\n".encode()
        cikti += b"0000000000 65535 f \n"
        for k in konum:
            cikti += f"{k:010d} 00000 n \n".encode()
        cikti += (f"trailer\n<< /Size {len(nesneler) + 1} /Root 1 0 R >>\n"
                  f"startxref\n{xref}\n%%EOF\n").encode()
        return bytes(cikti)


# ----------------------------------------------------------------------
# Bicimlendirme yardimcilari
# ----------------------------------------------------------------------
def _zaman(t):
    return time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(t)) if t else "-"


def _sure(sn):
    from . import sure as SURE
    return SURE.sure_metni(sn) if sn is not None else "-"


def _sayi(v, basamak=1, birim=""):
    if v is None:
        return "-"
    return f"{float(v):.{basamak}f}{birim}"


def dosya_adi(veri, uzanti):
    """'kapak_001__2026-08-02_1435.pdf' gibi bir ad uretir."""
    import os
    import re
    ad = os.path.splitext(veri.get("ad") or "is")[0] or "is"
    ad = re.sub(r"[^A-Za-z0-9_.-]+", "_", _duz(ad))[:48]
    zaman = time.strftime("%Y-%m-%d_%H%M",
                          time.localtime(veri.get("baslangic_t") or
                                         time.time()))
    return f"{ad}__{zaman}.{uzanti}"


# ----------------------------------------------------------------------
# JSON / CSV
# ----------------------------------------------------------------------
def json_uret(veri):
    return json.dumps(veri, ensure_ascii=False, indent=2, default=str)


def csv_uret(veri):
    """Blok tablosu + ozet satirlari (Excel icin ; ayraci)."""
    import csv
    import io
    tampon = io.StringIO()
    y = csv.writer(tampon, delimiter=";", lineterminator="\r\n")

    y.writerow(["Is raporu"])
    y.writerow(["Dosya", veri.get("ad")])
    y.writerow(["Yol", veri.get("dosya")])
    y.writerow(["Baslangic", _zaman(veri.get("baslangic_t"))])
    y.writerow(["Bitis", _zaman(veri.get("bitis_t"))])
    y.writerow(["Bitis sebebi", veri.get("bitis_sebep")])
    y.writerow(["Gecen sure (sn)", veri.get("gecen_sn")])
    y.writerow(["Calisan sure (sn)", veri.get("calisan_sn")])
    y.writerow(["Duraklama (sn)", veri.get("duraklama_sn")])
    y.writerow(["Duraklama sayisi", veri.get("duraklama_sayisi")])
    y.writerow(["Tahmini sure (sn)", veri.get("tahmini_sn")])
    y.writerow(["Sapma orani (gercek/tahmin)", veri.get("sapma_orani")])
    y.writerow(["Kat edilen yol (mm)", veri.get("yol_mm")])
    y.writerow(["Toplam yol (mm)", veri.get("toplam_yol_mm")])
    y.writerow(["Profil", veri.get("profil")])
    y.writerow(["Tezgah tabani (makine Z)", veri.get("taban")])
    y.writerow([])

    y.writerow(["Blok", "Yol (mm)", "Tahmini (sn)", "Gercek (sn)",
                "Fark (sn)", "Baslangic", "Bitis"])
    for b in veri.get("bloklar") or []:
        tah, ger = b.get("tahmin_sn"), b.get("gercek_sn")
        fark = None if (tah is None or ger is None) else round(ger - tah, 1)
        y.writerow([b.get("no"), b.get("yol_mm"), tah, ger, fark,
                    _zaman(b.get("basladi_t")), _zaman(b.get("bitti_t"))])

    y.writerow([])
    y.writerow(["Zaman", "Tur", "Olay"])
    for o in veri.get("olaylar") or []:
        y.writerow([_zaman(o.get("t")), o.get("tur"), o.get("mesaj")])
    return tampon.getvalue()


# ----------------------------------------------------------------------
# PDF
# ----------------------------------------------------------------------
def pdf_uret(veri):
    p = PDFYazici()

    # -- baslik --------------------------------------------------------
    p.satir("Mach3-Kontrol - Is Raporu", 17, kalin=True, bosluk=22)
    p.satir(veri.get("ad") or "-", 12, kalin=True, bosluk=15)
    p.satir(veri.get("dosya") or "", 8, renk=(0.45, 0.45, 0.45), bosluk=14)
    p.ayirac()

    # -- ozet ----------------------------------------------------------
    p.satir("Ozet", 12, kalin=True, bosluk=17)
    ozet = [
        ("Baslangic", _zaman(veri.get("baslangic_t"))),
        ("Bitis", _zaman(veri.get("bitis_t"))),
        ("Sonuc", str(veri.get("bitis_sebep") or "-")),
        ("Gecen sure", _sure(veri.get("gecen_sn"))),
        ("Calisan sure (duraklamasiz)", _sure(veri.get("calisan_sn"))),
        ("Duraklama",
         f"{_sure(veri.get('duraklama_sn'))}  "
         f"({veri.get('duraklama_sayisi') or 0} kez)"),
        ("Tahmini sure", _sure(veri.get("tahmini_sn"))),
        ("Sapma (gercek / tahmin)",
         "-" if veri.get("sapma_orani") is None
         else f"{veri['sapma_orani']:.2f}x"),
        ("Kat edilen yol",
         f"{_sayi(veri.get('yol_mm'), 0, ' mm')} / "
         f"{_sayi(veri.get('toplam_yol_mm'), 0, ' mm')}"),
        ("Baslangic blogu", str((veri.get("baslangic_blok") or 0) + 1)),
        ("Is sifiri profili", veri.get("profil") or "-"),
        ("Tezgah tabani (makine Z)",
         "olculmedi" if veri.get("taban") is None
         else _sayi(veri.get("taban"), 2, " mm")),
    ]
    for etiket, deger in ozet:
        p.yer_ac(14)
        p.metin(p.kenar, etiket, 9.0, renk=(0.35, 0.35, 0.35))
        p.metin(p.kenar + 190, deger, 9.5, kalin=True)
        p.y -= 14
    p.ayirac()

    # -- blok tablosu --------------------------------------------------
    bloklar = veri.get("bloklar") or []
    p.satir(f"Bloklar ({len(bloklar)})", 12, kalin=True, bosluk=17)
    gen = [46, 78, 84, 84, 74, 100]
    p.tablo_satiri(["Blok", "Yol (mm)", "Tahmini", "Gercek", "Fark",
                    "Baslangic"], gen, kalin=True, vurgu=True)
    for b in bloklar:
        tah, ger = b.get("tahmin_sn"), b.get("gercek_sn")
        fark = None if (tah is None or ger is None) else ger - tah
        fark_yazi = "-" if fark is None else f"{fark:+.0f} sn"
        p.tablo_satiri([
            str(b.get("no")),
            _sayi(b.get("yol_mm"), 0),
            _sure(tah),
            _sure(ger) if ger is not None else "-",
            fark_yazi,
            time.strftime("%H:%M:%S", time.localtime(b["basladi_t"]))
            if b.get("basladi_t") else "-",
        ], gen)

    # -- besleme degisimleri -------------------------------------------
    fro = veri.get("fro_gecmis") or []
    if fro:
        p.bosluk(6)
        p.satir(f"Besleme override degisimleri ({len(fro)})", 12, kalin=True,
                bosluk=17)
        for f in fro[:40]:
            p.satir(f"{time.strftime('%H:%M:%S', time.localtime(f['t']))}   "
                    f"%{f.get('fro')}", 9.0, bosluk=12)

    # -- olaylar -------------------------------------------------------
    olaylar = veri.get("olaylar") or []
    if olaylar:
        p.bosluk(6)
        p.satir(f"Olaylar ({len(olaylar)})", 12, kalin=True, bosluk=17)
        for o in olaylar:
            p.yer_ac(12)
            p.metin(p.kenar,
                    time.strftime("%H:%M:%S", time.localtime(o.get("t", 0))),
                    8.5, renk=(0.45, 0.45, 0.45))
            p.metin(p.kenar + 58, str(o.get("tur", "")), 8.5,
                    renk=(0.45, 0.45, 0.45))
            p.metin(p.kenar + 118, str(o.get("mesaj", "")), 9.0)
            p.y -= 12

    # -- dipnot --------------------------------------------------------
    p.bosluk(10)
    p.ayirac()
    p.satir("Ilerleme, DRO'dan okunan konumla kat edilen yoldan olculur; "
            "Mach3'un COM arayuzu calisan satiri vermez.", 7.5,
            renk=(0.5, 0.5, 0.5), bosluk=11)
    p.satir(f"Uretildi: {_zaman(time.time())}", 7.5,
            renk=(0.5, 0.5, 0.5), bosluk=11)
    return p.bytes()
