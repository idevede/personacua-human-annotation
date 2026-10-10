#!/usr/bin/env python3
"""Print one model's trace for writing data/claude_notes.json by hand.

Frame numbers match the viewer (tools/build_evidence_notes.py frames_for).
Page text comes from the full tool result in result.json, not the 400-character
preview. Model names and automated scores are never printed.

    python3 tools/show_trace.py art--80257c727b8e--practical A
    python3 tools/show_trace.py art--80257c727b8e--practical A --chars 3000
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tools"))

from build_evidence_notes import frames_for, load_json  # noqa: E402


def full_results(case_id: str, slot: str) -> list[dict]:
    private = load_json(PROJECT / "data" / "manifest.private.json")
    identity = ((private.get("cases") or {}).get(case_id) or {}).get("output_identity") or {}
    path = (identity.get(slot) or {}).get("result_path")
    if not path or not Path(path).is_file():
        return []
    return list((load_json(Path(path)).get("run") or {}).get("trajectory") or [])


def collapse_menus(value: str, keep: int = 6) -> str:
    """Long runs of short lines are menus and region lists; keep the first few."""
    lines = value.split("\n")
    out: list[str] = []
    run: list[str] = []

    def flush() -> None:
        out.extend(run[:keep])
        if len(run) > keep:
            out.append(f"…（省略 {len(run) - keep} 行短导航）")
        run.clear()

    for line in lines:
        if len(line.strip()) < 28:
            run.append(line)
        else:
            flush()
            out.append(line)
    flush()
    return "\n".join(out)


def squeeze(value: str, limit: int) -> str:
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n\s*\n+", "\n", value).strip()
    value = collapse_menus(value)
    return value if len(value) <= limit else value[:limit] + f" …（截断，共 {len(value)} 字）"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_id")
    parser.add_argument("slot")
    parser.add_argument("--chars", type=int, default=1500, help="Page text per frame")
    parser.add_argument("--only", default="", help="Comma-separated frame numbers to print, e.g. 12,40")
    args = parser.parse_args()
    only = {int(part) for part in args.only.split(",") if part.strip()}

    manifest = load_json(PROJECT / "data" / "manifest.public.json")
    case = next(item for item in manifest["cases"] if item["case_id"] == args.case_id)
    output = next(item for item in case["outputs"] if item["slot"] == args.slot)
    trajectory = full_results(args.case_id, args.slot)

    frames = frames_for(output)
    print(f"# {args.case_id} 模型 {args.slot} · stop_reason={output.get('stop_reason')} · 共 {len(frames)} 张")
    if not only:
        print("\n## 原始任务\n" + str(case.get("task_description") or ""))
        for message in output.get("conversation") or []:
            if message.get("role") == "user":
                print("\n## 用户说\n" + str(message.get("text") or ""))
        print("\n## Rubrics")
        for rubric in case["rubrics"]:
            print(f"- {rubric['rubric_id']}: {rubric.get('criterion')}\n  核对: {rubric.get('verification')}")

    print("\n## 截图")
    for frame in frames:
        if only and frame["n"] not in only:
            continue
        step = frame["step"]
        index = step.get("index")
        tool = step.get("tool")
        head = f"\n### 第 {frame['n']} 张 · step {index} · {tool}"
        if frame.get("final"):
            head += " · 结束时仍开着"
        print(head)
        print(f"图: {frame['path']}")
        step_args = step.get("args") or {}
        if step_args:
            print("args: " + json.dumps(step_args, ensure_ascii=False)[:400])
        text = ""
        if isinstance(index, int) and 0 <= index < len(trajectory):
            text = str(trajectory[index].get("result") or "")
        text = text or str(step.get("result_preview") or "")
        if text:
            print(squeeze(text, args.chars))

    if not only:
        print("\n## 最终回答\n" + str(output.get("final_answer") or "（没有最终回答）"))


if __name__ == "__main__":
    main()
