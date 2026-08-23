"""Treatment pathway optimization engine.

Rule-based + ML hybrid for cancer treatment recommendations
and scheduling optimization.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class TreatmentStep:
    step: int
    treatment: str
    category: str  # surgery, chemotherapy, radiation, supportive
    estimated_days: int
    priority: str  # immediate, scheduled, follow-up


# Standard treatment pathways by cancer type
STANDARD_PATHWAYS: dict[str, list[dict]] = {
    "Lung": [
        {"treatment": "Diagnostic biopsy & staging CT/PET", "category": "diagnostic", "days": 7, "priority": "immediate"},
        {"treatment": "Surgical resection (if operable)", "category": "surgery", "days": 14, "priority": "scheduled"},
        {"treatment": "Adjuvant chemotherapy (cisplatin-based)", "category": "chemotherapy", "days": 90, "priority": "scheduled"},
        {"treatment": "Radiation therapy (if indicated)", "category": "radiation", "days": 42, "priority": "scheduled"},
        {"treatment": "Follow-up CT q3 months", "category": "supportive", "days": 90, "priority": "follow-up"},
    ],
    "Breast": [
        {"treatment": "Core needle biopsy & receptor testing", "category": "diagnostic", "days": 7, "priority": "immediate"},
        {"treatment": "Neoadjuvant chemotherapy (if locally advanced)", "category": "chemotherapy", "days": 120, "priority": "scheduled"},
        {"treatment": "Surgical excision / mastectomy", "category": "surgery", "days": 14, "priority": "scheduled"},
        {"treatment": "Radiation therapy", "category": "radiation", "days": 35, "priority": "scheduled"},
        {"treatment": "Endocrine therapy (if ER+)", "category": "chemotherapy", "days": 1825, "priority": "follow-up"},
    ],
    "Colon": [
        {"treatment": "Colonoscopy with biopsy", "category": "diagnostic", "days": 5, "priority": "immediate"},
        {"treatment": "CT staging abdomen/pelvis/chest", "category": "diagnostic", "days": 3, "priority": "immediate"},
        {"treatment": "Surgical resection (colectomy)", "category": "surgery", "days": 14, "priority": "scheduled"},
        {"treatment": "Adjuvant FOLFOX chemotherapy", "category": "chemotherapy", "days": 180, "priority": "scheduled"},
        {"treatment": "CEA monitoring q3 months", "category": "supportive", "days": 90, "priority": "follow-up"},
    ],
    "Colorectal": [
        {"treatment": "Colonoscopy with biopsy", "category": "diagnostic", "days": 5, "priority": "immediate"},
        {"treatment": "Neoadjuvant chemoradiation (rectal)", "category": "chemotherapy", "days": 42, "priority": "scheduled"},
        {"treatment": "Surgical resection", "category": "surgery", "days": 14, "priority": "scheduled"},
        {"treatment": "Adjuvant chemotherapy", "category": "chemotherapy", "days": 180, "priority": "scheduled"},
    ],
    "Prostate": [
        {"treatment": "MRI-guided biopsy", "category": "diagnostic", "days": 7, "priority": "immediate"},
        {"treatment": "Active surveillance OR radical prostatectomy", "category": "surgery", "days": 14, "priority": "scheduled"},
        {"treatment": "Radiation therapy (if non-surgical)", "category": "radiation", "days": 56, "priority": "scheduled"},
        {"treatment": "Androgen deprivation therapy", "category": "chemotherapy", "days": 730, "priority": "follow-up"},
    ],
    "Leukemia (Myeloid)": [
        {"treatment": "Bone marrow biopsy & cytogenetics", "category": "diagnostic", "days": 5, "priority": "immediate"},
        {"treatment": "Induction chemotherapy (7+3)", "category": "chemotherapy", "days": 28, "priority": "immediate"},
        {"treatment": "Consolidation chemotherapy", "category": "chemotherapy", "days": 28, "priority": "scheduled"},
        {"treatment": "Stem cell transplant evaluation", "category": "surgery", "days": 14, "priority": "scheduled"},
        {"treatment": "Maintenance therapy", "category": "chemotherapy", "days": 365, "priority": "follow-up"},
    ],
    "Non-Hodgkin Lymphoma": [
        {"treatment": "Excisional biopsy & staging PET/CT", "category": "diagnostic", "days": 10, "priority": "immediate"},
        {"treatment": "R-CHOP immunochemotherapy", "category": "chemotherapy", "days": 126, "priority": "scheduled"},
        {"treatment": "Radiation (if bulky/residual)", "category": "radiation", "days": 28, "priority": "scheduled"},
        {"treatment": "Surveillance PET q6 months", "category": "supportive", "days": 180, "priority": "follow-up"},
    ],
    "Multiple Myeloma": [
        {"treatment": "Bone marrow biopsy & FISH", "category": "diagnostic", "days": 7, "priority": "immediate"},
        {"treatment": "Induction (VRd: bortezomib/lenalidomide/dex)", "category": "chemotherapy", "days": 120, "priority": "scheduled"},
        {"treatment": "Autologous stem cell transplant", "category": "surgery", "days": 30, "priority": "scheduled"},
        {"treatment": "Maintenance lenalidomide", "category": "chemotherapy", "days": 730, "priority": "follow-up"},
    ],
}

# Default pathway for unknown cancer types
DEFAULT_PATHWAY = [
    {"treatment": "Biopsy and pathological staging", "category": "diagnostic", "days": 10, "priority": "immediate"},
    {"treatment": "Multidisciplinary tumor board review", "category": "diagnostic", "days": 3, "priority": "immediate"},
    {"treatment": "Primary treatment per tumor board", "category": "surgery", "days": 21, "priority": "scheduled"},
    {"treatment": "Adjuvant therapy as indicated", "category": "chemotherapy", "days": 120, "priority": "scheduled"},
    {"treatment": "Surveillance per guidelines", "category": "supportive", "days": 90, "priority": "follow-up"},
]


class TreatmentPathwayEngine:
    """Rule-based + heuristic treatment pathway recommender.

    Uses standard oncology treatment protocols adjusted by patient factors
    (age, comorbidities, prior treatments).
    """

    def recommend_pathway(
        self,
        cancer_type: str,
        age: int = 65,
        stage_proxy: int = 2,
        charlson_score: int = 0,
        has_prior_chemo: bool = False,
        has_prior_surgery: bool = False,
        has_prior_radiation: bool = False,
    ) -> dict:
        """Generate treatment pathway recommendation."""

        # Get base pathway
        pathway_template = STANDARD_PATHWAYS.get(cancer_type, DEFAULT_PATHWAY)

        # Build steps with adjustments
        steps = []
        cumulative_days = 0
        for i, tmpl in enumerate(pathway_template):
            step = TreatmentStep(
                step=i + 1,
                treatment=tmpl["treatment"],
                category=tmpl["category"],
                estimated_days=tmpl["days"],
                priority=tmpl["priority"],
            )

            # Adjustments based on patient factors
            notes = []

            # Age adjustments
            if age >= 80 and tmpl["category"] == "surgery":
                step.treatment += " (consider fitness for surgery)"
                notes.append("Elderly patient - assess surgical fitness")
            if age >= 75 and tmpl["category"] == "chemotherapy":
                step.treatment += " (dose-reduced)"
                notes.append("Consider dose reduction for elderly")

            # Comorbidity adjustments
            if charlson_score >= 4 and tmpl["category"] in ("surgery", "chemotherapy"):
                notes.append(f"High comorbidity burden (Charlson={charlson_score})")

            # Prior treatment adjustments
            if has_prior_chemo and tmpl["category"] == "chemotherapy":
                notes.append("Prior chemotherapy exposure - check cumulative toxicity")
            if has_prior_radiation and tmpl["category"] == "radiation":
                notes.append("Prior radiation - assess re-irradiation feasibility")

            # Stage adjustments
            if stage_proxy >= 3:
                if tmpl["priority"] == "scheduled":
                    step.priority = "immediate"
                    notes.append("Advanced stage - expedite treatment")

            cumulative_days += step.estimated_days
            steps.append(step)

        # Urgency score based on stage, age, comorbidities
        urgency = min(1.0, (stage_proxy / 4) * 0.5 + (age / 100) * 0.2 + (charlson_score / 10) * 0.3)

        # Build notes
        all_notes = []
        if stage_proxy >= 3:
            all_notes.append("Advanced stage disease - prioritize timely treatment initiation")
        if charlson_score >= 3:
            all_notes.append("Significant comorbidity burden - multidisciplinary review recommended")
        if age >= 75:
            all_notes.append("Geriatric oncology assessment recommended")
        all_notes.append("All recommendations subject to tumor board review and patient preferences")

        return {
            "cancer_type": cancer_type,
            "recommended_treatments": [s.treatment for s in steps],
            "treatment_sequence": [
                {
                    "step": s.step,
                    "treatment": s.treatment,
                    "category": s.category,
                    "estimated_days": s.estimated_days,
                    "priority": s.priority,
                }
                for s in steps
            ],
            "estimated_duration_days": cumulative_days,
            "urgency_score": round(urgency, 2),
            "notes": all_notes,
        }

    def assess_treatment_delay_risk(
        self,
        cancer_type: str,
        days_since_diagnosis: float,
        stage_proxy: int = 2,
    ) -> dict:
        """Assess whether patient is at risk of treatment delay."""

        # Recommended max days to first treatment by stage
        max_days = {1: 90, 2: 45, 3: 21, 4: 14}
        threshold = max_days.get(stage_proxy, 45)

        delay_risk = min(1.0, days_since_diagnosis / threshold)
        is_delayed = days_since_diagnosis > threshold

        return {
            "delay_risk_score": round(delay_risk, 2),
            "is_delayed": is_delayed,
            "days_since_diagnosis": round(days_since_diagnosis, 1),
            "recommended_max_days": threshold,
            "message": (
                f"ALERT: Treatment initiation delayed ({days_since_diagnosis:.0f} days vs "
                f"recommended {threshold} days for stage {stage_proxy})"
                if is_delayed
                else f"Within recommended timeframe ({days_since_diagnosis:.0f}/{threshold} days)"
            ),
        }
