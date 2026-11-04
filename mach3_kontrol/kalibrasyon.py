#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kalibrasyon
===========
Tezgahi **dijital kumpasla** hassas kalibre etmek icin adim adim yurutulen
olcum oturumlari ve bunlarin matematigi.

NEDEN AYRI BIR MODUL: Mach3'un COM/OLE arayuzunde `Steps per unit` (motor
adim sayisi) ve `Backlash` (bosluk telafisi) degerlerini **okuyan ya da yazan
bir cagri yoktur**. Bu degerler Mach3'un profil dosyasinda tutulur ve
yalnizca Mach3'un kendi pencerelerinden degistirilebilir:

    Config -> Motor Tuning and Setup -> <eksen> -> "Steps per"
    Config -> Backlash               -> <eksen>

Bu yuzden program **olcumu yapar, hesabi yapar ve Mach3'un hangi kutusuna
hangi sayiyi yazacaginizi tam olarak soyler**. Makineyi hareket ettirmek ve
sonucu hesaplamak otomatiktir; son yazma islemi operatordedir.

Olcum yontemi (kumpas icin)
---------------------------
1. **Bosluk alma:** olcum yonunun TERSINE `on_yaklasma` kadar gidilir, sonra
   olcum yonunde ayni kadar geri gelinir. Boylece vida/kayis boslugu olcum
   yonunde toplanmis olur; bosluk, olcek olcumune karismaz.
2. **A okumasi:** kumpas sifirlanir (ya da okunan deger yazilir).
3. **Olcum hareketi:** ayni yonde tam `komut_mm` gidilir.
4. **B okumasi:** kumpas okunur. Gercek mesafe = |B - A|.
5. Adim sayisi orantiyla duzeltilir:

       yeni_adim_basi = eski_adim_basi x (komut / olculen)

   Mach3 `adim_basi x komut` kadar darbe gonderir; olculen mesafe komuttan
   BUYUKSE adim sayisi fazladir ve KUCULTULMELIDIR. Formul bunu saglar.

Bosluk (backlash) olcumu ayni kurulumda yapilir: A okumasindan sonra ayni
noktaya bu kez TERS yonden yaklasilir; iki okuma arasindaki fark, o eksenin
kayip hareketidir.

Bu modul makineye dokunmaz - yalnizca durum makinesi ve matematiktir;
hareketleri `denetleyici` gonderir (ve her adim guvenlik katmanindan gecer).
"""

import statistics
import time

TURLER = ("olcek", "bosluk", "dogrulama")
EKSENLER = ("X", "Y", "Z", "A")

# Oturum asamalari
HAZIR = "hazir"                    # oturum kuruldu, bosluk alma bekleniyor
OKUMA_A = "okuma_a"                # kumpasi sifirla / A okumasini gir
HAREKET = "hareket"                # eksen hareket ediyor (bekleyin)
OKUMA_B = "okuma_b"                # olcum hareketi bitti, B okumasini gir
TEKRAR = "tekrar"                  # olcum kaydedildi, tekrar ya da bitir
BITTI = "bitti"

# Kumpas cozunurlugu 0.01 mm; bir olcumun tekrarlanabilirligi bunun bu kati
# icinde kalmiyorsa sonuc guvenilmez sayilir (kumpas kaymis, parca gevsek...)
YAYILIM_KATI = 3.0

# Dogrulama olcumunde kabul edilen sapma: 100 mm'de 0.02 mm (200 ppm) ya da
# kumpas cozunurlugunun 2 kati - hangisi buyukse.
TOLERANS_ORANI = 0.0002


def _sayi(v, ad):
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{ad} icin gecerli bir sayi girin")
    if f != f or f in (float("inf"), float("-inf")):
        raise ValueError(f"{ad} icin gecerli bir sayi girin")
    return f


# ----------------------------------------------------------------------
# Matematik
# ----------------------------------------------------------------------
def yeni_adim_basi(eski_adim_basi, komut_mm, olculen_mm):
    """Duzeltilmis 'Steps per unit'. Eski deger bilinmiyorsa None.

    Mach3 `eski x komut` darbe gonderip `olculen` kadar yol aldiysa gercek
    adim sayisi `eski x komut / olculen`'dir.
    """
    komut_mm = _sayi(komut_mm, "Komut mesafesi")
    olculen_mm = _sayi(olculen_mm, "Olculen mesafe")
    if olculen_mm <= 0:
        raise ValueError("Olculen mesafe sifirdan buyuk olmali")
    if not eski_adim_basi:
        return None
    eski = _sayi(eski_adim_basi, "Mevcut adim sayisi")
    if eski <= 0:
        return None
    return eski * (komut_mm / olculen_mm)


def carpan(komut_mm, olculen_mm):
    """Mevcut adim sayisinin carpilacagi duzeltme katsayisi."""
    olculen_mm = _sayi(olculen_mm, "Olculen mesafe")
    if olculen_mm <= 0:
        raise ValueError("Olculen mesafe sifirdan buyuk olmali")
    return _sayi(komut_mm, "Komut mesafesi") / olculen_mm


def istatistik(degerler):
    """Olcumlerin ortalamasi ve yayilimi (tekrarlanabilirlik gostergesi)."""
    d = [float(v) for v in degerler]
    if not d:
        return {"adet": 0, "ortalama": None, "en_kucuk": None,
                "en_buyuk": None, "yayilim": None, "std": None}
    return {
        "adet": len(d),
        "ortalama": sum(d) / len(d),
        "en_kucuk": min(d),
        "en_buyuk": max(d),
        "yayilim": max(d) - min(d),
        "std": statistics.pstdev(d) if len(d) > 1 else 0.0,
    }


def tolerans(komut_mm, cozunurluk=0.01):
    """Dogrulama olcumu icin kabul edilebilir sapma (mm)."""
    return max(abs(float(komut_mm)) * TOLERANS_ORANI, float(cozunurluk) * 2.0)


# ----------------------------------------------------------------------
# Mach3'e girilecek degerler
# ----------------------------------------------------------------------
# Bu fonksiyonun tek isi, "elimde sonuc var, simdi Mach3'te NEREYE NE
# yazacagim?" sorusunu bosluk birakmadan yanitlamaktir.
def mach3_talimat(sonuc):
    """Sonuctan, Mach3 pencerelerinde elle girilecek degerleri uretir."""
    if not sonuc:
        return []
    eksen = sonuc.get("eksen", "?")
    tur = sonuc.get("tur")

    if tur == "olcek":
        yeni = sonuc.get("yeni_adim_basi")
        eski = sonuc.get("eski_adim_basi")
        notlar = [
            "Sadece 'Steps per' kutusu degisir; Velocity ve Acceleration "
            "degerlerine DOKUNMAYIN.",
            "Degeri yazdiktan sonra mutlaka <SAVE AXIS SETTINGS> dugmesine "
            "basin; basmadan pencereyi kapatirsaniz deger kaybolur.",
        ]
        if yeni is None:
            notlar.insert(0, (
                "Mevcut 'Steps per' degerini bilmiyoruz. Mach3'teki degeri "
                f"{sonuc['carpan']:.6f} ile carpip sonucu ayni kutuya yazin "
                "(ve degeri Kalibrasyon sekmesine de kaydedin)."))
        return [{
            "pencere": "Config → Motor Tuning and Setup",
            "yer": f"{eksen} Axis",
            "alanlar": [{
                "ad": "Steps per",
                "eski": None if eski is None else round(eski, 4),
                "yeni": None if yeni is None else round(yeni, 4),
                "carpan": round(sonuc["carpan"], 6),
            }],
            "notlar": notlar,
        }]

    if tur == "bosluk":
        b = sonuc.get("bosluk_mm")
        return [{
            "pencere": "Config → Backlash",
            "yer": f"{eksen} ekseni",
            "alanlar": [
                {"ad": f"{eksen} Backlash", "eski": None,
                 "yeni": None if b is None else round(b, 4), "carpan": None},
                {"ad": "Backlash Enabled", "eski": None, "yeni": "isaretli",
                 "carpan": None},
            ],
            "notlar": [
                "Deger MM cinsindendir (Mach3 birimi inc ise once mm'ye "
                "gecin: Config → Select Native Units).",
                "Bosluk telafisi hizli hareketlerde ve CV (Constant "
                "Velocity) modunda tam calismayabilir; ayardan sonra "
                "'Dogrulama' olcumu yapin.",
                "Mach3 surumune gore pencerenin adi 'Backlash "
                "Configuration' olabilir.",
            ],
        }]

    if tur == "dogrulama":
        return [{
            "pencere": "—",
            "yer": f"{eksen} ekseni",
            "alanlar": [],
            "notlar": ([
                "Sapma tolerans icinde: Mach3'te degistirilecek bir sey yok."
            ] if sonuc.get("gecti") else [
                "Sapma hala tolerans disinda. 'Olcek' kalibrasyonunu "
                "tekrarlayin; onceki degeri Mach3'e yazmayi ya da SAVE AXIS "
                "SETTINGS'e basmayi atlamis olabilirsiniz.",
            ]),
        }]
    return []


def talimat_metni(sonuc):
    """Talimatlari panoya kopyalanabilir duz metne cevirir."""
    satirlar = []
    for t in mach3_talimat(sonuc):
        satirlar.append(f"{t['pencere']}  →  {t['yer']}")
        for a in t["alanlar"]:
            if a.get("eski") is not None and a.get("yeni") is not None:
                satirlar.append(f"    {a['ad']}: {a['eski']}  ->  {a['yeni']}")
            elif a.get("yeni") is not None:
                satirlar.append(f"    {a['ad']}: {a['yeni']}")
        for n in t["notlar"]:
            satirlar.append(f"    * {n}")
    return "\n".join(satirlar)


# ----------------------------------------------------------------------
# Olcum oturumu (durum makinesi)
# ----------------------------------------------------------------------
class Oturum:
    """Tek bir eksenin tek bir kalibrasyon turu icin adim adim akisi.

    Makineyi hareket ettirmez; hangi hareketin yapilmasi gerektigini
    `hareket_plani()` ile bildirir, denetleyici de guvenlik katmanindan
    gecirerek gonderir.
    """

    def __init__(self, eksen, tur="olcek", komut_mm=100.0, feed=300.0,
                 yon=1, on_yaklasma=5.0, tekrar=3, adim_basi=None,
                 cozunurluk=0.01):
        eksen = str(eksen).upper()
        if eksen not in EKSENLER:
            raise ValueError(f"Bilinmeyen eksen: {eksen}")
        if tur not in TURLER:
            raise ValueError(f"Bilinmeyen kalibrasyon turu: {tur}")
        self.eksen = eksen
        self.tur = tur
        self.komut_mm = abs(_sayi(komut_mm, "Olcum mesafesi"))
        self.feed = abs(_sayi(feed, "Olcum beslemesi"))
        self.yon = 1 if int(yon) >= 0 else -1
        self.on_yaklasma = abs(_sayi(on_yaklasma, "On yaklasma"))
        self.tekrar = max(1, int(tekrar))
        self.adim_basi = (None if adim_basi in (None, "", 0)
                          else _sayi(adim_basi, "Mevcut adim sayisi"))
        self.cozunurluk = abs(_sayi(cozunurluk, "Kumpas cozunurlugu")) or 0.01

        if self.komut_mm <= 0:
            raise ValueError("Olcum mesafesi sifirdan buyuk olmali")
        if self.feed <= 0:
            raise ValueError("Olcum beslemesi sifirdan buyuk olmali")

        self.asama = HAZIR
        self.olcumler = []            # [{"a":..,"b":..,"olculen":..,"t":..}]
        self.hata = ""
        self.mesgul = False           # hareket surerken True
        self.baslangic_t = time.time()
        self.bitis_t = None

    # -- akis ------------------------------------------------------------
    def hareket_plani(self, ad):
        """Verilen adim icin gonderilecek (eksen, mesafe, feed) listesi."""
        y, d = self.yon, self.on_yaklasma
        if ad == "bosluk_al":
            # Boslugu olcum yonunde topla: once ters yone, sonra geri.
            return [(self.eksen, -y * d, self.feed),
                    (self.eksen, y * d, self.feed)]
        if ad == "olc":
            if self.tur == "bosluk":
                # Ayni noktaya bu kez TERS yonden yaklas: fark = kayip hareket
                return [(self.eksen, y * d, self.feed),
                        (self.eksen, -y * d, self.feed)]
            return [(self.eksen, y * self.komut_mm, self.feed)]
        if ad == "geri":
            if self.tur == "bosluk":
                return [(self.eksen, -y * d, self.feed),
                        (self.eksen, y * d, self.feed)]
            # Baslangica don ve boslugu tekrar olcum yonunde topla
            return [(self.eksen, -y * (self.komut_mm + d), self.feed),
                    (self.eksen, y * d, self.feed)]
        raise ValueError(f"Bilinmeyen kalibrasyon adimi: {ad}")

    def sure_tahmini(self, plan):
        """Plandaki hareketlerin kabaca kac saniye surecegi."""
        return sum(abs(m) / max(f, 1.0) * 60.0 for _, m, f in plan)

    def ekle_olcum(self, olculen=None, a=None, b=None):
        """Kumpas okumasini kaydeder. `olculen` ya da (a, b) verilir."""
        if olculen is None:
            if b is None:
                raise ValueError(
                    "Olculen mesafeyi ya da kumpas okumasini (B) girin")
            # Kumpas sifirlandiysa A okumasi 0'dir; sirf onu yazmak icin
            # operatoru ikinci bir alana zorlamak anlamsiz.
            a = 0.0 if a is None else _sayi(a, "A okumasi")
            b = _sayi(b, "B okumasi")
            olculen = abs(b - a)
        else:
            olculen = abs(_sayi(olculen, "Olculen mesafe"))
        if self.tur != "bosluk" and olculen <= 0:
            raise ValueError(
                "Olculen mesafe sifir olamaz. Kumpasi sifirlamayi ya da "
                "B okumasini girmeyi atladiniz mi?")
        beklenen = (self.on_yaklasma if self.tur == "bosluk" else self.komut_mm)
        if self.tur != "bosluk" and beklenen and olculen > 0:
            oran = olculen / beklenen
            if oran < 0.5 or oran > 2.0:
                raise ValueError(
                    f"Olculen mesafe {olculen:.3f} mm, komut ise "
                    f"{beklenen:.3f} mm. Aradaki fark bir kalibrasyon "
                    "sapmasi icin fazla buyuk: yanlis kutuya yazmis ya da "
                    "kumpasi kaydirmis olabilirsiniz. Olcumu tekrarlayin.")
        if self.tur == "bosluk" and olculen > self.on_yaklasma:
            raise ValueError(
                f"Olculen bosluk {olculen:.3f} mm, yaklasma mesafesinden "
                f"({self.on_yaklasma:.3f} mm) buyuk olamaz. Okumalari "
                "kontrol edin.")
        self.olcumler.append({"a": a, "b": b, "olculen": olculen,
                              "t": time.time()})
        self.asama = TEKRAR if len(self.olcumler) < self.tekrar else BITTI
        if self.asama == BITTI:
            self.bitis_t = time.time()
        return self.olcumler[-1]

    def bitir(self):
        self.asama = BITTI
        self.bitis_t = time.time()

    # -- sonuc -----------------------------------------------------------
    def sonuc(self):
        if not self.olcumler:
            return None
        ist = istatistik([o["olculen"] for o in self.olcumler])
        ort = ist["ortalama"]
        uyarilar = []
        guvenilir = True
        if ist["adet"] > 1 and ist["yayilim"] > self.cozunurluk * YAYILIM_KATI:
            guvenilir = False
            uyarilar.append(
                f"Olcumler arasindaki yayilim {ist['yayilim']:.3f} mm; kumpas "
                f"cozunurlugunun ({self.cozunurluk:g} mm) "
                f"{YAYILIM_KATI:g} katindan fazla. Kumpas ya da baglanti "
                "gevsek olabilir - olcumu tekrarlamadan Mach3'e deger "
                "yazmayin.")
        if ist["adet"] < 2:
            uyarilar.append(
                "Tek olcumle kalibrasyon yapmayin; en az 2-3 tekrar alin.")

        temel = {
            "tur": self.tur, "eksen": self.eksen,
            "komut_mm": round(self.komut_mm, 4),
            "feed": self.feed, "yon": self.yon,
            "olcum_sayisi": ist["adet"],
            "olculen_ort": round(ort, 4),
            "yayilim_mm": round(ist["yayilim"], 4),
            "std_mm": round(ist["std"], 4),
            "olcumler": [round(o["olculen"], 4) for o in self.olcumler],
            "guvenilir": guvenilir,
            "uyarilar": uyarilar,
            "zaman": time.time(),
        }

        if self.tur == "bosluk":
            temel.update({"bosluk_mm": round(ort, 4)})
            if ort < self.cozunurluk:
                temel["uyarilar"].append(
                    f"Olculen bosluk ({ort:.3f} mm) kumpas cozunurlugunun "
                    "altinda; bu eksende olcülebilir bir bosluk yok sayilir. "
                    "Mach3'e 0 yazip telafiyi kapali birakabilirsiniz.")
            temel["talimat"] = mach3_talimat(temel)
            temel["talimat_metni"] = talimat_metni(temel)
            return temel

        k = carpan(self.komut_mm, ort)
        hata_mm = ort - self.komut_mm
        temel.update({
            "carpan": round(k, 6),
            "hata_mm": round(hata_mm, 4),
            "hata_yuzde": round(hata_mm / self.komut_mm * 100.0, 4),
            "hata_m_basina_mm": round(hata_mm / self.komut_mm * 1000.0, 3),
            "tolerans_mm": round(tolerans(self.komut_mm, self.cozunurluk), 4),
        })
        temel["gecti"] = abs(hata_mm) <= temel["tolerans_mm"]

        if self.tur == "olcek":
            temel["eski_adim_basi"] = self.adim_basi
            temel["yeni_adim_basi"] = (
                None if self.adim_basi is None
                else round(yeni_adim_basi(self.adim_basi, self.komut_mm, ort),
                           4))
            if temel["gecti"]:
                temel["uyarilar"].append(
                    f"Sapma zaten tolerans icinde ({hata_mm:+.3f} mm ≤ "
                    f"{temel['tolerans_mm']:.3f} mm). Degeri degistirmeniz "
                    "sart degil.")
        temel["talimat"] = mach3_talimat(temel)
        temel["talimat_metni"] = talimat_metni(temel)
        return temel

    # -- arayuz ----------------------------------------------------------
    def yonerge(self):
        """Operatorun SU AN ne yapmasi gerektigi (arayuzde gosterilir)."""
        e, y = self.eksen, ("+" if self.yon > 0 else "−")
        if self.asama == HAZIR:
            return (f"Kumpasi {e} ekseninde olcum yapacak sekilde sabitleyin. "
                    f"'Bosluk al' dediginizde eksen once {self.on_yaklasma:g} "
                    f"mm ters yone, sonra ayni kadar {y} yonune gidecek; "
                    "boylece vida boslugu olcum yonunde toplanir.")
        if self.asama == HAREKET:
            return "Eksen hareket ediyor — bitmesini bekleyin."
        if self.asama == OKUMA_A:
            return ("Kumpasi SIFIRLAYIN (ya da su anki okumasini A alanina "
                    "yazin). Kumpas ile parca arasinda oynama olmadigindan "
                    "emin olun.")
        if self.asama == OKUMA_B:
            if self.tur == "bosluk":
                return ("Ayni noktaya ters yonden gelindi. Kumpasin YENI "
                        "okumasini girin: sifirdan sapma, bu eksenin "
                        "boslugudur.")
            return (f"Eksen {y} yonunde {self.komut_mm:g} mm gitti. Kumpasin "
                    "yeni okumasini (B) girin; program gercek mesafeyi ve "
                    "duzeltmeyi hesaplayacak.")
        if self.asama == TEKRAR:
            kalan = self.tekrar - len(self.olcumler)
            return (f"{len(self.olcumler)}/{self.tekrar} olcum alindi. "
                    f"{kalan} tekrar kaldi: 'Basa don ve tekrarla' ile ayni "
                    "olcumu yineleyin ya da 'Bitir' deyip sonuca gecin.")
        if self.asama == BITTI:
            return ("Olcum tamam. Asagidaki degerleri Mach3'te ilgili "
                    "pencereye yazin, sonra 'Mach3'e girdim' deyin.")
        return ""

    def durum(self):
        return {
            "aktif": self.asama != BITTI,
            "eksen": self.eksen, "tur": self.tur,
            "asama": self.asama, "mesgul": self.mesgul,
            "yonerge": self.yonerge(),
            "komut_mm": self.komut_mm, "feed": self.feed, "yon": self.yon,
            "on_yaklasma": self.on_yaklasma,
            "tekrar": self.tekrar, "alinan": len(self.olcumler),
            "adim_basi": self.adim_basi,
            "cozunurluk": self.cozunurluk,
            "olcumler": [round(o["olculen"], 4) for o in self.olcumler],
            "hata": self.hata,
            "sonuc": self.sonuc(),
        }
