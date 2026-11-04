#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Komut satiri
============
    python main.py                      # web arayuzunu baslat (varsayilan)
    python main.py --port 8010
    python main.py --simulasyon         # Mach3 olmadan dene
    python main.py --sure kesim.tap     # yalnizca sure tahmini yaz
    python main.py --sira kesim.tap     # blok (kesim sirasi) listesini yaz
"""

import argparse
import os
import sys


def _sure_yaz(yol, ayarlar):
    from . import gcode as GC, sure as SURE
    p = GC.Program(yol)
    t = SURE.program_tahmini(p, ayarlar["makine"], ayarlar["gcode"])
    print(f"\n{os.path.basename(yol)}  ({len(p.bloklar)} kesim blogu, "
          f"birim {p.birim})")
    print("-" * 58)
    print(f"  Tahmini toplam sure : {t['toplam_metin']}")
    print(f"    kesim             : {SURE.sure_metni(t['kesim_sn'])} "
          f"({t['kesim_mm']:.0f} mm)")
    print(f"    bosta (G0)        : {SURE.sure_metni(t['hizli_sn'])} "
          f"({t['hizli_mm']:.0f} mm)")
    print(f"    bekleme/takim     : {SURE.sure_metni(t['ek_sn'])}")
    if t["feed_eksik_hareket"]:
        print(f"  ! {t['feed_eksik_hareket']} harekette F yok; 600 mm/dk "
              f"varsayildi.")
    for u in p.uyarilar:
        print(f"  ! {u}")


def _sira_yaz(yol, ayarlar):
    from . import gcode as GC, sure as SURE
    p = GC.Program(yol)
    sureler = SURE.blok_tahminleri(p, ayarlar["makine"], ayarlar["gcode"])
    print(f"\n{os.path.basename(yol)} - kesim sirasi ({len(p.bloklar)} blok)")
    print("-" * 72)
    print(f"{'#':>4}  {'baslangic X/Y':>18}  {'satir':>6}  {'kesim mm':>9}  "
          f"{'sure':>10}")
    for o, s in zip(p.ozet(), sureler):
        print(f"{o['no']:>4}  {o['x']:>8.2f} {o['y']:>8.2f}  "
              f"{o['satir']:>6}  {o['kesim_uz']:>9.1f}  "
              f"{SURE.sure_metni(s['toplam_sn']):>10}")


def _konsol_guvenli():
    """Windows konsolu (cp1252/cp857) Turkce karakterlerde coker; yazdirirken
    karakteri `?` yapip devam etsin."""
    for akim in (sys.stdout, sys.stderr):
        try:
            akim.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv=None):
    from . import ayarlar as AY
    _konsol_guvenli()

    ap = argparse.ArgumentParser(
        prog="mach3-kontrol",
        description="Mach3 icin guvenlik onlemli web kontrol arayuzu")
    ap.add_argument("--port", type=int, help="Web portu (varsayilan 8010)")
    ap.add_argument("--host", help="Dinlenecek adres (varsayilan 0.0.0.0)")
    ap.add_argument("--tarayici-ac", action="store_true",
                    help="Baslarken tarayiciyi ac")
    ap.add_argument("--simulasyon", action="store_true",
                    help="Mach3 yerine sanal tezgah (deneme/egitim)")
    ap.add_argument("--com", action="store_true",
                    help="Gercek Mach3 COM baglantisini zorla")
    ap.add_argument("--mach3-baslat", action="store_true",
                    dest="mach3_baslat",
                    help="Mach3 kapaliysa onu baslat (varsayilan acik; "
                         "elle acilan Mach3'e COM ile baglanilamaz)")
    ap.add_argument("--profil", dest="profil",
                    help="Mach3 profili (or. Mach3Mill). Profil verilmezse "
                         "Mach3 profil secim penceresinde bekler ve COM "
                         "arayuzunu acmaz.")
    ap.add_argument("--baglanma", action="store_false", dest="baglan",
                    help="Acilista baglanmayi deneme")
    ap.add_argument("--ayar", help="Ayar dosyasi yolu")
    ap.add_argument("--sure", metavar="DOSYA", help="Sure tahmini yaz ve cik")
    ap.add_argument("--sira", metavar="DOSYA",
                    help="Kesim sirasi listesini yaz ve cik")
    ap.add_argument("--tani", action="store_true",
                    help="Baglanti teshisi calistir ve cik "
                         "('baglanti yok' sorununda bunu calistirin)")
    a = ap.parse_args(argv)

    ayar = AY.Ayarlar(a.ayar)
    # DIKKAT: --simulasyon / --com yalnizca BU calistirma icin gecerlidir;
    # ayar dosyasina yazilmaz. (Aksi halde bir kez simulasyonda denedikten
    # sonra program kalici olarak simulasyonda kalir.)
    if a.simulasyon:
        ayar.veri["baglanti"]["tur"] = "simulasyon"
    elif a.com:
        ayar.veri["baglanti"]["tur"] = "com"
    if a.mach3_baslat:
        ayar.veri["baglanti"]["mach3_baslat"] = True
    if a.profil:
        ayar.veri["baglanti"]["profil"] = a.profil

    if a.tani:
        from . import tani
        return tani.calistir(ayar)

    if a.sure:
        _sure_yaz(a.sure, ayar)
        return 0
    if a.sira:
        _sira_yaz(a.sira, ayar)
        return 0

    from . import webapp
    webapp.calistir(port=a.port, host=a.host,
                    ac=True if a.tarayici_ac else None,
                    ayarlar_yolu=a.ayar, baglan=a.baglan,
                    tur=("simulasyon" if a.simulasyon
                         else "com" if a.com else None),
                    mach3_baslat=a.mach3_baslat or None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
