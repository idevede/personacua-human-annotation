#!/usr/bin/env python3
"""Build per-model rubric hints from each output's screenshots and final answer.

The annotation page shows these notes under every rubric. Each note names the
screenshot frames to open and whether that evidence supports the rubric.
Frame numbers match the viewer: trace shots first, then final shots, starting at 1.

    python3 tools/build_evidence_notes.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
PUBLIC = PROJECT / "data" / "manifest.public.json"
GUIDE = PROJECT / "data" / "guide.zh.json"
OUT = PROJECT / "data" / "evidence_notes.json"

STOP = {
    "that", "this", "with", "from", "have", "were", "been", "will", "your", "their",
    "about", "after", "before", "into", "over", "under", "than", "then", "them",
    "they", "what", "when", "where", "which", "while", "would", "could", "should",
    "there", "these", "those", "each", "both", "such", "only", "also", "just",
    "more", "most", "some", "other", "another", "using", "used", "use", "make",
    "provide", "including", "include", "includes", "based", "page", "pages",
    "open", "opened", "browser", "final", "response", "answer", "note", "brief",
    "grader", "confirm", "visible", "shown", "show", "shows", "website", "site",
    "official", "separate", "tabs", "tab", "link", "links", "direct", "result",
    "results", "search", "google", "task", "agent", "correctly", "identify",
    "report", "reported", "summary", "summarize", "find", "found", "least",
    "along", "through", "during", "within", "without", "between", "across",
    "whether", "either", "every", "still", "live", "real", "actual", "related",
    "relevant", "specific", "information", "details", "detail", "section",
    "content", "text", "name", "names", "title", "available", "option", "options",
    "please", "would", "like", "need", "want", "book", "booking",
}

BLOCK_MARKERS = (
    "legal terms and privacy",
    "use of cookies on this site",
    "this website uses cookies",
    "agree and proceed",
    "sorry, this job was removed",
    "file not found",
    "error 404",
    "404 (not found)",
    "click failed",
    "typing failed",
    "did not accept the text",
    "tab limit",
    "no visible element matching",
    "covered by another element",
)

CONFIRM_MARKERS = (
    "booking complete",
    "you are all set",
    "reservation confirmed",
    "your table is booked",
    "message sent",
    "email sent",
    "successfully",
    "confirmation",
    "has been created",
    "job posted",
    "tour booked",
    "ride booked",
    "request submitted",
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def frames_for(output: dict[str, Any]) -> list[dict[str, Any]]:
    """Same order as web/app.js framesFor. n is the 1-based viewer index."""
    frames: list[dict[str, Any]] = []
    for step in output.get("steps") or []:
        if not isinstance(step, dict):
            continue
        shots = step.get("screenshot_paths") or []
        if step.get("screenshot_path"):
            shots = [step["screenshot_path"], *shots]
        for path in shots:
            frames.append({"path": path, "step": step, "final": False})
    seen = {frame["path"]: frame for frame in frames}
    for offset, path in enumerate(output.get("final_screenshot_paths") or []):
        if path in seen:
            seen[path]["final"] = True
            continue
        frames.append(
            {
                "path": path,
                "step": {
                    "index": "final",
                    "tool": "final_state",
                    "result_preview": "",
                    "args": {},
                },
                "final": True,
                "final_offset": offset,
            }
        )
    for index, frame in enumerate(frames, start=1):
        frame["n"] = index
    return frames


def host_of(step: dict[str, Any]) -> str:
    args = step.get("args") or {}
    url = args.get("url") if isinstance(args, dict) else ""
    preview = str(step.get("result_preview") or "")
    match = re.search(r"https?://(?:www\.)?([^/\s\"']+)", f"{url}\n{preview}")
    if match:
        return match.group(1)
    return str(step.get("tool") or "screenshot")


def page_text(frame: dict[str, Any]) -> str:
    """Text captured with this shot. The finish summary is the answer, not the page."""
    step = frame["step"]
    if step.get("tool") in {"finish", "list_tabs"}:
        return ""
    return str(step.get("result_preview") or "")


def readable_text(blob: str) -> str:
    """Drop tool JSON and URLs so a search query is not treated as page evidence."""
    if not blob.strip() or blob.lstrip()[:1] in "{[":
        return ""
    if blob.count('"url"') >= 2 or blob.count('"tab_id"') >= 1:
        return ""
    lines = []
    for line in blob.splitlines():
        line = re.sub(r"https?://\S+", "", line)
        line = re.sub(r"\s+", " ", line).strip(" |\"'")
        if len(line) < 12:
            continue
        lines.append(line)
    return "\n".join(lines)


def answer_entities(answer: str, needles: list[str]) -> list[str]:
    """Names the final answer uses in sentences that already match this rubric."""
    extra: list[str] = []
    seen = {needle.lower() for needle in needles}
    parts = re.split(r"\n+|(?<=[.!?])\s+", answer)
    for part in parts:
        if not covered(part, needles):
            continue
        for token in re.findall(r"[A-Z][A-Za-z0-9.+-]{2,}(?:\s+[A-Z][A-Za-z0-9.+-]{2,}){0,3}", part):
            key = token.lower()
            if key in seen or key in STOP:
                continue
            seen.add(key)
            extra.append(token)
            if len(extra) >= 8:
                return extra
    return extra


def anchors(text: str) -> list[str]:
    """Distinctive phrases and words a screenshot should contain."""
    found: list[str] = []
    seen: set[str] = set()

    def add(value: str, minimum: int = 3) -> None:
        cleaned = re.sub(r"\s+", " ", value).strip(" .,:;\"'")
        key = cleaned.lower()
        if len(cleaned) < minimum or key in seen or key in STOP:
            return
        seen.add(key)
        found.append(cleaned)

    for quote in re.findall(r"[\"“]([^\"”]{3,80})[\"”]", text):
        add(quote, 3)
    for number in re.findall(r"\$?\d[\d,]*(?:\.\d+)?%?", text):
        add(number, 2)
    for token in re.findall(r"[A-Za-z][A-Za-z0-9'’+./-]{2,}", text):
        lower = token.lower().strip("'/.")
        if lower in STOP or len(lower) < 4:
            continue
        if token[:1].isupper() or any(char.isdigit() for char in token) or len(lower) >= 7:
            add(token, 4)
    return found[:18]


def contains(blob: str, anchor: str) -> bool:
    if anchor[:1].isdigit() or anchor[:1] == "$":
        return anchor.lower() in blob.lower()
    return re.search(rf"(?<![a-z0-9]){re.escape(anchor.lower())}(?![a-z0-9])", blob.lower()) is not None


def covered(blob: str, needles: list[str]) -> list[str]:
    return [needle for needle in needles if contains(blob, needle)]


def blocked(blob: str) -> bool:
    low = blob.lower()
    return any(marker in low for marker in BLOCK_MARKERS)


def confirmed(blob: str) -> bool:
    low = blob.lower()
    return any(marker in low for marker in CONFIRM_MARKERS)


def excerpt(blob: str, needles: list[str], limit: int = 220) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in blob.splitlines()]
    lines = [line for line in lines if len(line) >= 24]
    if not lines:
        compact = re.sub(r"\s+", " ", blob).strip()
        return compact[:limit]
    ranked = sorted(lines, key=lambda line: (len(covered(line, needles)), len(line)), reverse=True)
    chosen: list[str] = []
    for line in ranked:
        if line in chosen:
            continue
        if not covered(line, needles) and chosen:
            continue
        chosen.append(line[:180])
        if len(chosen) == 2 or sum(len(line) for line in chosen) > limit:
            break
    text = "；".join(chosen)
    return text[:limit].rstrip("； ")


def checker_rubric(rubric: dict[str, Any]) -> bool:
    verification = rubric.get("verification") or ""
    return "prints SUCCESS" in verification or "state checker" in verification.lower()


def note_for_checker(output: dict[str, Any], frames: list[dict[str, Any]]) -> dict[str, Any]:
    answer = output.get("final_answer") or ""
    ranked = sorted(
        frames,
        key=lambda frame: (confirmed(page_text(frame)), frame["n"]),
        reverse=True,
    )
    hits = [frame for frame in ranked if confirmed(page_text(frame))]
    picked = hits[:2] or frames[-2:]
    answer_ok = confirmed(answer)
    page_ok = bool(hits)
    if page_ok and answer_ok:
        support = "yes"
        verdict = "支持。最后几张截图和最终回答里都有完成或确认的字样。请打开截图，核对是不是这条任务要求的结果。"
    elif page_ok or answer_ok:
        support = "partial"
        verdict = "只支持一部分。完成提示只出现在截图或最终回答其中一处。请打开截图核对页面上的结果，不要只看文字总结。"
    else:
        support = "no"
        verdict = "不支持。截图和最终回答里都没有看到预订、发送或创建成功的确认。"
    if output.get("stop_reason") != "finish":
        verdict += "这条在步数上限就停了。"
    bits = []
    for frame in picked:
        text = excerpt(page_text(frame), ["booked", "confirmation", "complete", "sent", "created"])
        where = host_of(frame["step"])
        bits.append(f"第 {frame['n']} 张（{where}）能看到：{text or '这一步没有可读的页面文字'}")
    if answer.strip():
        bits.append(f"最终回答里写了：{excerpt(answer, ['booked', 'confirmation', 'complete', 'sent'])}")
    return {
        "frames": [frame["n"] for frame in picked] or [frames[-1]["n"]],
        "support": support,
        "finding": "。".join(bits) + "。",
        "verdict": verdict,
    }


def note_for_rubric(output: dict[str, Any], rubric: dict[str, Any], frames: list[dict[str, Any]]) -> dict[str, Any]:
    if checker_rubric(rubric):
        return note_for_checker(output, frames)
    needles = anchors(f"{rubric.get('criterion') or ''} {rubric.get('verification') or ''}")
    answer = output.get("final_answer") or ""
    answer_hits = covered(answer, needles)
    extra = answer_entities(answer, needles)
    scored: list[tuple[int, bool, dict[str, Any]]] = []
    for frame in frames:
        raw = page_text(frame)
        text = readable_text(raw)
        if not text:
            continue
        hits = covered(text, needles)
        extra_hits = covered(text, extra)
        if not hits and not extra_hits:
            continue
        scored.append((len(hits) * 3 + len(extra_hits), blocked(raw), frame))
    scored.sort(key=lambda item: (item[0], not item[1], item[2]["n"]), reverse=True)
    useful = [item for item in scored if item[0] > 0]
    open_frames = [item for item in useful if not item[1]]
    pool = open_frames or useful
    destination = [item for item in pool if "google." not in host_of(item[2]["step"])]
    picked_items: list[tuple[int, bool, dict[str, Any]]] = []
    seen_hosts: set[str] = set()
    for item in destination or pool:
        host = host_of(item[2]["step"])
        if host in seen_hosts:
            continue
        seen_hosts.add(host)
        picked_items.append(item)
        if len(picked_items) == 3:
            break
    if not picked_items and frames:
        last = frames[-1]
        picked_items = [(0, False, last)]
    page_hits: set[str] = set()
    blocked_hits = 0
    open_hit_frames = 0
    for count, is_blocked, frame in picked_items:
        if count <= 0:
            continue
        if is_blocked:
            blocked_hits += 1
            continue
        open_hit_frames += 1
        page_hits.update(covered(readable_text(page_text(frame)), needles))
    page_blocked = blocked_hits > 0 and open_hit_frames == 0
    if not needles:
        need = 1
    elif len(needles) <= 3:
        need = len(needles)
    else:
        need = max(2, len(needles) // 3)
    page_ok = len(page_hits) >= need and not page_blocked
    answer_ok = len(answer_hits) >= need
    if page_ok and answer_ok:
        support = "yes"
        verdict = "支持。这些截图对应的页面文字和最终回答都能对上这条。请打开截图确认这些字露在画面上；被弹窗挡住或还没滚到的部分不算。"
    elif page_blocked and answer_ok:
        support = "partial"
        verdict = "只支持一部分。最终回答里写了相关内容，但最接近的截图被弹窗、报错或下架提示挡住，画面本身看不到要求的细节。"
    elif page_ok and not answer_ok:
        support = "partial"
        verdict = "只支持一部分。截图里有相关页面，最终回答没有把这条写全。请以画面为准。"
    elif answer_ok and not page_ok:
        support = "partial"
        verdict = "只支持一部分。最终回答里有相关内容，截图里对不上同样的关键词。请打开截图确认画面是否真的支持。"
    elif page_hits or answer_hits:
        support = "partial"
        verdict = "只支持一部分。只对上了这条要求里的一部分关键词。请打开截图看缺的那部分在不在。"
    else:
        support = "no"
        verdict = "不支持。截图和最终回答里都没有对上这条要求的关键内容。"
    if output.get("stop_reason") != "finish":
        verdict += "这条在步数上限就停了。"
    bits = []
    for _count, _blocked, frame in picked_items:
        text = excerpt(readable_text(page_text(frame)) or page_text(frame), needles + extra)
        where = host_of(frame["step"])
        label = f"第 {frame['n']} 张（{where}）"
        if frame.get("final"):
            label += "，结束时仍开着"
        bits.append(f"{label}能看到：{text or '这一步没有可读的页面文字'}")
    if answer.strip() and answer_hits:
        bits.append(f"最终回答里写了：{excerpt(answer, needles)}")
    frames_out = [item[2]["n"] for item in picked_items] or [frames[-1]["n"]]
    return {
        "frames": frames_out,
        "support": support,
        "finding": "。".join(bits) + "。",
        "verdict": verdict,
    }


def build(manifest: dict[str, Any]) -> dict[str, Any]:
    cases: dict[str, Any] = {}
    for case in manifest.get("cases") or []:
        slot_notes: dict[str, Any] = {}
        for output in case.get("outputs") or []:
            frames = frames_for(output)
            if not frames:
                continue
            notes = {
                str(rubric.get("rubric_id")): note_for_rubric(output, rubric, frames)
                for rubric in case.get("rubrics") or []
                if rubric.get("rubric_id")
            }
            if notes:
                slot_notes[str(output.get("slot"))] = notes
        if slot_notes:
            cases[str(case.get("case_id"))] = slot_notes
    return {"credit": "Grok 4.7", "cases": cases}


def main() -> None:
    manifest = load_json(PUBLIC)
    payload = build(manifest)
    if OUT.is_file():
        previous = load_json(OUT)
        hand = ((previous.get("cases") or {}).get("art--3868f9b52e96--expert") or {}).get("B")
        if isinstance(hand, dict) and hand:
            payload["cases"]["art--3868f9b52e96--expert"]["B"] = hand
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rubric_notes = sum(len(slot) for case in payload["cases"].values() for slot in case.values())
    print(json.dumps({"cases": len(payload["cases"]), "slot_rubric_notes": rubric_notes}, ensure_ascii=False))


if __name__ == "__main__":
    main()
