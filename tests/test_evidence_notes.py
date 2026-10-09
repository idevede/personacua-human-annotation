from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from server import clean_evidence_note  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
