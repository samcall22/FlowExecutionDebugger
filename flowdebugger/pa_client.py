# Flow Execution Debugger
# Copyright (C) 2026 samcall22 (https://github.com/samcall22)
# SPDX-License-Identifier: GPL-3.0-or-later
# This program comes with ABSOLUTELY NO WARRANTY. See the LICENSE file for details.

from __future__ import annotations

import copy
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote

import requests

from .http_utils import HttpStatusError, LogFn, get_string_with_retry, report
from .transforms import g, to_text

FLOW_API = "https://api.flow.microsoft.com"
API_VERSION = "2016-11-01"

DELAY_BETWEEN_CALLS_S = 0.12

# The /flows endpoint rejects any $top greater than 50.
FLOWS_PAGE_SIZE = 50
# The /runs endpoint does accept larger pages.
RUNS_PAGE_SIZE = 250

# Without this the /flows endpoint omits flows that live in Dataverse solutions.
SOLUTION_FLOWS_PARAM = "&include=includeSolutionCloudFlows"

ALL_STATUSES = "All"

TokenFn = Callable[[], str]


@dataclass
class EnvironmentInfo:
    id: str
    display_name: str
    instance_url: Optional[str]
    is_default: bool = False

    def __str__(self) -> str:
        suffix = "  [default]" if self.is_default else ""
        return f"{self.display_name}  ({self.id}){suffix}"


@dataclass
class FlowInfo:
    id: str
    name: str
    state: Optional[str]
    scope: str  # "admin", "personal" or "team"

    def __str__(self) -> str:
        return f"{self.name}  ({self.state})"


@dataclass
class RunInfo:
    name: str
    status: Optional[str]
    start: Optional[datetime]  # aware, local time
    end: Optional[datetime]
    start_time: Optional[str]  # "yyyy-MM-dd HH-mm-ss", valid for file names
    end_time: Optional[str]
    trigger_outputs_link: Optional[str]
    trigger_inputs: Optional[str] = None


@dataclass
class ParamMeta:
    key: str
    title: str
    description: str


# ===================== DATES =====================

_ISO_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$", re.IGNORECASE)


def to_local(value: Any) -> Optional[datetime]:
    """Parses an ISO date (with up to 7 fractional digits) and converts it to local time."""
    if not isinstance(value, str) or not value.strip():
        return None
    m = _ISO_RE.match(value.strip())
    if not m:
        return None
    base, frac, tz = m.groups()
    dt = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S")
    if frac:
        dt = dt.replace(microsecond=int((frac[1:] + "000000")[:6]))
    if not tz or tz.upper() == "Z":
        tzinfo = timezone.utc
    else:
        sign = 1 if tz[0] == "+" else -1
        digits = tz[1:].replace(":", "")
        tzinfo = timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:])))
    return dt.replace(tzinfo=tzinfo).astimezone()


def _utc_filter(dt: datetime) -> str:
    # Naive datetimes are interpreted as local time.
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _next_link(page: Any) -> Optional[str]:
    if not isinstance(page, dict):
        return None
    return page.get("nextLink") or page.get("@odata.nextLink")


def list_environments(token_fn: TokenFn, log: LogFn = None) -> List[EnvironmentInfo]:
    """Power Platform environments the signed-in user can access."""
    session = requests.Session()
    url = f"{FLOW_API}/providers/Microsoft.ProcessSimple/environments?api-version={API_VERSION}"
    envs: Dict[str, EnvironmentInfo] = {}
    while url:
        data = json.loads(get_string_with_retry(session, url, {"Authorization": "Bearer " + token_fn()}, log))
        for it in data.get("value") or []:
            env_id = it.get("name")
            if not env_id:
                continue
            props = it.get("properties") or {}
            envs[env_id.lower()] = EnvironmentInfo(
                id=env_id,
                display_name=props.get("displayName") or env_id,
                instance_url=g(props, "linkedEnvironmentMetadata", "instanceUrl"),
                is_default=bool(props.get("isDefault")),
            )
        url = _next_link(data)
    return sorted(envs.values(), key=lambda e: (not e.is_default, e.display_name.casefold()))


def build_run_url(env_id: str, flow_id: str, run_id: str) -> str:
    return f"https://make.powerautomate.com/environments/{env_id}/flows/{flow_id}/runs/{run_id}?v3=false"


class PowerAutomateClient:
    def __init__(self, token_fn: TokenFn, environment_id: str):
        self._token_fn = token_fn
        self.environment_id = environment_id
        self._http = requests.Session()
        # SAS links must never receive the bearer token.
        self._plain_http = requests.Session()
        self.log: LogFn = None
        # "admin" (whole environment) or "personal+team" (partial list).
        self.last_flow_list_scope: Optional[str] = None

    def _report(self, msg: str) -> None:
        report(self.log, msg)

    def _get(self, url: str) -> str:
        return get_string_with_retry(self._http, url, {"Authorization": "Bearer " + self._token_fn()}, self.log)

    def _get_plain(self, url: str) -> str:
        return get_string_with_retry(self._plain_http, url, None, self.log)

    def _get_json(self, url: str) -> Dict[str, Any]:
        return json.loads(self._get(url))

    @property
    def _env_base(self) -> str:
        return f"{FLOW_API}/providers/Microsoft.ProcessSimple/environments/{self.environment_id}"

    def build_run_url(self, flow_id: str, run_id: str) -> str:
        return build_run_url(self.environment_id, flow_id, run_id)

    # ===================== FLOWS =====================

    def list_flows(self) -> List[FlowInfo]:
        """
        Merges admin scope (whole environment, if permitted) with personal + team
        (including solution flows), since the admin list can miss some flows.
        A failing scope never aborts the whole listing.
        """
        by_id: Dict[str, FlowInfo] = {}
        admin_ok = False

        admin_url = (
            f"{FLOW_API}/providers/Microsoft.ProcessSimple/scopes/admin/environments/{self.environment_id}"
            f"/v2/flows?api-version={API_VERSION}&$top={FLOWS_PAGE_SIZE}"
        )
        try:
            self._report("Listing flows (admin scope)...")
            self._collect_flows(admin_url, by_id, "admin")
            admin_ok = bool(by_id)
            self._report(f"Admin scope: {len(by_id)} flows.")
        except Exception as ex:
            self._report(f"Admin scope not available ({ex}).")

        base_url = f"{self._env_base}/flows?api-version={API_VERSION}&$top={FLOWS_PAGE_SIZE}"
        for scope in ("personal", "team"):
            flt = "&$filter=" + quote(f"search('{scope}')", safe="")
            try:
                self._report(f"Listing flows ({scope})...")
                try:
                    self._collect_flows(base_url + SOLUTION_FLOWS_PARAM + flt, by_id, scope)
                except HttpStatusError as ex:
                    if ex.status != 400:
                        raise
                    self._report("Solution flows parameter rejected. Retrying without it...")
                    self._collect_flows(base_url + flt, by_id, scope)
            except Exception as ex:
                self._report(f"Could not list the '{scope}' flows: {ex}")

        if not by_id:
            try:
                self._report("Listing flows (no filter)...")
                self._collect_flows(base_url, by_id, "personal")
            except Exception as ex:
                self._report(f"Could not list the flows: {ex}")
                raise

        self.last_flow_list_scope = "admin" if admin_ok else "personal+team"
        self._report(f"Total: {len(by_id)} flows.")
        return self._sort_flows(by_id)

    def _collect_flows(self, url: Optional[str], target: Dict[str, FlowInfo], scope: str) -> None:
        page = 0
        while url:
            page += 1
            j = self._get_json(url)
            for it in j.get("value") or []:
                fid = it.get("name")
                if not fid:
                    continue
                # Deduplicate: a flow may appear in more than one scope.
                if fid.lower() in target:
                    continue
                props = it.get("properties") or {}
                target[fid.lower()] = FlowInfo(
                    id=fid,
                    name=props.get("displayName") or fid,
                    state=props.get("state"),
                    scope=scope,
                )
            self._report(f"Flows downloaded: {len(target)} (page {page}, {scope})")
            url = _next_link(j)
            if url:
                time.sleep(DELAY_BETWEEN_CALLS_S)

    @staticmethod
    def _sort_flows(by_id: Dict[str, FlowInfo]) -> List[FlowInfo]:
        return sorted(by_id.values(), key=lambda f: (f.name or "").casefold())

    # ===================== RUNS =====================

    def list_runs(
        self,
        flow_id: str,
        from_dt: Optional[datetime] = None,
        to_dt: Optional[datetime] = None,
        max_runs: int = 2000,
        status_filter: Optional[str] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> List[RunInfo]:
        """Runs with a SERVER-side date/status filter and a maximum limit."""
        top_token = f"$top={RUNS_PAGE_SIZE}"
        base_url = f"{self._env_base}/flows/{flow_id}/runs?api-version={API_VERSION}&{top_token}"

        filters = []
        if from_dt:
            filters.append(f"startTime ge {_utc_filter(from_dt)}")
        if to_dt:
            filters.append(f"startTime lt {_utc_filter(to_dt)}")
        if status_filter and status_filter.lower() != ALL_STATUSES.lower():
            filters.append(f"status eq '{status_filter}'")
        if filters:
            base_url += "&$filter=" + quote(" and ".join(filters), safe="")

        url: Optional[str] = base_url
        result: List[RunInfo] = []
        page = 0

        while url:
            if is_cancelled and is_cancelled():
                break
            page += 1
            try:
                body = self._get(url)
            except HttpStatusError as ex:
                if page == 1 and ex.status == 400 and top_token.lower() in url.lower():
                    # Some environments also cap the runs endpoint at 50 items per page.
                    self._report("The server rejected the page size. Retrying with $top=50...")
                    url = url.replace(top_token, "$top=50")
                    base_url = base_url.replace(top_token, "$top=50")
                    page = 0
                    continue
                if page > 1:
                    self._report(f"Pagination interrupted on page {page}: {ex}")
                    break
                raise
            except Exception as ex:
                if page > 1:
                    self._report(f"Pagination interrupted on page {page}: {ex}")
                    break
                raise

            j = json.loads(body)
            for it in j.get("value") or []:
                props = it.get("properties") or {}
                start = to_local(props.get("startTime"))
                end = to_local(props.get("endTime"))
                result.append(
                    RunInfo(
                        name=it.get("name"),
                        status=props.get("status"),
                        start=start,
                        end=end,
                        start_time=start.strftime("%Y-%m-%d %H-%M-%S") if start else None,
                        end_time=end.strftime("%Y-%m-%d %H-%M-%S") if end else None,
                        trigger_outputs_link=g(props, "trigger", "outputsLink", "uri"),
                    )
                )

            self._report(f"Runs downloaded: {len(result)} (page {page})")

            if len(result) >= max_runs:
                self._report(f"Limit of {max_runs} runs reached. Narrow the date range to see more.")
                break

            url = _next_link(j)
            if url:
                time.sleep(DELAY_BETWEEN_CALLS_S)

        return result

    def get_trigger_outputs(self, run: RunInfo) -> Optional[str]:
        if run is None:
            return None
        if run.trigger_inputs is not None:
            return run.trigger_inputs
        if not run.trigger_outputs_link:
            return None
        run.trigger_inputs = self._get_plain(run.trigger_outputs_link) or ""
        return run.trigger_inputs

    def get_trigger_params(self, run: RunInfo) -> Dict[str, str]:
        """Flattened trigger body: 'a/b[0]/c' -> value."""
        result: Dict[str, str] = {}
        try:
            text = self.get_trigger_outputs(run)
        except Exception as ex:
            self._report(f"Could not read the trigger parameters: {ex}")
            return result

        if not text:
            return result
        try:
            root = json.loads(text)
            if isinstance(root, dict) and "body" in root:
                _flatten(root["body"], "", result)
        except Exception:
            pass
        return result

    # ===================== PARAMETER METADATA =====================

    def get_flow_definition(self, flow_id: str) -> Dict[str, Any]:
        url = f"{self._env_base}/flows/{flow_id}?api-version={API_VERSION}"
        try:
            return self._get_json(url)
        except HttpStatusError as ex:
            if ex.status not in (403, 404):
                raise
            # Flow not owned by the user: try the admin scope before giving up.
            admin_url = (
                f"{FLOW_API}/providers/Microsoft.ProcessSimple/scopes/admin/environments/"
                f"{self.environment_id}/flows/{flow_id}?api-version={API_VERSION}"
            )
            return self._get_json(admin_url)

    def try_get_flow_display_name(self, flow_id: Optional[str]) -> Optional[str]:
        if not flow_id:
            return None
        try:
            return to_text(g(self.get_flow_definition(flow_id), "properties", "displayName")) or None
        except Exception:
            return None

    def get_trigger_param_meta(self, flow_id: str) -> Dict[str, ParamMeta]:
        """Key (lower case) -> title/description taken from the trigger schema."""
        result: Dict[str, ParamMeta] = {}
        try:
            flow = self.get_flow_definition(flow_id)
        except Exception:
            return result

        triggers = g(flow, "properties", "definition", "triggers")
        if not isinstance(triggers, dict):
            return result

        for trig in triggers.values():
            schema = _find_schema(trig)
            props = g(schema, "properties")
            if isinstance(props, dict):
                _collect_props(props, "", result)
        return result

    # ===================== FULL RUN DOWNLOAD =====================

    def _download_link(self, link_obj: Any) -> Any:
        if not isinstance(link_obj, dict):
            return None
        uri = to_text(link_obj.get("uri"))
        if not uri:
            return None
        try:
            text = self._get_plain(uri)
            try:
                return json.loads(text)
            except ValueError:
                return text
        except Exception as e:
            self._report(f"Could not download link: {e}")
            return {"_error": str(e), "_uri": uri}

    def _hydrate_action(self, action: Dict[str, Any]) -> Dict[str, Any]:
        p = action.get("properties") if isinstance(action.get("properties"), dict) else {}
        hydrated = copy.deepcopy(action)
        hydrated["_inputs"] = self._download_link(p.get("inputsLink"))
        time.sleep(DELAY_BETWEEN_CALLS_S)
        hydrated["_outputs"] = self._download_link(p.get("outputsLink"))
        time.sleep(DELAY_BETWEEN_CALLS_S)
        return hydrated

    def _list_run_actions(self, flow_id: str, run_id: str) -> List[Dict[str, Any]]:
        url: Optional[str] = f"{self._env_base}/flows/{flow_id}/runs/{run_id}/actions?api-version={API_VERSION}"
        actions: List[Dict[str, Any]] = []
        page = 0
        while url:
            page += 1
            data = self._get_json(url)
            vals = data.get("value") if isinstance(data.get("value"), list) else []
            self._report(f"Actions - page {page}: {len(vals)} items")
            actions.extend(a for a in vals if isinstance(a, dict))
            url = _next_link(data)
            if url:
                time.sleep(DELAY_BETWEEN_CALLS_S)
        self._report(f"Total actions downloaded: {len(actions)}")
        return actions

    def _list_action_repetitions(self, flow_id: str, run_id: str, action_name: str) -> List[Dict[str, Any]]:
        reps = self._list_repetitions_from_endpoint(flow_id, run_id, action_name, "repetitions")
        if not reps:
            self._report(f"'{action_name}': no repetitions, trying scopeRepetitions...")
            reps = self._list_repetitions_from_endpoint(flow_id, run_id, action_name, "scopeRepetitions")
        return reps

    def _list_repetitions_from_endpoint(
        self, flow_id: str, run_id: str, action_name: str, endpoint: str
    ) -> List[Dict[str, Any]]:
        url: Optional[str] = (
            f"{self._env_base}/flows/{flow_id}/runs/{run_id}/actions/{quote(action_name, safe='')}"
            f"/{endpoint}?api-version={API_VERSION}"
        )
        reps: List[Dict[str, Any]] = []
        while url:
            try:
                data = self._get_json(url)
            except HttpStatusError as ex:
                if ex.status not in (400, 404):
                    self._report(f"Could not list {endpoint} of {action_name}: {ex}")
                break
            except Exception as ex:
                self._report(f"Could not list {endpoint} of {action_name}: {ex}")
                break
            vals = data.get("value") if isinstance(data.get("value"), list) else []
            reps.extend(r for r in vals if isinstance(r, dict))
            url = _next_link(data)
            if url:
                time.sleep(DELAY_BETWEEN_CALLS_S)
        return reps

    def download_full_run(self, flow_id: str, run_id: str) -> Dict[str, Any]:
        self._report("Downloading run metadata...")
        run = self._get_json(
            f"{self._env_base}/flows/{flow_id}/runs/{run_id}?api-version={API_VERSION}&$expand=properties/trigger"
        )

        trig = g(run, "properties", "trigger")
        trig = trig if isinstance(trig, dict) else {}
        trigger_full = copy.deepcopy(trig)
        self._report("Downloading trigger inputs/outputs...")
        trigger_full["_inputs"] = self._download_link(trig.get("inputsLink"))
        trigger_full["_outputs"] = self._download_link(trig.get("outputsLink"))

        self._report("Listing actions...")
        actions_raw = self._list_run_actions(flow_id, run_id)

        actions_full = []
        for idx, a in enumerate(actions_raw, start=1):
            name = to_text(a.get("name")) or "(unnamed)"
            self._report(f"Processing action {idx}/{len(actions_raw)}: {name}")

            ha = self._hydrate_action(a)

            p = a.get("properties") if isinstance(a.get("properties"), dict) else {}
            action_type = (to_text(p.get("type")) or "").lower()
            inputs_obj = ha.get("_inputs")

            looks_foreach = isinstance(inputs_obj, dict) and isinstance(inputs_obj.get("foreachItems"), list)
            inside_scope = isinstance(p.get("repetitionIndexes"), list)
            unknown_type = not action_type

            try_reps = (
                looks_foreach
                or inside_scope
                or unknown_type
                or "foreach" in action_type
                or "until" in action_type
                or "scope" in action_type
                or "repetitionCount" in p
            )

            if try_reps:
                self._report(f"Downloading repetitions of {name}...")
                reps = self._list_action_repetitions(flow_id, run_id, name)
                if reps:
                    self._report(f"'{name}': {len(reps)} repetitions.")
                    ha["_repetitions"] = [self._hydrate_action(r) for r in reps]

            actions_full.append(ha)

        now = datetime.now(timezone.utc)
        return {
            "flow": flow_id,
            "environment": self.environment_id,
            "run": run,
            "trigger": trigger_full,
            "actions": actions_full,
            "_downloadedAt": now.strftime("%Y-%m-%dT%H:%M:%S.%f") + "0Z",
        }


# ===================== helpers =====================


def _flatten(token: Any, prefix: str, out: Dict[str, str]) -> None:
    if isinstance(token, dict):
        for k, v in token.items():
            _flatten(v, k if not prefix else f"{prefix}/{k}", out)
    elif isinstance(token, list):
        for i, item in enumerate(token):
            _flatten(item, f"{prefix}[{i}]", out)
    elif prefix:
        out[prefix] = to_text(token) or ""


def _find_schema(trigger: Any) -> Any:
    for path in (("inputs", "schema"), ("inputs", "parameters", "schema"), ("parameters", "schema")):
        s = g(trigger, *path)
        if s is not None:
            return s
    return None


def _collect_props(props: Dict[str, Any], prefix: str, out: Dict[str, ParamMeta]) -> None:
    for name, val in props.items():
        key = name if not prefix else f"{prefix}/{name}"
        if not isinstance(val, dict):
            continue
        title = to_text(val.get("title"))
        desc = to_text(val.get("description"))
        child = val.get("properties") if isinstance(val.get("properties"), dict) else None
        if child is not None:
            _collect_props(child, key, out)
        if title is not None or desc is not None or child is None:
            out[key.lower()] = ParamMeta(key=key, title=title or key, description=desc or "")
