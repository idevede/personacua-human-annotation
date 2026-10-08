#!/usr/bin/env python3
"""Shared rules for the annotation subset a classmate's agent must produce.

The output layout is what tools/import_subset.py reads. Do not invent a second layout.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "personacua.annotation-subset.v1"
SELECTION_ID = "personacua-22-tasks-v1"
TAGS = ("odysseys", "real")
NAMED_ROLES = ["expert", "practical", "senior", "young"]
CONTROL_ROLE = "none"
CELLS_PER_MODEL = 110


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def model_key(value: str) -> str:
    """Stable directory name for run_config.agent.model."""
    text = value.strip().lower().replace("/", "-").replace("\\", "-")
    cleaned = "".join(char if char.isalnum() or char in "-._" else "-" for char in text)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-.")
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError(f"Unsafe model key from {value!r}")
    return cleaned


def expected_cells(selection: dict[str, Any]) -> list[dict[str, str]]:
    """One record per task × persona. 22 tasks × 5 personas = 110, including none."""
    roles = [str(role) for role in selection.get("persona_roles") or []]
    if roles != ["expert", "practical", "senior", "young", "none"]:
        raise ValueError(f"Unexpected persona_roles: {roles}")
    cells: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for task in selection.get("tasks") or []:
        task_id = str(task["task_id"])
        scenario = str(task["scenario"])
        tag = str(task.get("tag") or task.get("suite"))
        if tag not in TAGS:
            raise ValueError(f"Task {task_id} has tag {tag!r}; expected one of {TAGS}")
        for role in roles:
            persona = "none" if role == "none" else f"{scenario}-{role}"
            key = (task_id, persona)
            if key in seen:
                raise ValueError(f"Duplicate cell {key}")
            seen.add(key)
            cells.append(
                {
                    "task_id": task_id,
                    "tag": tag,
                    "scenario": scenario,
                    "role": role,
                    "persona": persona,
                    "title": str(task.get("title") or ""),
                }
            )
    if not cells:
        raise ValueError("Selection has no cells")
    if selection.get("selection_id") == SELECTION_ID and len(cells) != 110:
        raise ValueError(f"Expected 110 cells, got {len(cells)}")
    return cells


def result_relpath(model: str, cell: dict[str, str]) -> str:
    return f"runs/{model_key(model)}/{cell['tag']}/{cell['task_id']}/{cell['persona']}/result.json"


def referenced_files(result: dict[str, Any]) -> list[str]:
    """Relative files result.json names. Paths are relative to result.json's directory."""
    rels: list[str] = []
    run = result.get("run") if isinstance(result.get("run"), dict) else {}
    for step in run.get("trajectory") or []:
        if not isinstance(step, dict):
            continue
        shot = step.get("screenshot")
        if isinstance(shot, dict) and shot.get("file"):
            rels.append(str(shot["file"]))
    for shot in run.get("final_screenshots") or []:
        if isinstance(shot, dict) and shot.get("file"):
            rels.append(str(shot["file"]))
    state = run.get("real_state")
    if isinstance(state, dict) and state.get("file"):
        rels.append(str(state["file"]))
    return rels


def run_sort_key(path: Path, result: dict[str, Any]) -> tuple[int, int, float]:
    """Lower is better. Prefer a real run, then replicate r1, then the newest file."""
    run = result.get("run") if isinstance(result.get("run"), dict) else {}
    failed = 1 if run.get("stop_reason") == "no_model_output" else 0
    replicate = str(
        result.get("replicate_id")
        or (result.get("run_config") or {}).get("replicate_id")
        or ""
    )
    prefer = 0 if replicate in {"", "r1", "first"} else 1
    return (failed, prefer, -path.stat().st_mtime)


def agent_model(result: dict[str, Any]) -> str:
    config = result.get("run_config")
    agent = config.get("agent") if isinstance(config, dict) else None
    model = agent.get("model") if isinstance(agent, dict) else None
    if not isinstance(model, str) or not model.strip():
        raise ValueError("result.json has no run_config.agent.model")
    return model
