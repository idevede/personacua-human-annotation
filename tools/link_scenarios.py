#!/usr/bin/env python3
"""Group PersonaCUA task folders by the benchmark's 11 scenario families.

The mapping is DOMAIN_FAMILY in PersonaCUABench's persona_assignments.py.
Each family directory holds symlinks to that family's persona files and to
every task result folder under src/results/s1/{odysseys,real}/<task_id>.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path


BENCH = Path("/fsx/home/defu.cao/workspace/PersonaCUABench-grok-4.6-azure")
SRC = BENCH / "src"
PROJECT = Path(__file__).resolve().parents[1]

# Kept in the same order as the annotation subset.
FAMILIES = [
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
ROLES = ("expert", "senior", "young", "practical")
DOMAIN_FAMILY = {
    "Travel": "travel",
    "Staynb (Airbnb)": "travel",
    "Udriver (Uber)": "travel",
    "FlyUnified (United Airlines)": "travel",
    "Science": "science",
    "Tech": "tech",
    "Community": "community",
    "Art": "art",
    "Lifestyle": "lifestyle",
    "Zilloft (Zillow)": "lifestyle",
    "Ecommerce": "ecommerce",
    "Health": "health",
    "Fitness": "health",
    "Food": "food",
    "DashDish (DoorDash)": "food",
    "OpenDining (OpenTable)": "food",
    "GoMail (Gmail)": "productivity",
    "GoCalendar (Google Calendar)": "productivity",
    "NetworkIn (LinkedIn)": "professional",
    "TopWork (Upwork)": "professional",
}
LABELS = {
    "art": "艺术",
    "community": "社区",
    "ecommerce": "电商",
    "food": "美食",
    "health": "健康",
    "lifestyle": "生活",
    "productivity": "效率",
    "professional": "职业",
    "science": "科学",
    "tech": "科技",
    "travel": "出行",
}
FOCUS = {
    "art": "手作、布置、创作项目",
    "community": "本地活动、食物柜、捐赠、节日、志愿",
    "ecommerce": "商品比较、球鞋、汽车、优惠",
    "food": "食谱、备餐、餐饮、订位、外卖",
    "health": "健身房、瑜伽、诊所、外科、健身计划",
    "lifestyle": "衣服、礼物、家居、个人整理、房源",
    "productivity": "邮件收件箱和日历日程",
    "professional": "找工作、招聘、人脉、自由职业平台",
    "science": "学习辅导、学位项目、研究方法、教学材料",
    "tech": "消费硬件、软件排错、订阅、使用能力",
    "travel": "航班、住宿、打车、行程",
}
DOMAIN_ZH = {
    "Art": "艺术",
    "Community": "社区",
    "Ecommerce": "电商",
    "Food": "美食",
    "Health": "健康",
    "Fitness": "健身",
    "Lifestyle": "生活方式",
    "Science": "科学",
    "Tech": "科技",
    "Travel": "出行",
    "OpenDining (OpenTable)": "订餐 OpenDining（OpenTable）",
    "DashDish (DoorDash)": "外卖 DashDish（DoorDash）",
    "Zilloft (Zillow)": "找房 Zilloft（Zillow）",
    "GoMail (Gmail)": "邮件 GoMail（Gmail）",
    "GoCalendar (Google Calendar)": "日历 GoCalendar（Google 日历）",
    "TopWork (Upwork)": "接单 TopWork（Upwork）",
    "NetworkIn (LinkedIn)": "职业社交 NetworkIn（LinkedIn）",
    "Udriver (Uber)": "打车 Udriver（Uber）",
    "Staynb (Airbnb)": "住宿 Staynb（Airbnb）",
    "FlyUnified (United Airlines)": "机票 FlyUnified（美联航）",
}


def link(target: Path, destination: Path) -> None:
    """Copy the benchmark file or directory into this repo. Do not leave a symlink."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        destination.unlink()
    elif destination.is_dir():
        shutil.rmtree(destination)
    elif destination.exists():
        destination.unlink()
    if target.is_dir():
        shutil.copytree(target, destination, symlinks=False)
    else:
        shutil.copy2(target, destination, follow_symlinks=True)


def main() -> None:
    guide_path = PROJECT / "data" / "guide.zh.json"
    guide = json.loads(guide_path.read_text(encoding="utf-8")) if guide_path.is_file() else {}
    titles_zh = {
        task_id: body.get("title_zh")
        for task_id, body in (guide.get("tasks") or {}).items()
        if isinstance(body, dict)
    }
    selected = {
        task["task_id"]
        for task in json.loads((PROJECT / "config" / "selected_tasks.json").read_text())["tasks"]
    }
    root = PROJECT / "scenarios"
    families: dict[str, dict] = {
        family: {
            "label": LABELS[family],
            "focus": FOCUS[family],
            "domains": [],
            "principle": "",
            "persona_files": [],
            "tasks": [],
        }
        for family in FAMILIES
    }
    for domain, family in DOMAIN_FAMILY.items():
        families[family]["domains"].append({"id": domain, "label": DOMAIN_ZH.get(domain, domain)})

    for family in FAMILIES:
        names = "、".join(item["label"] for item in families[family]["domains"])
        families[family]["principle"] = (
            f"{FOCUS[family]}。domain 是{names}的任务归到这个场景。"
        )
        persona_dir = root / family / "personas"
        for role in ROLES:
            filename = f"{family}-{role}.yaml"
            link(SRC / "personas" / filename, persona_dir / filename)
            families[family]["persona_files"].append(f"scenarios/{family}/personas/{filename}")

    for suite in ("odysseys", "real"):
        tasks = json.loads((SRC / "task_suites" / suite / "000-core.json").read_text())
        task_file = f"src/task_suites/{suite}/000-core.json"
        for task in tasks:
            if task["task_id"] not in selected:
                continue
            family = DOMAIN_FAMILY[task["domain"]]
            folder_name = f"{suite}--{task['task_id']}"
            relative = f"scenarios/{family}/tasks/{folder_name}"
            link(SRC / "results" / "s1" / suite / task["task_id"], root / family / "tasks" / folder_name)
            families[family]["tasks"].append(
                {
                    "task_id": task["task_id"],
                    "title": task.get("title") or task["task_id"][:12],
                    "title_zh": titles_zh.get(task["task_id"]) or "",
                    "suite": suite,
                    "tag": suite,
                    "domain": task["domain"],
                    "domain_zh": DOMAIN_ZH.get(task["domain"], task["domain"]),
                    "folder": relative,
                    "task_file": task_file,
                    "in_annotation": True,
                }
            )

    for family in FAMILIES:
        families[family]["tasks"].sort(key=lambda item: (item["suite"], item["title"]))

    catalog = {
        "principle": (
            "一条任务进哪个场景，只看它的 domain，对照 PersonaCUABench 里 "
            "persona_assignments.py 的 DOMAIN_FAMILY。REAL 的模拟网站跟它实际在办的事走："
            "订桌是美食，住宿或打车是出行，收件箱是效率，招人是职业。"
        ),
        "roles_principle": (
            "每个场景固定四个人设：专家是做这行的，年长的人在旁边生活过但没有专门学，"
            "年轻的人有兴趣但没受过训练，务实的人会做但忙。人设文件在该场景的 personas/ 目录。"
        ),
        "sources": {
            "assignments": "src/persona_assignments.py",
            "personas": "src/make_personas.py",
            "results": "src/results/s1",
        },
        "family_order": FAMILIES,
        "families": families,
    }
    out = PROJECT / "data" / "scenarios.json"
    out.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    task_count = sum(len(body["tasks"]) for body in families.values())
    print(json.dumps({"families": len(families), "tasks": task_count, "catalog": str(out)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
