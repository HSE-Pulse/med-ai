"""HSE TrolleyGAR client — real national trolley counts, by zone and hospital.

Source
------
The HSE Special Delivery Unit publishes the TrolleyGAR report daily. Acute
hospitals report at 08:00, 14:00 and 20:00; the 08:00 snapshot is the one
carried on the public report and is updated through the morning.

There is no documented JSON API. The public page at
``www2.hse.ie/services/urgent-emergency-care-report/`` posts a date to
``uec.hse.ie/uec/TGAR.php?EDDATE=DD/MM/YYYY`` and renders HTML, so this
module fetches that endpoint and parses the table. Both hosts allow
automated access under robots.txt (``Allow: /``), and the scheduler calls
this once per day with a descriptive User-Agent — one request per day is
well inside anything a public report is sized for.

Because we are parsing HTML that we do not control, every parse is
RECONCILED before it is accepted: per-hospital trolley counts must sum to
each zone's published subtotal, and the zone subtotals must sum to the
published national total. A layout change upstream therefore surfaces as a
rejected parse rather than silently wrong numbers.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import date as _date
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TGAR_URL = "https://uec.hse.ie/uec/TGAR.php"
USER_AGENT = "HSE-Pulse/1.0 (+https://dashboard.harishankar.info) daily-trolleygar"

# The six HSE health regions ("zones") as they appear in the report. Matched
# case-insensitively on a normalised form so "&"/"and" variants both hit —
# the report uses "HSE Dublin & North East" as a heading and "HSE Dublin and
# North East Total" on the subtotal row.
ZONES = [
    "HSE Dublin and North East",
    "HSE Dublin and Midlands",
    "HSE Dublin and South East",
    "HSE South West",
    "HSE Mid West",
    "HSE West and North West",
]

# Values sit at fixed offsets FROM THE LABEL CELL, not at absolute column
# indices. Two quirks in the source make absolute indexing wrong:
#
#   1. The report emits 89 <tr> but only 54 </tr>. Splitting on "<tr" rather
#      than matching "<tr>...</tr>" is the only way to recover every row —
#      a close-tag-based regex silently swallows 35 of them, including
#      Beaumont and Galway University Hospital.
#   2. Because of those missing close tags, a zone heading and that zone's
#      FIRST hospital arrive concatenated in one row (44 cells = 21 + 23),
#      shifting every column by 21.
#
# Anchoring on the label absorbs both.
OFF_ED, OFF_WARD, OFF_TOTAL = 9, 10, 11
OFF_SURGE, OFF_DTOC, OFF_OVER24 = 13, 15, 18
# The national row carries one fewer leading pad cell than hospital rows.
OFF_NATIONAL = 1
ZONE_ROW_SPLIT = 21


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("&", "and")).strip().lower()


def _cells(row_html: str) -> List[str]:
    """Cell text with empties preserved and colspans expanded.

    Cell content is read up to the next tag boundary rather than requiring
    a closing </td>, for the same reason rows are split rather than matched.
    """
    out: List[str] = []
    for m in re.finditer(
        r"<t[hd]([^>]*)>(.*?)(?=</t[hd]>|<t[hd]\b|</tr>|$)", row_html, flags=re.S | re.I
    ):
        attrs, inner = m.group(1), m.group(2)
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", inner))).strip()
        out.append(text)
        span = re.search(r'colspan="?(\d+)', attrs, re.I)
        if span:
            out.extend([""] * (int(span.group(1)) - 1))
    return out


def _split_rows(table_html: str) -> List[str]:
    """Every <tr>, including the 35-odd that never get closed."""
    return re.split(r"<tr\b", table_html, flags=re.I)[1:]


def _label_index(cells: List[str]) -> int:
    for i, c in enumerate(cells):
        if c.strip():
            return i
    return -1


def _metrics(cells: List[str], anchor: int) -> Dict[str, Optional[int]]:
    return {
        "ed_trolleys": _int_at(cells, anchor + OFF_ED),
        "ward_trolleys": _int_at(cells, anchor + OFF_WARD),
        "total_trolleys": _int_at(cells, anchor + OFF_TOTAL),
        "surge_capacity": _int_at(cells, anchor + OFF_SURGE),
        "delayed_transfers": _int_at(cells, anchor + OFF_DTOC),
        "waiting_over_24h": _int_at(cells, anchor + OFF_OVER24),
    }


def _int_at(cells: List[str], idx: int) -> Optional[int]:
    if idx >= len(cells):
        return None
    v = cells[idx].strip()
    if not v or not re.fullmatch(r"-?\d+", v):
        return None
    return int(v)


@dataclass
class HospitalRow:
    hospital: str
    zone: str
    ed_trolleys: int = 0
    ward_trolleys: int = 0
    total_trolleys: int = 0
    surge_capacity: Optional[int] = None
    delayed_transfers: Optional[int] = None
    waiting_over_24h: Optional[int] = None


@dataclass
class TrolleyGARReport:
    report_date: str
    national: Dict[str, Optional[int]] = field(default_factory=dict)
    zones: Dict[str, Dict[str, Optional[int]]] = field(default_factory=dict)
    hospitals: List[HospitalRow] = field(default_factory=list)
    reconciled: bool = False
    reconciliation_notes: List[str] = field(default_factory=list)
    source_url: str = ""
    fetched_at_wall: str = ""

    def to_doc(self) -> Dict[str, Any]:
        d = asdict(self)
        d["hospitals"] = [asdict(h) if not isinstance(h, dict) else h for h in self.hospitals]
        return d


def parse_trolleygar(page_html: str, report_date: str) -> TrolleyGARReport:
    """Parse the TrolleyGAR HTML into a reconciled report."""
    rep = TrolleyGARReport(report_date=report_date, source_url=TGAR_URL)

    body = re.sub(r"<(script|style).*?</\1>", "", page_html, flags=re.S | re.I)
    table = re.search(r"<table.*?</table>", body, flags=re.S | re.I)
    if not table:
        rep.reconciliation_notes.append("no <table> found in response")
        return rep

    zone_lookup = {_norm(z): z for z in ZONES}
    current_zone: Optional[str] = None

    def zone_at(text: str, want_total: bool) -> Optional[str]:
        n = _norm(text)
        for norm_name, canonical in zone_lookup.items():
            has_total = f"{norm_name} total" in n
            if norm_name in n and has_total == want_total:
                return canonical
        return None

    for raw in _split_rows(table.group(0)):
        cells = _cells(raw)
        li = _label_index(cells)
        if li < 0:
            continue
        label = cells[li].strip()

        # National total — one fewer leading pad cell than hospital rows.
        if _norm(label).startswith("national total"):
            rep.national = _metrics(cells, li + OFF_NATIONAL)
            continue

        # A zone SUBTOTAL row carries the next zone's heading after it.
        sub = zone_at(label, want_total=True)
        if sub:
            rep.zones[sub] = _metrics(cells, li + OFF_NATIONAL)
            trailing = " ".join(c for c in cells[li + 1:] if c)
            nxt = zone_at(trailing, want_total=False)
            current_zone = nxt
            continue

        # A zone HEADING row also carries that zone's first hospital,
        # concatenated because the heading row is never closed.
        head = zone_at(label, want_total=False)
        if head:
            current_zone = head
            rest = cells[ZONE_ROW_SPLIT:]
            ri = _label_index(rest)
            if ri >= 0 and rest[ri].strip():
                rep.hospitals.append(_hospital(rest, ri, current_zone))
            continue

        if not current_zone:
            continue
        rep.hospitals.append(_hospital(cells, li, current_zone))

    _reconcile(rep)
    return rep


def _hospital(cells: List[str], anchor: int, zone: str) -> HospitalRow:
    m = _metrics(cells, anchor)
    return HospitalRow(
        hospital=cells[anchor].strip(),
        zone=zone,
        ed_trolleys=m["ed_trolleys"] or 0,
        ward_trolleys=m["ward_trolleys"] or 0,
        total_trolleys=m["total_trolleys"] or 0,
        surge_capacity=m["surge_capacity"],
        delayed_transfers=m["delayed_transfers"],
        waiting_over_24h=m["waiting_over_24h"],
    )


def _reconcile(rep: TrolleyGARReport) -> None:
    """Accept the parse only if the numbers add up.

    We are scraping a page we do not own. Without this, an upstream layout
    change shifts a column and we would store confident, wrong national
    trolley figures — worse than storing nothing.
    """
    notes = rep.reconciliation_notes
    ok = True

    if not rep.zones:
        notes.append("no zone subtotals parsed")
        ok = False
    if not rep.hospitals:
        notes.append("no hospital rows parsed")
        ok = False
    if not rep.national.get("total_trolleys") and rep.national.get("total_trolleys") != 0:
        notes.append("no national total parsed")
        ok = False

    # Hospitals must sum to their zone subtotal (trolley columns only —
    # the other columns carry merged cells we deliberately don't rely on).
    for zone, totals in rep.zones.items():
        for key in ("ed_trolleys", "ward_trolleys", "total_trolleys"):
            published = totals.get(key)
            if published is None:
                continue
            summed = sum(getattr(h, key) for h in rep.hospitals if h.zone == zone)
            if summed != published:
                notes.append(
                    f"{zone}: {key} hospitals sum to {summed}, report says {published}"
                )
                ok = False

    # Surge capacity and delayed transfers are displayed, so they get the
    # same treatment. Hospitals may legitimately report None (no figure
    # published for that site), so only non-null values are summed —
    # a parse error that nulled a real value still shows up as a shortfall.
    for key in ("surge_capacity", "delayed_transfers"):
        published = rep.national.get(key)
        if published is None:
            continue
        summed = sum(
            getattr(h, key) or 0 for h in rep.hospitals if getattr(h, key) is not None
        )
        if summed != published:
            notes.append(
                f"national {key}: hospitals sum to {summed}, report says {published}"
            )
            ok = False

    # Zones must sum to the national total.
    for key in ("ed_trolleys", "ward_trolleys", "total_trolleys"):
        published = rep.national.get(key)
        if published is None:
            continue
        summed = sum(
            (z.get(key) or 0) for z in rep.zones.values() if z.get(key) is not None
        )
        if summed != published:
            notes.append(f"national {key}: zones sum to {summed}, report says {published}")
            ok = False

    if len(rep.zones) != len(ZONES):
        notes.append(f"expected {len(ZONES)} zones, parsed {len(rep.zones)}")
        ok = False

    rep.reconciled = ok


def fetch_trolleygar(on: Optional[_date] = None, timeout: float = 30.0) -> TrolleyGARReport:
    """Fetch and parse one day's TrolleyGAR report. Blocking."""
    import datetime as _dt
    import httpx

    day = on or _dt.date.today()
    eddate = day.strftime("%d/%m/%Y")
    with httpx.Client(timeout=timeout, follow_redirects=True) as c:
        r = c.get(TGAR_URL, params={"EDDATE": eddate},
                  headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        page = r.text

    rep = parse_trolleygar(page, report_date=day.isoformat())
    rep.fetched_at_wall = _dt.datetime.utcnow().isoformat()
    if not rep.reconciled:
        logger.warning(
            "trolleygar_parse_unreconciled date=%s notes=%s",
            rep.report_date, "; ".join(rep.reconciliation_notes)[:400],
        )
    return rep
