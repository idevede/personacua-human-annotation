from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "handoff"))
sys.path.insert(0, str(PROJECT / "tools"))

from subset_format import expected_cells, load_json, model_key, result_relpath  # noqa: E402


def write_result(directory: Path, task_id: str, persona: str, tag: str, model: str) -> None:
    shots = directory / "shots"
    shots.mkdir(parents=True)
    (shots / "000.jpg").write_bytes(b"jpg")
    payload = {
        "task_id": task_id,
        "suite_id": tag,
        "persona": persona,
        "run_config": {"agent": {"model": model}},
        "task_snapshot": {
            "confirmed_task": "Do the task.",
            "title": "Example",
            "rubrics": {"R1": {"requirement": "Finish it.", "verification": "See the page."}},
        },
        "run": {
            "answer": "Done.",
            "stop_reason": "finish",
            "steps": 1,
            "conversation": [{"role": "user", "text": "Please do the task."}],
            "trajectory": [
                {
                    "step": 0,
                    "tool": "open_tab",
                    "args": {"url": "https://example.com"},
                    "screenshot": {"file": "shots/000.jpg"},
                }
            ],
        },
    }
    (directory / "result.json").write_text(json.dumps(payload), encoding="utf-8")


class SubsetTest(unittest.TestCase):
    def test_official_selection_has_110_cells(self) -> None:
        selection = load_json(PROJECT / "handoff" / "selection.json")
        cells = expected_cells(selection)
        self.assertEqual(len(cells), 110)
        self.assertEqual(cells[-1]["persona"], "none")
        self.assertEqual(model_key("Meta/Muse Glimmer"), "meta-muse-glimmer")
        self.assertIn(
            "/art-expert/result.json",
            result_relpath("grok-4.6", cells[0]),
        )

    def test_checker_and_import_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            task_id = "80257c727b8e8c5426c1b03a2a4493231747e5d7"
            selection = {
                "selection_id": "tiny",
                "persona_roles": ["expert", "practical", "senior", "young", "none"],
                "tasks": [
                    {
                        "task_id": task_id,
                        "tag": "odysseys",
                        "suite": "odysseys",
                        "scenario": "art",
                        "title": "Example",
                    }
                ],
            }
            selection_path = root / "selection.json"
            selection_path.write_text(json.dumps(selection), encoding="utf-8")
            subset = root / "personacua-annotation-subset"
            cells = []
            for cell in expected_cells(selection):
                result_dir = subset / Path(result_relpath("demo-model", cell)).parent
                write_result(result_dir, task_id, cell["persona"], "odysseys", "Demo Model")
                cells.append({**cell, "model_key": "demo-model", "result": result_relpath("demo-model", cell)})
            (subset / "subset.json").write_text(
                json.dumps(
                    {
                        "schema_version": "personacua.annotation-subset.v1",
                        "selection_id": "tiny",
                        "models": [{"model_key": "demo-model", "display_name": "Demo Model"}],
                        "cells": cells,
                    }
                ),
                encoding="utf-8",
            )
            check = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT / "handoff" / "check_subset.py"),
                    "--subset",
                    str(subset),
                    "--selection",
                    str(selection_path),
                ],
                cwd=PROJECT / "handoff",
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(check.returncode, 0, check.stderr)
            project = root / "project"
            (project / "data").mkdir(parents=True)
            imported = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT / "tools" / "import_subset.py"),
                    "--subset",
                    str(subset),
                    "--selection",
                    str(selection_path),
                    "--project-root",
                    str(project),
                    "--force",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(imported.returncode, 0, imported.stderr)
            public = json.loads((project / "data" / "manifest.public.json").read_text())
            private = json.loads((project / "data" / "manifest.private.json").read_text())
            case = public["cases"][0]
            self.assertEqual(case["tag"], "odysseys")
            self.assertEqual([item["slot"] for item in case["outputs"]], ["A"])
            self.assertNotIn("demo-model", json.dumps(public))
            self.assertEqual(
                private["cases"][case["case_id"]]["output_identity"]["A"]["model"],
                "demo-model",
            )
            self.assertTrue((project / "assets").is_dir())


if __name__ == "__main__":
    unittest.main()
