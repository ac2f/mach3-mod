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


if __name__ == "__main__":
    for ad, fn in sorted(list(globals().items())):
        if ad.startswith("test_"):
            fn()
            print("ok", ad)
    print("Teshis testleri gecti.")
