"""Unit tests for the plain-language mapper."""

from __future__ import annotations

import pytest

from shared.clinical.plain_language import (
    PlainLanguageMapper,
    icd_to_plain,
    mts_to_plain,
    news2_to_plain,
    sofa_to_plain,
)


def test_known_icd_code():
    assert "infection" in icd_to_plain("A40").lower()


def test_icd_with_subcode_falls_back_to_prefix():
    # A40.3 should match A40
    assert "infection" in icd_to_plain("A40.3").lower()


def test_unknown_icd_returned_literal():
    assert icd_to_plain("ZZZ99") == "Medical code ZZZ99"


def test_icd_none_returns_placeholder():
    assert "not recorded" in icd_to_plain(None).lower()


def test_sofa_bands():
    assert "normal" in sofa_to_plain(0).lower()
    assert "watching" in sofa_to_plain(3).lower()
    assert "review" in sofa_to_plain(7).lower()
    assert "urgent" in sofa_to_plain(12).lower()
    assert "critical" in sofa_to_plain(20).lower()


def test_mts_known_categories():
    for cat in range(1, 6):
        assert mts_to_plain(cat)
    assert "Seen immediately" in mts_to_plain(1)


def test_news2_bands():
    assert "normal" in news2_to_plain(0).lower()
    assert "slightly" in news2_to_plain(2).lower()
    assert "30" in news2_to_plain(5)
    assert "urgent" in news2_to_plain(8).lower()


def test_mapper_strips_jargon_from_text():
    m = PlainLanguageMapper()
    out = m.strip_jargon("Patient in ICU with SOFA 7 — monitor NEWS2")
    # All the jargon strings should be substituted
    assert "intensive care" in out
    assert "organ-stress" in out
    assert "early warning score" in out


def test_mapper_transforms_nested_dict():
    m = PlainLanguageMapper()
    out = m.transform({"icd_codes": ["A40", "J18"], "sofa": 5, "mts_category": 1})
    assert all(isinstance(x, str) for x in out["icd_codes"])
    assert "review" in out["sofa"].lower() or "stress" in out["sofa"].lower()
    assert "immediately" in out["mts_category"]


def test_mapper_transforms_list_of_dicts():
    m = PlainLanguageMapper()
    out = m.transform([{"sofa_score": 3}, {"sofa_score": 12}])
    assert "watching" in out[0]["sofa_score"].lower()
    assert "urgent" in out[1]["sofa_score"].lower()
