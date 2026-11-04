#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
G-Code ayristirma, sira gosterimi ve blok-tan-baslatma
======================================================
Cnc-Assistant ile ayni blok tanimini kullanir: **bir kesim blogu, X ve Y
iceren bir G0 (hizli) hareketle baslar** ve bir sonraki G0 XY'ye kadar surer.
Boylece Cnc-Assistant'in `*_reordered.tap` ciktilari burada birebir ayni
numaralarla listelenir.

Bu modul G-Code URETMEZ / DEGISTIRMEZ. Yaptigi uc sey:

1. `Program`  - dosyayi header / kesim bloklari / footer olarak ayristirir,
                arayuzde sira listesi ve takim yolu onizlemesi icin ozet verir.
2. `modal_durum` - bir bloga gelene kadarki modal durumu (G20/G21, G90/G91,
                besleme, devir, spindle yonu, takim) toplar.
3. `bloktan_baslat_uret` - secilen bloktan itibaren **guvenli** calisacak
                gecici bir program uretir: once guvenli Z'ye cikar, modal
                durumu geri kurar, hedef blogun baslangicina havadan gider,
                sonra kalan bloklari aynen calistirir.
"""

import math
import os
import re

_WORD_RE = re.compile(r"([A-Za-z])\s*([+-]?\d*\.?\d+)")
_INCH_MM = 25.4


def satir_kelimeleri(satir):
    """'G1 X10 Y-2 (yorum)' -> {'G': 1.0, 'X': 10.0, 'Y': -2.0}"""
    s = re.sub(r"\(.*?\)", "", satir).split(";", 1)[0]
    return {h.upper(): float(v) for h, v in _WORD_RE.findall(s)}


def blok_basi_mi(satir):
    w = satir_kelimeleri(satir)
    return w.get("G") == 0.0 and "X" in w and "Y" in w


def g91_var_mi(satirlar):
    rx = re.compile(r"\bG91(?!\.)\b", re.I)
    for s in satirlar:
        if rx.search(re.sub(r"\(.*?\)", "", s).split(";", 1)[0]):
            return True
    return False


def birim_tespit(satirlar):
    for s in satirlar:
        w = satir_kelimeleri(s)
        if w.get("G") == 20:
            return "inch"
        if w.get("G") == 21:
            return "mm"
    return None


# ----------------------------------------------------------------------
# Blok geometrisi
# ----------------------------------------------------------------------
def blok_bas_xy(blok):
    w = satir_kelimeleri(blok[0])
    return (w.get("X", 0.0), w.get("Y", 0.0))


def blok_bbox(blok):
    """Blogun gercek sinir kutusu.

    DIKKAT: yalnizca X/Y kelimelerine bakmak YANLIS sonuc verir - bir
    G2/G3 yayinin uc noktalari, yayin en uc noktasi degildir (bir cember
    icin X/Y kelimeleri yalnizca iki uc noktayi verir). Bu yuzden yol
    interpolasyonu (blok_yol) uzerinden hesaplanir.
    """
    pts = blok_yol(blok, bolut=24)
    if not pts:
        bx, by = blok_bas_xy(blok)
        return (bx, by, bx, by)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _yay_merkez(x0, y0, x1, y1, w, g):
    """I/J (bagil merkez) veya R (yaricap) ile yay merkezi."""
    if "I" in w or "J" in w:
        return (x0 + w.get("I", 0.0), y0 + w.get("J", 0.0))
    r = w.get("R")
    if not r:
        return None
    dx, dy = x1 - x0, y1 - y0
    q = math.hypot(dx, dy)
    if q < 1e-9 or abs(r) < q / 2 - 1e-9:
        return None
    h = math.sqrt(max(abs(r) ** 2 - (q / 2) ** 2, 0.0))
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    # R>0 kisa yay, R<0 uzun yay; yon G2 (saat yonu) / G3 (ters)
    isaret = 1.0 if (r > 0) == (g == 3.0) else -1.0
    return (mx - isaret * h * dy / q, my + isaret * h * dx / q)


def _yay_acilari(x0, y0, x1, y1, cx, cy, g):
    a0 = math.atan2(y0 - cy, x0 - cx)
    a1 = math.atan2(y1 - cy, x1 - cx)
    if g == 2.0:                      # saat yonu -> aci azalir
        while a1 >= a0:
            a1 -= 2 * math.pi
    else:                             # saat yonu tersi -> aci artar
        while a1 <= a0:
            a1 += 2 * math.pi
    return a0, a1


def _yay_noktalari(x0, y0, x1, y1, w, g, bolut=18):
    merkez = _yay_merkez(x0, y0, x1, y1, w, g)
    if not merkez:
        return [(x1, y1)]
    cx, cy = merkez
    r = math.hypot(x0 - cx, y0 - cy)
    a0, a1 = _yay_acilari(x0, y0, x1, y1, cx, cy, g)
    n = max(2, min(120, int(bolut * abs(a1 - a0) / (math.pi / 2)) + 2))
    return [(cx + r * math.cos(a0 + (a1 - a0) * i / n),
             cy + r * math.sin(a0 + (a1 - a0) * i / n))
            for i in range(1, n + 1)]


def yay_uzunlugu(x0, y0, x1, y1, dz, w, g):
    merkez = _yay_merkez(x0, y0, x1, y1, w, g)
    if not merkez:
        return math.sqrt((x1 - x0) ** 2 + (y1 - y0) ** 2 + dz * dz)
    cx, cy = merkez
    r = math.hypot(x0 - cx, y0 - cy)
    a0, a1 = _yay_acilari(x0, y0, x1, y1, cx, cy, g)
    yay = abs(a1 - a0) * r
    return math.sqrt(yay * yay + dz * dz)


def blok_yol(blok, bolut=18):
    """Blogun (yaylar dahil) gercek takim yolu noktalari."""
    pts, x, y, g = [], None, None, None
    for s in blok:
        w = satir_kelimeleri(s)
        if "G" in w and w["G"] in (0.0, 1.0, 2.0, 3.0):
            g = w["G"]
        if "X" not in w and "Y" not in w:
            continue
        nx = w.get("X", x if x is not None else 0.0)
        ny = w.get("Y", y if y is not None else 0.0)
        if x is None or y is None:
            x, y = nx, ny
            pts.append((x, y))
            continue
        if g in (2.0, 3.0) and ("I" in w or "J" in w or "R" in w):
            pts += _yay_noktalari(x, y, nx, ny, w, g, bolut)
        else:
            pts.append((nx, ny))
        x, y = nx, ny
    return pts


def blok_svg_komut(blok):
    """Onizleme icin SVG `d` yolu (yaylar gercek A komutu ile)."""
    d, x, y, g = [], None, None, None
    for s in blok:
        w = satir_kelimeleri(s)
        if "G" in w and w["G"] in (0.0, 1.0, 2.0, 3.0):
            g = w["G"]
        if "X" not in w and "Y" not in w:
            continue
        nx = w.get("X", x if x is not None else 0.0)
        ny = w.get("Y", y if y is not None else 0.0)
        if x is None or y is None:
            d.append(f"M{nx:.4f} {ny:.4f}")
        elif g in (2.0, 3.0) and ("I" in w or "J" in w or "R" in w):
            merkez = _yay_merkez(x, y, nx, ny, w, g)
            if merkez:
                r = math.hypot(x - merkez[0], y - merkez[1])
                a0, a1 = _yay_acilari(x, y, nx, ny, merkez[0], merkez[1], g)
                buyuk = 1 if abs(a1 - a0) > math.pi else 0
                supurme = 0 if g == 2.0 else 1     # SVG y-ekseni ters degil
                d.append(f"A{r:.4f} {r:.4f} 0 {buyuk} {supurme} "
                         f"{nx:.4f} {ny:.4f}")
            else:
                d.append(f"L{nx:.4f} {ny:.4f}")
        else:
            d.append(f"L{nx:.4f} {ny:.4f}")
        x, y = nx, ny
    return " ".join(d)


def blok_z_araligi(blok):
    zs = [satir_kelimeleri(s)["Z"] for s in blok if "Z" in satir_kelimeleri(s)]
    return (min(zs), max(zs)) if zs else (None, None)


def blok_kesim_var_mi(blok):
    """Blokta gercek bir kesim (G1/G2/G3 + XY hareketi) var mi?"""
    g = None
    for s in blok:
        w = satir_kelimeleri(s)
        if "G" in w and w["G"] in (0.0, 1.0, 2.0, 3.0):
            g = w["G"]
        if g in (1.0, 2.0, 3.0) and ("X" in w or "Y" in w):
            return True
    return False


# ----------------------------------------------------------------------
# Hareket listesi (sure tahmini + guvenlik denetimi icin)
# ----------------------------------------------------------------------
def hareket_listesi(satirlar, baslangic=(0.0, 0.0, 0.0)):
    """Satirlari sirayla yorumlayip hareket kayitlari uretir.

    Her kayit: {"tip": hizli|kesim|bekleme|takim|spindle, "uzunluk": mm,
                "feed": mm/dk, "z": hedef Z, "satir": indeks}
    Birim (G20/G21), mutlak/artimli (G90/G91) ve modal F takip edilir;
    inch dosyalar mm'ye cevrilir.
    """
    x, y, z = baslangic
    g = 0.0
    feed = 0.0
    olcek = 1.0                      # G20 -> 25.4
    mutlak = True
    kayit = []
    for i, s in enumerate(satirlar):
        w = satir_kelimeleri(s)
        if not w:
            continue
        if "G" in w:
            gv = w["G"]
            if gv == 20:
                olcek = _INCH_MM
            elif gv == 21:
                olcek = 1.0
            elif gv == 90:
                mutlak = True
            elif gv == 91:
                mutlak = False
            elif gv == 4:
                p = w.get("P", w.get("X", 0.0))
                kayit.append({"tip": "bekleme", "uzunluk": 0.0, "feed": 0.0,
                              "sure": p if p < 100 else p / 1000.0,
                              "z": z, "satir": i})
                continue
            if gv in (0.0, 1.0, 2.0, 3.0):
                g = gv
        if "F" in w:
            feed = w["F"] * olcek
        if "M" in w and int(w["M"]) == 6:
            kayit.append({"tip": "takim", "uzunluk": 0.0, "feed": 0.0,
                          "z": z, "satir": i})
        if "M" in w and int(w["M"]) in (3, 4):
            kayit.append({"tip": "spindle", "uzunluk": 0.0, "feed": 0.0,
                          "z": z, "satir": i})
        if not any(e in w for e in ("X", "Y", "Z")):
            continue

        def _hedef(eksen, mevcut):
            if eksen not in w:
                return mevcut
            v = w[eksen] * olcek
            return v if mutlak else mevcut + v

        nx, ny, nz = _hedef("X", x), _hedef("Y", y), _hedef("Z", z)
        if g in (2.0, 3.0) and ("I" in w or "J" in w or "R" in w):
            wo = {k: (v * olcek if k in ("I", "J", "R") else v)
                  for k, v in w.items()}
            uz = yay_uzunlugu(x, y, nx, ny, nz - z, wo, g)
        else:
            uz = math.sqrt((nx - x) ** 2 + (ny - y) ** 2 + (nz - z) ** 2)
        kayit.append({"tip": "hizli" if g == 0.0 else "kesim",
                      "uzunluk": uz, "feed": feed, "z": nz, "satir": i,
                      "dz": nz - z})
        x, y, z = nx, ny, nz
    return kayit


def kapsam(satirlar, baslangic=(0.0, 0.0, 0.0)):
    """Satirlarin ulastigi X/Y/Z araligi (is koordinatinda, mm).

    Yumusak limit denetiminin temelidir; bu yuzden **yaylarin uc noktalari
    degil, gercek gecis noktalari** dikkate alinir. G20 (inch) mm'ye
    cevrilir, G91 (artimli) takip edilir.
    """
    x, y, z = baslangic
    g, mutlak, olcek = 0.0, True, 1.0
    mn = {"X": None, "Y": None, "Z": None}
    mx = {"X": None, "Y": None, "Z": None}

    def _kay(eksen, v):
        mn[eksen] = v if mn[eksen] is None else min(mn[eksen], v)
        mx[eksen] = v if mx[eksen] is None else max(mx[eksen], v)

    for s in satirlar:
        w = satir_kelimeleri(s)
        if not w:
            continue
        if "G" in w:
            gv = w["G"]
            if gv == 20:
                olcek = _INCH_MM
            elif gv == 21:
                olcek = 1.0
            elif gv == 90:
                mutlak = True
            elif gv == 91:
                mutlak = False
            if gv in (0.0, 1.0, 2.0, 3.0):
                g = gv
        if not any(e in w for e in ("X", "Y", "Z")):
            continue
        nx = (w["X"] * olcek if mutlak else x + w["X"] * olcek) if "X" in w else x
        ny = (w["Y"] * olcek if mutlak else y + w["Y"] * olcek) if "Y" in w else y
        nz = (w["Z"] * olcek if mutlak else z + w["Z"] * olcek) if "Z" in w else z
        if g in (2.0, 3.0) and ("I" in w or "J" in w or "R" in w):
            wo = {k: (v * olcek if k in ("I", "J", "R") else v)
                  for k, v in w.items()}
            for px, py in _yay_noktalari(x, y, nx, ny, wo, g, bolut=24):
                _kay("X", px)
                _kay("Y", py)
        _kay("X", nx)
        _kay("Y", ny)
        _kay("Z", nz)
        x, y, z = nx, ny, nz
    return {e: [mn[e], mx[e]] for e in ("X", "Y", "Z")}


# ----------------------------------------------------------------------
# Modal durum + bloktan baslatma
# ----------------------------------------------------------------------
def modal_durum(satirlar):
    """Verilen satirlarin SONUNDAKI modal durum."""
    d = {"birim": None, "mutlak": True, "feed": None, "devir": None,
         "spindle": None, "takim": None, "sogutma": None, "duzlem": None}
    for s in satirlar:
        w = satir_kelimeleri(s)
        if "G" in w:
            g = w["G"]
            if g == 20:
                d["birim"] = "G20"
            elif g == 21:
                d["birim"] = "G21"
            elif g == 90:
                d["mutlak"] = True
            elif g == 91:
                d["mutlak"] = False
            elif g in (17, 18, 19):
                d["duzlem"] = f"G{int(g)}"
        if "F" in w:
            d["feed"] = w["F"]
        if "S" in w:
            d["devir"] = w["S"]
        if "T" in w:
            d["takim"] = w["T"]
        if "M" in w:
            m = int(w["M"])
            if m in (3, 4):
                d["spindle"] = f"M{m}"
            elif m == 5:
                d["spindle"] = None
            elif m in (7, 8):
                d["sogutma"] = f"M{m}"
            elif m == 9:
                d["sogutma"] = None
    return d


# ----------------------------------------------------------------------
class Program:
    """Bir G-Code dosyasini blok blok ayristirir (Cnc-Assistant uyumlu)."""

    def __init__(self, yol=None, satirlar=None):
        self.yol = yol or ""
        if satirlar is None:
            with open(yol, "r", encoding="utf-8", errors="replace") as f:
                satirlar = f.read().splitlines()
        self.satirlar = satirlar
        self.artimli = g91_var_mi(satirlar)
        self.birim = birim_tespit(satirlar) or "mm"
        self.header, self.bloklar, self.footer = [], [], []
        self.uyarilar = []
        if self.artimli:
            self.uyarilar.append(
                "Dosyada G91 (artimli mod) var: bloktan baslatma guvenli "
                "degildir, bastan calistirin.")
        self._ayristir()

    def _ayristir(self):
        bas = [i for i, s in enumerate(self.satirlar) if blok_basi_mi(s)]
        if not bas:
            self.uyarilar.append(
                "X-Y iceren G0 bulunamadi; blok tespiti yapilamadi.")
            self.header = list(self.satirlar)
            return
        self.header = list(self.satirlar[:bas[0]])
        ham = []
        for n, bi in enumerate(bas):
            son = bas[n + 1] if n + 1 < len(bas) else len(self.satirlar)
            ham.append(self.satirlar[bi:son])
        kesim = [blok_kesim_var_mi(b) for b in ham]
        # bastaki/sondaki kesim olmayan bloklar header/footer'a
        while ham and not kesim[-1]:
            self.footer = ham.pop() + self.footer
            kesim.pop()
        while ham and not kesim[0]:
            self.header += ham.pop(0)
            kesim.pop(0)
        self.bloklar = ham

    # -- ozet ------------------------------------------------------------
    def ozet(self):
        """Arayuzdeki sira listesi + onizleme icin blok ozetleri."""
        out = []
        for i, blok in enumerate(self.bloklar):
            bx, by = blok_bas_xy(blok)
            zmin, zmax = blok_z_araligi(blok)
            hareketler = hareket_listesi(blok, (bx, by, zmax or 0.0))
            kesim_uz = sum(h["uzunluk"] for h in hareketler
                           if h["tip"] == "kesim")
            yol = blok_yol(blok)
            son = yol[-1] if yol else (bx, by)
            out.append({
                "id": i, "no": i + 1, "x": round(bx, 3), "y": round(by, 3),
                "son": [round(son[0], 3), round(son[1], 3)],
                "bbox": [round(v, 3) for v in blok_bbox(blok)],
                "komut": blok_svg_komut(blok),
                "satir": len(blok),
                "kesim_uz": round(kesim_uz, 2),
                "z_min": zmin, "z_max": zmax,
            })
        return out

    def olculer(self):
        """Programin is koordinatindaki olculeri (onizleme bilgi seridi).

        Iki ayri kapsam verilir; ikisini karistirmak yanlis karar dogurur:

        * `kesim` - yalnizca KESIM bloklarinin kapladigi alan. "Parca ne
          kadar yer kaplayacak, malzemeyi nereye baglayayim" sorusunun
          yanitidir. Yay uc noktalari degil gercek yay gecisleri sayilir.
        * `tum`   - bosta (G0) gidislerle birlikte TUM hareket zarfi.
          Yumusak limit denetimi bunu kullanir; genelde kesim alanindan
          buyuktur (basa/sona donus hareketleri).

        Ayrica is sifirina (X0 Y0) gore konum bilgisi verilir: sol/alt
        bosluk, en sag ve en ust nokta.
        """
        satirlar = list(self.header)
        for b in self.bloklar:
            satirlar += b
        satirlar += self.footer
        tum = kapsam(satirlar, (0.0, 0.0, 0.0))

        kx0 = ky0 = kx1 = ky1 = None
        for blok in self.bloklar:
            bx0, by0, bx1, by1 = blok_bbox(blok)
            kx0 = bx0 if kx0 is None else min(kx0, bx0)
            ky0 = by0 if ky0 is None else min(ky0, by0)
            kx1 = bx1 if kx1 is None else max(kx1, bx1)
            ky1 = by1 if ky1 is None else max(ky1, by1)

        def _kutu(x0, y0, x1, y1):
            if x0 is None or y0 is None:
                return None
            return {"x": [round(x0, 3), round(x1, 3)],
                    "y": [round(y0, 3), round(y1, 3)],
                    "genislik": round(x1 - x0, 3),
                    "yukseklik": round(y1 - y0, 3)}

        z = tum["Z"]
        return {
            "kesim": _kutu(kx0, ky0, kx1, ky1),
            "tum": _kutu(tum["X"][0], tum["Y"][0], tum["X"][1], tum["Y"][1]),
            "z": {"min": None if z[0] is None else round(z[0], 3),
                  "max": None if z[1] is None else round(z[1], 3)},
            # Is sifirina gore: parcanin sol/alt kosesi nerede baslar,
            # en sag ve en ust noktasi kaca kadar gider.
            "sifira_gore": None if kx0 is None else {
                "sol": round(kx0, 3), "alt": round(ky0, 3),
                "sag": round(kx1, 3), "ust": round(ky1, 3),
                "kosegen": round(math.hypot(kx1 - kx0, ky1 - ky0), 3),
            },
            "birim": self.birim,
        }

    # -- guvenli Z vektorleri ---------------------------------------------
    def vektorler(self, guvenli_z=None):
        """Guvenli Z'ye HER yukselisin kapattigi kesim parcalarini ("vektor")
        sirayla listeler.

        Bir vektor, takimin guvenli yuksekligin ALTINA indigi andan (dalis)
        tekrar guvenli yuksekliğe ciktigi ana kadar olan yoldur; arada
        yapilan butun hareketler (kesim, derinlikte bosta gidis, yaylar) o
        vektore aittir. Vektorler dosyadaki sirayla numaralanir; bu, takimin
        malzemede gercekte hangi sirayla gezdigidir. (Blok = "X-Y iceren G0
        ile baslayan parca" kuralindan farklidir: bir blokta, ornegin kopruler
        icin birden cok yukselis olabilir; her biri ayri vektordur.)

        Guvenli Z once DOSYADAN bulunur (en cok kullanilan "yukari G0 Z"
        hedefi); dosyada yoksa `guvenli_z` argumani, o da yoksa en yuksek Z.
        Koordinatlar dosyanin kendi birimindedir (blok yollariyla ayni).

        Doner: {"guvenli_z", "kaynak", "vektorler": [...], "uyari"}
        """
        if self.artimli:
            return {"guvenli_z": None, "kaynak": None, "vektorler": [],
                    "uyari": "G91 (artimli) iceren dosyada vektor siralamasi "
                             "cikarilamaz."}
        # -- 1) hareketleri topla (modal G90 mutlak)
        x = y = None
        z = None
        g = 0.0
        hareketler = []
        for i, satir in enumerate(self.satirlar):
            w = satir_kelimeleri(satir)
            if not w:
                continue
            if "G" in w and w["G"] in (0.0, 1.0, 2.0, 3.0):
                g = w["G"]
            if not any(e in w for e in ("X", "Y", "Z")):
                continue
            nx = w.get("X", x if x is not None else 0.0)
            ny = w.get("Y", y if y is not None else 0.0)
            nz = w.get("Z", z)
            if x is None:                      # ilk konum bilinmiyor: sabitle
                x, y = nx, ny
            if z is None:
                z = nz if nz is not None else 0.0
                if nz is None:
                    nz = z
            ark = None
            if g in (2.0, 3.0) and ("I" in w or "J" in w or "R" in w):
                ark = (w, g)
            hareketler.append((i, g, x, y, z, nx, ny, nz, ark, "Z" in w))
            x, y, z = nx, ny, nz
        if not hareketler:
            return {"guvenli_z": None, "kaynak": None, "vektorler": [],
                    "uyari": "Dosyada hareket yok."}

        # -- 2) guvenli Z
        # (a) dosyanin ILK "G0 Z..." hedefi: program hep once buraya cikar
        #     (kisa "kopru" yukselisleri guvenli Z sayilmaz).
        # (b) yoksa en cok tekrarlanan (>=2) en yuksek G0 yukselis hedefi.
        zs = None
        for h in hareketler:
            if h[1] != 0.0:
                break                              # ilk kesimden sonrasi sayilmaz
            if h[9]:
                zs = round(h[7], 3)
                break
        kaynak = "dosya"
        if zs is None:
            yukselis = {}
            for h in hareketler:
                if h[1] == 0.0 and h[7] > h[4] + 1e-6:
                    k = round(h[7], 3)
                    yukselis[k] = yukselis.get(k, 0) + 1
            tekrar = [k for k, n in yukselis.items() if n >= 2]
            if tekrar:
                zs = max(tekrar)
            elif yukselis:
                zs = max(yukselis)
        if zs is not None:
            pass
        elif guvenli_z is not None:
            zs, kaynak = float(guvenli_z), "ayar"
        else:
            zs, kaynak = max(h[7] for h in hareketler), "en_yuksek"
        tol = 1e-3

        def yukarida(zv):
            return zv >= zs - tol

        # -- 3) vektorleri kes
        blok_baslari, ofs = [], len(self.header)
        for b in self.bloklar:
            blok_baslari.append(ofs)
            ofs += len(b)

        def blok_no(satir_i):
            if not blok_baslari or satir_i < blok_baslari[0]:
                return None
            idx = 0
            for k, bs in enumerate(blok_baslari):
                if bs <= satir_i:
                    idx = k
                else:
                    break
            return idx if satir_i < ofs else None

        def hareket_noktalari(h):
            (_, _g, x0, y0, _z0, x1, y1, _z1, ark, _zw) = h
            if ark:
                w, gg = ark
                return _yay_noktalari(x0, y0, x1, y1, w, gg, bolut=18)
            return [(x1, y1)]

        def hareket_uzunlugu(h):
            (_, _g, x0, y0, z0, x1, y1, z1, ark, _zw) = h
            if ark:
                w, gg = ark
                return yay_uzunlugu(x0, y0, x1, y1, z1 - z0, w, gg)
            return math.sqrt((x1 - x0) ** 2 + (y1 - y0) ** 2 + (z1 - z0) ** 2)

        vektorler, cur = [], None

        def baslat(h):
            (i, _g, x0, y0, z0, x1, y1, z1, _a, _zw) = h
            return {"bas": (x0, y0), "pts": [(x0, y0)], "uz": 0.0,
                    "zmin": min(z0, z1), "satir_bas": i, "satir_son": i}

        def ekle(v, h):
            v["pts"] += hareket_noktalari(h)
            v["uz"] += hareket_uzunlugu(h)
            v["zmin"] = min(v["zmin"], h[4], h[7])
            v["satir_son"] = h[0]

        def bitir(v, son_xy):
            pts = v["pts"]
            xs = [q[0] for q in pts]
            ys = [q[1] for q in pts]
            yon = None
            for q in pts[1:]:
                dx, dy = q[0] - pts[0][0], q[1] - pts[0][1]
                d = math.hypot(dx, dy)
                if d > 1e-6:
                    yon = [round(dx / d, 4), round(dy / d, 4)]
                    break
            vektorler.append({
                "no": len(vektorler) + 1,
                "bas": [round(v["bas"][0], 3), round(v["bas"][1], 3)],
                "son": [round(son_xy[0], 3), round(son_xy[1], 3)],
                "yon": yon,
                "uzunluk": round(v["uz"], 2),
                "z_min": round(v["zmin"], 3),
                "bbox": [round(min(xs), 3), round(min(ys), 3),
                         round(max(xs), 3), round(max(ys), 3)],
                "satir_bas": v["satir_bas"] + 1,       # 1 tabanli dosya satiri
                "satir_son": v["satir_son"] + 1,
                "blok": blok_no(v["satir_bas"]),
                "nokta": len(pts),
            })

        for h in hareketler:
            (_i, _g, x0, y0, z0, x1, y1, z1, _a, _zw) = h
            sonda_yukari = yukarida(z1)
            if cur is None:
                if not sonda_yukari:               # dalis (ya da asagida baslangic)
                    cur = baslat(h)
                    ekle(cur, h)
            else:
                if sonda_yukari:                   # guvenli Z'ye yukselis
                    # yukselirken XY de degisiyorsa kesim, hareketin
                    # BASLADIGI noktada bitmistir
                    bitir(cur, (x0, y0) if (x1, y1) != (x0, y0) else (x1, y1))
                    cur = None
                else:
                    ekle(cur, h)
        if cur is not None:                        # dosya asagida bitti
            bitir(cur, cur["pts"][-1])
        return {"guvenli_z": round(zs, 3), "kaynak": kaynak,
                "vektorler": vektorler, "uyari": ""}

    def satir_araligi(self, blok_index):
        """Blogun dosyadaki satir araligi (1'den baslayan, kapsayici)."""
        onceki = len(self.header) + sum(
            len(b) for b in self.bloklar[:blok_index])
        return (onceki + 1, onceki + len(self.bloklar[blok_index]))

    def modal_baslangic(self, blok_index):
        """Secilen bloga gelene kadar olusan modal durum."""
        onceki = list(self.header)
        for b in self.bloklar[:blok_index]:
            onceki += b
        return modal_durum(onceki)

    # -- bloktan baslatma -------------------------------------------------
    def bloktan_baslat_uret(self, blok_index, guvenli_z=20.0,
                            bitis_blok=None, spindle_bekleme=4.0):
        """`blok_index`ten (0 tabanli) itibaren calisan guvenli program.

        Uretilen satirlar:
          1. Guvenlik acilisi: G90, birim, duzlem, guvenli Z'ye G0 ile cikis.
          2. Modal geri kurma: takim, devir + spindle yonu, sogutma, besleme.
          3. Hedef blogun baslangic XY'sine **havada** (guvenli Z'de) gidis.
          4. Secilen bloklar + orijinal footer (program sonu) aynen.
        """
        if not self.bloklar:
            raise ValueError("Programda kesim blogu yok.")
        if not 0 <= blok_index < len(self.bloklar):
            raise ValueError(f"Blok araligi disinda: {blok_index}")
        if self.artimli:
            raise ValueError(
                "G91 (artimli) iceren programda bloktan baslatma yapilamaz.")

        son = len(self.bloklar) if bitis_blok is None else bitis_blok + 1
        secili = self.bloklar[blok_index:son]
        d = self.modal_baslangic(blok_index)
        hx, hy = blok_bas_xy(secili[0])

        gz = guvenli_z if self.birim == "mm" else round(guvenli_z / _INCH_MM, 4)
        satir = [
            f"({os.path.basename(self.yol)} - blok {blok_index + 1} / "
            f"{len(self.bloklar)} arasindan baslatildi)",
            "(Mach3-Kontrol tarafindan uretilen guvenli yeniden baslatma)",
            d["birim"] or "G21",
            "G90",
            d["duzlem"] or "G17",
            "G40",
            f"G0 Z{gz}",                       # ONCE guvenli yukseklige cik
        ]
        if d["takim"] is not None:
            satir.append(f"T{int(d['takim'])} M6")
        if d["devir"] is not None:
            satir.append(f"S{d['devir']}")
        if d["spindle"]:
            satir.append(d["spindle"])
            satir.append(f"G4 P{max(spindle_bekleme, 0.0)}")
        if d["sogutma"]:
            satir.append(d["sogutma"])
        satir.append(f"G0 X{hx} Y{hy}")        # havada hedef blogun basina
        if d["feed"] is not None:
            satir.append(f"F{d['feed']}")

        govde = [ln for blok in secili for ln in blok]
        # Secilen bolum orijinal programin sonunu icermiyorsa guvenli bir
        # kapanis eklenir (yoksa takim malzemede kalabilir).
        kapanis = list(self.footer) if son >= len(self.bloklar) else [
            f"G0 Z{gz}", "M5", "M9", "M30"]
        return satir + govde + kapanis

    def bloktan_baslat_yaz(self, blok_index, hedef_yol, **kw):
        satirlar = self.bloktan_baslat_uret(blok_index, **kw)
        with open(hedef_yol, "w", encoding="utf-8") as f:
            f.write("\n".join(satirlar) + "\n")
        return hedef_yol
