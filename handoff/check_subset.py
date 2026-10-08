#!/usr/bin/env python3
"""Fail unless a subset directory matches the annotation handoff layout.

Run this on the classmate's machine after the reorganization script finishes,
before zipping:

    python3 check_subset.py --subset /path/to/personacua-annotation-subset
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from subset_format import (
    SCHEMA_VERSION,
    SELECTION_ID,
    agent_model,
    expected_cells,
    load_json,
    model_key,
    referenced_files,
    result_relpath,
)


def parse_args() -> argparse.Namespace:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subset", type=Path, required=True)
    parser.add_argument("--selection", type=Path, default=here / "selection.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    subset = args.subset.resolve()
    selection = load_json(args.selection.resolve())
    cells = expected_cells(selection)
    subset_path = subset / "subset.json"
    if not subset_path.is_file():
        print(f"missing {subset_path}", file=sys.stderr)
        return 1
    payload = load_json(subset_path)
    errors: list[str] = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if payload.get("selection_id") != selection.get("selection_id", SELECTION_ID):
        errors.append("selection_id does not match selection.json")

    models = payload.get("models")
    if not isinstance(models, list) or not models:
        errors.append("models must be a non-empty list")
        models = []
    model_names: list[str] = []
    for item in models:
        if not isinstance(item, dict) or not item.get("model_key"):
            errors.append(f"bad model entry: {item!r}")
            continue
        key = str(item["model_key"])
        if key != model_key(key):
            errors.append(f"model_key {key!r} is not canonical; expected {model_key(key)}")
        model_names.append(key)
    if len(set(model_names)) != len(model_names):
        errors.append("duplicate model_key in subset.json")

    listed = payload.get("cells")
    if not isinstance(listed, list):
        errors.append("cells must be a list")
        listed = []
    expected = {
        (key, cell["task_id"], cell["persona"]): cell
        for key in model_names
        for cell in cells
    }
    seen: set[tuple[str, str, str]] = set()
    for item in listed:
        if not isinstance(item, dict):
            errors.append(f"bad cell: {item!r}")
            continue
        identity = (str(item.get("model_key")), str(item.get("task_id")), str(item.get("persona")))
        if identity in seen:
            errors.append(f"duplicate cell {identity}")
        seen.add(identity)
        cell = expected.get(identity)
        if cell is None:
            errors.append(f"unexpected cell {identity}")
            continue
        if item.get("tag") != cell["tag"] or item.get("role") != cell["role"]:
            errors.append(f"tag/role mismatch for {identity}")
        rel = str(item.get("result") or "")
        if rel != result_relpath(identity[0], cell):
            errors.append(f"result path for {identity} must be {result_relpath(identity[0], cell)}")
        result_path = subset / rel
        if not result_path.is_file():
            errors.append(f"missing file {rel}")
            continue
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{rel} is not JSON: {exc}")
            continue
        if result.get("task_id") != cell["task_id"]:
            errors.append(f"{rel} task_id is {result.get('task_id')}")
        if result.get("persona") != cell["persona"]:
            errors.append(f"{rel} persona is {result.get('persona')}")
        suite = result.get("suite_id") or (result.get("suite_snapshot") or {}).get("id")
        if suite != cell["tag"]:
            errors.append(f"{rel} suite_id is {suite}, expected {cell['tag']}")
        try:
            found_key = model_key(agent_model(result))
        except ValueError as exc:
            errors.append(f"{rel}: {exc}")
            continue
        if found_key != identity[0]:
            errors.append(f"{rel} model {found_key} does not match directory {identity[0]}")
        for ref in referenced_files(result):
            if ref.startswith("/") or ".." in Path(ref).parts:
                errors.append(f"{rel} has an unsafe file reference {ref}")
                continue
            if not (result_path.parent / ref).is_file():
                errors.append(f"{rel} references missing file {ref}")

    missing = sorted(set(expected) - seen)
    for identity in missing[:20]:
        errors.append(f"missing cell {identity}")
    if len(missing) > 20:
        errors.append(f"... and {len(missing) - 20} more missing cells")

    if errors:
        print(f"{len(errors)} error(s)", file=sys.stderr)
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "models": len(model_names),
                "cells": len(listed),
                "cells_per_model": len(cells),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
