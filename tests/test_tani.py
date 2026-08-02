#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Teshis araci testleri (Windows disinda da calisir)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mach3_kontrol import baglanti as BAG, tani as TANI   # noqa: E402

# Gercek bir makinenin kayit defterinden gelen, icinde "machine" gecen ama
# Mach3 ile ILGISI OLMAYAN siniflar. Bunlardan birine baglanmak, teshisin
# yanlislikla "baglanti kuruldu" demesine yol acmisti.
ALAKASIZ = [
    "IAS.SdoMachine", "IAS.SdoMachine.1",
    "IAS.MachineAccountValidation", "IAS.MachineNameMapper",
    "IAS.MachineNTGroups.1",
    "MicrosoftEdgeUpdate.CoreMachineClass",
    "MicrosoftEdgeUpdate.CredentialDialogMachine.1.0",
    "MicrosoftEdgeUpdate.PolicyStatusMachineFallback.1.0",
    "X509Enrollment.CX509MachineEnrollmentFactory",
]

GERCEK = ["Mach3.Document", "Mach4.Document", "mach3.document",
          "Mach.Document", "MACH4.DOCUMENT"]


def test_alakasiz_siniflar_elenir():
    for ad in ALAKASIZ:
        assert not TANI._PROGID_RE.match(ad), f"elenmeliydi: {ad}"


def test_mach_progidleri_gecer():
    for ad in GERCEK:
        assert TANI._PROGID_RE.match(ad), f"gecmeliydi: {ad}"


def test_windows_disinda_guvenli_doner():
    """Windows'a ozgu sorgular baska platformda cokmemeli."""
    assert BAG.mach3_calisiyor() is None
    assert BAG._pencere_ara("mach3") is None
    assert BAG._surec_ara("mach3.exe") is None
    assert TANI._progid_ara(32) == []
    assert TANI._progid_ara(64) == []
    assert TANI._mach3_pid() is None


def test_tani_windows_disinda_temiz_cikar():
    assert TANI.calistir() == 1          # "Windows degil" ile duzgun biter


# ---------------------------------------------------------------------
# betik_nesnesi: Mach3'te asil metotlar ust nesnede degil, ondan alinan
# ayri bir "betik" nesnesinde olabilir.
# ---------------------------------------------------------------------
class _Betik:
    def GetOEMDRO(self, kod):
        return 12.5


class _UstNesne:
    """GetOEMDRO'yu dogrudan sunmayan, betik nesnesine kapi acan nesne."""

    def __init__(self, erisimci="GetScriptDispatch"):
        setattr(self, erisimci, lambda: _Betik())


class _Patlayan:
    @property
    def GetScriptDispatch(self):
        raise RuntimeError("COM hatasi")


def test_dogrudan_metot_varsa_ayni_nesne_doner():
    b = _Betik()
    nesne, ad = BAG.betik_nesnesi(b)
    assert nesne is b and ad == ""


def test_betik_nesnesi_bulunur():
    for erisimci in ("GetScriptDispatch", "GetScript", "ScriptDispatch"):
        ust = _UstNesne(erisimci)
        nesne, ad = BAG.betik_nesnesi(ust)
        assert ad == erisimci, (erisimci, ad)
        assert nesne.GetOEMDRO(800) == 12.5


def test_bulunamazsa_ust_nesne_korunur():
    class Bos:
        pass
    bos = Bos()
    nesne, ad = BAG.betik_nesnesi(bos)
    assert nesne is bos and ad == ""


def test_patlayan_erisimci_yutulur():
    p = _Patlayan()
    nesne, ad = BAG.betik_nesnesi(p)     # istisna disari sizmamali
    assert nesne is p and ad == ""


def test_com_hata_aciklamalari():
    assert "bit" in TANI.com_hata_aciklama(
        "(-2147221005, 'Invalid class string')") or \
        "kayitli degil" in TANI.com_hata_aciklama(
            "(-2147221005, 'Invalid class string')")
    assert "yetki" in TANI.com_hata_aciklama(
        "(-2146959355, 'Server execution failed')")
    assert TANI.com_hata_aciklama("bilinmeyen hata") == ""


if __name__ == "__main__":
    for ad, fn in sorted(list(globals().items())):
        if ad.startswith("test_"):
            fn()
            print("ok", ad)
    print("Teshis testleri gecti.")
