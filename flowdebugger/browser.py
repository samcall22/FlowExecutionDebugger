# Flow Execution Debugger
# Copyright (C) 2026 samcall22 (https://github.com/samcall22)
# SPDX-License-Identifier: GPL-3.0-or-later
# This program comes with ABSOLUTELY NO WARRANTY. See the LICENSE file for details.

from __future__ import annotations

import json
import os
import subprocess
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class BrowserProfile:
    directory_name: str
    display_name: str

    def __str__(self) -> str:
        return self.display_name


@dataclass
class BrowserInfo:
    name: str
    exe_path: str
    user_data_path: str

    def __str__(self) -> str:
        return self.name


def detect_browsers() -> List[BrowserInfo]:
    local = os.environ.get("LOCALAPPDATA", "")
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")

    candidates = [
        ("Chrome", os.path.join(local, r"Google\Chrome\User Data"), [
            os.path.join(pf, r"Google\Chrome\Application\chrome.exe"),
            os.path.join(pf86, r"Google\Chrome\Application\chrome.exe"),
            os.path.join(local, r"Google\Chrome\Application\chrome.exe"),
        ]),
        ("Edge", os.path.join(local, r"Microsoft\Edge\User Data"), [
            os.path.join(pf86, r"Microsoft\Edge\Application\msedge.exe"),
            os.path.join(pf, r"Microsoft\Edge\Application\msedge.exe"),
        ]),
        ("Brave", os.path.join(local, r"BraveSoftware\Brave-Browser\User Data"), [
            os.path.join(pf, r"BraveSoftware\Brave-Browser\Application\brave.exe"),
            os.path.join(pf86, r"BraveSoftware\Brave-Browser\Application\brave.exe"),
            os.path.join(local, r"BraveSoftware\Brave-Browser\Application\brave.exe"),
        ]),
    ]

    result = []
    for name, user_data, exes in candidates:
        exe = next((e for e in exes if os.path.isfile(e)), None)
        if exe:
            result.append(BrowserInfo(name, exe, user_data))
    return result


def get_profiles(browser: Optional[BrowserInfo]) -> List[BrowserProfile]:
    if not browser or not browser.user_data_path:
        return []
    local_state = Path(browser.user_data_path) / "Local State"
    profiles: List[BrowserProfile] = []
    try:
        root = json.loads(local_state.read_text(encoding="utf-8"))
        info_cache = (root.get("profile") or {}).get("info_cache") or {}
        for dir_name, info in info_cache.items():  # "Default", "Profile 1", ...
            name = (info or {}).get("name") or dir_name
            profiles.append(BrowserProfile(dir_name, f"{name} ({dir_name})"))
    except Exception:
        pass
    return sorted(profiles, key=lambda p: (p.directory_name != "Default", p.display_name))


def open_url(url: str, browser: Optional[BrowserInfo] = None, profile: Optional[BrowserProfile] = None) -> None:
    if not url:
        return
    if not url.lower().startswith("https://"):
        raise ValueError("Only https:// links can be opened.")
    if browser is None or not browser.exe_path:
        webbrowser.open(url)
        return
    args = [browser.exe_path]
    if profile and profile.directory_name:
        args.append(f"--profile-directory={profile.directory_name}")
    args.append(url)
    subprocess.Popen(args, close_fds=True)
