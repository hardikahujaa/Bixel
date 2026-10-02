"""LIVE: drives the real phone. Opt-in:  BIXEL_LIVE=1 pytest tools/bixel_doctor/tests/test_doctor_live.py

Every case runs with revert=True and asserts the original value was restored and read back.
"""
import os

import pytest

from tools.bixel_doctor import doctor

pytestmark = pytest.mark.skipif(os.environ.get("BIXEL_LIVE") != "1", reason="live phone test; set BIXEL_LIVE=1")

CASES = [
    ("The brightness on my Galaxy S24 keeps changing by itself. It gets dim when I walk outside and I can't read the screen.", "DL-0020"),
    ("My screen turns itself off after a few seconds while I am reading and I have to keep touching it to keep it awake.", "DL-0220"),
    ("Everything on my Galaxy turned black. The menus and apps are all dark and I can't read them in bright sunlight.", "DL-0078"),
]


@pytest.mark.parametrize("complaint,catalog_id", CASES)
def test_complaint_changes_the_phone_and_restores_it(complaint, catalog_id):
    r = doctor.diagnose_and_fix(complaint, revert=True)
    assert r.verdict == "APPLIED_VERIFIED", r.to_json()
    assert r.catalog_id == catalog_id
    assert r.effect_verified and r.before["os"] != r.after["os"]
    assert r.reverted["restore_confirmed"], r.to_json()
