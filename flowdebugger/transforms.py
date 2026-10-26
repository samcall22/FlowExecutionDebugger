# Flow Execution Debugger
# Copyright (C) 2026 samcall22 (https://github.com/samcall22)
# SPDX-License-Identifier: GPL-3.0-or-later
# This program comes with ABSOLUTELY NO WARRANTY. See the LICENSE file for details.

"""Pure transformations over a downloaded run (no network calls)."""
from __future__ import annotations

import copy
import json
import re
from typing import Any, Dict, Iterable, List, Optional

_GUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_MISSING = object()


def g(obj: Any, *path: str) -> Any:
    """Safe nested get through dicts; None when any step is missing or not an object."""
    cur = obj
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def has(obj: Any, *path: str) -> bool:
    cur = obj
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return False
        cur = cur[key]
    return True


def to_text(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, indent=2)
    return str(v)


def _to_int(v: Any) -> Optional[int]:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str):
        try:
            return int(v.strip())
        except ValueError:
            return None
    return None


def _dicts(items: Any) -> Iterable[Dict[str, Any]]:
    return (x for x in items if isinstance(x, dict)) if isinstance(items, list) else ()


def _name(a: Dict[str, Any]) -> str:
    n = to_text(a.get("name"))
    return "(unnamed)" if n is None else n


# ===================== CALLS TO CHILD FLOWS =====================


def build_child_flow_calls(full_run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extracts calls to other flows: native "Run a child flow" (type=Workflow) and,
    as a best effort, HTTP invocations to the Flow API. Never raises.
    """
    calls: List[Dict[str, Any]] = []
    for a in _dicts(g(full_run, "actions")):
        try:
            name = _name(a)
            reps = a.get("_repetitions")
            if not isinstance(reps, list) or not reps:
                _try_add_call(calls, name, a, None)
                continue
            # Inside loops: one entry per repetition, with its path.
            for rep in _dicts(reps):
                _try_add_call(calls, name, rep, build_repetition_path(rep))
        except Exception:
            pass  # a problematic action must not abort the rest
    return calls


def _try_add_call(target: List[Dict[str, Any]], action_name: str, node: Dict[str, Any], path: Optional[str]) -> None:
    props = node.get("properties") if isinstance(node.get("properties"), dict) else None
    action_type = to_text(g(props, "type")) or ""
    inputs = node.get("_inputs") if isinstance(node.get("_inputs"), dict) else None
    outputs = node.get("_outputs") if isinstance(node.get("_outputs"), dict) else None

    is_workflow_type = "workflow" in action_type.lower()
    has_workflow_host = has(inputs, "host", "workflow") or has(inputs, "host", "workflowReferenceName")

    uri = to_text(g(inputs, "uri"))
    looks_like_flow_http = bool(uri) and "/providers/microsoft.processsimple/" in uri.lower()

    if not is_workflow_type and not has_workflow_host and not looks_like_flow_http:
        return

    child_flow_id = _first_guid(
        g(inputs, "host", "workflowReferenceName"),
        g(inputs, "host", "workflow", "id"),
        g(inputs, "host", "workflow", "name"),
        g(inputs, "host", "flowId"),
        g(props, "workflowReferenceName"),
        uri if looks_like_flow_http else None,
    )

    child_run_id = _first_string(
        g(outputs, "body", "workflowOperationId"),
        g(outputs, "body", "name"),
        g(outputs, "workflowOperationId"),
        g(outputs, "name"),
        g(props, "workflowRunId"),
        g(outputs, "headers", "x-ms-workflow-run-id"),
    )

    if not child_flow_id and not child_run_id:
        return

    o: Dict[str, Any] = {
        "action": action_name,
        "status": to_text(g(props, "status")),
        "startTime": to_text(g(props, "startTime")),
        "kind": "Workflow" if (is_workflow_type or has_workflow_host) else "Http",
    }
    if child_flow_id:
        o["childFlowId"] = child_flow_id
    if child_run_id:
        o["childRunId"] = child_run_id
    if path:
        o["path"] = path
    if not child_run_id:
        o["note"] = "The child run id is not exposed; no direct link to the run."
    target.append(o)


def _first_string(*candidates: Any) -> Optional[str]:
    for c in candidates:
        s = to_text(c)
        if s and s.strip():
            return s.strip()
    return None


def _first_guid(*candidates: Any) -> Optional[str]:
    """First GUID found (bare or inside an ARM path / URL); otherwise the last path segment."""
    for c in candidates:
        raw = to_text(c)
        if not raw or not raw.strip():
            continue
        m = _GUID_RE.search(raw)
        if m:
            return m.group(0)
        idx = max(raw.rfind("/"), raw.rfind("\\"))
        tail = raw[idx + 1:] if idx >= 0 else raw
        tail = tail.split("?")[0].strip()
        if tail and len(tail) >= 8:
            return tail
    return None


# ===================== NESTING PATHS =====================


def build_repetition_path(repetition: Any) -> Optional[str]:
    idxs = g(repetition, "properties", "repetitionIndexes")
    if not isinstance(idxs, list) or not idxs:
        return None
    parts = []
    for i in _dicts(idxs):
        scope = to_text(i.get("scopeName")) or "?"
        item_index = _to_int(i.get("itemIndex"))
        parts.append(f"{scope}[{item_index + 1}]" if item_index is not None else scope)
    return " > ".join(parts)


def _build_parent_loop_path(repetition: Any, own_name: str) -> Optional[str]:
    idxs = g(repetition, "properties", "repetitionIndexes")
    if not isinstance(idxs, list) or not idxs:
        return None
    parts = [
        s for s in (to_text(i.get("scopeName")) for i in _dicts(idxs))
        if s and s.lower() != own_name.lower()
    ]
    return " > ".join(parts) if parts else None


def _iteration_entry(loop_name: str, i: int, total: int, item: Any) -> Dict[str, Any]:
    return {
        "loop": loop_name,
        "label": f"{loop_name} - iteration {i + 1}/{total}",
        "index": i,
        "portalIndex": i + 1,
        "item": item,
    }


def annotate_foreach_iterations(full_run: Dict[str, Any]) -> int:
    """Numbers every 'Apply to each' item with the same index the portal shows (portalIndex)."""
    total = 0
    for a in _dicts(full_run.get("actions")):
        if not is_foreach_action(a):
            continue
        inputs = a.get("_inputs")
        if not isinstance(inputs, dict):
            continue
        items = inputs.get("foreachItems")
        if not isinstance(items, list) or not items:
            continue

        loop_name = _name(a)
        annotated = [_iteration_entry(loop_name, i, len(items), items[i]) for i in range(len(items))]

        inputs["foreachItems"] = annotated
        inputs["_iterationCount"] = len(annotated)
        inputs["_loop"] = loop_name
        a["_iterationCount"] = len(annotated)
        total += len(annotated)
    return total


def is_foreach_action(action: Dict[str, Any]) -> bool:
    meta = g(action, "properties", "inputsLink", "metadata")
    if isinstance(meta, dict) and "foreachItemsCount" in meta:
        return True
    inputs = action.get("_inputs")
    return isinstance(inputs, dict) and isinstance(inputs.get("foreachItems"), list)


def _run_summary(full_run: Dict[str, Any]) -> Dict[str, Any]:
    run_props = g(full_run, "run", "properties")
    run_props = run_props if isinstance(run_props, dict) else {}
    return {
        "id": to_text(g(full_run, "run", "name")),
        "status": to_text(run_props.get("status")),
        "startTime": to_text(run_props.get("startTime")),
        "endTime": to_text(run_props.get("endTime")),
    }


def _trigger_body(full_run: Dict[str, Any]) -> Any:
    for key in ("_outputs", "_inputs"):
        obj = g(full_run, "trigger", key)
        if isinstance(obj, dict) and obj.get("body") is not None:
            return obj["body"]
    return _MISSING


# ===================== CLEAN JSON (LOOPS ONLY, FLAT) =====================


def build_clean_run(full_run: Dict[str, Any]) -> Dict[str, Any]:
    clean_trigger: Dict[str, Any] = {}
    body = _trigger_body(full_run)
    if body is not _MISSING:
        clean_trigger["body"] = body

    loops: List[Dict[str, Any]] = []
    for a in _dicts(full_run.get("actions")):
        name = _name(a)
        action_type = (to_text(g(a, "properties", "type")) or "").lower()

        if is_foreach_action(a):
            items = g(a, "_inputs", "foreachItems")
            if isinstance(items, list) and items:
                iters = []
                for i, it in enumerate(items):
                    if isinstance(it, dict):
                        it["loop"] = name
                        it["label"] = f"{name} - iteration {i + 1}/{len(items)}"
                        iters.append(it)
                    else:
                        iters.append(_iteration_entry(name, i, len(items), it))
                loops.append({
                    "action": name,
                    "type": "Foreach",
                    "nested": False,
                    "iterationCount": len(iters),
                    "iterations": iters,
                })
                continue

        reps = a.get("_repetitions")
        if isinstance(reps, list) and reps:
            loop_type = "Until" if "until" in action_type else "Foreach"
            first_rep = next(iter(_dicts(reps)), None)
            parent_path = _build_parent_loop_path(first_rep, name)
            is_nested = bool(parent_path)

            iterations = []
            for i, rep in enumerate(reps):
                rep = rep if isinstance(rep, dict) else None
                rep_path = build_repetition_path(rep)
                iter_obj: Dict[str, Any] = {
                    "loop": name,
                    "label": (
                        f"{rep_path}  ({name} - iteration {i + 1}/{len(reps)})"
                        if rep_path is not None
                        else f"{name} - iteration {i + 1}/{len(reps)}"
                    ),
                    "index": i,
                    "portalIndex": i + 1,
                    "status": to_text(g(rep, "properties", "status")),
                    "outputs": g(rep, "_outputs"),
                }
                if rep_path is not None:
                    iter_obj["path"] = rep_path
                rep_idx = g(rep, "properties", "repetitionIndexes")
                if rep_idx is not None:
                    iter_obj["repetitionIndexes"] = rep_idx
                iterations.append(iter_obj)

            loop_obj: Dict[str, Any] = {
                "action": name,
                "type": loop_type,
                "nested": is_nested,
                "iterationCount": len(reps),
                "iterations": iterations,
            }
            if is_nested:
                loop_obj["parentLoops"] = parent_path
            loops.append(loop_obj)

    return {
        "flow": full_run.get("flow"),
        "environment": full_run.get("environment"),
        "run": _run_summary(full_run),
        "trigger": clean_trigger,
        "loops": loops,
        "_downloadedAt": full_run.get("_downloadedAt"),
    }


# ===================== TREE JSON (NESTED LOOPS) =====================


class _IterNode:
    def __init__(self, item_index: int = 0, path: str = ""):
        self.item_index = item_index
        self.path = path
        self.item: Any = _MISSING
        self.status: Optional[str] = None
        self.actions: List[Dict[str, Any]] = []
        self.loops: Dict[str, _LoopNode] = {}  # key: upper-cased loop name


class _LoopNode:
    def __init__(self, name: str):
        self.name = name
        self.iterations: Dict[int, _IterNode] = {}


def _ensure_iter(parent: _IterNode, scope: str, item_index: int) -> _IterNode:
    loop = parent.loops.get(scope.upper())
    if loop is None:
        loop = _LoopNode(scope)
        parent.loops[scope.upper()] = loop
    it = loop.iterations.get(item_index)
    if it is None:
        label = f"{scope}[{item_index + 1}]"
        it = _IterNode(item_index, label if not parent.path else f"{parent.path} > {label}")
        loop.iterations[item_index] = it
    return it


def _resolve_context(root: _IterNode, repetition_indexes: Any) -> _IterNode:
    cur = root
    for i in _dicts(repetition_indexes):
        scope = to_text(i.get("scopeName"))
        idx = _to_int(i.get("itemIndex"))
        if not scope or idx is None:
            continue
        cur = _ensure_iter(cur, scope, idx)
    return cur


def _normalize_item(raw: Any) -> Any:
    if isinstance(raw, dict) and "item" in raw and "portalIndex" in raw:
        return raw["item"]
    return raw


def _simple_action(a: Dict[str, Any], name: str) -> Dict[str, Any]:
    res: Dict[str, Any] = {"action": name, "status": to_text(g(a, "properties", "status"))}

    inputs_present = "_inputs" in a
    inputs = a.get("_inputs")
    if isinstance(inputs, dict) and "foreachItems" in inputs:
        clone = copy.deepcopy(inputs)
        for k in ("foreachItems", "_iterationCount", "_loop"):
            clone.pop(k, None)
        inputs = clone if clone else None
        inputs_present = inputs is not None

    if inputs_present:
        res["inputs"] = inputs
    if "_outputs" in a:
        res["outputs"] = a["_outputs"]
    return res


def _serialize_loops(node: _IterNode, loop_types: Dict[str, str]) -> List[Dict[str, Any]]:
    arr = []
    for key in sorted(node.loops):
        loop = node.loops[key]
        iters = []
        for idx in sorted(loop.iterations):
            it = loop.iterations[idx]
            o: Dict[str, Any] = {"portalIndex": it.item_index + 1, "index": it.item_index, "path": it.path}
            if it.status:
                o["status"] = it.status
            if it.item is not _MISSING:
                o["item"] = it.item
            if it.actions:
                o["actions"] = list(it.actions)
            child = _serialize_loops(it, loop_types)
            if child:
                o["loops"] = child
            iters.append(o)

        arr.append({
            "loop": loop.name,
            "type": loop_types.get(loop.name.lower(), "Foreach"),
            "iterationCount": len(iters),
            "iterations": iters,
        })
    return arr


def _enumerate_repetition_indexes(action: Dict[str, Any]) -> Iterable[list]:
    own = g(action, "properties", "repetitionIndexes")
    if isinstance(own, list):
        yield own
    for r in _dicts(action.get("_repetitions")):
        idxs = g(r, "properties", "repetitionIndexes")
        if isinstance(idxs, list):
            yield idxs


def build_tree_run(full_run: Dict[str, Any]) -> Dict[str, Any]:
    root = _IterNode()
    actions = list(_dicts(full_run.get("actions")))

    loop_types: Dict[str, str] = {}
    scope_names = set()

    for a in actions:
        name = _name(a)
        action_type = to_text(g(a, "properties", "type")) or ""
        loop_types[name.lower()] = "Until" if "until" in action_type.lower() else "Foreach"

        if is_foreach_action(a):
            scope_names.add(name.lower())

        for idxs in _enumerate_repetition_indexes(a):
            for i in _dicts(idxs):
                sc = to_text(i.get("scopeName"))
                if sc:
                    scope_names.add(sc.lower())

    for a in actions:
        name = _name(a)
        reps = a.get("_repetitions")

        if not isinstance(reps, list) or not reps:
            items = g(a, "_inputs", "foreachItems")
            if isinstance(items, list):
                for k, item in enumerate(items):
                    _ensure_iter(root, name, k).item = _normalize_item(item)
            elif name.lower() not in scope_names:
                root.actions.append(_simple_action(a, name))
            continue

        for rep in _dicts(reps):
            ctx = _resolve_context(root, g(rep, "properties", "repetitionIndexes"))
            rep_items = g(rep, "_inputs", "foreachItems")

            if isinstance(rep_items, list):
                for k, item in enumerate(rep_items):
                    _ensure_iter(ctx, name, k).item = _normalize_item(item)
            elif name.lower() in scope_names:
                st = to_text(g(rep, "properties", "status"))
                if st and not ctx.status:
                    ctx.status = st
            else:
                ctx.actions.append(_simple_action(rep, name))

    body = _trigger_body(full_run)
    return {
        "flow": full_run.get("flow"),
        "environment": full_run.get("environment"),
        "run": _run_summary(full_run),
        "trigger": {"body": None if body is _MISSING else body},
        "actions": list(root.actions),
        "loops": _serialize_loops(root, loop_types),
        "_downloadedAt": full_run.get("_downloadedAt"),
    }


# ===================== STATISTICS =====================


def count_tree_iterations(tree: Dict[str, Any]) -> int:
    return _count_loops(g(tree, "loops"))


def _count_loops(loops: Any) -> int:
    total = 0
    for loop in _dicts(loops):
        iters = loop.get("iterations")
        if not isinstance(iters, list):
            continue
        total += len(iters)
        for it in _dicts(iters):
            total += _count_loops(it.get("loops"))
    return total
