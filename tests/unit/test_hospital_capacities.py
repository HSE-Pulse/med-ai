"""Invariants for hospital master data.

None of these were covered by a test before, and each corresponds to a way
the department model has actually drifted or misled in this deployment.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from shared.constants.hospital import (
    BED_CATEGORY_MIX,
    CAPACITIES,
    DEPARTMENT_TYPES,
    DEPARTMENTS,
    REPLAY_CAPACITIES,
    STAFF_DEFAULTS,
    MIMIC_CAREUNITS,
    UNREPRESENTED_IN_REPLAY,
    _MIMIC_TO_IRISH_DEPT,
    map_department,
    replay_capacity,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_CONSTANTS = REPO_ROOT / "dashboard" / "src" / "lib" / "constants.ts"


# --------------------------------------------------------------- consistency


@pytest.mark.parametrize("dept", sorted(CAPACITIES))
def test_bed_category_mix_sums_to_capacity(dept: str) -> None:
    """Every bed must have a category, and no ward may declare phantom beds.

    bed_management materialises one bed record per unit of CAPACITIES and
    assigns categories by walking BED_CATEGORY_MIX in order. If the two
    disagree, beds either come out uncategorised or the ward is short.
    """
    assert sum(BED_CATEGORY_MIX[dept].values()) == CAPACITIES[dept]


def test_every_department_is_fully_described() -> None:
    """DEPARTMENTS, CAPACITIES, DEPARTMENT_TYPES, STAFF_DEFAULTS agree."""
    depts = set(DEPARTMENTS)
    assert set(CAPACITIES) == depts
    assert set(DEPARTMENT_TYPES) == depts
    assert set(BED_CATEGORY_MIX) == depts
    assert set(STAFF_DEFAULTS) == depts


def test_mapping_only_targets_real_departments() -> None:
    """A typo in the mapping table would silently invent a department."""
    unknown = set(_MIMIC_TO_IRISH_DEPT.values()) - set(DEPARTMENTS)
    assert not unknown, f"mapping targets non-existent departments: {unknown}"


def test_map_department_never_returns_unknown_department() -> None:
    """Including the keyword fallbacks and the catch-all."""
    probes = [
        "", "Admitting", "Unknown", "wat", "Medical Intensive Care Unit (MICU)",
        "Emergency Department", "Neuro Stepdown", "PACU", "Labor & Delivery",
    ]
    for probe in probes:
        assert map_department(probe) in set(DEPARTMENTS), probe


# ------------------------------------------------------------ replay vs DES


def test_replay_capacity_never_below_irish_capacity() -> None:
    """Replay denominators may add headroom, never remove it.

    A replay capacity below the DES capacity would make the same ward look
    *more* crowded in the MIMIC view than the simulated one, which is
    backwards — the replay is the unconstrained model.
    """
    for dept in DEPARTMENTS:
        assert replay_capacity(dept) >= CAPACITIES[dept], dept


def test_replay_capacity_defined_for_every_department() -> None:
    for dept in DEPARTMENTS:
        assert replay_capacity(dept) > 0, dept


def test_unrepresented_is_derived_from_the_data_not_the_mapping() -> None:
    """A ward flagged 'not modelled' must be unreachable from real careunits.

    The distinction matters and a hand-maintained list got it wrong:
    Respiratory and Day_Ward both HAVE mapping entries ("respiratory",
    "day surgery", "endoscopy") — they are unpopulated because MIMIC's
    careunit vocabulary contains no such string, not because the mapping
    omits them. So the property must be checked against MIMIC_CAREUNITS.
    """
    assert UNREPRESENTED_IN_REPLAY <= set(DEPARTMENTS)
    for dept in UNREPRESENTED_IN_REPLAY:
        producers = [u for u in MIMIC_CAREUNITS if map_department(u) == dept]
        assert not producers, (
            f"{dept} is flagged 'not modelled' but {producers} map to it"
        )


def test_represented_departments_have_a_producing_careunit() -> None:
    """The converse: anything NOT flagged must actually be reachable."""
    for dept in set(DEPARTMENTS) - UNREPRESENTED_IN_REPLAY:
        producers = [u for u in MIMIC_CAREUNITS if map_department(u) == dept]
        assert producers, f"{dept} has no producing careunit but isn't flagged"


def test_assessment_units_are_not_inpatient_dumping_grounds() -> None:
    """MAU/AMAU/SAU/CDU are short-stay front-door units.

    Routing inpatient specialty wards (Neurology, Med/Surg) into them both
    misdescribes the patient and inverts occupancy — measured AMAU at 138%
    and SAU at 275% while Medicine sat at 78%.
    """
    front_door = {"MAU", "AMAU", "SAU", "CDU"}
    inpatient_units = [
        "neurology", "psychiatry", "med/surg", "med/surg/trauma",
        "med/surg/gyn", "medical/surgical (gynecology)",
        "hematology/oncology", "transplant",
    ]
    for unit in inpatient_units:
        assert map_department(unit) not in front_door, (
            f"{unit!r} maps to the front-door unit {map_department(unit)!r}"
        )


# ------------------------------------------------------------ frontend sync


def test_frontend_capacities_mirror_backend() -> None:
    """dashboard/src/lib/constants.ts is hand-synced with only a comment
    guarding it. Drift there silently changes every occupancy bar."""
    if not FRONTEND_CONSTANTS.exists():          # pragma: no cover
        pytest.skip("dashboard source not present in this checkout")
    src = FRONTEND_CONSTANTS.read_text()
    block = re.search(
        r"export const CAPACITIES[^=]*=\s*\{(.*?)\}", src, re.S,
    )
    assert block, "CAPACITIES not found in constants.ts"
    pairs = dict(
        (m.group(1), int(m.group(2)))
        for m in re.finditer(r"(\w+)\s*:\s*(\d+)", block.group(1))
    )
    assert pairs == CAPACITIES, (
        "frontend CAPACITIES drifted from shared/constants/hospital.py: "
        f"{ {k: (pairs.get(k), CAPACITIES.get(k)) for k in set(pairs) | set(CAPACITIES) if pairs.get(k) != CAPACITIES.get(k)} }"
    )


def test_frontend_department_order_matches_backend() -> None:
    if not FRONTEND_CONSTANTS.exists():          # pragma: no cover
        pytest.skip("dashboard source not present in this checkout")
    src = FRONTEND_CONSTANTS.read_text()
    block = re.search(r"export const DEPARTMENT_ORDER\s*=\s*\[(.*?)\]", src, re.S)
    assert block, "DEPARTMENT_ORDER not found in constants.ts"
    order = re.findall(r'"([^"]+)"', block.group(1))
    assert order == DEPARTMENTS


def test_every_replay_bed_gets_a_valid_category() -> None:
    """Wards grown to REPLAY_CAPACITIES run past their declared mix.

    The old fallback returned "general" for those beds, which for ICU means
    a bed that fails MONITORING_COMPATIBLE — the allocator would refuse to
    place a critical-care patient in an ICU bed.
    """
    from shared.constants.hospital import (
        MONITORING_COMPATIBLE, resolve_bed_category_for,
    )
    for dept in DEPARTMENTS:
        categories = {
            resolve_bed_category_for(dept, i)
            for i in range(1, replay_capacity(dept) + 1)
        }
        assert categories, dept
        assert "" not in categories, dept
        declared = set(BED_CATEGORY_MIX[dept])
        assert categories <= declared, (
            f"{dept} produced categories outside its declared mix: "
            f"{categories - declared}"
        )
    # ICU and HDU must stay monitoring-capable across their whole range.
    for dept in ("ICU", "HDU"):
        for i in range(1, replay_capacity(dept) + 1):
            assert resolve_bed_category_for(dept, i) in MONITORING_COMPATIBLE, (
                f"{dept} bed {i} is not monitoring-capable"
            )
