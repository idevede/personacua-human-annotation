from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from server import (  # noqa: E402
    AnnotationStore,
    attach_claude_notes,
    attach_evidence_notes,
    create_server,
    enrich_public_manifest,
    safe_annotator_name,
)


class ServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for directory in ("web", "assets", "data", "annotations"):
            (self.root / directory).mkdir()
        (self.root / "web" / "index.html").write_text("hello", encoding="utf-8")
        (self.root / "assets" / "shot.png").write_bytes(b"PNG")
        (self.root / "data" / "manifest.public.json").write_text(
            json.dumps(
                {
                    "schema_version": "test",
                    "dataset_id": "dataset-1",
                    "cases": [
                        {
                            "case_id": "case-1",
                            "rubrics": [{"rubric_id": "R1"}],
                            "output_identity": {"A": {"model": "must-not-leak"}},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (self.root / "data" / "manifest.private.json").write_text(
            json.dumps(
                {"cases": {
                    "case-1": {
                        "output_identity": {
                            "A": {
                                "model": "secret-model",
                                "display_name": "Secret Model",
                                "result_path": "/private/result.json",
                                "automated_verdicts": {"R1": {"met": True}},
                                "real_state": {"large": "blob"},
                            }
                        },
                        "real_state": {"another": "blob"},
                    }
                }}
            ),
            encoding="utf-8",
        )
        self.server = create_server(self.root, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def get_json(self, path: str):
        with urlopen(self.base + path) as response:
            return response.status, json.load(response)

    def test_health_and_static(self) -> None:
        status, data = self.get_json("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        with urlopen(self.base + "/") as response:
            self.assertEqual(response.read(), b"hello")
        with urlopen(self.base + "/assets/shot.png") as response:
            self.assertEqual(response.read(), b"PNG")

    def test_bootstrap_does_not_reveal_private_identity(self) -> None:
        _, data = self.get_json("/api/bootstrap?annotator=Alice")
        self.assertEqual(data["annotation"]["annotator"], "Alice")
        self.assertNotIn("secret-model", json.dumps(data))
        self.assertNotIn("must-not-leak", json.dumps(data))
        with urlopen(self.base + "/data/manifest.public.json") as response:
            self.assertNotIn("must-not-leak", response.read().decode())

    def test_save_is_atomic_and_resume_is_public(self) -> None:
        payload = {
            "dataset_id": "dataset-1",
            "annotator": "张 三",
            "started_at": "2026-01-01T00:00:00Z",
            "annotations": {"case-1": {"rubrics": {}}},
            "model_identity_by_case": {"fake": "must-not-round-trip"},
        }
        request = Request(
            self.base + "/api/save",
            method="POST",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request) as response:
            result = json.load(response)
        saved_path = self.root / "annotations" / result["filename"]
        saved = json.loads(saved_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["annotator"], "张 三")
        self.assertEqual(
            saved["_private"]["model_identity_by_case"]["case-1"]["A"]["model"],
            "secret-model",
        )
        self.assertNotIn("automated_verdicts", json.dumps(saved["_private"]))
        self.assertNotIn("real_state", json.dumps(saved["_private"]))
        _, resumed = self.get_json("/api/bootstrap?annotator=%E5%BC%A0%20%E4%B8%89")
        self.assertEqual(resumed["annotation"]["annotations"], payload["annotations"])
        self.assertNotIn("_private", resumed["annotation"])
        self.assertNotIn("model_identity_by_case", resumed["annotation"])
        self.assertEqual(list((self.root / "annotations").glob("*.tmp")), [])

    def test_rejects_unsafe_name_and_unknown_case(self) -> None:
        with self.assertRaises(ValueError):
            safe_annotator_name("../escape")
        payload = {
            "dataset_id": "dataset-1",
            "annotator": "Alice",
            "annotations": {"unknown": {}},
        }
        request = Request(
            self.base + "/api/save",
            method="POST",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(request)
        self.assertEqual(caught.exception.code, 400)

    def test_store_rejects_dataset_mismatch(self) -> None:
        store = AnnotationStore(self.root)
        with self.assertRaisesRegex(ValueError, "dataset_id"):
            store.save_annotation(
                {"dataset_id": "wrong", "annotator": "Alice", "annotations": {}}
            )

    def test_guide_is_public_and_slots_follow_the_case(self) -> None:
        enriched = enrich_public_manifest(
            {
                "cases": [{
                    "case_id": "case-1",
                    "task_id": "task-1",
                    "rubrics": [{"rubric_id": "R1", "criterion": "English"}],
                    "outputs": [{"slot": "A"}, {"slot": "C"}],
                }]
            },
            {
                "credit": "Grok 4.7",
                "labels": {"domain": {"Food": "美食"}},
                "tasks": {
                    "task-1": {
                        "title_zh": "中文标题",
                        "domain": "Food",
                        "rubrics": {"R1": {"criterion_zh": "中文标准", "hint_zh": "看到这些就可以判得分。"}},
                    }
                },
            },
        )
        self.assertEqual(enriched["cases"][0]["title_zh"], "中文标题")
        self.assertEqual(enriched["cases"][0]["rubrics"][0]["hint_zh"], "看到这些就可以判得分。")
        noted = attach_evidence_notes(
            enriched,
            {
                "credit": "Grok 4.7",
                "cases": {
                    "case-1": {
                        "A": {
                            "R1": {
                                "frames": [5, 5, 0],
                                "support": "partial",
                                "finding": "第 5 张截图里有结果。",
                                "verdict": "只支持一部分。",
                            }
                        }
                    }
                },
            },
        )
        note = noted["cases"][0]["outputs"][0]["evidence_notes"]["R1"]
        self.assertEqual(note["frames"], [5])
        self.assertEqual(note["support"], "partial")
        self.assertEqual(noted["evidence_credit"], "Grok 4.7")
        self.assertNotIn("evidence_notes", noted["cases"][0]["outputs"][1])
        judged = attach_claude_notes(
            noted,
            {
                "credit": "Claude Opus 5.5",
                "cases": {
                    "case-1": {
                        "A": {
                            "R1": {
                                "decision": "unsure",
                                "frames": [3, 3, True],
                                "evidence": "第 3 张被弹窗挡住。",
                                "reason": "看不到要求的内容。",
                            },
                            "R2": {"decision": "partial", "frames": [], "evidence": "x", "reason": "y"},
                        }
                    }
                },
            },
        )
        claude = judged["cases"][0]["outputs"][0]["claude_notes"]
        self.assertEqual(set(claude), {"R1"})
        self.assertEqual(claude["R1"]["frames"], [3])
        self.assertEqual(claude["R1"]["decision"], "unsure")
        self.assertEqual(judged["claude_credit"], "Claude Opus 5.5")
        self.assertIn("evidence_notes", judged["cases"][0]["outputs"][0])
        self.assertNotIn("model", json.dumps(enriched))
        store = AnnotationStore(self.root)
        manifest = store.public_manifest()
        manifest["cases"][0]["outputs"] = [{"slot": "A"}, {"slot": "C"}]
        with self.assertRaisesRegex(ValueError, "Unknown slot"):
            store.save_annotation(
                {
                    "dataset_id": "dataset-1",
                    "annotator": "Alice",
                    "annotations": {"case-1": {"rubrics": {"R1": {"B": "yes"}}}},
                }
            )

    def test_store_validates_decisions_and_rubric_ids(self) -> None:
        store = AnnotationStore(self.root)
        with self.assertRaisesRegex(ValueError, "Invalid A decision"):
            store.save_annotation(
                {
                    "dataset_id": "dataset-1",
                    "annotator": "Alice",
                    "annotations": {
                        "case-1": {"rubrics": {"R1": {"A": "maybe"}}}
                    },
                }
            )
        with self.assertRaisesRegex(ValueError, "Unknown rubric_id"):
            store.save_annotation(
                {
                    "dataset_id": "dataset-1",
                    "annotator": "Alice",
                    "annotations": {
                        "case-1": {"rubrics": {"R99": {"A": "yes"}}}
                    },
                }
            )

    def test_unicode_filename_is_safe_and_collision_resistant(self) -> None:
        display, stem = safe_annotator_name("  张   三  ")
        self.assertEqual(display, "张 三")
        self.assertEqual(stem, "张 三")
        self.assertNotEqual(safe_annotator_name("Alice!")[1], safe_annotator_name("Alice_")[1])
        with self.assertRaises(ValueError):
            safe_annotator_name("Alice\u202ejson")

    def test_save_requires_json_content_type(self) -> None:
        request = Request(self.base + "/api/save", method="POST", data=b"{}")
        with self.assertRaises(HTTPError) as caught:
            urlopen(request)
        self.assertEqual(caught.exception.code, 415)


if __name__ == "__main__":
    unittest.main()
