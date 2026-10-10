#!/usr/bin/env python3
"""Check one model's Claude notes and merge them into data/claude_notes.json.

Notes are read from stdin as {rubric_id: {decision, frames, evidence, reason}}.
Several writers can run at once: the merge holds a file lock and replaces the
file atomically, so the server never reads half a file.

    python3 tools/save_claude_notes.py CASE_ID SLOT < notes.json
    python3 tools/save_claude_notes.py --has CASE_ID SLOT
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tools"))

from build_evidence_notes import frames_for, load_json  # noqa: E402

OUT = PROJECT / "data" / "claude_notes.json"
LOCK = PROJECT / "data" / ".claude_notes.lock"
CREDIT = "Claude Opus 5.5"


def check(case: dict, output: dict, notes: dict) -> dict:
    rubric_ids = {rubric["rubric_id"] for rubric in case["rubrics"]}
    if set(notes) != rubric_ids:
        raise ValueError(f"rubric ids must be exactly {sorted(rubric_ids)}, got {sorted(notes)}")
    frame_count = len(frames_for(output))
    cleaned = {}
    for rubric_id in sorted(notes):
        note = notes[rubric_id]
        if note.get("decision") not in {"yes", "no", "unsure"}:
            raise ValueError(f"{rubric_id}: decision must be yes, no or unsure")
        for key in ("evidence", "reason"):
            if not isinstance(note.get(key), str) or not note[key].strip():
                raise ValueError(f"{rubric_id}: {key} must be non-empty text")
        frames = []
        for frame in note.get("frames") or []:
            if isinstance(frame, bool) or not isinstance(frame, int) or not 1 <= frame <= frame_count:
                raise ValueError(f"{rubric_id}: frame {frame!r} is not between 1 and {frame_count}")
            if frame not in frames:
                frames.append(frame)
        cleaned[rubric_id] = {
            "decision": note["decision"],
            "frames": frames,
            "evidence": note["evidence"].strip(),
            "reason": note["reason"].strip(),
        }
    return cleaned


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_id")
    parser.add_argument("slot")
    parser.add_argument("--has", action="store_true", help="Exit 0 if notes already exist, 1 if not")
    args = parser.parse_args()

    if args.has:
        data = load_json(OUT) if OUT.is_file() else {}
        sys.exit(0 if args.slot in ((data.get("cases") or {}).get(args.case_id) or {}) else 1)

    manifest = load_json(PROJECT / "data" / "manifest.public.json")
    case = next((item for item in manifest["cases"] if item["case_id"] == args.case_id), None)
    if case is None:
        raise SystemExit(f"unknown case_id {args.case_id}")
    output = next((item for item in case["outputs"] if item["slot"] == args.slot), None)
    if output is None:
        raise SystemExit(f"unknown slot {args.slot} for {args.case_id}")
    try:
        notes = check(case, output, json.loads(sys.stdin.read()))
    except (ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"not saved: {exc}")

    with LOCK.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = load_json(OUT) if OUT.is_file() else {"credit": CREDIT, "cases": {}}
        data.setdefault("cases", {}).setdefault(args.case_id, {})[args.slot] = notes
        fd, temp = tempfile.mkstemp(prefix=".claude_notes.", suffix=".tmp", dir=OUT.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temp, OUT)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    print(json.dumps({"saved": f"{args.case_id} {args.slot}", "decisions": {k: v["decision"] for k, v in notes.items()}}))


if __name__ == "__main__":
    main()
