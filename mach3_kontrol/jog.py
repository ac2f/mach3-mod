#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jog motoru — Mach3'ün jog modelinin birebir karşılığı
====================================================
Mach3'te jog şöyle çalışır; bu modül aynısını uygular:

* **Jog ON/OFF** (Tab): kapalıyken hiçbir jog tuşu çalışmaz.
* **Continuous (sürekli)**: tuş basılıyken eksen **Slow Jog Rate %** ×
  eksen azami hızıyla gider; bırakınca eksen kendi ivmesiyle **yavaşlayarak**
  durur. **Shift** basılıyken hız %100 olur (canlı: basılıyken Shift'e
  basmak ya da bırakmak jogu kesmeden hızı değiştirir).
* **Step (adım)**: bir basış = tam olarak **Step** mesafesi (aynı hız kuralı).
  Tuşu basılı tutmak tekrar etmez. Hareket sürerken yapılan basışlar sınırlı
  bir tampona (Mach3 profilindeki ``IncBuffer``, varsayılan 5) alınır.
* **Yumuşak limit**: eksen limite **yavaşlayarak varır** ve orada durur.

İki sürücü
----------
``artimli`` (varsayılan, her Mach3'te çalışır)
    Mach3'e MDI ile kısa **mutlak** hedefler zincirlenir. Zincir hep, eksenin
    durma mesafesinden uzun bir yol kuyrukta tutar (aksi halde Mach3'ün
    planlayıcısı hedef hıza hiç ulaşamaz ve eksen "tik tik" gider). Bırakınca
    Feed Hold → hareket durunca Stop: yavaşlama Mach3'ün kendi ivmesiyle olur,
    yani Mach3'ün kendi tuş bırakışıyla aynı **durma mesafesi** elde edilir.
    Mutlak hedef kullanıldığı için G91 gibi kalıcı bir kip bırakılmaz.

``mach3`` (isteğe bağlı)
    Mach3'ün **kendi jog motoru**: COM'daki ``JogOn(eksen, yön)`` /
    ``JogOff(eksen)`` (imzalar Mach3.exe'nin dispatch map'inden okundu). Jog
    kipi, ivme ve limit davranışı tamamen Mach3'ündür. ``JogOn``'un yön
    değerinin hangi işarete karşılık geldiği eksene göre farklı olabilir
    (X ters kodlanmış görünüyor), bu yüzden **Jog doğrulama** ile ÖĞRENİLİR
    ve yalnızca doğrulama başarılıysa kullanılır. Step kipi her zaman
    ``artimli`` sürücüyle yapılır.

Güvenlik (iki sürücüde de)
--------------------------
Kalp atışı (istemci susarsa dur), azami süre, Reset algılama, tezgah
referansı/limit/taban menzili, program çalışırken jog yasağı, mm birimi
zorlaması. **Hiçbir şey donanım acil-stop'un yerini tutmaz.**
"""

import threading
import time

from . import guvenlik as GUV
from .baglanti import EKSEN_NO

EKSENLER = ("X", "Y", "Z", "A")
MODLAR = ("surekli", "adim")
SINIR_PAYI = 0.5          # mm: limite bu kadar kala dur


class JogMotoru:
    def __init__(self, den):
        self.den = den
        self._k = threading.RLock()
        self._is = None
        self._dur = threading.Event()
        self._bitti = threading.Event()
        self._bitti.set()
        self._aktif = None            # calisan surekli jog
        self._hizli = False
        self._sabit_feed = None       # feed acikca verildiyse (uyumluluk)
        self._sebep = ""              # son durma sebebi (arayuz icin)
        self._adim_hedef = {}         # Step: eksen -> kuyruk sonu (is koord.)
        self._adim_say = 0
        self._uyari_yerel = False

    # ==================================================================
    # Ayarlar / model
    # ==================================================================
    @property
    def ay(self):
        return self.den.ayarlar["jog"]

    def _vmax(self, eksen):
        """Eksen azami hizi (mm/dk) = Mach3 Motor Tuning 'Velocity'."""
        hiz = self.den.ayarlar["makine"].get("hizli_hiz") or {}
        return max(float(hiz.get(eksen, 3000.0 if eksen in ("Z", "A")
                                  else 8000.0)), 1.0)

    def _ivme(self, eksen):
        iv = self.den.ayarlar["makine"].get("ivme") or {}
        return max(float(iv.get(eksen, 250.0 if eksen in ("Z", "A")
                               else 400.0)), 1.0)

    def hiz_mm_dk(self, eksen, hizli=False):
        """Jog hizi: yuzde x eksen azami hizi (Mach3'teki gibi), sinirli."""
        ay = self.ay
        yuzde = float(ay.get("hizli_yuzde", 100.0) if hizli
                      else ay.get("yavas_yuzde", 20.0))
        f = yuzde / 100.0 * self._vmax(eksen)
        f = min(f, float(self.den.ayarlar["guvenlik"].get("max_jog_feed",
                                                          6000.0)))
        return max(f, 1.0)

    def _feed(self, eksen):
        if self._sabit_feed:
            return min(self._sabit_feed, float(
                self.den.ayarlar["guvenlik"].get("max_jog_feed", 6000.0)))
        return self.hiz_mm_dk(eksen, self._hizli)

    def durum(self):
        ay = self.ay
        a = self._aktif
        b = self.den.baglanti
        eksenler = self.den.ayarlar["makine"].get("eksenler", ["X", "Y", "Z"])
        return {
            "aktif": bool(a),
            "eksen": a["eksen"] if a else None,
            "yon": a["yon"] if a else 0,
            "hizli": bool(self._hizli) if a else False,
            "feed": self._feed(a["eksen"]) if a else None,
            "surucu": a["surucu"] if a else None,
            "acik": bool(ay.get("acik", True)),
            "mod": ay.get("mod", "surekli"),
            "yavas_yuzde": float(ay.get("yavas_yuzde", 20.0)),
            "hizli_yuzde": float(ay.get("hizli_yuzde", 100.0)),
            "adim": float(ay.get("adim", 1.0)),
            "adim_listesi": list(ay.get("adim_listesi") or []),
            "adim_kuyrugu": int(ay.get("adim_kuyrugu", 5)),
            "tercih_surucu": ay.get("surucu", "artimli"),
            "mach3_dogrulandi": bool(ay.get("mach3_dogrulandi")),
            "yerel_var": bool(b and getattr(b, "destekler", None)
                              and b.destekler("JogOn")
                              and b.destekler("JogOff")),
            "hiz": {"yavas": {e: round(self.hiz_mm_dk(e, False), 1)
                              for e in eksenler},
                    "hizli": {e: round(self.hiz_mm_dk(e, True), 1)
                              for e in eksenler}},
            "son_sebep": self._sebep,
        }

    def ayarla(self, **kw):
        """Jog ayarlarini dogrular ve kaydeder (Mach3'teki duğmelerin karsiligi)."""
        yama = {}
        ay = self.ay
        maks_adim = float(self.den.ayarlar["guvenlik"].get("max_jog_adim",
                                                           100.0))
        if "acik" in kw and kw["acik"] is not None:
            yama["acik"] = bool(kw["acik"])
        if kw.get("mod") is not None:
            if kw["mod"] not in MODLAR:
                raise GUV.GuvenlikHatasi(f"Bilinmeyen jog kipi: {kw['mod']}")
            yama["mod"] = kw["mod"]
        for ad, alt, ust in (("yavas_yuzde", 0.1, 100.0),
                             ("hizli_yuzde", 1.0, 100.0)):
            if kw.get(ad) is not None:
                try:
                    v = float(kw[ad])
                except (TypeError, ValueError):
                    raise GUV.GuvenlikHatasi(f"{ad} sayi olmali")
                if not (alt <= v <= ust):
                    raise GUV.GuvenlikHatasi(
                        f"{ad} {alt:g}..{ust:g} araliginda olmali")
                yama[ad] = round(v, 2)
        if kw.get("adim") is not None:
            try:
                v = float(kw["adim"])
            except (TypeError, ValueError):
                raise GUV.GuvenlikHatasi("adim sayi olmali")
            if v <= 0:
                raise GUV.GuvenlikHatasi("Step mesafesi sifirdan buyuk olmali")
            if v > maks_adim:
                raise GUV.GuvenlikHatasi(
                    f"Step {v:g} mm, izin verilen en buyuk jog adimi "
                    f"{maks_adim:g} mm (Guvenlik sekmesi)")
            yama["adim"] = v
        if kw.get("adim_listesi") is not None:
            try:
                liste = [float(x) for x in kw["adim_listesi"]]
            except (TypeError, ValueError):
                raise GUV.GuvenlikHatasi("adim_listesi sayilardan olusmali")
            liste = [x for x in liste if 0 < x <= maks_adim]
            if not liste:
                raise GUV.GuvenlikHatasi("adim_listesi bos olamaz")
            yama["adim_listesi"] = liste
        if kw.get("surucu") is not None:
            if kw["surucu"] not in ("artimli", "mach3"):
                raise GUV.GuvenlikHatasi("surucu 'artimli' ya da 'mach3' olmali")
            yama["surucu"] = kw["surucu"]
        if kw.get("adim_kuyrugu") is not None:
            yama["adim_kuyrugu"] = max(1, min(int(kw["adim_kuyrugu"]), 20))
        if not yama:
            return self.durum()
        # kip degisirken ya da jog kapanirken suren jog durdurulur
        if (("mod" in yama and yama["mod"] != ay.get("mod"))
                or yama.get("acik") is False):
            self.durdur("jog ayari degisti")
            self.bekle(2.5)
        self.den.ayarlar.guncelle({"jog": yama})
        self.den.gunluk.ekle("jog", "Jog ayari: " + ", ".join(
            f"{k}={v}" for k, v in yama.items() if k != "adim_listesi"))
        return self.durum()

    def adim_sonraki(self, yon=1):
        """Mach3'teki 'Cycle Jog Step': listedeki sonraki degere gec."""
        liste = list(self.ay.get("adim_listesi") or [1.0])
        mevcut = float(self.ay.get("adim", liste[0]))
        try:
            i = min(range(len(liste)), key=lambda k: abs(liste[k] - mevcut))
        except ValueError:
            i = 0
        return self.ayarla(adim=liste[(i + (1 if yon >= 0 else -1)) % len(liste)])

    # ==================================================================
    # Sürücü seçimi
    # ==================================================================
    def _surucu_sec(self, eksen):
        """Surekli jog icin 'mach3' mi 'artimli' mi? (sebep metniyle)"""
        ay = self.ay
        if ay.get("surucu") != "mach3":
            return "artimli", ""
        b = self.den.baglanti
        if not (b and getattr(b, "destekler", None) and b.destekler("JogOn")
                and b.destekler("JogOff")):
            return "artimli", "Mach3'te JogOn/JogOff yok"
        if not ay.get("mach3_dogrulandi"):
            return "artimli", "Mach3 jog dogrulamasi yapilmadi"
        if not (ay.get("yon_esleme") or {}).get(eksen):
            return "artimli", f"{eksen} icin yon eslemesi ogrenilmedi"
        if self.den.led_degeri("jog_adim"):
            return "artimli", "Mach3 Step kipinde (Continuous gerekli)"
        return "mach3", ""

    # ==================================================================
    # Başlat / bırak
    # ==================================================================
    def basla(self, eksen, yon, feed=None, hizli=None, istemci=None):
        """Jog tusuna BASILDI. Kip Step ise bir adim yapar, Continuous ise
        birakilana kadar gider (Mach3 ile ayni tek giris noktasi)."""
        d = self.den
        d.kilit.dogrula(istemci)
        eksen = str(eksen).upper()
        yon = 1 if yon >= 0 else -1
        if eksen not in EKSENLER or eksen not in \
                d.ayarlar["makine"].get("eksenler", ["X", "Y", "Z"]):
            raise GUV.GuvenlikHatasi(f"Bilinmeyen/etkin olmayan eksen: {eksen}")
        if not self.ay.get("acik", True):
            raise GUV.GuvenlikHatasi(
                "Jog KAPALI (Jog ON/OFF). Acmak icin 'Jog ON'a basin.")
        if d.calisiyor:
            # Program calisirken jog hem anlamsiz hem tehlikelidir; ayrica
            # birakinca gonderilen Feed Hold/Stop programi da keserdi.
            raise GUV.GuvenlikHatasi(
                "Program calisirken jog yapilamaz. Once programi durdurun.")
        d.hareket_hazirla([eksen])
        if self.ay.get("mod") == "adim":
            return self._adim_yap(eksen, yon, feed, bool(hizli))
        return self._surekli_baslat(eksen, yon, feed, bool(hizli))

    def _surekli_baslat(self, eksen, yon, feed, hizli):
        d = self.den
        a = self._aktif
        if a and not self._dur.is_set():
            # Ayni tus icin tekrarlanan istek (ag cift gonderdi): zaten calisiyor
            if a["eksen"] == eksen and a["yon"] == yon:
                return {"eksen": eksen, "yon": yon,
                        "feed": self._feed(eksen), "hizli": self._hizli,
                        "surucu": a["surucu"], "mod": "surekli"}
            raise GUV.GuvenlikHatasi(
                f"Zaten {a['eksen']}{a['yon']:+d} jog ediliyor; once birakin "
                "(ayni anda tek eksen)")
        # onceki jog hala yavasliyorsa bitmesini bekle (tus degisimi)
        if not self._bitti.wait(3.0):
            raise GUV.GuvenlikHatasi("Onceki jog hala duruyor, tekrar deneyin")
        surucu, neden = self._surucu_sec(eksen)
        if neden and self.ay.get("surucu") == "mach3" and not self._uyari_yerel:
            self._uyari_yerel = True
            d.gunluk.ekle("jog", f"Mach3 yerel jog kullanilamadi ({neden}); "
                                 "artimli surucu kullaniliyor")
        with self._k:
            self._dur.clear()
            self._bitti.clear()
            self._hizli = hizli
            self._sabit_feed = float(feed) if feed else None
            self._sebep = ""
            self._aktif = {"eksen": eksen, "yon": yon, "surucu": surucu,
                           "t0": time.time(), "gonderildi": False}
            d.bekci.baslat()
            self._is = threading.Thread(
                target=self._calis, args=(eksen, yon, surucu), daemon=True,
                name="jog")
            self._is.start()
        f = self._feed(eksen)
        d.gunluk.ekle("hareket",
                      f"Surekli jog basladi {eksen}{yon:+d} @F{f:.0f} "
                      f"({self.ay.get('yavas_yuzde'):g}%"
                      f"{' · Shift' if hizli else ''}, {surucu})")
        return {"eksen": eksen, "yon": yon, "feed": f, "hizli": hizli,
                "surucu": surucu, "mod": "surekli"}

    def hiz(self, hizli=None, feed=None, istemci=None):
        """Basili tutarken Shift: jogu KESMEDEN hizi degistirir."""
        self.den.kilit.dogrula(istemci)
        if hizli is not None:
            self._hizli = bool(hizli)
        if feed:
            self._sabit_feed = float(feed)
        a = self._aktif
        return {"feed": self._feed(a["eksen"]) if a else None,
                "hizli": self._hizli}

    def kalp(self, istemci=None):
        self.den.kilit.dogrula(istemci)
        return {"aktif": self.den.bekci.dokun()}

    def birak(self):
        """Jog tusu BIRAKILDI (Continuous: yavaslayarak dur; Step: etkisiz)."""
        self._dur.set()
        return True

    def durdur(self, sebep="durduruldu"):
        """Her kosulda cagrilabilir; beklemez."""
        if self._aktif and not self._sebep:
            self._sebep = sebep
        self._dur.set()
        self.den.bekci.bitir()

    def bekle(self, zaman_asimi=3.0):
        return self._bitti.wait(zaman_asimi)

    # ==================================================================
    # Step kipi: bir basis = tam Step mesafesi
    # ==================================================================
    def _adim_yap(self, eksen, yon, feed, hizli):
        d = self.den
        ay = self.ay
        mesafe = float(ay.get("adim", 1.0))
        if mesafe <= 0:
            raise GUV.GuvenlikHatasi("Step mesafesi sifirdan buyuk olmali")
        f = float(feed) if feed else self.hiz_mm_dk(eksen, hizli)
        f = min(f, float(d.ayarlar["guvenlik"].get("max_jog_feed", 6000.0)))
        o = d.oku_simdi()
        akt = o["konum"].get(eksen)
        if akt is None:
            raise GUV.GuvenlikHatasi(f"{eksen} konumu okunamiyor")
        with self._k:
            hareketli = bool(o.get("hareket"))
            if not hareketli:
                self._adim_hedef.clear()
                self._adim_say = 0
            tampon = int(ay.get("adim_kuyrugu", 5))
            if hareketli and self._adim_say >= tampon:
                return {"eksen": eksen, "yon": yon, "yoksayildi": True,
                        "sebep": "adim tamponu dolu"}
            taban = self._adim_hedef.get(eksen, akt) if hareketli else akt
            hedef = taban + yon * mesafe
            # Guvenlik: kuyruk SONUNDAN itibaren denetle (okunan konumdan
            # degil) - ardisik basislar toplamda limiti asmasin.
            fark = taban - akt
            tez = dict(o["tezgah"])
            kon = dict(o["konum"])
            if tez.get(eksen) is not None:
                tez[eksen] += fark
            kon[eksen] = taban
            d.guvenlik.jog_dogrula(eksen, yon * mesafe, f, tez, kon)
            d._mdi(f"G90 G1 {eksen}{hedef:.4f} F{f:.1f}")
            self._adim_hedef[eksen] = hedef
            self._adim_say += 1
        d.gunluk.ekle("hareket", f"Step jog {eksen}{yon * mesafe:+g} mm "
                                 f"@F{f:.0f}")
        return {"eksen": eksen, "yon": yon, "mesafe": mesafe, "feed": f,
                "mod": "adim", "hedef": hedef}

    # ==================================================================
    # Sürekli jog: iş parçacığı
    # ==================================================================
    def _calis(self, eksen, yon, surucu):
        sebep = "birakildi"
        ctx = {"gonderildi": False}
        kayit = []                       # durdurmadan SONRA yazilacak olaylar
        try:
            if surucu == "mach3":
                sebep = self._yerel_dongu(eksen, yon, ctx)
            else:
                sebep = self._artimli_dongu(eksen, yon, ctx)
        except GUV.GuvenlikHatasi as e:
            sebep = "guvenlik: " + "; ".join(e.sebepler)
            kayit.append(("guvenlik", f"Jog durduruldu: {sebep}"))
        except Exception as e:                        # noqa: BLE001
            sebep = f"hata: {type(e).__name__}: {e}"
            kayit.append(("hata", f"Jog hatasi: {sebep}"))
        finally:
            try:
                # 1) HAREKETI DURDUR - diske/ayara dokunmadan
                self._bitir(eksen, surucu, ctx)
            finally:
                if not self._sebep:
                    self._sebep = sebep
                self._aktif = None
                self.den.bekci.bitir()
                self._bitti.set()
        # 2) Ancak simdi kalici islemler
        if ctx.get("ters_yon"):
            self.den.ayarlar.guncelle({"jog": {"mach3_dogrulandi": False}})
            kayit.append(("hata", f"{eksen} jog YONU TERS: Mach3 yerel jog "
                          "dogrulamasi gecersiz kilindi; 'Jog dogrulama'yi "
                          "yeniden calistirin"))
        for tur, mesaj in kayit:
            self.den.gunluk.ekle(tur, mesaj)

    # -- artimli (MDI zinciri) ---------------------------------------------
    def _artimli_dongu(self, eksen, yon, ctx):
        d = self.den
        ay = self.ay
        tik = max(float(ay.get("tik_ms", 40)), 15.0) / 1000.0
        ileri_sn = max(float(ay.get("ileri_sn", 0.3)), 0.1)
        a = self._ivme(eksen)
        komut = None            # Mach3 kuyrugunun SONU (is koordinati)
        sinirda = False
        son_gonderim = 0.0
        ilk = True
        while not self._dur.is_set():
            if not d.bekci.aktif:
                return "kalp atisi kesildi / sure siniri"
            o = d.oku_simdi()
            if o.get("estop"):
                return "Mach3 RESET (E-Stop)"
            akt = o["konum"].get(eksen)
            if akt is None:
                return "konum okunamadi"
            if komut is None:
                komut = akt
            f = self._feed(eksen)
            v = f / 60.0
            menzil, m_sebep = d.guvenlik.jog_menzil(eksen, yon, o["tezgah"],
                                                    o["konum"])
            sinir = (None if menzil is None
                     else akt + yon * max(menzil - SINIR_PAYI, 0.0))
            ileride = (komut - akt) * yon
            # Planlayici hedef hiza ulasabilsin diye kuyrukta en az durma
            # mesafesinden fazla yol bulunmali.
            lead = max(v * ileri_sn, 1.2 * v * v / (2.0 * a) + 2.0 * v * tik)

            if not sinirda and ileride < lead:
                hedef = komut + yon * (lead - ileride)
                if sinir is not None and (hedef - sinir) * yon > 0:
                    hedef = sinir
                if (hedef - komut) * yon > 1e-4:
                    onek = "G64 " if (ilk and d.led_degeri("cv") is False) \
                        else ""
                    d._mdi(f"{onek}G90 G1 {eksen}{hedef:.4f} F{f:.1f}")
                    ilk = False
                    komut = hedef
                    ctx["gonderildi"] = True
                    son_gonderim = time.time()
                if sinir is not None and (sinir - komut) * yon <= 1e-4:
                    sinirda = True
                    ctx["limit"] = m_sebep
            if sinirda:
                # Kuyruk sonu limitte: Mach3 orada yavaslayip durur. Hic
                # komut gitmediyse hemen, gittiyse hareket bitince cik.
                if not ctx["gonderildi"]:
                    return f"limit: {m_sebep}"
                if time.time() - son_gonderim > 0.3 and not o.get("hareket"):
                    return f"limit: {m_sebep}"
            self._dur.wait(tik)
        return "birakildi"

    # -- Mach3 yerel jog (JogOn / JogOff) ------------------------------------
    def _yuzde_yaz(self, hizli):
        d = self.den
        yuzde = float(self.ay.get("hizli_yuzde", 100.0) if hizli
                      else self.ay.get("yavas_yuzde", 20.0))
        kod = d.kodlar.dro.get("yavas_jog")
        try:
            d.baglanti.dro_yaz(kod, yuzde)
        except Exception as e:                        # noqa: BLE001
            d.gunluk.ekle("hata", f"Slow Jog Rate yazilamadi: {e}")

    def _yerel_dongu(self, eksen, yon, ctx):
        d = self.den
        b = d.baglanti
        ay = self.ay
        esl = (ay.get("yon_esleme") or {}).get(eksen) or {}
        deger = esl.get(str(yon))
        if deger is None:
            raise GUV.GuvenlikHatasi(
                f"{eksen}{yon:+d} icin JogOn yon degeri ogrenilmemis")
        tik = max(float(ay.get("tik_ms", 40)), 15.0) / 1000.0
        a = self._ivme(eksen)
        no = EKSEN_NO[eksen]
        o = d.oku_simdi()
        p0 = o["konum"].get(eksen)
        if p0 is None:
            raise GUV.GuvenlikHatasi(f"{eksen} konumu okunamiyor")
        menzil, m_sebep = d.guvenlik.jog_menzil(eksen, yon, o["tezgah"],
                                                o["konum"])
        if menzil is not None and menzil <= SINIR_PAYI:
            return f"limit: {m_sebep}"
        eski_hizli = self._hizli
        self._yuzde_yaz(eski_hizli)
        b.jog_ac(no, int(deger))
        ctx["gonderildi"] = True
        ctx["yerel"] = no
        t0 = time.time()
        yon_kontrol = False
        while not self._dur.is_set():
            if not d.bekci.aktif:
                return "kalp atisi kesildi / sure siniri"
            if self._hizli != eski_hizli:                  # canli Shift
                eski_hizli = self._hizli
                self._yuzde_yaz(eski_hizli)
            o = d.oku_simdi()
            if o.get("estop"):
                return "Mach3 RESET (E-Stop)"
            akt = o["konum"].get(eksen)
            if akt is None:
                return "konum okunamadi"
            # YON DOGRULAMA: yanlis yone gidiyorsak HEMEN kes. Ogrenilen esleme
            # gercek makinede tersse hareket ilk 0.3 mm'de yakalanir.
            if not yon_kontrol:
                ilerleme = (akt - p0) * yon
                if ilerleme > 0.3:
                    yon_kontrol = True
                elif ilerleme < -0.3:
                    ctx["ters_yon"] = True       # kayit durdurmadan SONRA
                    raise GUV.GuvenlikHatasi(
                        f"{eksen} ekseni istenenin TERS yonune gitti; Mach3 "
                        "yerel jog dogrulamasi iptal edildi")
                elif time.time() - t0 > 2.0:
                    yon_kontrol = True
            v = self._feed(eksen) / 60.0
            if menzil is not None:
                kalan, m_sebep = d.guvenlik.jog_menzil(
                    eksen, yon, o["tezgah"], o["konum"])
                fren = v * v / (2.0 * a) + v * (tik + 0.08) + SINIR_PAYI
                if kalan is not None and kalan <= fren:
                    return f"limit: {m_sebep}"
            self._dur.wait(tik)
        return "birakildi"

    # -- ortak bitirme --------------------------------------------------------
    def _bitir(self, eksen, surucu, ctx):
        """Hareketi YAVASLAYARAK durdur ve kuyrugu bosalt."""
        d = self.den
        b = d.baglanti
        if not (b and getattr(b, "bagli", False)):
            return
        bekleme = float(self.ay.get("durma_beklemesi_sn", 1.5))
        acil = getattr(b, "dugme_acil", b.dugme)
        try:
            if surucu == "mach3" and ctx.get("yerel") is not None:
                try:
                    b.jog_kapat(ctx["yerel"])            # Mach3 kendi rampasi
                except Exception as e:                   # noqa: BLE001
                    d.gunluk.ekle("hata", f"JogOff basarisiz: {e}")
                    acil(d.kodlar.buton["feed_hold"])
                self._hareketin_bitmesini_bekle(bekleme)
                if self._hareket_var():
                    acil(d.kodlar.buton["feed_hold"])
                    self._hareketin_bitmesini_bekle(0.8)
                    acil(d.kodlar.buton["stop"])
            elif ctx.get("gonderildi"):
                # Feed Hold: Mach3'un kendi yavaslama rampasi (adim kaybi yok)
                acil(d.kodlar.buton["feed_hold"])
                self._hareketin_bitmesini_bekle(bekleme)
                # Stop: kuyrukta kalan hedefleri at, hold durumundan cik
                acil(d.kodlar.buton["stop"])
        except Exception as e:                           # noqa: BLE001
            d.gunluk.ekle("hata", f"Jog durdurma: {e}")
        try:
            d.oku_simdi()
        except Exception:                                # noqa: BLE001
            pass

    def _hareket_var(self):
        try:
            return bool(self.den.baglanti.hareket_ediyor())
        except Exception:                                # noqa: BLE001
            return False

    def _hareketin_bitmesini_bekle(self, sn):
        bitis = time.time() + sn
        while time.time() < bitis:
            try:
                h = self.den.baglanti.hareket_ediyor()
            except Exception:                            # noqa: BLE001
                return
            if h is False or h is None:
                return
            time.sleep(0.03)

    # ==================================================================
    # Mach3 yerel jog doğrulaması (kullanıcı onayıyla, sınırlı hareket)
    # ==================================================================
    def yerel_dogrula(self, eksenler=("X", "Y", "Z"), istemci=None):
        """JogOn'un yon degerlerini her eksen icin OGRENIR.

        Neden gerekli: ``JogOn(eksen, yon)`` icin hangi degerin + yonu verdigi
        belgelenmemis, ustelik X eksenine ozel bir ters cevirme var. Bu test
        her eksende iki degeri de dener, DRO'nun hangi yone gittigine bakar
        ve esleme tablosunu kaydeder.

        Sinirlar: Slow Jog Rate once %5'e indirilir (geri okunup dogrulanir,
        yazilamiyorsa HIC hareket edilmez); her deneme ~0.35 sn surer; eksen
        iki yonde de en az 6 mm bosluga sahip olmalidir.
        """
        d = self.den
        d.kilit.dogrula(istemci)
        b = d._gerekli()
        if not (b.destekler("JogOn") and b.destekler("JogOff")):
            raise GUV.GuvenlikHatasi("Bu Mach3 surumunde JogOn/JogOff yok")
        if self._aktif or d.calisiyor:
            raise GUV.GuvenlikHatasi("Jog ya da program sururken test yapilamaz")
        eksenler = [e.upper() for e in eksenler]
        d.hareket_hazirla(eksenler)
        if d.led_degeri("jog_adim"):
            raise GUV.GuvenlikHatasi(
                "Mach3 Step jog kipinde. Mach3'te Jog Mode dugmesiyle "
                "Continuous'a alin, sonra testi tekrarlayin.")
        kod = d.kodlar.dro.get("yavas_jog")
        eski = None
        try:
            eski = float(b.dro(kod))
        except Exception:                                # noqa: BLE001
            pass
        TEST = 5.0
        b.dro_yaz(kod, TEST)
        time.sleep(0.25)
        try:
            okunan = float(b.dro(kod))
        except Exception:                                # noqa: BLE001
            okunan = None
        if okunan is None or abs(okunan - TEST) > 0.6:
            raise GUV.GuvenlikHatasi(
                "Slow Jog Rate yazilamadi/okunamadi "
                f"(okunan: {okunan}). Mach3 Reset'te olabilir ya da OEM DRO "
                f"{kod} bu profilde baska bir sey. Guvenlik icin HIC hareket "
                "yapilmadi.")
        yeni = dict(self.ay.get("yon_esleme") or {})
        sonuc = {}
        try:
            for e in eksenler:
                if e not in EKSEN_NO:
                    continue
                no = EKSEN_NO[e]
                o = d.oku_simdi()
                for y in (1, -1):
                    m, ms = d.guvenlik.jog_menzil(e, y, o["tezgah"], o["konum"])
                    if m is not None and m < 6.0:
                        raise GUV.GuvenlikHatasi(
                            f"{e} ekseninde {y:+d} yonde yeterli bosluk yok "
                            f"({m:.1f} mm, {ms}); en az 6 mm gerekli. Ekseni "
                            "sinirlardan uzaklastirin.")
                bulunan = {}
                for deger in (0, 1):
                    o = d.oku_simdi()
                    p0 = o["konum"].get(e)
                    b.jog_ac(no, deger)
                    time.sleep(0.35)
                    b.jog_kapat(no)
                    self._hareketin_bitmesini_bekle(3.0)
                    time.sleep(0.15)
                    p1 = d.oku_simdi()["konum"].get(e)
                    delta = (p1 - p0) if (p0 is not None and p1 is not None) \
                        else 0.0
                    if abs(delta) > 12.0:
                        d.acil_durdur("jog dogrulama: beklenenden fazla hareket")
                        raise GUV.GuvenlikHatasi(
                            f"{e} beklenenden cok hareket etti ({delta:.1f} mm)"
                            "; test durduruldu")
                    if abs(delta) < 0.02:
                        raise GUV.GuvenlikHatasi(
                            f"{e} JogOn({no},{deger}) ile hareket etmedi. "
                            "Jog ON/OFF kapali, Reset acik ya da Mach3 Step "
                            "kipinde olabilir.")
                    bulunan[1 if delta > 0 else -1] = deger
                if sorted(bulunan) != [-1, 1]:
                    raise GUV.GuvenlikHatasi(
                        f"{e}: iki yon degeri de ayni yone gitti; esleme "
                        "cikarilamadi")
                yeni[e] = {"1": bulunan[1], "-1": bulunan[-1]}
                sonuc[e] = dict(yeni[e])
        finally:
            for e in eksenler:
                try:
                    if e in EKSEN_NO:
                        b.jog_kapat(EKSEN_NO[e])
                except Exception:                        # noqa: BLE001
                    pass
            if eski is not None:
                try:
                    b.dro_yaz(kod, eski)
                except Exception:                        # noqa: BLE001
                    pass
        d.ayarlar.guncelle({"jog": {"yon_esleme": yeni,
                                    "mach3_dogrulandi": True}})
        d.gunluk.ekle("jog", f"Mach3 yerel jog dogrulandi: {sonuc}")
        return {"ok": True, "esleme": sonuc}
