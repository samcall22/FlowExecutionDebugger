# Flow Execution Debugger
# Copyright (C) 2026 samcall22 (https://github.com/samcall22)
# SPDX-License-Identifier: GPL-3.0-or-later
# This program comes with ABSOLUTELY NO WARRANTY. See the LICENSE file for details.

"""Solution -> flow mapping read from Dataverse through the Web API."""
from __future__ import annotations

import json
import uuid
from typing import Callable, Dict, List, Optional, Set, Tuple

import requests

from .http_utils import LogFn, get_string_with_retry, report

PAGE_SIZE = 5000
EXCLUDED_SOLUTIONS = {"default", "active", "basic"}

# (solution -> flow ids, solution -> flow names, flow id -> (name, is_on))
SolutionMaps = Tuple[Dict[str, Set[str]], Dict[str, Set[str]], Dict[str, Tuple[str, bool]]]


def normalize_guid(value: Optional[str]) -> str:
    """Lower-case 'D' GUID format when possible, so braces / upper case still match."""
    if not value:
        return ""
    try:
        return str(uuid.UUID(value.strip()))
    except ValueError:
        return value.strip().lower()


def _retrieve_all(session: requests.Session, base: str, query: str, token_fn: Callable[[], str], log: LogFn) -> List[dict]:
    """Follows @odata.nextLink so results are never truncated at the first page."""
    url: Optional[str] = f"{base}/api/data/v9.2/{query}"
    rows: List[dict] = []
    while url:
        headers = {
            "Authorization": "Bearer " + token_fn(),
            "Accept": "application/json",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
            "Prefer": f"odata.maxpagesize={PAGE_SIZE}",
        }
        data = json.loads(get_string_with_retry(session, url, headers, log))
        rows.extend(data.get("value") or [])
        url = data.get("@odata.nextLink")
    return rows


def load_solution_maps(instance_url: str, token_fn: Callable[[], str], log: LogFn = None) -> SolutionMaps:
    """
    Returns (solution name -> flow GUIDs, solution name -> flow names,
    flow GUID -> (name, is_on) for every cloud flow that belongs to a solution).

    A solution cloud flow has TWO workflow rows: the ROOT one (referenced by
    solutioncomponent.objectid) and the ACTIVATED copy whose workflowid is the
    GUID the Power Automate API returns ('parentworkflowid' points to the root).
    Both ids are registered for each solution.
    """
    id_map: Dict[str, Set[str]] = {}
    name_map: Dict[str, Set[str]] = {}
    flow_details: Dict[str, Tuple[str, bool]] = {}
    base = instance_url.rstrip("/")
    session = requests.Session()

    report(log, "Reading solutions...")
    sols = _retrieve_all(session, base, "solutions?$select=solutionid,friendlyname,uniquename", token_fn, log)

    sol_names: Dict[str, str] = {}
    excluded: Set[str] = set()
    for s in sols:
        sid = normalize_guid(s.get("solutionid"))
        unique = s.get("uniquename") or ""
        sol_names[sid] = s.get("friendlyname") or unique
        if unique.lower() in EXCLUDED_SOLUTIONS:
            excluded.add(sid)

    # Cloud flows (category 5 = Modern Flow).
    report(log, "Reading cloud flows from Dataverse...")
    wfs = _retrieve_all(
        session, base,
        "workflows?$select=workflowid,name,category,type,statecode,_parentworkflowid_value&$filter=category eq 5",
        token_fn, log,
    )

    cloud_flow_ids: Set[str] = set()
    wf_names: Dict[str, str] = {}
    wf_on: Dict[str, bool] = {}
    children_of_root: Dict[str, List[str]] = {}
    for wf in wfs:
        wid = normalize_guid(wf.get("workflowid"))
        cloud_flow_ids.add(wid)
        if wf.get("name"):
            wf_names[wid] = wf["name"]
        wf_on[wid] = wf.get("statecode") == 1
        parent = wf.get("_parentworkflowid_value")
        if parent:
            children_of_root.setdefault(normalize_guid(parent), []).append(wid)

    # Solution components (componenttype 29 = Workflow).
    report(log, "Reading solution components...")
    comps = _retrieve_all(
        session, base,
        "solutioncomponents?$select=objectid,_solutionid_value,componenttype&$filter=componenttype eq 29",
        token_fn, log,
    )

    for c in comps:
        object_id = normalize_guid(c.get("objectid"))
        sol_id = normalize_guid(c.get("_solutionid_value"))
        if not object_id or not sol_id or sol_id in excluded:
            continue
        sol_name = sol_names.get(sol_id)
        if sol_name is None:
            continue
        # Discard classic workflows / business process flows.
        if object_id not in cloud_flow_ids:
            continue

        id_set = id_map.setdefault(sol_name, set())
        name_set = name_map.setdefault(sol_name, set())

        id_set.add(object_id)
        if object_id in wf_names:
            name_set.add(wf_names[object_id].lower())

        for kid in children_of_root.get(object_id, []):
            id_set.add(kid)
            if kid in wf_names:
                name_set.add(wf_names[kid].lower())

        # The activated copy (if any) is the id the Power Automate API knows.
        primary = (children_of_root.get(object_id) or [object_id])[0]
        name = wf_names.get(primary) or wf_names.get(object_id) or primary
        flow_details[primary] = (name, wf_on.get(primary, False) or wf_on.get(object_id, False))

    return id_map, name_map, flow_details
