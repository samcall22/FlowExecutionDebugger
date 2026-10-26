# Flow Execution Debugger
# Copyright (C) 2026 samcall22 (https://github.com/samcall22)
# SPDX-License-Identifier: GPL-3.0-or-later
# This program comes with ABSOLUTELY NO WARRANTY. See the LICENSE file for details.

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Optional


def app_data_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.join(Path.home(), ".config")
    path = Path(base) / "FlowExecutionDebugger"
    path.mkdir(parents=True, exist_ok=True)
    return path


SETTINGS_FILE = app_data_dir() / "settings.json"


@dataclass
class Settings:
    tenant: str = ""
    client_id: str = ""
    last_environment_id: str = ""
    last_used_organization_webapp_url: str = ""
    browser: str = ""
    profile: str = ""
    json_mode: int = 2
    max_runs: int = 2000

    @classmethod
    def load(cls) -> "Settings":
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            known = {f.name for f in fields(cls)}
            return cls(**{k: v for k, v in data.items() if k in known})
        except Exception:
            return cls()

    def save(self) -> Optional[str]:
        try:
            SETTINGS_FILE.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
            return None
        except Exception as ex:
            return str(ex)
