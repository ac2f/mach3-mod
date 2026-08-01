#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kesim suresi tahmini
====================
"Ne kadar surer?" sorusuna, dosyayi gercekten yorumlayarak cevap verir.

Model
-----
Her hareket icin **yamuk (trapez) hiz profili** kullanilir: makine durustan
baslar, ivmelenir, (yol yeterse) besleme hizina ulasir, sonra yavaslar.

    d >= v^2/a  ->  t = d/v + v/a          (besleme hizina ulasilir)
    d <  v^2/a  ->  t = 2*sqrt(d/a)        (ucgen profil - hiza ulasilamaz)

Gercek makine her kosede tam durmaz; ardisik hareketler harmanlanir. Bunun
icin `kose_kaybi` (0..1) katsayisi vardir: 1 = her kosede tam duruyormus gibi
(kotumser), 0 = hic yavaslamiyormus gibi (iyimser). Varsayilan 0.5.

Ek sureler: G4 beklemeleri, takim degisimi (M6) ve spindle devire ulasma.
Son olarak `sure_katsayi` ile kendi makinenize gore kalibre edebilirsiniz:
gercek sure / tahmin oranini ayarlara yazmaniz yeterli.
"""

import math

from . import gcode as GC


def _eksen_hizli(makine, hareket):
    """G0 icin gecerli hiz: hareketin bilesenlerine gore en yavas eksen."""
    hizlar = makine.get("hizli_hiz", {})
    return min([v for v in hizlar.values() if v] or [6000.0])


def _hareket_suresi(uzunluk, feed_mm_dk, ivme, kose_kaybi):
    """Tek hareketin saniye cinsinden suresi."""
    if uzunluk <= 1e-9:
        return 0.0
    v = max(feed_mm_dk, 1.0) / 60.0                 # mm/s
    a = max(ivme, 1.0)                              # mm/s^2
    if uzunluk >= v * v / a:
        t = uzunluk / v + (v / a) * max(min(kose_kaybi, 1.0), 0.0)
    else:
        # ucgen profil; kose_kaybi kadar "duraksama" payi
        t_ucgen = 2.0 * math.sqrt(uzunluk / a)
        t_duz = uzunluk / v
        t = t_duz + (t_ucgen - t_duz) * max(min(kose_kaybi, 1.0), 0.0)
    return t


def tahmin(satirlar, makine=None, ayar=None, fro=100.0):
    """Satirlar icin sure tahmini.

    fro: besleme override yuzdesi (Mach3'teki FRO). Kesim hareketlerini
    dogrudan olceklendirir; hizli (G0) hareketler etkilenmez.
    """
    makine = makine or {}
    ayar = ayar or {}
    ivmeler = makine.get("ivme", {})
    ivme_xy = min(ivmeler.get("X", 400.0), ivmeler.get("Y", 400.0))
    ivme_z = ivmeler.get("Z", ivme_xy)
    kose_kaybi = float(ayar.get("kose_kaybi", 0.5))
    katsayi = float(ayar.get("sure_katsayi", 1.0))
    takim_sn = float(ayar.get("takim_degisim_sn", 20.0))
    spindle_sn = float(ayar.get("spindle_bekleme_sn", 4.0))
    hizli_feed = _eksen_hizli(makine, None)
    olcek_fro = max(float(fro or 100.0), 1.0) / 100.0

    kesim_sn = hizli_sn = ek_sn = 0.0
    kesim_mm = hizli_mm = dalis_mm = 0.0
    feed_yok = 0

    for h in GC.hareket_listesi(satirlar):
        tip = h["tip"]
        if tip == "bekleme":
            ek_sn += h.get("sure", 0.0)
            continue
        if tip == "takim":
            ek_sn += takim_sn
            continue
        if tip == "spindle":
            ek_sn += spindle_sn
            continue
        uz = h["uzunluk"]
        if uz <= 1e-9:
            continue
        dz = abs(h.get("dz", 0.0))
        dikey = dz > 0.9 * uz                      # neredeyse saf Z hareketi
        ivme = ivme_z if dikey else ivme_xy
        if tip == "hizli":
            hizli_sn += _hareket_suresi(uz, hizli_feed, ivme, kose_kaybi)
            hizli_mm += uz
        else:
            feed = h["feed"]
            if not feed:
                feed_yok += 1
                feed = 600.0                        # F yoksa ihtiyatli varsayim
            kesim_sn += _hareket_suresi(uz, feed * olcek_fro, ivme, kose_kaybi)
            kesim_mm += uz
            if dikey and h.get("dz", 0.0) < 0:
                dalis_mm += dz

    toplam = (kesim_sn + hizli_sn + ek_sn) * katsayi
    return {
        "toplam_sn": round(toplam, 1),
        "toplam_metin": sure_metni(toplam),
        "kesim_sn": round(kesim_sn * katsayi, 1),
        "hizli_sn": round(hizli_sn * katsayi, 1),
        "ek_sn": round(ek_sn * katsayi, 1),
        "kesim_mm": round(kesim_mm, 1),
        "hizli_mm": round(hizli_mm, 1),
        "dalis_mm": round(dalis_mm, 1),
        "fro": fro,
        "feed_eksik_hareket": feed_yok,
    }


def blok_tahminleri(program, makine=None, ayar=None, fro=100.0):
    """Her kesim blogu icin ayri tahmin (sira listesinde gosterilir).

    Kumulatif sure de dondurulur: "bu bloktan baslarsam ne kadar kalir"
    sorusunun cevabi (`kalan_sn`).
    """
    makine, ayar = makine or {}, ayar or {}
    tekil = [tahmin(b, makine, ayar, fro) for b in program.bloklar]
    toplamlar = [t["toplam_sn"] for t in tekil]
    kalan = []
    birikim = 0.0
    for v in reversed(toplamlar):
        birikim += v
        kalan.insert(0, round(birikim, 1))
    for i, t in enumerate(tekil):
        t["blok"] = i
        t["kalan_sn"] = kalan[i]
        t["kalan_metin"] = sure_metni(kalan[i])
    return tekil


def program_tahmini(program, makine=None, ayar=None, fro=100.0,
                    baslangic_blok=0):
    """Tum program (header + bloklar + footer) icin tahmin.

    `baslangic_blok` verilirse yalnizca o bloktan sonuna kadarki sure.
    """
    satirlar = list(program.header)
    for b in program.bloklar[baslangic_blok:]:
        satirlar += b
    satirlar += program.footer
    t = tahmin(satirlar, makine, ayar, fro)
    t["baslangic_blok"] = baslangic_blok
    t["blok_sayisi"] = max(len(program.bloklar) - baslangic_blok, 0)
    return t


def sure_metni(sn):
    """3725 -> '1 sa 2 dk 5 sn'"""
    sn = int(round(max(sn, 0)))
    sa, kalan = divmod(sn, 3600)
    dk, s = divmod(kalan, 60)
    if sa:
        return f"{sa} sa {dk} dk {s} sn"
    if dk:
        return f"{dk} dk {s} sn"
    return f"{s} sn"
