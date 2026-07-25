"""Hospital master data constants — single source of truth.

All modules should import from here instead of defining their own
department lists, capacities, or staff numbers. Data matches
app_15_erp/backend/app/data.py.
"""

DEPARTMENTS = [
    "ED", "MAU", "AMAU", "SAU", "CDU",
    "Medicine", "Surgery", "Cardiology", "Respiratory", "Orthopaedics",
    "ICU", "HDU", "Day_Ward", "Discharge_Lounge",
]

DEPARTMENT_TYPES = {
    "ED": "emergency", "MAU": "assessment", "AMAU": "assessment",
    "SAU": "assessment", "CDU": "observation",
    "Medicine": "inpatient", "Surgery": "inpatient", "Cardiology": "inpatient",
    "Respiratory": "inpatient", "Orthopaedics": "inpatient",
    "ICU": "critical", "HDU": "high_dependency",
    "Day_Ward": "day_case", "Discharge_Lounge": "discharge",
}

CAPACITIES = {
    "ED": 30, "MAU": 24, "AMAU": 16, "SAU": 12, "CDU": 8,
    "Medicine": 40, "Surgery": 36, "Cardiology": 20,
    "Respiratory": 18, "Orthopaedics": 24, "ICU": 12,
    "HDU": 8, "Day_Ward": 20, "Discharge_Lounge": 10,
}

# ---------------------------------------------------------------------------
# Replay capacities — denominators for MIMIC-sourced views
# ---------------------------------------------------------------------------
#
# Two different hospitals share this module, and they mean different things
# by "capacity":
#
#   * hospital_ops runs a DES over synthetic arrivals. For it, CAPACITIES is
#     a HARD RESOURCE LIMIT — departments report is_full, queue patients, and
#     cap their queues at 2x capacity. Occupancy there can never exceed 100%.
#
#   * data_ingestion replays real MIMIC admissions and transfers. It has no
#     concept of capacity at all: a patient goes wherever the source record
#     says they went. CAPACITIES is only ever a DISPLAY DENOMINATOR there.
#
# Dividing replay occupancy by the DES's Irish bed model is a category error,
# and it showed: MIMIC (BIDMC, a US tertiary centre) runs six distinct ICUs
# — SICU, CVICU, TSICU, MICU/SICU, MICU, Neuro SICU — which map onto the one
# Irish "ICU". Measured on this deployment that is 42 patients against 12
# beds, rendered as 350% occupancy. An Irish Model 4 hospital runs ICU at
# roughly 5% of beds; this dataset runs it at ~20%.
#
# So replay-sourced views divide by these instead. They describe the DATASET,
# not a hospital — sized from observed steady-state demand with roughly 1.25x
# headroom so normal fluctuation doesn't re-breach 100%. Departments absent
# here fall back to CAPACITIES, correct wherever the mapping is 1:1.
#
# Sized against the CORRECTED mapping below, not the one it replaced: moving
# the Med/Surg* and Neurology wards out of the SAU/AMAU assessment units
# empties those two and lands the volume on Medicine and Surgery instead
# (measured 58 and 57 against Irish 40 and 36), so those are what need room.
# Sizing rule: ~1.5x observed steady-state demand, measured 2026-07-25 with
# 233 active patients. 1.25x was tried first and proved too tight — the census
# is still climbing toward steady state (arrival rate x mean LOS, and MIMIC
# LOS runs to days), so Cardiology reached exactly 20/20 and went "black"
# within minutes of the previous sizing. Headroom here costs nothing but a
# few bed records; a pinned ward blocks allocation entirely.
REPLAY_CAPACITIES = {
    **CAPACITIES,
    "ICU": 64,         # 6 MIMIC critical-care units collapse to one Irish ICU
    "Medicine": 96,    # + Neurology, Psychiatry, Med/Surg, Haem/Onc, Transplant
    "Surgery": 96,     # + Med/Surg/Trauma, Med/Surg/GYN, Cardiac Surgery
    "Cardiology": 32,  # + Medicine/Cardiology, CCU, Cardiology Surgery Interm.
}

# The careunit vocabulary this dataset actually contains — every distinct
# non-null `careunit` in MIMIC_SIM.transfers (38 values, captured from the
# live deployment). Recorded here so that "which wards can this dataset
# populate?" is answerable statically instead of by querying Mongo.
MIMIC_CAREUNITS = frozenset({
    "Cardiac Surgery",
    "Cardiac Vascular Intensive Care Unit (CVICU)",
    "Cardiology",
    "Cardiology Surgery Intermediate",
    "Coronary Care Unit (CCU)",
    "Discharge Lounge",
    "Emergency Department",
    "Emergency Department Observation",
    "Hematology/Oncology",
    "Hematology/Oncology Intermediate",
    "Labor & Delivery",
    "Med/Surg",
    "Med/Surg/GYN",
    "Med/Surg/Trauma",
    "Medical Intensive Care Unit (MICU)",
    "Medical/Surgical (Gynecology)",
    "Medical/Surgical Intensive Care Unit (MICU/SICU)",
    "Medicine",
    "Medicine/Cardiology",
    "Medicine/Cardiology Intermediate",
    "Neuro Intermediate",
    "Neuro Stepdown",
    "Neuro Surgical Intensive Care Unit (Neuro SICU)",
    "Neurology",
    "Observation",
    "Obstetrics (Postpartum & Antepartum)",
    "Obstetrics Antepartum",
    "Obstetrics Postpartum",
    "PACU",
    "Psychiatry",
    "Surgery",
    "Surgery/Pancreatic/Biliary/Bariatric",
    "Surgery/Trauma",
    "Surgical Intensive Care Unit (SICU)",
    "Thoracic Surgery",
    "Transplant",
    "Trauma SICU (TSICU)",
    "Vascular",
})


def replay_capacity(department: str) -> int:
    """Bed denominator to display for a MIMIC-replay-sourced department."""
    return REPLAY_CAPACITIES.get(department, CAPACITIES.get(department, 0))

# Aggregated staff defaults (day shift doctors+nurses for DES compatibility)
STAFF_DEFAULTS = {
    "ED": {"doctors": 11, "nurses": 12, "hca": 4},
    "MAU": {"doctors": 4, "nurses": 6, "hca": 2},
    "AMAU": {"doctors": 3, "nurses": 5, "hca": 2},
    "SAU": {"doctors": 3, "nurses": 4, "hca": 1},
    "CDU": {"doctors": 1, "nurses": 3, "hca": 1},
    "Medicine": {"doctors": 8, "nurses": 8, "hca": 3},
    "Surgery": {"doctors": 7, "nurses": 7, "hca": 2},
    "Cardiology": {"doctors": 4, "nurses": 5, "hca": 1},
    "Respiratory": {"doctors": 3, "nurses": 4, "hca": 1},
    "Orthopaedics": {"doctors": 4, "nurses": 5, "hca": 2},
    "ICU": {"doctors": 4, "nurses": 13, "hca": 2},
    "HDU": {"doctors": 2, "nurses": 5, "hca": 1},
    "Day_Ward": {"doctors": 3, "nurses": 4, "hca": 1},
    "Discharge_Lounge": {"doctors": 0, "nurses": 2, "hca": 1},
}

SERVICE_PARAMS = {
    "ED": (1.2, 0.7),
    "MAU": (2.5, 0.6),
    "AMAU": (2.3, 0.6),
    "SAU": (2.5, 0.7),
    "CDU": (2.0, 0.6),
    "Medicine": (3.5, 0.8),
    "Surgery": (3.2, 0.9),
    "Cardiology": (3.0, 0.7),
    "Respiratory": (3.3, 0.8),
    "Orthopaedics": (3.4, 0.8),
    "ICU": (3.8, 1.0),
    "HDU": (3.0, 0.8),
    "Day_Ward": (0.8, 0.4),
    # Discharge_Lounge is a transitional short-stay — patients wait for
    # transport / paperwork, typically 30min-2h. Previously (0.5, 0.5)
    # gave median ~1.65h per patient and with only 10 beds caused a
    # severe hospital-wide bottleneck. Tightened to median ~0.5h so
    # throughput matches arrival intensity.
    "Discharge_Lounge": (-0.7, 0.4),
}

# LOS values aligned with ERP (app_15_erp/backend/app/data.py) — single source of truth
LOS_PARAMS = {
    "ED": {"median_h": 6.7, "mean_h": 8.2, "p90_h": 14.0},
    "MAU": {"median_h": 18.0, "mean_h": 22.0, "p90_h": 48.0},
    "AMAU": {"median_h": 12.0, "mean_h": 14.0, "p90_h": 36.0},
    "SAU": {"median_h": 10.0, "mean_h": 12.0, "p90_h": 30.0},
    "CDU": {"median_h": 8.0, "mean_h": 10.0, "p90_h": 24.0},
    "Medicine": {"median_h": 120.0, "mean_h": 144.0, "p90_h": 288.0},
    "Surgery": {"median_h": 96.0, "mean_h": 108.0, "p90_h": 240.0},
    "Cardiology": {"median_h": 72.0, "mean_h": 96.0, "p90_h": 192.0},
    "Respiratory": {"median_h": 96.0, "mean_h": 120.0, "p90_h": 264.0},
    "Orthopaedics": {"median_h": 84.0, "mean_h": 96.0, "p90_h": 216.0},
    "ICU": {"median_h": 72.0, "mean_h": 96.0, "p90_h": 240.0},
    "HDU": {"median_h": 48.0, "mean_h": 60.0, "p90_h": 144.0},
    "Day_Ward": {"median_h": 6.0, "mean_h": 7.0, "p90_h": 10.0},
    "Discharge_Lounge": {"median_h": 2.0, "mean_h": 3.0, "p90_h": 6.0},
}

PET_TARGET_HOURS = 6
NEDOCS_THRESHOLDS = {"normal": 100, "busy": 140, "crowded": 180, "severe": 200}

MTS_CATEGORIES = {
    1: {"name": "Immediate", "color": "Red", "target_minutes": 0},
    2: {"name": "Very Urgent", "color": "Orange", "target_minutes": 10},
    3: {"name": "Urgent", "color": "Yellow", "target_minutes": 60},
    4: {"name": "Standard", "color": "Green", "target_minutes": 120},
    5: {"name": "Non-Urgent", "color": "Blue", "target_minutes": 240},
}

ALERT_LEVELS = ["green", "amber", "red", "black"]
BED_STATUSES = ["available", "occupied", "blocked", "cleaning", "reserved"]

# ---------------------------------------------------------------------------
# Bed categories — orthogonal to department.type, drives clinical suitability
# matching during allocation. A single physical bed can carry multiple
# categories (e.g. a Medicine bed that is also isolation-capable).
#
# HIQA infection-control standards expect ring-fenced tracking of isolation
# beds; Sláintecare paediatric / maternity safety standards require explicit
# paediatric and maternity bed identification; National Stroke Programme
# requires thrombolysis-capable beds to be flagged distinctly.
# ---------------------------------------------------------------------------
BED_CATEGORIES = [
    "general",
    "isolation",              # single-room with en-suite; infection control
    "bariatric",              # reinforced frame + lifting equipment
    "paediatric",             # paediatric-specific cot/bed, child-safe
    "maternity",              # obstetric bed, CTG equipped
    "stroke_thrombolysis",    # stroke unit with thrombolysis capability
    "cardiac_monitored",      # telemetry/continuous ECG
    "observation",            # short-stay clinical decision / CDU
    "critical",               # ICU
    "high_dependency",        # HDU
    "day_case",               # day-of-treatment only
]


# Per-department bed category mix. Values are counts; total must equal
# CAPACITIES[dept]. Where not specified, all beds default to "general".
# These are illustrative defaults for a Model 4 Irish acute hospital.
BED_CATEGORY_MIX = {
    "ED": {"general": 22, "isolation": 4, "paediatric": 2, "bariatric": 2},
    "MAU": {"general": 20, "isolation": 3, "cardiac_monitored": 1},
    "AMAU": {"general": 13, "isolation": 2, "cardiac_monitored": 1},
    "SAU": {"general": 10, "isolation": 2},
    "CDU": {"observation": 8},
    "Medicine": {"general": 32, "isolation": 4, "stroke_thrombolysis": 2, "bariatric": 2},
    "Surgery": {"general": 30, "isolation": 3, "bariatric": 3},
    "Cardiology": {"cardiac_monitored": 16, "general": 3, "isolation": 1},
    "Respiratory": {"general": 14, "isolation": 3, "cardiac_monitored": 1},
    "Orthopaedics": {"general": 20, "bariatric": 3, "isolation": 1},
    "ICU": {"critical": 12},
    "HDU": {"high_dependency": 8},
    "Day_Ward": {"day_case": 20},
    "Discharge_Lounge": {"general": 10},
}


def resolve_bed_category_for(
    department: str,
    index: int,
) -> str:
    """Given a department and 1-based bed index, return the bed category.

    Beds are assigned categories in the order declared in BED_CATEGORY_MIX
    (e.g. the first 22 ED beds are "general", the next 4 are "isolation",
    the next 2 are "paediatric", the last 2 are "bariatric").

    ``BED_CATEGORY_MIX`` is declared against the Irish ``CAPACITIES``. Wards
    sized to ``REPLAY_CAPACITIES`` run past the end of their mix, so beds
    beyond it inherit the department's dominant declared category rather than
    a blanket "general" — ICU beds 13-48 categorised "general" would fail
    ``MONITORING_COMPATIBLE`` and the allocator would refuse to place a
    critical-care patient in what is, by construction, an ICU bed.
    """
    mix = BED_CATEGORY_MIX.get(department, {"general": CAPACITIES.get(department, 0)})
    running = 0
    for category, count in mix.items():
        running += count
        if index <= running:
            return category
    if mix:
        return max(mix.items(), key=lambda kv: kv[1])[0]
    return "general"


# Allocation compatibility — which bed categories satisfy which request flags.
# Used by the bed-allocation scorer in app_08.
ISOLATION_COMPATIBLE = {"isolation"}
MONITORING_COMPATIBLE = {"critical", "high_dependency", "cardiac_monitored", "stroke_thrombolysis"}
PAEDIATRIC_COMPATIBLE = {"paediatric"}
MATERNITY_COMPATIBLE = {"maternity"}
BARIATRIC_COMPATIBLE = {"bariatric"}


# ---------------------------------------------------------------------------
# Sláintecare Waiting List Action Plan — target wait thresholds
# ---------------------------------------------------------------------------

WAITING_LIST_TARGETS = {
    "elective_inpatient_weeks": 18,    # HSE Sláintecare 18-week target
    "urgent_outpatient_weeks": 12,     # HSE urgent OPD target
    "gi_scope_weeks": 6,               # NCCP 6-week GI scope target
    "routine_outpatient_weeks": 52,    # Legacy cap
    "paediatric_weeks": 12,            # Children's waiting list target
}


# ---------------------------------------------------------------------------
# HSE Health Regions (2024 six-region restructure)
# ---------------------------------------------------------------------------

HSE_REGIONS = {
    "HSE Dublin and Midlands": [
        "St. James's Hospital", "Tallaght University Hospital",
        "Naas General Hospital", "Midland Regional Hospital Tullamore",
        "Midland Regional Hospital Portlaoise", "Midland Regional Hospital Mullingar",
    ],
    "HSE Dublin and North East": [
        "Mater Misericordiae University Hospital", "Beaumont Hospital",
        "Connolly Hospital Blanchardstown", "Our Lady of Lourdes Hospital Drogheda",
        "Cavan General Hospital", "Monaghan Hospital", "Louth County Hospital",
    ],
    "HSE Dublin and South East": [
        "St. Vincent's University Hospital", "St. Luke's General Hospital Kilkenny",
        "University Hospital Waterford", "Wexford General Hospital",
        "St. Michael's Hospital", "National Maternity Hospital",
    ],
    "HSE Mid West": [
        "University Hospital Limerick", "Ennis Hospital",
        "Nenagh Hospital", "St. John's Hospital Limerick",
        "Croom Orthopaedic Hospital",
    ],
    "HSE South West": [
        "Cork University Hospital", "Mercy University Hospital Cork",
        "South Infirmary Victoria University Hospital",
        "University Hospital Kerry", "Bantry General Hospital",
        "Mallow General Hospital",
    ],
    "HSE West and North West": [
        "University Hospital Galway", "Portiuncula University Hospital",
        "Mayo University Hospital", "Sligo University Hospital",
        "Letterkenny University Hospital", "Roscommon University Hospital",
    ],
}

# Default simulation hospital assignment — each department is treated as
# part of the Dublin and Midlands region unless the ERP overrides it.
DEFAULT_HSE_REGION = "HSE Dublin and Midlands"
DEPARTMENT_REGIONS = {dept: DEFAULT_HSE_REGION for dept in DEPARTMENTS}


def region_for_department(department: str) -> str:
    """Return the HSE region for a given department name.

    Falls back to :data:`DEFAULT_HSE_REGION` if the department is unknown.
    """
    return DEPARTMENT_REGIONS.get(department, DEFAULT_HSE_REGION)


# ---------------------------------------------------------------------------
# MIMIC → Irish Department Mapping
# ---------------------------------------------------------------------------

_MIMIC_TO_IRISH_DEPT = {
    "emergency department": "ED", "emergency department observation": "CDU",
    "obstetrics (postpartum & antepartum)": "MAU", "obstetrics postpartum": "MAU",
    "labor & delivery": "MAU",
    # Neurology / Psychiatry are inpatient specialties, not front-door
    # assessment. AMAU is Ireland's Acute Medical Assessment Unit — a
    # short-stay triage ward — so parking 21 neurology inpatients there
    # both misdescribes them and pushes AMAU to 138% while Medicine sits
    # at 78%. Mapped to Medicine, the closest inpatient equivalent the
    # Irish model has.
    "neurology": "Medicine", "psychiatry": "Medicine",
    # Same misclassification on the surgical side: SAU is the Surgical
    # Assessment Unit (short-stay), while MIMIC's Med/Surg* wards are
    # general inpatient wards. Trauma/GYN variants are surgical; the plain
    # "med/surg" ward is mixed and goes to Medicine.
    "med/surg": "Medicine", "med/surg/trauma": "Surgery",
    "med/surg/gyn": "Surgery",
    "medical/surgical (gynecology)": "Surgery",
    "pacu": "CDU",
    "medicine": "Medicine", "general medicine": "Medicine",
    "hematology/oncology": "Medicine", "transplant": "Medicine",
    "surgery": "Surgery", "cardiac surgery": "Surgery",
    "surgery/pancreatic/biliary/bariatric": "Surgery", "surgery/trauma": "Surgery",
    "cardiology": "Cardiology", "medicine/cardiology": "Cardiology",
    "medicine/cardiology intermediate": "Cardiology",
    "coronary care unit (ccu)": "Cardiology",
    "respiratory": "Respiratory",
    "orthopaedics": "Orthopaedics", "orthopedics": "Orthopaedics",
    "vascular": "Orthopaedics",
    "medical intensive care unit (micu)": "ICU",
    "surgical intensive care unit (sicu)": "ICU",
    "cardiac vascular intensive care unit (cvicu)": "ICU",
    "trauma sicu (tsicu)": "ICU",
    "medical/surgical intensive care unit (micu/sicu)": "ICU",
    "neuro intermediate": "HDU", "neuro stepdown": "HDU",
    "day surgery": "Day_Ward", "endoscopy": "Day_Ward",
    "discharge lounge": "Discharge_Lounge",
}


def map_department(mimic_dept: str) -> str:
    """Map a MIMIC department name to the closest Irish HSE department."""
    if not mimic_dept:
        return "ED"
    lower = mimic_dept.lower().strip()
    if lower in _MIMIC_TO_IRISH_DEPT:
        return _MIMIC_TO_IRISH_DEPT[lower]
    if "icu" in lower or "intensive" in lower:
        return "ICU"
    if "stepdown" in lower or "intermediate" in lower:
        return "HDU"
    if "emergency" in lower:
        return "ED"
    if "observation" in lower or "pacu" in lower:
        return "CDU"
    if "obstet" in lower or "labor" in lower or "delivery" in lower:
        return "MAU"
    if "neuro" in lower or "psych" in lower:
        return "AMAU"
    if "med/surg" in lower or "gynecol" in lower:
        return "SAU"
    if "surg" in lower or "trauma" in lower:
        return "Surgery"
    if "cardio" in lower or "cardiac" in lower or "coronary" in lower:
        return "Cardiology"
    if "respiratory" in lower or "pulmon" in lower:
        return "Respiratory"
    if "ortho" in lower or "vascular" in lower:
        return "Orthopaedics"
    if "day" in lower or "endoscop" in lower:
        return "Day_Ward"
    if "discharge" in lower:
        return "Discharge_Lounge"
    return "Medicine"


# Departments this dataset can never populate. Derived rather than
# hand-listed, because "unrepresented" is a property of the DATA, not of the
# mapping table: `respiratory`, `day surgery` and `endoscopy` all have
# mapping entries, they simply never appear in MIMIC's careunit vocabulary.
# Hand-maintaining the list got that exactly backwards on the first attempt.
#
# Deriving it means adding a careunit or a mapping entry updates this
# automatically, and a ward can never be labelled "not modelled" while
# actually holding patients.
UNREPRESENTED_IN_REPLAY = frozenset(
    dept for dept in DEPARTMENTS
    if not any(map_department(unit) == dept for unit in MIMIC_CAREUNITS)
)
