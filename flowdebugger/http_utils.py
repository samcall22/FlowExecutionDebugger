# Flow Execution Debugger
# Copyright (C) 2026 samcall22 (https://github.com/samcall22)
# SPDX-License-Identifier: GPL-3.0-or-later
# This program comes with ABSOLUTELY NO WARRANTY. See the LICENSE file for details.

from __future__ import annotations

import email.utils
import re
import time
from datetime import datetime, timezone
from typing import Callable, Dict, Optional

import requests

MAX_RETRIES = 5
DEFAULT_TIMEOUT = (20, 180)

LogFn = Optional[Callable[[str], None]]

# SAS links (inputsLink/outputsLink) carry their credential in the "sig" parameter.
_SIG_RE = re.compile(r"([?&]sig=)[^&]+", re.IGNORECASE)


class HttpStatusError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def report(log: LogFn, msg: str) -> None:
    if log is None:
        return
    try:
        log(msg)
    except Exception:
        pass


def redact(url: str) -> str:
    return _SIG_RE.sub(r"\1***", url or "")


def truncate(s: Optional[str], max_len: int) -> Optional[str]:
    if not s:
        return s
    return s if len(s) <= max_len else s[:max_len] + "...(truncated)"


def _retry_after_ms(resp: requests.Response) -> Optional[int]:
    ra = resp.headers.get("Retry-After")
    if not ra:
        return None
    try:
        return int(float(ra) * 1000)
    except ValueError:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(ra)
        ms = (dt - datetime.now(timezone.utc)).total_seconds() * 1000
        return int(ms) if ms > 0 else None
    except Exception:
        return None


def _decode(resp: requests.Response) -> str:
    enc = (resp.encoding or "").lower()
    if not enc or enc in ("utf-8", "utf8"):
        return resp.content.decode("utf-8-sig", errors="replace")
    return resp.text


def get_string_with_retry(
    session: requests.Session,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    log: LogFn = None,
) -> str:
    """GET with retries on network errors, HTTP 429 and HTTP 5xx (honours Retry-After)."""
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = session.get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
        except requests.RequestException as ex:
            if attempt <= MAX_RETRIES:
                wait = attempt * 1000
                report(log, f"Network error (attempt {attempt}/{MAX_RETRIES}), retrying in {wait} ms: {ex}")
                time.sleep(wait / 1000)
                continue
            raise RuntimeError(
                f"GET failed after {MAX_RETRIES} attempts.\nURL: {redact(url)}\n{ex}"
            ) from ex

        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt <= MAX_RETRIES:
                wait = _retry_after_ms(resp)
                if wait is None:
                    wait = attempt * 2000
                report(log, f"HTTP {resp.status_code} (attempt {attempt}/{MAX_RETRIES}). Waiting {wait} ms...")
                resp.close()
                time.sleep(wait / 1000)
                continue

        body = _decode(resp)
        if not resp.ok:
            status, reason = resp.status_code, resp.reason
            resp.close()
            raise HttpStatusError(
                status,
                f"GET failed: HTTP {status} {reason}\nURL: {redact(url)}\nBody: {truncate(body, 1500)}",
            )

        resp.close()
        return body
