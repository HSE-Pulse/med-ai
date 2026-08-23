"""
Clinical Scribe Model Definitions
===================================
1. **ICDCoder** -- TF-IDF + per-code LogisticRegression for ICD-10 multi-label.
2. **KeywordNEREngine** -- Dictionary-based NER from MIMIC ground truth.
3. **SectionClassifier** -- TF-IDF + LogisticRegression for note sections.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("clinical_scribe.model")


class ICDCoder:
    """TF-IDF + per-code LogisticRegression for top-K ICD code prediction."""

    def __init__(self, max_features: int = 10000, ngram_range: Tuple[int, int] = (1, 2),
                 C: float = 1.0) -> None:
        self.max_features = max_features
        self.ngram_range = ngram_range
        self.C = C
        self.tfidf = None
        self.classifiers: Dict[str, Any] = {}
        self.icd_codes: List[str] = []

    def fit(self, texts: List[str], labels_df) -> "ICDCoder":
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression

        self.tfidf = TfidfVectorizer(
            max_features=self.max_features,
            ngram_range=self.ngram_range,
            stop_words="english",
            dtype=np.float32,
        )
        X = self.tfidf.fit_transform(texts)
        logger.info("TF-IDF matrix: %s", X.shape)

        # Identify ICD code columns
        self.icd_codes = [c for c in labels_df.columns
                          if c not in ("hadm_id", "subject_id", "text")]

        for i, code in enumerate(self.icd_codes):
            y = labels_df[code].values.astype(int)
            if y.sum() < 5:  # Skip very rare codes
                continue
            clf = LogisticRegression(C=self.C, max_iter=500, solver="liblinear")
            clf.fit(X, y)
            self.classifiers[code] = clf
            if (i + 1) % 10 == 0:
                logger.info("  Trained %d/%d code classifiers", i + 1, len(self.icd_codes))

        logger.info("ICDCoder trained: %d classifiers", len(self.classifiers))
        return self

    def predict_top_k(self, text: str, k: int = 10) -> List[Tuple[str, float]]:
        if self.tfidf is None:
            return []
        X = self.tfidf.transform([text])
        scores = {}
        for code, clf in self.classifiers.items():
            prob = clf.predict_proba(X)[0]
            scores[code] = float(prob[1]) if len(prob) > 1 else 0.0
        return sorted(scores.items(), key=lambda x: -x[1])[:k]

    def predict_batch(self, texts: List[str]) -> Dict[str, np.ndarray]:
        if self.tfidf is None:
            return {}
        X = self.tfidf.transform(texts)
        results = {}
        for code, clf in self.classifiers.items():
            proba = clf.predict_proba(X)
            results[code] = proba[:, 1] if proba.shape[1] > 1 else np.zeros(len(texts))
        return results

    def get_params(self) -> Dict:
        return {"max_features": self.max_features, "ngram_range": self.ngram_range,
                "C": self.C, "n_codes": len(self.classifiers)}


class KeywordNEREngine:
    """Dictionary-based NER populated from MIMIC ground truth."""

    def __init__(self) -> None:
        self.medication_set: set = set()
        self.diagnosis_terms: Dict[str, str] = {}  # term → code
        self.procedure_terms: Dict[str, str] = {}

    def fit(self, medications: List[str], diagnoses: List[Dict] = None,
            procedures: List[Dict] = None) -> "KeywordNEREngine":
        # Build medication dictionary (lowercase)
        self.medication_set = {str(m).lower().strip() for m in medications if m and isinstance(m, (str, int, float))}
        logger.info("KeywordNEREngine: %d medications", len(self.medication_set))

        # Common diagnosis terms
        self.diagnosis_terms = {
            "pneumonia": "J18.9", "heart failure": "I50.9", "sepsis": "A41.9",
            "diabetes": "E11.9", "hypertension": "I10", "copd": "J44.1",
            "stroke": "I63.9", "myocardial infarction": "I21.9",
            "atrial fibrillation": "I48.91", "urinary tract infection": "N39.0",
            "acute kidney injury": "N17.9", "deep vein thrombosis": "I82.90",
            "pulmonary embolism": "I26.99", "gastrointestinal bleeding": "K92.2",
            "anemia": "D64.9", "delirium": "F05", "hip fracture": "S72.009A",
        }
        return self

    def extract(self, text: str) -> Dict[str, List[Dict]]:
        text_lower = text.lower()
        result: Dict[str, List[Dict]] = {
            "medications": [], "diagnoses": [], "symptoms": [],
        }

        # Find medications
        for med in self.medication_set:
            if med in text_lower and len(med) > 3:
                result["medications"].append({"drug": med, "source": "dictionary"})

        # Find diagnoses
        for term, code in self.diagnosis_terms.items():
            if term in text_lower:
                result["diagnoses"].append({"term": term, "icd_code": code, "source": "dictionary"})

        # Common symptoms
        symptoms = ["pain", "fever", "cough", "dyspnea", "nausea", "vomiting",
                     "headache", "dizziness", "fatigue", "weakness", "edema"]
        result["symptoms"] = [{"symptom": s, "source": "keyword"} for s in symptoms if s in text_lower]

        return result

    def get_params(self) -> Dict:
        return {"n_medications": len(self.medication_set),
                "n_diagnosis_terms": len(self.diagnosis_terms)}


class SectionClassifier:
    """TF-IDF + LogisticRegression for clinical note section classification."""

    def __init__(self, max_features: int = 5000) -> None:
        self.max_features = max_features
        self.tfidf = None
        self.clf = None
        self.labels: List[str] = []

    def fit(self, texts: List[str], section_labels: List[str]) -> "SectionClassifier":
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression

        self.tfidf = TfidfVectorizer(max_features=self.max_features, stop_words="english",
                                      dtype=np.float32)
        X = self.tfidf.fit_transform(texts)
        self.labels = sorted(set(section_labels))

        self.clf = LogisticRegression(multi_class="multinomial", max_iter=500,
                                       solver="lbfgs", C=1.0)
        self.clf.fit(X, section_labels)
        logger.info("SectionClassifier trained: %d classes, %s features", len(self.labels), X.shape[1])
        return self

    def predict(self, text: str) -> str:
        if self.tfidf is None or self.clf is None:
            return "unknown"
        X = self.tfidf.transform([text])
        return self.clf.predict(X)[0]

    def predict_proba(self, text: str) -> Dict[str, float]:
        if self.tfidf is None or self.clf is None:
            return {}
        X = self.tfidf.transform([text])
        proba = self.clf.predict_proba(X)[0]
        return {label: float(p) for label, p in zip(self.clf.classes_, proba)}

    def get_params(self) -> Dict:
        return {"max_features": self.max_features, "n_classes": len(self.labels)}
