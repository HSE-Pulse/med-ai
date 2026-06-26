"""
Fallback intent detection when Ollama is unavailable.
Uses regex patterns to identify clinical intents from user messages.
"""

import re
from typing import Optional

INTENT_PATTERNS = {
    "patient_lookup": r"patient\s+(\d+)|look\s*up|find\s+patient|search\s+patient|patient\s+summary",
    "triage": r"triage|acuity|esi\s+level|predict.*triage|assess.*emergency|emergency\s+severity",
    "risk_assessment": r"risk|readmission|mortality|predict.*cancer|oncology.*risk|cancer\s+risk",
    "pathway": r"pathway|treatment\s+plan|recommend.*treatment|therapy|clinical\s+pathway",
    "lab_check": r"lab|blood\s+work|cbc|bmp|troponin|creatinine|wbc|hemoglobin|platelet|glucose",
    "vitals": r"vital|heart\s+rate|blood\s+pressure|spo2|temperature|respiratory\s+rate|bp\b|hr\b",
    "medication_review": r"medication|drug|prescription|antibiotic|dose|pharma|medicine",
    "cohort_stats": r"cohort|population|statistics|how\s+many|distribution|aggregate|overall",
    "note_analysis": r"note|clinical\s+note|analyze.*note|nlp|extract.*from.*note",
    "sofa": r"sofa|sepsis|organ\s+failure|deteriorat",
}


def detect_intent(message: str) -> dict:
    """
    Detect intent from a user message using regex patterns.
    Returns a dict with intent, params, and reasoning.
    """
    message_lower = message.lower().strip()

    # ── Knowledge questions: "What is X?", "Explain X", "How does X work?" ──
    # These are general medical knowledge queries, NOT data requests.
    KNOWLEDGE_PATTERNS = [
        r"^what\s+(is|are|does|do|was|were)\b",
        r"^how\s+(is|are|does|do|to|should|would|can)\b",
        r"^explain\b",
        r"^define\b",
        r"^describe\b",
        r"^tell\s+me\s+(about|what|how)",
        r"^can\s+you\s+explain",
        r"^what\s+does\s+\w+\s+mean",
        r"^(why|when|where)\s+(is|are|do|does|should|would)\b",
        r"^list\s+(the|common|types|causes)",
        r"(criteria|definition|meaning|guidelines|protocol|difference\s+between|normal\s+range|reference\s+range)",
    ]
    is_knowledge = any(re.search(p, message_lower) for p in KNOWLEDGE_PATTERNS)
    # Also check: no numbers (patient IDs) and no imperative verbs (show, check, get, look up)
    has_numbers = bool(re.search(r"\b\d{5,}\b", message_lower))
    has_imperative = bool(re.search(r"^(show|check|get|look|find|fetch|pull|run|predict|assess|triage\b)", message_lower))

    if is_knowledge and not has_numbers and not has_imperative:
        return {
            "intent": "general_clinical",
            "params": {},
            "reasoning": f"Detected as general knowledge question (no patient data request)",
        }

    best_intent = "general_clinical"
    best_score = 0
    match_details = []

    # Priority boost: if specific clinical keywords are present, they outweigh generic "patient" match
    PRIORITY_BOOST = {
        "vitals": 2,       # "vital signs" is more specific than "patient"
        "lab_check": 2,
        "triage": 10,      # triage is always the most explicit intent — if user says "triage", it IS triage
        "medication_review": 2,
        "sofa": 3,
        "note_analysis": 2,
        "pathway": 2,
        "risk_assessment": 2,
    }

    for intent, pattern in INTENT_PATTERNS.items():
        matches = re.findall(pattern, message_lower, re.IGNORECASE)
        if matches:
            score = len(matches) + PRIORITY_BOOST.get(intent, 0)
            match_details.append(f"{intent}({len(matches)} matches, score={score})")
            if score > best_score:
                best_score = score
                best_intent = intent

    params = {}

    # Extract patient ID (5-8 digit number commonly)
    patient_match = re.search(r"\b(\d{5,8})\b", message)
    if patient_match:
        params["patient_id"] = patient_match.group(1)

    # Extract admission ID (hadm_id) - match "admission XXXX" or second large number after patient_id
    hadm_match = re.search(r"(?:admission|hadm|admit)\s*(?:id)?\s*[:#]?\s*(\d+)", message_lower)
    if not hadm_match:
        # If two large numbers present, second one is likely hadm_id
        all_nums = re.findall(r"\b(\d{5,10})\b", message)
        if len(all_nums) >= 2 and "patient_id" in params:
            hadm_match_val = [n for n in all_nums if n != params["patient_id"]]
            if hadm_match_val:
                params["hadm_id"] = hadm_match_val[0]
                hadm_match = None  # already set
    if hadm_match:
        params["hadm_id"] = hadm_match.group(1)

    # Extract vital signs from the message
    vitals = _extract_vitals(message_lower)
    if vitals:
        params["vitals"] = vitals

    # Extract note text (everything after "note:" or between quotes)
    note_match = re.search(r'note[:\s]+"([^"]+)"', message_lower)
    if not note_match:
        note_match = re.search(r'note[:\s]+(.+?)(?:\.|$)', message_lower)
    if note_match:
        params["note_text"] = note_match.group(1).strip()

    reasoning = (
        f"Fallback regex detection matched: {', '.join(match_details) if match_details else 'none'}"
    )

    return {
        "intent": best_intent,
        "params": params,
        "reasoning": reasoning,
    }


def _extract_vitals(text: str) -> dict:
    """Extract vital sign values from text.

    Handles both "LABEL VALUE" and "VALUE LABEL" formats, e.g.:
      - "HR 98" or "98 HR (bpm)" or "heart rate: 98"
      - "158/94 BP" or "BP 158/94"
      - "93 SpO2" or "SpO2 93"
    """
    vitals = {}

    # ── Blood pressure (slash format must be parsed first) ───────────
    # Matches: "158/94 BP", "BP 158/94", "blood pressure: 158/94", "158/94 mmhg"
    bp = re.search(
        r"(?:(?:bp|blood\s*pressure)[:\s]*(\d{2,3})\s*/\s*(\d{2,3}))"
        r"|(?:(\d{2,3})\s*/\s*(\d{2,3})\s*(?:bp|blood\s*pressure|mmhg))",
        text,
    )
    if bp:
        vitals["sbp"] = int(bp.group(1) or bp.group(3))
        vitals["dbp"] = int(bp.group(2) or bp.group(4))

    # ── Single-value vitals ──────────────────────────────────────────
    # Each tuple: (output_key, list of label aliases)
    # We try "label<sep>value" first, then "value<sep>label".
    _VITAL_DEFS: list[tuple[str, list[str]]] = [
        ("heart_rate",       ["heart\\s*rate", "hr", "pulse", "bpm"]),
        ("respiratory_rate", ["respiratory\\s*rate", "resp\\s*rate", "rr"]),
        ("spo2",             ["spo2", "o2\\s*sat", "oxygen\\s*sat(?:uration)?"]),
        ("temperature",      ["temp(?:erature)?"]),
    ]

    # Plausible ranges to disambiguate when both patterns match
    _RANGES = {
        "heart_rate": (30, 250),
        "respiratory_rate": (4, 60),
        "spo2": (50, 100),
        "temperature": (30, 42),
    }

    for key, labels in _VITAL_DEFS:
        label_re = "|".join(labels)
        pat_label_first = rf"(?:{label_re})\s*[:\s]\s*([\d.]+)"
        pat_value_first = rf"(?<![.\d])([\d.]+)\s+(?:{label_re})\b"

        candidates = []
        for pat in [pat_label_first, pat_value_first]:
            m = re.search(pat, text)
            if m:
                val = m.group(1)
                candidates.append(float(val) if "." in val else int(val))

        if not candidates:
            continue
        if len(candidates) == 1:
            vitals[key] = candidates[0]
        else:
            # Pick the value within the plausible range; prefer label-first if both valid
            lo, hi = _RANGES.get(key, (0, 99999))
            valid = [v for v in candidates if lo <= v <= hi]
            vitals[key] = valid[0] if valid else candidates[0]

    # ── Standalone SBP/DBP (without slash) ───────────────────────────
    for bp_key, bp_labels in [("sbp", "systolic|sbp"), ("dbp", "diastolic|dbp")]:
        if bp_key not in vitals:
            pat_lf = rf"(?:{bp_labels})\s*[:\s]\s*(\d+)"
            pat_vf = rf"(?<![.\d])(\d+)\s+(?:{bp_labels})\b"
            candidates = []
            for pat in [pat_lf, pat_vf]:
                m = re.search(pat, text)
                if m:
                    candidates.append(int(m.group(1)))
            if candidates:
                lo, hi = (30, 350) if bp_key == "sbp" else (10, 250)
                valid = [v for v in candidates if lo <= v <= hi]
                vitals[bp_key] = valid[0] if valid else candidates[0]

    # ── Labs ─────────────────────────────────────────────────────────
    _LAB_DEFS: list[tuple[str, list[str]]] = [
        ("wbc",        ["wbc"]),
        ("hemoglobin", ["hemoglobin", "hgb", "hb"]),
        ("lactate",    ["lactate"]),
        ("glucose",    ["glucose"]),
        ("creatinine", ["creatinine"]),
    ]
    for key, labels in _LAB_DEFS:
        label_re = "|".join(labels)
        m = re.search(rf"(?:{label_re})\s*[:\s]\s*([\d.]+)", text)
        if not m:
            m = re.search(rf"(?<![.\d])([\d.]+)\s+(?:{label_re})\b", text)
        if m:
            vitals[key] = float(m.group(1))

    return vitals


def extract_patient_id(message: str) -> Optional[str]:
    """Extract a patient ID from a message."""
    match = re.search(r"\b(\d{5,8})\b", message)
    return match.group(1) if match else None


def extract_hadm_id(message: str) -> Optional[str]:
    """Extract an admission ID from a message."""
    match = re.search(r"(?:admission|hadm|admit)\s*(?:id)?\s*[:#]?\s*(\d+)", message.lower())
    return match.group(1) if match else None
