#!/usr/bin/env python3
"""Load a classmate's annotation subset into this web project.

The subset is the directory described in handoff/README.md. This rewrites
data/manifest.public.json, data/manifest.private.json, and assets/. Chinese
guide files and saved annotations are left in place.

    python3 tools/import_subset.py --subset /path/to/personacua-annotation-subset --force
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "handoff"))
sys.path.insert(0, str(PROJECT / "tools"))

from build_dataset import (  # noqa: E402
    MODEL_SPECS,
    ROLES as NAMED_ROLES,
    SCENARIOS,
    clean_generated,
    dump_json,
    materialize_output,
    normalized_rubrics,
)
from subset_format import SELECTION_ID, agent_model, expected_cells, load_json, model_key  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subset", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=PROJECT)
    parser.add_argument("--selection", type=Path, default=PROJECT / "handoff" / "selection.json")
    parser.add_argument("--seed", default="personacua-human-annotation-v1")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def slot_name(index: int) -> str:
    name = ""
    number = index + 1
    while number:
        number, remainder = divmod(number - 1, 26)
        name = chr(ord("A") + remainder) + name
    return name


def blinded_models(case_id: str, model_keys: list[str], seed: str) -> list[str]:
    ordered = sorted(model_keys)
    rng = random.Random(hashlib.sha256(f"{seed}:{case_id}".encode()).digest())
    rng.shuffle(ordered)
    return ordered


def main() -> int:
    args = parse_args()
    subset = args.subset.resolve()
    project = args.project_root.resolve()
    selection = load_json(args.selection.resolve())
    payload = load_json(subset / "subset.json")
    if payload.get("selection_id") not in {None, selection.get("selection_id", SELECTION_ID)}:
        raise ValueError("subset selection_id does not match")
    cells = expected_cells(selection)
    models = [str(item["model_key"]) for item in payload.get("models") or [] if isinstance(item, dict)]
    if not models:
        raise ValueError("subset.json has no models")
    display = {
        str(item["model_key"]): str(item.get("display_name") or item["model_key"])
        for item in payload["models"]
        if isinstance(item, dict) and item.get("model_key")
    }
    runs: dict[tuple[str, str, str], Path] = {}
    for item in payload.get("cells") or []:
        if not isinstance(item, dict):
            continue
        key = (str(item.get("model_key")), str(item.get("task_id")), str(item.get("persona")))
        path = subset / str(item.get("result") or "")
        if not path.is_file():
            raise FileNotFoundError(path)
        runs[key] = path
    missing = [
        (model, cell["task_id"], cell["persona"])
        for model in models
        for cell in cells
        if (model, cell["task_id"], cell["persona"]) not in runs
    ]
    if missing:
        raise ValueError(f"{len(missing)} cells missing; first: {missing[0]}")
    for model in models:
        for cell in cells:
            path = runs[(model, cell["task_id"], cell["persona"])]
            result = json.loads(path.read_text(encoding="utf-8"))
            if result.get("task_id") != cell["task_id"] or result.get("persona") != cell["persona"]:
                raise ValueError(f"{path} does not match {cell['task_id']} / {cell['persona']}")
            if model_key(agent_model(result)) != model:
                raise ValueError(f"{path} model does not match {model}")

    clean_generated(project, args.force)
    task_order = {
        (str(task["tag"] or task["suite"]), str(task["task_id"])): index
        for index, task in enumerate(selection["tasks"])
    }
    link_stats: Counter[str] = Counter()
    public_cases: list[dict[str, Any]] = []
    private_cases: dict[str, Any] = {}
    total_screenshots = 0
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    for cell in cells:
        case_id = f"{cell['scenario']}--{cell['task_id'][:12]}--{cell['role']}"
        loaded: dict[str, tuple[Path, dict[str, Any]]] = {}
        for model in models:
            path = runs[(model, cell["task_id"], cell["persona"])]
            result = json.loads(path.read_text(encoding="utf-8"))
            if model_key(agent_model(result)) != model:
                raise ValueError(f"{path} model does not match {model}")
            loaded[model] = (path, result)
        first = loaded[models[0]][1]
        rubrics = normalized_rubrics(first.get("task_snapshot") or {})
        for model in models[1:]:
            other = normalized_rubrics(loaded[model][1].get("task_snapshot") or {})
            if other != rubrics:
                raise ValueError(f"Rubric mismatch for {case_id} between {models[0]} and {model}")
        order = blinded_models(case_id, models, args.seed)
        public_outputs = []
        private_outputs: dict[str, Any] = {}
        for index, model in enumerate(order):
            slot = slot_name(index)
            path, result = loaded[model]
            public_output, private_output = materialize_output(
                result=result,
                result_path=path,
                project=project,
                case_id=case_id,
                slot=slot,
                link_mode="hardlink",
                link_stats=link_stats,
                require_screenshots=False,
            )
            total_screenshots += int(public_output["screenshot_count"])
            public_outputs.append(public_output)
            known = MODEL_SPECS.get(model, {})
            private_outputs[slot] = {
                "model": model,
                "display_name": display.get(model) or known.get("display_name") or model,
                "model_key": known.get("model_key") or model,
                **private_output,
            }
        snapshot = first.get("task_snapshot") or {}
        public_cases.append(
            {
                "case_id": case_id,
                "scenario": cell["scenario"],
                "suite": cell["tag"],
                "tag": cell["tag"],
                "task_id": cell["task_id"],
                "title": cell["title"] or snapshot.get("title") or cell["task_id"],
                "level": snapshot.get("level") or first.get("level"),
                "persona": {"id": cell["persona"], "role": cell["role"], "scenario": cell["scenario"]},
                "task_description": snapshot.get("confirmed_task") or "",
                "rubrics": rubrics,
                "outputs": public_outputs,
            }
        )
        private_cases[case_id] = {"output_identity": private_outputs}

    public_cases.sort(
        key=lambda case: (
            SCENARIOS.index(case["scenario"]) if case["scenario"] in SCENARIOS else 99,
            task_order.get((case["tag"], case["task_id"]), 999),
            [*NAMED_ROLES, "none"].index(case["persona"]["role"])
            if case["persona"]["role"] in {*NAMED_ROLES, "none"} else 99,
        )
    )
    public_manifest = {
        "schema_version": "personacua.annotation-public.v1",
        "dataset_id": selection.get("selection_id", SELECTION_ID),
        "generated_at": generated_at,
        "description": "Model-blind PersonaCUA rubric annotation dataset.",
        "instructions": {
            "unit": "Each case compares anonymous model outputs on one task and persona.",
            "decision_values": ["yes", "no", "unsure"],
            "note": "Judge every rubric independently using the opening, tool trace, screenshots, and final answer.",
        },
        "counts": {
            "scenarios": len({case["scenario"] for case in public_cases}),
            "tasks": len({case["task_id"] for case in public_cases}),
            "cases": len(public_cases),
            "outputs": sum(len(case["outputs"]) for case in public_cases),
            "models": len(models),
            "screenshot_files": total_screenshots,
        },
        "scenarios": [name for name in SCENARIOS if any(case["scenario"] == name for case in public_cases)],
        "persona_roles": [*NAMED_ROLES, "none"] if any(cell["role"] == "none" for cell in cells) else NAMED_ROLES,
        "cases": public_cases,
    }
    private_manifest = {
        "schema_version": "personacua.annotation-private.v1",
        "dataset_id": public_manifest["dataset_id"],
        "generated_at": generated_at,
        "warning": "Private evaluator data: do not serve this manifest to annotators.",
        "model_slot_seed": args.seed,
        "models": [{"model_key": key, "display_name": display.get(key, key)} for key in sorted(models)],
        "materialization": {"methods": dict(sorted(link_stats.items()))},
        "cases": private_cases,
    }
    dump_json(project / "data" / "manifest.public.json", public_manifest)
    dump_json(project / "data" / "manifest.private.json", private_manifest)
    print(
        json.dumps(
            {
                "cases": len(public_cases),
                "models": len(models),
                "outputs": public_manifest["counts"]["outputs"],
                "screenshots": total_screenshots,
                "materialization": dict(sorted(link_stats.items())),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"import_subset.py: {error}", file=sys.stderr)
        raise
