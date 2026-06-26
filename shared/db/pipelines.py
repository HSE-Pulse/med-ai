"""Reusable MongoDB aggregation pipelines for MIMIC data.

Consolidates the vital-sign, lab, and transfer aggregation patterns
that were repeated 10+ times across app_07 endpoints.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from shared.constants.mimic import (
    LAB_ITEMID_TO_NAME,
    LAB_ITEM_IDS,
    VITAL_ITEMID_TO_SHORT,
    VITAL_ITEM_IDS,
)


def latest_vitals_pipeline(
    hadm_ids: List[int],
    item_ids: Optional[List[int]] = None,
) -> List[Dict[str, Any]]:
    """Build an aggregation pipeline for latest vital per patient per itemid.

    Returns dicts with ``_id: {hadm_id, itemid}`` and ``val``.
    """
    ids = item_ids or VITAL_ITEM_IDS
    return [
        {"$match": {"hadm_id": {"$in": hadm_ids}, "itemid": {"$in": ids}}},
        {"$sort": {"charttime": -1}},
        {"$group": {
            "_id": {"hadm_id": "$hadm_id", "itemid": "$itemid"},
            "val": {"$first": "$valuenum"},
        }},
    ]


def latest_labs_pipeline(
    hadm_ids: List[int],
    item_ids: Optional[List[int]] = None,
) -> List[Dict[str, Any]]:
    """Build an aggregation pipeline for latest lab per patient per itemid."""
    ids = item_ids or LAB_ITEM_IDS
    return [
        {"$match": {"hadm_id": {"$in": hadm_ids}, "itemid": {"$in": ids}}},
        {"$sort": {"charttime": -1}},
        {"$group": {
            "_id": {"hadm_id": "$hadm_id", "itemid": "$itemid"},
            "val": {"$first": "$valuenum"},
        }},
    ]


def latest_department_pipeline(
    hadm_ids: List[int],
) -> List[Dict[str, Any]]:
    """Build an aggregation pipeline for latest department per patient."""
    return [
        {"$match": {"hadm_id": {"$in": hadm_ids}}},
        {"$sort": {"intime": -1}},
        {"$group": {
            "_id": "$hadm_id",
            "careunit": {"$first": "$careunit"},
            "eventtype": {"$first": "$eventtype"},
        }},
    ]


def collect_vitals(
    db: Any,
    hadm_ids: List[int],
    collection: str = "chartevents",
    item_ids: Optional[List[int]] = None,
    name_map: Optional[Dict[int, str]] = None,
) -> Dict[int, Dict[str, float]]:
    """Run vital aggregation and return {hadm_id: {short_name: value}}.

    Parameters
    ----------
    db : MongoDB database object (e.g., ``mongo.sim``)
    hadm_ids : list of admission IDs to query
    collection : MongoDB collection name
    item_ids : optional override for vital item IDs
    name_map : optional override for itemid → short name mapping
    """
    names = name_map or VITAL_ITEMID_TO_SHORT
    result: Dict[int, Dict[str, float]] = {}
    pipeline = latest_vitals_pipeline(hadm_ids, item_ids)
    for doc in db[collection].aggregate(pipeline):
        hid = doc["_id"]["hadm_id"]
        iid = doc["_id"]["itemid"]
        short = names.get(iid)
        if short:
            result.setdefault(hid, {})[short] = doc["val"]
    return result


def collect_labs(
    db: Any,
    hadm_ids: List[int],
    collection: str = "labevents",
    item_ids: Optional[List[int]] = None,
    name_map: Optional[Dict[int, str]] = None,
) -> Dict[int, Dict[str, float]]:
    """Run lab aggregation and return {hadm_id: {lab_name: value}}."""
    names = name_map or LAB_ITEMID_TO_NAME
    result: Dict[int, Dict[str, float]] = {}
    pipeline = latest_labs_pipeline(hadm_ids, item_ids)
    for doc in db[collection].aggregate(pipeline):
        hid = doc["_id"]["hadm_id"]
        iid = doc["_id"]["itemid"]
        name = names.get(iid)
        if name:
            result.setdefault(hid, {})[name] = doc["val"]
    return result


def collect_departments(
    db: Any,
    hadm_ids: List[int],
    collection: str = "transfers",
) -> Dict[int, str]:
    """Run transfer aggregation and return {hadm_id: department_name}."""
    result: Dict[int, str] = {}
    pipeline = latest_department_pipeline(hadm_ids)
    for doc in db[collection].aggregate(pipeline):
        result[doc["_id"]] = doc.get("careunit", "Unknown")
    return result
