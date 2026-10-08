#!/usr/bin/env python3
"""Build the fixed PersonaCUA human-annotation dataset.

The public manifest is model-blind. The private manifest contains the slot-to-model
mapping, original result paths, and automated judge outputs. Screenshot assets are
hard-linked by default and copied when hard-linking is unavailable.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


SCENARIOS = [
    "art",
    "community",
    "ecommerce",
    "food",
    "health",
    "lifestyle",
    "productivity",
    "professional",
    "science",
    "tech",
    "travel",
]
ROLES = ["expert", "practical", "senior", "young"]
MODEL_SPECS = {
    "grok": {"display_name": "Grok 4.6", "model_key": "grok-4.6"},
    "muse": {"display_name": "Muse Glimmer 30B", "model_key": "muse-glimmer-30b"},
}


def parse_args() -> argparse.Namespace:
    script = Path(__file__).resolve()
    project = script.parents[1]
    workspace = project.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=project)
    parser.add_argument(
        "--selection",
        type=Path,
        default=project / "config" / "selected_tasks.json",
    )
    parser.add_argument(
        "--paired-csv",
        type=Path,
        default=workspace / "analysis" / "grok-vs-muse-20261003" / "paired-cells.csv",
    )
    parser.add_argument(
        "--link-mode",
        choices=("hardlink", "copy"),
        default="hardlink",
        help="How to materialize screenshot assets (hardlink falls back to copy across filesystems).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace existing generated data/manifest*.json and assets/.",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def dump_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(path)


def clean_generated(project: Path, force: bool) -> None:
    assets = project / "assets"
    manifests = [project / "data" / "manifest.public.json", project / "data" / "manifest.private.json"]
    present = ([assets] if assets.exists() else []) + [p for p in manifests if p.exists()]
    if present and not force:
        names = ", ".join(str(p) for p in present)
        raise FileExistsError(f"Generated outputs already exist ({names}); rerun with --force")
    if assets.exists():
        shutil.rmtree(assets)
    for path in manifests:
        path.unlink(missing_ok=True)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    required = {
        "suite",
        "task_id",
        "persona",
        "role",
        "grok_score",
        "muse_score",
        "grok_result_path",
        "muse_result_path",
    }
    missing = required - set(rows[0] if rows else {})
    if missing:
        raise ValueError(f"Missing columns in {path}: {sorted(missing)}")
    return rows


def validate_selection(selection: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = selection.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("Selection must contain a tasks list")
    if len(tasks) != 22:
        raise ValueError(f"Expected 22 selected tasks, got {len(tasks)}")
    ids = [str(task.get("task_id", "")) for task in tasks]
    if len(set(ids)) != len(ids):
        raise ValueError("Selected task IDs must be unique")
    counts = Counter(str(task.get("scenario", "")) for task in tasks)
    if counts != Counter({scenario: 2 for scenario in SCENARIOS}):
        raise ValueError(f"Expected exactly two tasks per scenario, got {dict(counts)}")
    return tasks


def select_rows(
    all_rows: list[dict[str, str]], selected_tasks: list[dict[str, Any]]
) -> dict[tuple[str, str, str], dict[str, str]]:
    selected_keys = {(str(t["suite"]), str(t["task_id"])) for t in selected_tasks}
    rows: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in all_rows:
        task_key = (row["suite"], row["task_id"])
        if task_key not in selected_keys or row["role"] not in ROLES:
            continue
        key = (*task_key, row["role"])
        if key in rows:
            raise ValueError(f"Duplicate paired row: {key}")
        rows[key] = row
    expected = len(selected_tasks) * len(ROLES)
    if len(rows) != expected:
        missing = [
            (str(t["suite"]), str(t["task_id"]), role)
            for t in selected_tasks
            for role in ROLES
            if (str(t["suite"]), str(t["task_id"]), role) not in rows
        ]
        raise ValueError(f"Expected {expected} paired rows; got {len(rows)}. Missing: {missing}")
    return rows


def validate_result(data: dict[str, Any], path: Path, task_id: str, role: str) -> None:
    if data.get("task_id") != task_id:
        raise ValueError(f"Task mismatch in {path}")
    persona = str(data.get("persona", ""))
    if not persona.endswith(f"-{role}"):
        raise ValueError(f"Persona/role mismatch in {path}: {persona} vs {role}")
    run = data.get("run")
    judgement = data.get("judgement")
    if not isinstance(run, dict) or not isinstance(run.get("trajectory"), list):
        raise ValueError(f"Missing valid run trajectory: {path}")
    if run.get("stop_reason") == "no_model_output":
        raise ValueError(f"Invalid no_model_output result: {path}")
    if not isinstance(judgement, dict) or not isinstance(judgement.get("score"), (int, float)):
        raise ValueError(f"Missing numeric judgement score: {path}")


def opening_text(run: dict[str, Any]) -> str:
    conversation = run.get("conversation") or []
    for item in conversation:
        if not isinstance(item, dict) or item.get("role") != "user":
            continue
        text = item.get("text") if "text" in item else item.get("content")
        if isinstance(text, str):
            return text
    raise ValueError("Could not find a user opening in run.conversation")


def safe_name(value: str) -> str:
    name = PurePosixPath(value).name
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
    cleaned = "".join(char if char in allowed else "_" for char in name)
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError(f"Unsafe asset name: {value!r}")
    return cleaned


def place_asset(source: Path, destination: Path, mode: str) -> str:
    if not source.is_file():
        raise FileNotFoundError(f"Screenshot does not exist: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if os.path.samefile(source, destination):
            return "existing-hardlink"
        raise FileExistsError(f"Asset collision: {destination}")
    if mode == "hardlink":
        try:
            os.link(source, destination)
            return "hardlink"
        except OSError:
            shutil.copy2(source, destination)
            return "copy-fallback"
    shutil.copy2(source, destination)
    return "copy"


def result_preview(step: dict[str, Any]) -> str:
    preview = step.get("result_preview")
    if isinstance(preview, str):
        return preview
    result = step.get("result")
    if result is None:
        return ""
    if not isinstance(result, str):
        result = json.dumps(result, ensure_ascii=False)
    return result[:4000]


def real_state_reference(result: dict[str, Any], result_dir: Path) -> dict[str, Any] | None:
    descriptor = result.get("run", {}).get("real_state")
    if not isinstance(descriptor, dict) or not descriptor.get("file"):
        return None
    state_path = result_dir / str(descriptor["file"])
    return {
        "descriptor": descriptor,
        "source_path": str(state_path.resolve()),
        "exists": state_path.is_file(),
    }


def materialize_output(
    *,
    result: dict[str, Any],
    result_path: Path,
    project: Path,
    case_id: str,
    slot: str,
    link_mode: str,
    link_stats: Counter[str],
    require_screenshots: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    run = result["run"]
    result_dir = result_path.parent
    trace: list[dict[str, Any]] = []
    all_asset_paths: list[str] = []

    for position, source_step in enumerate(run.get("trajectory") or []):
        if not isinstance(source_step, dict):
            continue
        screenshot_paths: list[str] = []
        screenshot = source_step.get("screenshot")
        if isinstance(screenshot, dict) and screenshot.get("file"):
            source_rel = str(screenshot["file"])
            filename = safe_name(source_rel)
            rel = Path("assets") / case_id / slot / "trace" / f"{position:03d}-{filename}"
            method = place_asset(result_dir / source_rel, project / rel, link_mode)
            link_stats[method] += 1
            rel_url = rel.as_posix()
            screenshot_paths.append(rel_url)
            all_asset_paths.append(rel_url)
        trace.append(
            {
                "index": position,
                "source_step": source_step.get("step"),
                "kind": "tool",
                "tool": source_step.get("tool"),
                "args": source_step.get("args") or {},
                "result_preview": result_preview(source_step),
                "screenshot_paths": screenshot_paths,
            }
        )

    final_paths: list[str] = []
    for position, screenshot in enumerate(run.get("final_screenshots") or []):
        if not isinstance(screenshot, dict) or not screenshot.get("file"):
            continue
        source_rel = str(screenshot["file"])
        filename = safe_name(source_rel)
        rel = Path("assets") / case_id / slot / "final" / f"{position:03d}-{filename}"
        method = place_asset(result_dir / source_rel, project / rel, link_mode)
        link_stats[method] += 1
        rel_url = rel.as_posix()
        final_paths.append(rel_url)
        all_asset_paths.append(rel_url)

    if require_screenshots and not all_asset_paths:
        raise ValueError(f"No screenshots found for {result_path}")

    public = {
        "slot": slot,
        "opening": opening_text(run),
        "conversation": run.get("conversation") or [],
        "final_answer": run.get("answer") or "",
        "stop_reason": run.get("stop_reason"),
        "step_count": run.get("steps"),
        "steps": trace,
        "final_screenshot_paths": final_paths,
        "screenshot_count": len(all_asset_paths),
    }
    judgement = result.get("judgement") or {}
    private = {
        "result_path": str(result_path),
        "automated_score": judgement.get("score"),
        "automated_met": judgement.get("met"),
        "automated_total": judgement.get("total"),
        "automated_verdicts": judgement.get("verdicts") or {},
        "real_state_reference": real_state_reference(result, result_dir),
        "source_screenshot_count": len(all_asset_paths),
    }
    return public, private


def normalized_rubrics(task_snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rubrics = task_snapshot.get("rubrics") or {}
    if not isinstance(rubrics, dict) or not rubrics:
        raise ValueError("Task snapshot contains no rubrics")
    return [
        {
            "rubric_id": rubric_id,
            "criterion": body.get("requirement", ""),
            "verification": body.get("verification", ""),
        }
        for rubric_id, body in rubrics.items()
        if isinstance(body, dict)
    ]


def balanced_slots(case_ids: list[str], seed: str) -> dict[str, dict[str, str]]:
    ranked = sorted(
        case_ids,
        key=lambda case_id: hashlib.sha256(f"{seed}:{case_id}".encode()).hexdigest(),
    )
    grok_a = set(ranked[: len(ranked) // 2])
    return {
        case_id: ({"A": "grok", "B": "muse"} if case_id in grok_a else {"A": "muse", "B": "grok"})
        for case_id in case_ids
    }


def main() -> int:
    args = parse_args()
    project = args.project_root.resolve()
    selection = load_json(args.selection.resolve())
    tasks = validate_selection(selection)
    paired_rows = select_rows(read_rows(args.paired_csv.resolve()), tasks)
    clean_generated(project, args.force)

    task_order = {(str(task["suite"]), str(task["task_id"])): index for index, task in enumerate(tasks)}
    case_specs: list[tuple[dict[str, Any], str, dict[str, str]]] = []
    for task in tasks:
        for role in ROLES:
            key = (str(task["suite"]), str(task["task_id"]), role)
            case_id = f"{task['scenario']}--{str(task['task_id'])[:12]}--{role}"
            case_specs.append((task, case_id, paired_rows[key]))

    slot_maps = balanced_slots(
        [case_id for _, case_id, _ in case_specs], str(selection["model_slot_seed"])
    )
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    public_cases: list[dict[str, Any]] = []
    private_cases: dict[str, Any] = {}
    link_stats: Counter[str] = Counter()
    suite_counts: Counter[str] = Counter()
    scenario_counts: Counter[str] = Counter()
    total_screenshots = 0

    for task, case_id, row in case_specs:
        role = row["role"]
        expected_persona = f"{task['scenario']}-{role}"
        if row["persona"] != expected_persona:
            raise ValueError(
                f"Scenario/persona mismatch for {case_id}: {row['persona']} != {expected_persona}"
            )

        loaded: dict[str, tuple[Path, dict[str, Any]]] = {}
        for model in MODEL_SPECS:
            path = Path(row[f"{model}_result_path"]).resolve()
            if not path.is_file():
                raise FileNotFoundError(path)
            result = load_json(path)
            validate_result(result, path, str(task["task_id"]), role)
            loaded[model] = (path, result)

        grok_snapshot = loaded["grok"][1].get("task_snapshot") or {}
        muse_snapshot = loaded["muse"][1].get("task_snapshot") or {}
        grok_rubrics = normalized_rubrics(grok_snapshot)
        muse_rubrics = normalized_rubrics(muse_snapshot)
        if grok_rubrics != muse_rubrics:
            raise ValueError(f"Rubric mismatch between models for {case_id}")

        public_outputs: list[dict[str, Any]] = []
        private_outputs: dict[str, Any] = {}
        for slot in ("A", "B"):
            model = slot_maps[case_id][slot]
            path, result = loaded[model]
            public_output, private_output = materialize_output(
                result=result,
                result_path=path,
                project=project,
                case_id=case_id,
                slot=slot,
                link_mode=args.link_mode,
                link_stats=link_stats,
            )
            total_screenshots += int(public_output["screenshot_count"])
            public_outputs.append(public_output)
            private_outputs[slot] = {
                "model": model,
                **MODEL_SPECS[model],
                **private_output,
            }

        public_cases.append(
            {
                "case_id": case_id,
                "scenario": task["scenario"],
                "suite": task["suite"],
                "task_id": task["task_id"],
                "title": task["title"],
                "level": grok_snapshot.get("level") or loaded["grok"][1].get("level"),
                "persona": {
                    "id": expected_persona,
                    "role": role,
                    "scenario": task["scenario"],
                },
                "task_description": grok_snapshot.get("confirmed_task") or "",
                "rubrics": grok_rubrics,
                "outputs": public_outputs,
            }
        )
        private_cases[case_id] = {
            "output_identity": private_outputs,
            "selection_rationale": task["rationale"],
        }
        suite_counts[str(task["suite"])] += 1
        scenario_counts[str(task["scenario"])] += 1

    public_cases.sort(
        key=lambda case: (
            SCENARIOS.index(case["scenario"]),
            task_order[(case["suite"], case["task_id"])],
            ROLES.index(case["persona"]["role"]),
        )
    )
    public_manifest = {
        "schema_version": "personacua.annotation-public.v1",
        "dataset_id": selection["selection_id"],
        "generated_at": generated_at,
        "description": "Model-blind PersonaCUA rubric annotation dataset.",
        "instructions": {
            "unit": "Each case compares two anonymous model outputs on one task and persona.",
            "decision_values": ["yes", "no", "unsure"],
            "note": "Judge every rubric independently using the opening, tool trace, screenshots, and final answer.",
        },
        "counts": {
            "scenarios": len(SCENARIOS),
            "tasks": len(tasks),
            "cases": len(public_cases),
            "outputs": len(public_cases) * 2,
            "screenshot_files": total_screenshots,
            "cases_by_suite": dict(sorted(suite_counts.items())),
            "cases_by_scenario": {scenario: scenario_counts[scenario] for scenario in SCENARIOS},
        },
        "scenarios": SCENARIOS,
        "persona_roles": ROLES,
        "cases": public_cases,
    }
    private_manifest = {
        "schema_version": "personacua.annotation-private.v1",
        "dataset_id": selection["selection_id"],
        "generated_at": generated_at,
        "warning": "Private evaluator data: do not serve this manifest to annotators.",
        "public_manifest": "manifest.public.json",
        "model_slot_seed": selection["model_slot_seed"],
        "slot_balance": {
            "A": dict(
                Counter(value["A"] for value in slot_maps.values())
            ),
            "B": dict(
                Counter(value["B"] for value in slot_maps.values())
            ),
        },
        "materialization": {
            "requested_mode": args.link_mode,
            "methods": dict(sorted(link_stats.items())),
        },
        "cases": private_cases,
    }

    dump_json(project / "data" / "manifest.public.json", public_manifest)
    dump_json(project / "data" / "manifest.private.json", private_manifest)
    bytes_total = sum(path.stat().st_size for path in (project / "assets").rglob("*") if path.is_file())
    print(
        json.dumps(
            {
                "tasks": len(tasks),
                "cases": len(public_cases),
                "outputs": len(public_cases) * 2,
                "screenshots": total_screenshots,
                "asset_bytes_logical": bytes_total,
                "asset_gib_logical": round(bytes_total / (1024**3), 3),
                "materialization": dict(sorted(link_stats.items())),
                "public_manifest": str(project / "data" / "manifest.public.json"),
                "private_manifest": str(project / "data" / "manifest.private.json"),
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
        print(f"build_dataset.py: {error}", file=sys.stderr)
        raise
