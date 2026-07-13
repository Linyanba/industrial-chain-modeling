#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Resolve document-specific parsing rules without leaking them into generic stages."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import yaml


DEFAULT_PROFILE_ID = "generic"


def _merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _profile_dir(project_root: Path) -> Path:
    return Path(project_root) / "rag" / "config" / "document_profiles"


def load_document_profiles(project_root: Path) -> Dict[str, Dict[str, Any]]:
    profiles: Dict[str, Dict[str, Any]] = {}
    for path in sorted(_profile_dir(project_root).glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        profile_id = str(data.get("profile_id") or path.stem).strip()
        if not profile_id:
            raise ValueError(f"Document profile has no profile_id: {path}")
        data["profile_id"] = profile_id
        data["_source_path"] = str(path)
        profiles[profile_id] = data
    if DEFAULT_PROFILE_ID not in profiles:
        raise FileNotFoundError(
            f"Missing required document profile: {_profile_dir(project_root) / 'generic.yaml'}"
        )
    return profiles


def _matches(profile: Dict[str, Any], haystack: str) -> bool:
    match = profile.get("match") or {}
    any_contains = [str(x).casefold() for x in match.get("any_contains", []) if str(x).strip()]
    all_contains = [str(x).casefold() for x in match.get("all_contains", []) if str(x).strip()]
    regexes = [str(x) for x in match.get("regex", []) if str(x).strip()]
    folded = haystack.casefold()
    if any_contains and not any(token in folded for token in any_contains):
        return False
    if all_contains and not all(token in folded for token in all_contains):
        return False
    if regexes and not any(re.search(pattern, haystack, flags=re.IGNORECASE) for pattern in regexes):
        return False
    return bool(any_contains or all_contains or regexes)


def resolve_document_profile(
    project_root: Path,
    hints: Iterable[object] = (),
    explicit: Optional[str] = None,
) -> Dict[str, Any]:
    """Return generic defaults merged with an explicitly selected or matched profile."""
    profiles = load_document_profiles(Path(project_root))
    generic = profiles[DEFAULT_PROFILE_ID]
    explicit_id = str(explicit or "").strip()
    if explicit_id and explicit_id.lower() != "auto":
        if explicit_id not in profiles:
            raise ValueError(
                f"Unknown document profile '{explicit_id}'. Available: {sorted(profiles)}"
            )
        selected = profiles[explicit_id]
    else:
        haystack = "\n".join(str(h) for h in hints if h is not None)
        matches = [
            profile for pid, profile in profiles.items()
            if pid != DEFAULT_PROFILE_ID and _matches(profile, haystack)
        ]
        selected = max(matches, key=lambda p: int(p.get("priority", 0))) if matches else generic
    merged = _merge(generic, selected)
    merged["profile_id"] = selected["profile_id"]
    merged["_source_path"] = selected.get("_source_path", "")
    return merged


def document_rules(profile: Dict[str, Any]) -> Dict[str, Any]:
    return dict(profile.get("document_rules") or {})

