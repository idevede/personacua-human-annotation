from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

sys.path.insert(0, str(PROJECT / "tools"))

from build_evidence_notes import frames_for  # noqa: E402
from server import clean_claude_note, clean_evidence_note  # noqa: E402


class EvidenceNotesTest(unittest.TestCase):
    def test_every_model_on_every_task_has_a_note(self) -> None:
        manifest = json.loads((PROJECT / "data" / "manifest.public.json").read_text(encoding="utf-8"))
        notes = json.loads((PROJECT / "data" / "evidence_notes.json").read_text(encoding="utf-8"))
        self.assertEqual(notes["credit"], "Grok 4.7")
        cases = notes["cases"]
        self.assertEqual(set(cases), {case["case_id"] for case in manifest["cases"]})
        for case in manifest["cases"]:
            bundle = cases[case["case_id"]]
            self.assertEqual(set(bundle), {output["slot"] for output in case["outputs"]})
            for output in case["outputs"]:
                slot_notes = bundle[output["slot"]]
                self.assertEqual(set(slot_notes), {rubric["rubric_id"] for rubric in case["rubrics"]})
                for raw in slot_notes.values():
                    self.assertIsNotNone(clean_evidence_note(raw))

    def test_claude_notes_point_at_real_frames(self) -> None:
        path = PROJECT / "data" / "claude_notes.json"
        if not path.is_file():
            self.skipTest("no Claude notes yet")
        manifest = json.loads((PROJECT / "data" / "manifest.public.json").read_text(encoding="utf-8"))
        notes = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(notes["credit"], "Claude Opus 5.5")
        cases = {case["case_id"]: case for case in manifest["cases"]}
        for case_id, bundle in notes["cases"].items():
            case = cases[case_id]
            outputs = {output["slot"]: output for output in case["outputs"]}
            rubric_ids = {rubric["rubric_id"] for rubric in case["rubrics"]}
            for slot, slot_notes in bundle.items():
                self.assertEqual(set(slot_notes), rubric_ids, f"{case_id} {slot}")
                frame_count = len(frames_for(outputs[slot]))
                for rubric_id, raw in slot_notes.items():
                    note = clean_claude_note(raw)
                    self.assertIsNotNone(note, f"{case_id} {slot} {rubric_id}")
                    self.assertEqual(note["frames"], raw["frames"], f"{case_id} {slot} {rubric_id}")
                    for frame in note["frames"]:
                        self.assertLessEqual(frame, frame_count, f"{case_id} {slot} {rubric_id}")


if __name__ == "__main__":
    unittest.main()
