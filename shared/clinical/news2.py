"""NEWS2 (National Early Warning Score 2) — Irish deterioration scoring.

Implements the Royal College of Physicians NEWS2 specification adopted by
the HSE for acute inpatient deterioration detection. Used by the
Predictive Deterioration Monitor (port 8220) and the Digital Twin
orchestrator on every vital event for non-ICU inpatients.

Aggregate score thresholds (RCP NEWS2 2017):
  - 0          routine monitoring
  - 1-4        ward-based review
  - ≥5 (or 3 in single parameter)  urgent clinical review
  - ≥7         continuous monitoring + ICU outreach
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple


@dataclass
class NEWS2Result:
    total: int
    components: Dict[str, int]
    any_param_eq_3: bool
    risk_band: str
    recommended_response: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "components": self.components,
            "any_param_eq_3": self.any_param_eq_3,
            "risk_band": self.risk_band,
            "recommended_response": self.recommended_response,
        }


def _score_rr(rr: Optional[float]) -> int:
    if rr is None:
        return 0
    if rr <= 8 or rr >= 25:
        return 3
    if rr >= 21:
        return 2
    if rr <= 11:
        return 1
    return 0


def _score_spo2_scale1(spo2: Optional[float]) -> int:
    if spo2 is None:
        return 0
    if spo2 <= 91:
        return 3
    if spo2 <= 93:
        return 2
    if spo2 <= 95:
        return 1
    return 0


def _score_spo2_scale2(spo2: Optional[float], on_air: bool) -> int:
    # NEWS2 Scale 2 — hypercapnic respiratory failure target range 88-92%
    if spo2 is None:
        return 0
    if on_air and spo2 >= 93:
        return 0
    if spo2 <= 83 or spo2 >= 97:
        return 3
    if spo2 <= 85 or spo2 >= 95:
        return 2
    if spo2 <= 87 or spo2 >= 93:
        return 1
    return 0


def _score_supplemental_o2(on_supplemental: bool) -> int:
    return 2 if on_supplemental else 0


def _score_temp(t: Optional[float]) -> int:
    if t is None:
        return 0
    if t <= 35.0:
        return 3
    if t >= 39.1:
        return 2
    if t <= 36.0 or t >= 38.1:
        return 1
    return 0


def _score_sbp(sbp: Optional[float]) -> int:
    if sbp is None:
        return 0
    if sbp <= 90 or sbp >= 220:
        return 3
    if sbp <= 100:
        return 2
    if sbp <= 110:
        return 1
    return 0


def _score_hr(hr: Optional[float]) -> int:
    if hr is None:
        return 0
    if hr <= 40 or hr >= 131:
        return 3
    if hr >= 111:
        return 2
    if hr <= 50 or hr >= 91:
        return 1
    return 0


def _score_consciousness(level: Optional[str]) -> int:
    """ACVPU: Alert / Confusion (new) / Voice / Pain / Unresponsive."""
    if not level:
        return 0
    normalised = level.strip().upper()
    return 0 if normalised in {"A", "ALERT"} else 3


def _risk_band(total: int, any_three: bool) -> tuple[str, str]:
    if total >= 7:
        return (
            "high",
            "Urgent senior review; continuous monitoring; ICU outreach referral",
        )
    if total >= 5 or any_three:
        return (
            "medium",
            "Urgent clinical review by competent clinician within 30 min",
        )
    if total >= 1:
        return ("low", "Ward-based monitoring; minimum 4-6 hourly observations")
    return ("none", "Minimum 12-hourly observations")


def compute_news2(
    *,
    respiratory_rate: Optional[float] = None,
    spo2: Optional[float] = None,
    on_supplemental_o2: bool = False,
    scale2_hypercapnic_target: bool = False,
    temperature_c: Optional[float] = None,
    systolic_bp: Optional[float] = None,
    heart_rate: Optional[float] = None,
    consciousness: Optional[str] = None,
) -> NEWS2Result:
    """Compute a NEWS2 result from vitals.

    Parameters mirror RCP NEWS2 observation chart. ``scale2_hypercapnic_target``
    should be set for patients with a documented target SpO₂ range of 88-92%
    (e.g. COPD with chronic hypercapnia).
    """
    on_air = not on_supplemental_o2
    components = {
        "respiratory_rate": _score_rr(respiratory_rate),
        "spo2": (
            _score_spo2_scale2(spo2, on_air)
            if scale2_hypercapnic_target
            else _score_spo2_scale1(spo2)
        ),
        "supplemental_o2": _score_supplemental_o2(on_supplemental_o2),
        "temperature": _score_temp(temperature_c),
        "systolic_bp": _score_sbp(systolic_bp),
        "heart_rate": _score_hr(heart_rate),
        "consciousness": _score_consciousness(consciousness),
    }
    total = sum(components.values())
    any_three = any(v == 3 for v in components.values())
    band, response = _risk_band(total, any_three)
    return NEWS2Result(
        total=total,
        components=components,
        any_param_eq_3=any_three,
        risk_band=band,
        recommended_response=response,
    )


# ---------------------------------------------------------------------------
# Trend analysis — a rising NEWS2 is clinically more concerning than a static
# score of the same magnitude. These helpers compute the slope over a
# trailing window and classify the trajectory.
# ---------------------------------------------------------------------------
@dataclass
class NEWS2TrendResult:
    """Trend information for a patient's NEWS2 scores over time."""

    current_score: int
    prior_score: Optional[int]
    delta: int
    window_minutes: int
    num_points_in_window: int
    slope_per_hour: float
    trajectory: str  # "rising" | "falling" | "stable" | "insufficient_data"
    is_clinically_rising: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "current_score": self.current_score,
            "prior_score": self.prior_score,
            "delta": self.delta,
            "window_minutes": self.window_minutes,
            "num_points_in_window": self.num_points_in_window,
            "slope_per_hour": self.slope_per_hour,
            "trajectory": self.trajectory,
            "is_clinically_rising": self.is_clinically_rising,
        }


def _parse_timestamp(ts: Any) -> datetime:
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, str):
        try:
            parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return datetime.now(timezone.utc)
    return datetime.now(timezone.utc)


def compute_news2_trend(
    history: Sequence[Dict[str, Any]],
    *,
    window_minutes: int = 240,
    now: Optional[datetime] = None,
) -> NEWS2TrendResult:
    """Compute slope of NEWS2 over a trailing window.

    Parameters
    ----------
    history:
        Sequence of ``{"total": int, "timestamp": str|datetime}`` snapshots,
        oldest-first or newest-first (the function sorts).
    window_minutes:
        Trailing window in minutes to regress over. Defaults to 240 min (4 h).
    now:
        Current simulation/wall time — defaults to UTC now.

    A score is "clinically rising" when:
      - at least 2 observations exist in the window, AND
      - slope > 1 point/hour, OR
      - delta between oldest-in-window and current is ≥ 2.
    """
    if not history:
        return NEWS2TrendResult(
            current_score=0,
            prior_score=None,
            delta=0,
            window_minutes=window_minutes,
            num_points_in_window=0,
            slope_per_hour=0.0,
            trajectory="insufficient_data",
            is_clinically_rising=False,
        )

    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=window_minutes)

    # Normalise and sort by timestamp ascending
    points: List[Tuple[datetime, int]] = []
    for snap in history:
        ts = _parse_timestamp(snap.get("timestamp") or snap.get("observed_at"))
        total = snap.get("total")
        if total is None:
            total = (snap.get("news2") or {}).get("total", 0)
        points.append((ts, int(total)))
    points.sort(key=lambda p: p[0])

    current_score = points[-1][1]

    windowed = [p for p in points if p[0] >= cutoff]
    if len(windowed) < 2:
        return NEWS2TrendResult(
            current_score=current_score,
            prior_score=points[-2][1] if len(points) >= 2 else None,
            delta=0,
            window_minutes=window_minutes,
            num_points_in_window=len(windowed),
            slope_per_hour=0.0,
            trajectory="insufficient_data",
            is_clinically_rising=False,
        )

    # Slope via simple linear regression (least squares).
    start_ts = windowed[0][0]
    xs = [(ts - start_ts).total_seconds() / 3600.0 for ts, _ in windowed]
    ys = [float(y) for _, y in windowed]
    n = len(xs)
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    num = sum((xs[i] - x_mean) * (ys[i] - y_mean) for i in range(n))
    den = sum((xs[i] - x_mean) ** 2 for i in range(n))
    slope = num / den if den > 0 else 0.0

    first_in_window = windowed[0][1]
    delta = current_score - first_in_window

    if slope > 0.25 or delta >= 2:
        trajectory = "rising"
    elif slope < -0.25 or delta <= -2:
        trajectory = "falling"
    else:
        trajectory = "stable"

    is_rising = (slope > 1.0) or (delta >= 2)

    return NEWS2TrendResult(
        current_score=current_score,
        prior_score=first_in_window,
        delta=delta,
        window_minutes=window_minutes,
        num_points_in_window=n,
        slope_per_hour=round(slope, 3),
        trajectory=trajectory,
        is_clinically_rising=is_rising,
    )


__all__ = [
    "compute_news2",
    "NEWS2Result",
    "compute_news2_trend",
    "NEWS2TrendResult",
]
