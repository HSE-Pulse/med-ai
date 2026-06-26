"""ICD-10 / ICD-9 name lookup against the MIMIC reference tables.

MongoDB holds two reference collections sourced from MIMIC-IV:

- ``MIMIC.d_icd_diagnoses``  — keyed by ``(icd_code, icd_version)`` → ``long_title``
- ``MIMIC.d_icd_procedures`` — keyed by ``(icd_code, icd_version)`` → ``long_title``

Downstream modules (sim journey endpoint, Patient Journey, Scribe, etc.)
need to attach human-readable titles to raw ICD rows without paying the
database lookup cost on every request.  ``IcdNameResolver`` handles the
join and keeps an in-process LRU-style cache keyed by ``(code, version)``.

Usage
-----

>>> from shared.clinical.icd_names import get_icd_resolver
>>> resolver = get_icd_resolver(mongo_client)
>>> resolver.resolve_diagnoses([{"icd_code": "H4923", "icd_version": 10}])
[{'icd_code': 'H4923', 'icd_version': 10, 'long_title': 'Sixth [abducent] nerve palsy, bilateral'}]

Both lookup methods accept a sequence of dict rows and return new dicts
with ``long_title`` filled in (existing values are preserved, empty strings
are replaced).  Unknown codes fall back to ``"(unknown diagnosis/procedure)"``
so the UI always has something to render.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

# Per-process cache shared across all resolvers bound to the same DB.
_CACHE_LOCK = threading.Lock()
_DX_CACHE: Dict[Tuple[str, int], str] = {}
_PROC_CACHE: Dict[Tuple[str, int], str] = {}


def _key(code: Any, version: Any) -> Optional[Tuple[str, int]]:
    """Normalise a ``(code, version)`` pair into a hashable cache key."""
    if code is None:
        return None
    try:
        code_s = str(code).strip()
        if not code_s:
            return None
        ver = int(version) if version is not None else 10
    except (TypeError, ValueError):
        return None
    return code_s, ver


class IcdNameResolver:
    """Resolve ICD ``long_title`` values for diagnoses + procedures.

    Parameters
    ----------
    mongo_client:
        Any pymongo-compatible client.  Only read access to the reference
        database is required.
    reference_db:
        Database holding the ``d_icd_*`` collections.  Defaults to ``MIMIC``
        (where MIMIC-IV reference tables were loaded for this platform).
    """

    DEFAULT_DB = "MIMIC"
    DX_COLLECTION = "d_icd_diagnoses"
    PROC_COLLECTION = "d_icd_procedures"
    UNKNOWN_DX = "(unknown diagnosis)"
    UNKNOWN_PROC = "(unknown procedure)"

    def __init__(self, mongo_client: Any, reference_db: str = DEFAULT_DB) -> None:
        self._client = mongo_client
        self._reference_db = reference_db
        # Per-instance warmups skipped — the global cache handles repeat calls.

    # ------------------------------------------------------------------ lookup primitives
    def _db(self):
        try:
            return self._client[self._reference_db]
        except Exception as exc:  # noqa: BLE001 - degrade gracefully
            logger.debug("icd_resolver_db_unavailable: %s", exc)
            return None

    def _fetch_missing(
        self,
        collection_name: str,
        cache: Dict[Tuple[str, int], str],
        keys: Iterable[Tuple[str, int]],
    ) -> None:
        db = self._db()
        if db is None:
            return
        missing = [k for k in keys if k not in cache]
        if not missing:
            return
        # MIMIC-IV's reference collections store ICD-9 numeric codes as
        # INTEGERS (e.g. ``icd_code: 5118``) while ICD-9 E-codes and all
        # ICD-10 codes are stored as STRINGS (e.g. ``icd_code: 'E8798'``,
        # ``'H4923'``). We build a query clause that matches either shape
        # so both families resolve from a single round-trip.
        def _code_variants(code_s: str):
            variants = [code_s]
            try:
                variants.append(int(code_s))
            except ValueError:
                pass
            return variants

        or_clauses = []
        for code, version in missing:
            or_clauses.append({"icd_code": {"$in": _code_variants(code)}, "icd_version": version})
        query = {"$or": or_clauses}
        try:
            cursor = db[collection_name].find(
                query,
                {"_id": 0, "icd_code": 1, "icd_version": 1, "long_title": 1},
            )
            with _CACHE_LOCK:
                for doc in cursor:
                    k = _key(doc.get("icd_code"), doc.get("icd_version"))
                    if k and doc.get("long_title"):
                        cache[k] = str(doc["long_title"])
        except Exception as exc:  # noqa: BLE001 - log once per error
            logger.debug("icd_resolver_query_failed: %s", exc)

    # ------------------------------------------------------------------ public API
    def resolve_diagnoses(self, rows: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        """Return copies of each diagnosis row with ``long_title`` populated."""
        rows_list = list(rows)
        keys = [
            k for k in (_key(r.get("icd_code"), r.get("icd_version")) for r in rows_list)
            if k is not None
        ]
        self._fetch_missing(self.DX_COLLECTION, _DX_CACHE, keys)
        return [self._enrich(r, _DX_CACHE, self.UNKNOWN_DX) for r in rows_list]

    def resolve_procedures(self, rows: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        """Return copies of each procedure row with ``long_title`` populated."""
        rows_list = list(rows)
        keys = [
            k for k in (_key(r.get("icd_code"), r.get("icd_version")) for r in rows_list)
            if k is not None
        ]
        self._fetch_missing(self.PROC_COLLECTION, _PROC_CACHE, keys)
        return [self._enrich(r, _PROC_CACHE, self.UNKNOWN_PROC) for r in rows_list]

    def resolve_one_diagnosis(self, code: Any, version: Any = 10) -> Optional[str]:
        """Single-code convenience — returns ``long_title`` or ``None``."""
        k = _key(code, version)
        if k is None:
            return None
        self._fetch_missing(self.DX_COLLECTION, _DX_CACHE, [k])
        return _DX_CACHE.get(k)

    def resolve_one_procedure(self, code: Any, version: Any = 10) -> Optional[str]:
        k = _key(code, version)
        if k is None:
            return None
        self._fetch_missing(self.PROC_COLLECTION, _PROC_CACHE, [k])
        return _PROC_CACHE.get(k)

    # ------------------------------------------------------------------ internal
    @staticmethod
    def _enrich(
        row: Mapping[str, Any], cache: Mapping[Tuple[str, int], str], fallback: str
    ) -> Dict[str, Any]:
        out = dict(row)
        existing = out.get("long_title")
        if not existing:
            k = _key(out.get("icd_code"), out.get("icd_version"))
            out["long_title"] = cache.get(k) if k else None
        if not out.get("long_title"):
            out["long_title"] = fallback
        return out


# ------------------------------------------------------------------ module-level helpers

_SINGLETON_RESOLVER: Optional[IcdNameResolver] = None
_SINGLETON_LOCK = threading.Lock()


def get_icd_resolver(mongo_client: Any, reference_db: str = IcdNameResolver.DEFAULT_DB) -> IcdNameResolver:
    """Return a process-wide singleton bound to the given mongo client + reference DB."""
    global _SINGLETON_RESOLVER
    with _SINGLETON_LOCK:
        if _SINGLETON_RESOLVER is None or _SINGLETON_RESOLVER._client is not mongo_client:
            _SINGLETON_RESOLVER = IcdNameResolver(mongo_client, reference_db=reference_db)
    return _SINGLETON_RESOLVER


__all__ = ["IcdNameResolver", "get_icd_resolver"]
