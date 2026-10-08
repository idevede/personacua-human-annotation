#!/usr/bin/env python3
"""Small, dependency-free server for the PersonaCUA human annotation UI."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import mimetypes
import os
import re
import tempfile
import threading
import unicodedata
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit


MAX_BODY_BYTES = 25 * 1024 * 1024
SCHEMA_VERSION = "personacua.human-annotation.v1"
PRIVATE_PUBLIC_KEYS = {
    "_private",
    "automated_verdicts",
    "model_identity_by_case",
    "output_identity",
    "real_state",
    "result_path",
    "source_result_path",
}
PRIVATE_IDENTITY_FIELDS = {
    "model",
    "display_name",
    "model_key",
    "result_path",
    "automated_score",
    "automated_met",
    "automated_total",
}
PUBLIC_ANNOTATION_FIELDS = {
    "schema_version",
    "dataset_id",
    "annotator",
    "started_at",
    "updated_at",
    "client_revision",
    "client_updated_at",
    "annotations",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def safe_annotator_name(value: Any) -> tuple[str, str]:
    """Return (display name, safe filename stem), retaining Unicode names."""
    if not isinstance(value, str):
        raise ValueError("Annotator name must be text")
    if any(unicodedata.category(ch).startswith("C") for ch in value):
        raise ValueError("Annotator name contains an unsafe character")
    display = " ".join(unicodedata.normalize("NFKC", value).strip().split())
    if not display or len(display) > 80:
        raise ValueError("Annotator name must contain 1 to 80 characters")
    if display in {".", ".."} or ".." in display:
        raise ValueError("Annotator name cannot contain '..'")
    if any(ch in display for ch in "/\\") or any(ord(ch) < 32 for ch in display):
        raise ValueError("Annotator name contains an unsafe character")

    stem = "".join(ch if (ch.isalnum() or ch in "_-. ") else "_" for ch in display)
    stem = re.sub(r"_+", "_", stem).strip("._- ")
    if not stem:
        raise ValueError("Annotator name does not contain a usable filename character")
    # Preserve ordinary names exactly. If punctuation had to be replaced (or a
    # very long UTF-8 name truncated), add a stable suffix so two people cannot
    # silently map to the same annotation file.
    encoded = stem.encode("utf-8")
    changed = stem != display
    while len(encoded) > 220:
        stem = stem[:-1]
        encoded = stem.encode("utf-8")
        changed = True
    if changed:
        digest = hashlib.sha256(display.encode("utf-8")).hexdigest()[:10]
        stem = f"{stem}-{digest}"
    return display, stem


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected an object in {path}")
    return data


def public_annotation(data: dict[str, Any]) -> dict[str, Any]:
    return {key: data[key] for key in PUBLIC_ANNOTATION_FIELDS if key in data}


def strip_private_manifest_fields(value: Any) -> Any:
    """Fail closed on private metadata if a builder puts it in the public file."""
    if isinstance(value, dict):
        return {
            key: strip_private_manifest_fields(child)
            for key, child in value.items()
            if key not in PRIVATE_PUBLIC_KEYS
        }
    if isinstance(value, list):
        return [strip_private_manifest_fields(child) for child in value]
    return value


def case_output_slots(case: dict[str, Any]) -> set[str]:
    slots = {
        str(output.get("slot"))
        for output in case.get("outputs") or []
        if isinstance(output, dict) and output.get("slot")
    }
    return slots or {"A", "B"}


def enrich_public_manifest(manifest: dict[str, Any], guide: dict[str, Any]) -> dict[str, Any]:
    """Attach Chinese copy and task-type metadata. Guide content is public."""
    if not guide:
        return manifest
    enriched = copy.deepcopy(manifest)
    labels = guide.get("labels")
    if isinstance(labels, dict):
        enriched["labels_zh"] = labels
    if isinstance(guide.get("credit"), str):
        enriched["guide_credit"] = guide["credit"]
    tasks = guide.get("tasks") if isinstance(guide.get("tasks"), dict) else {}
    for case in enriched.get("cases") or []:
        if not isinstance(case, dict):
            continue
        task = tasks.get(str(case.get("task_id")))
        if not isinstance(task, dict):
            continue
        for key in ("title_zh", "description_zh", "domain", "setting"):
            if isinstance(task.get(key), str) and task[key]:
                case[key] = task[key]
        translations = task.get("rubrics") if isinstance(task.get("rubrics"), dict) else {}
        for rubric in case.get("rubrics") or []:
            if not isinstance(rubric, dict):
                continue
            extra = translations.get(str(rubric.get("rubric_id")))
            if not isinstance(extra, dict):
                continue
            for key in ("criterion_zh", "hint_zh"):
                if isinstance(extra.get(key), str) and extra[key]:
                    rubric[key] = extra[key]
    return enriched


def attach_scenarios(manifest: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    """Add the benchmark's scenario rules and task-folder paths. Catalog is public."""
    families = catalog.get("families") if isinstance(catalog.get("families"), dict) else None
    if not families:
        return manifest
    attached = copy.deepcopy(manifest)
    public_families: dict[str, Any] = {}
    folder_by_task: dict[str, str] = {}
    tag_by_task: dict[str, str] = {}
    for family_id, body in families.items():
        if not isinstance(body, dict):
            continue
        tasks = []
        for task in body.get("tasks") or []:
            if not isinstance(task, dict) or not task.get("task_id"):
                continue
            public_task = {
                key: task[key]
                for key in (
                    "task_id", "title", "title_zh", "suite", "tag", "domain", "domain_zh",
                    "folder", "task_file", "in_annotation",
                )
                if key in task
            }
            if "tag" not in public_task and task.get("suite") in {"real", "odysseys"}:
                public_task["tag"] = task["suite"]
            tasks.append(public_task)
            task_id = str(task["task_id"])
            if task.get("folder"):
                folder_by_task[task_id] = str(task["folder"])
            tag = public_task.get("tag") or task.get("suite")
            if tag in {"real", "odysseys"}:
                tag_by_task[task_id] = str(tag)
        public_families[str(family_id)] = {
            key: body[key]
            for key in ("label", "focus", "domains", "principle", "persona_files")
            if key in body
        }
        public_families[str(family_id)]["tasks"] = tasks
    attached["scenario_catalog"] = {
        "principle": catalog.get("principle") or "",
        "roles_principle": catalog.get("roles_principle") or "",
        "family_order": catalog.get("family_order") or list(public_families),
        "families": public_families,
    }
    for case in attached.get("cases") or []:
        if not isinstance(case, dict):
            continue
        task_id = str(case.get("task_id") or "")
        if task_id in folder_by_task:
            case["task_folder"] = folder_by_task[task_id]
        tag = tag_by_task.get(task_id) or case.get("suite")
        if tag in {"real", "odysseys"}:
            case["tag"] = tag
    return attached


def clean_evidence_note(value: Any) -> dict[str, Any] | None:
    """Keep only the fields the annotation page shows for one rubric."""
    if not isinstance(value, dict):
        return None
    finding = value.get("finding")
    verdict = value.get("verdict")
    support = value.get("support")
    frames = value.get("frames")
    if not isinstance(finding, str) or not finding.strip():
        return None
    if not isinstance(verdict, str) or not verdict.strip():
        return None
    if support not in {"yes", "partial", "no"}:
        return None
    if not isinstance(frames, list) or not frames:
        return None
    cleaned_frames: list[int] = []
    for frame in frames:
        if isinstance(frame, bool) or not isinstance(frame, int) or frame < 1 or frame in cleaned_frames:
            continue
        cleaned_frames.append(frame)
    if not cleaned_frames:
        return None
    return {
        "frames": cleaned_frames,
        "support": support,
        "finding": finding.strip(),
        "verdict": verdict.strip(),
    }


def attach_evidence_notes(manifest: dict[str, Any], notes: dict[str, Any]) -> dict[str, Any]:
    """Attach per-model screenshot findings. Notes are public and contain no model names."""
    cases = notes.get("cases") if isinstance(notes, dict) else None
    if not isinstance(cases, dict):
        return manifest
    attached = copy.deepcopy(manifest)
    if isinstance(notes.get("credit"), str) and notes["credit"].strip():
        attached["evidence_credit"] = notes["credit"].strip()
    for case in attached.get("cases") or []:
        if not isinstance(case, dict):
            continue
        bundle = cases.get(str(case.get("case_id")))
        if not isinstance(bundle, dict):
            continue
        for output in case.get("outputs") or []:
            if not isinstance(output, dict):
                continue
            slot_notes = bundle.get(str(output.get("slot")))
            if not isinstance(slot_notes, dict):
                continue
            cleaned = {
                str(rubric_id): note
                for rubric_id, raw in slot_notes.items()
                if (note := clean_evidence_note(raw)) is not None
            }
            if cleaned:
                output["evidence_notes"] = cleaned
    return attached


def validate_annotations(annotations: dict[str, Any], manifest: dict[str, Any]) -> None:
    """Validate the browser's compact, partially completed annotation schema."""
    case_rubrics: dict[str, set[str]] = {}
    case_slots: dict[str, set[str]] = {}
    for case in manifest.get("cases", []):
        if not isinstance(case, dict) or not case.get("case_id"):
            continue
        case_id = str(case["case_id"])
        case_rubrics[case_id] = {
            str(rubric["rubric_id"])
            for rubric in case.get("rubrics", [])
            if isinstance(rubric, dict) and rubric.get("rubric_id")
        }
        case_slots[case_id] = case_output_slots(case)

    unknown_cases = sorted(set(map(str, annotations)) - set(case_rubrics))
    if unknown_cases:
        raise ValueError(f"Unknown case_id: {unknown_cases[0]}")

    for case_id, case_record in annotations.items():
        if not isinstance(case_record, dict):
            raise ValueError(f"Annotation for {case_id} must be a JSON object")
        overall_note = case_record.get("overall_note", "")
        if not isinstance(overall_note, str) or len(overall_note) > 100_000:
            raise ValueError(f"overall_note for {case_id} must be text")
        rubrics = case_record.get("rubrics", {})
        if not isinstance(rubrics, dict):
            raise ValueError(f"rubrics for {case_id} must be a JSON object")
        unknown_rubrics = sorted(set(map(str, rubrics)) - case_rubrics[str(case_id)])
        if unknown_rubrics:
            raise ValueError(f"Unknown rubric_id for {case_id}: {unknown_rubrics[0]}")
        for rubric_id, rubric_record in rubrics.items():
            if not isinstance(rubric_record, dict):
                raise ValueError(f"Rubric {rubric_id} for {case_id} must be a JSON object")
            allowed_slots = case_slots[str(case_id)]
            for key, value in rubric_record.items():
                if key == "note":
                    continue
                if key not in allowed_slots:
                    raise ValueError(f"Unknown slot for {case_id}/{rubric_id}: {key}")
                if value not in {"yes", "no", "unsure"}:
                    raise ValueError(f"Invalid {key} decision for {case_id}/{rubric_id}")
            note = rubric_record.get("note", "")
            if not isinstance(note, str) or len(note) > 100_000:
                raise ValueError(f"note for {case_id}/{rubric_id} must be text")


class AnnotationStore:
    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.web_root = self.project_root / "web"
        self.assets_root = self.project_root / "assets"
        self.data_root = self.project_root / "data"
        self.annotations_root = self.project_root / "annotations"
        self.public_manifest_path = self.data_root / "manifest.public.json"
        self.private_manifest_path = self.data_root / "manifest.private.json"
        self.guide_path = self.data_root / "guide.zh.json"
        self.scenarios_path = self.data_root / "scenarios.json"
        self.evidence_path = self.data_root / "evidence_notes.json"
        self.annotations_root.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self._public_manifest_lock = threading.Lock()
        self._public_manifest_cache: dict[str, Any] | None = None
        self._public_manifest_signature: tuple[Any, ...] | None = None
        self._private_identity_lock = threading.Lock()
        self._private_identity_cache: dict[str, Any] | None = None

    def _manifest_signature(self) -> tuple[Any, ...]:
        stat = self.public_manifest_path.stat()
        def file_signature(path: Path) -> tuple[Any, ...]:
            if not path.is_file():
                return (0, 0)
            file_stat = path.stat()
            return (file_stat.st_mtime_ns, file_stat.st_size)

        return (
            stat.st_mtime_ns,
            stat.st_size,
            file_signature(self.guide_path),
            file_signature(self.scenarios_path),
            file_signature(self.evidence_path),
        )

    def public_manifest(self) -> dict[str, Any]:
        signature = self._manifest_signature()
        if self._public_manifest_cache is not None and signature == self._public_manifest_signature:
            return self._public_manifest_cache
        with self._public_manifest_lock:
            signature = self._manifest_signature()
            if self._public_manifest_cache is None or signature != self._public_manifest_signature:
                manifest = strip_private_manifest_fields(read_json(self.public_manifest_path))
                guide = read_json(self.guide_path) if self.guide_path.is_file() else {}
                scenarios = read_json(self.scenarios_path) if self.scenarios_path.is_file() else {}
                evidence = read_json(self.evidence_path) if self.evidence_path.is_file() else {}
                self._public_manifest_cache = attach_evidence_notes(
                    attach_scenarios(enrich_public_manifest(manifest, guide), scenarios),
                    evidence,
                )
                self._public_manifest_signature = signature
            return self._public_manifest_cache

    def annotation_path(self, annotator: str) -> tuple[str, str, Path]:
        display, stem = safe_annotator_name(annotator)
        path = (self.annotations_root / f"{stem}.json").resolve()
        if path.parent != self.annotations_root.resolve():
            raise ValueError("Unsafe annotation path")
        return display, stem, path

    def load_annotation(self, annotator: str, dataset_id: str) -> dict[str, Any]:
        display, _stem, path = self.annotation_path(annotator)
        if path.exists():
            return public_annotation(read_json(path))
        now = utc_now()
        return {
            "schema_version": SCHEMA_VERSION,
            "dataset_id": dataset_id,
            "annotator": display,
            "started_at": now,
            "updated_at": now,
            "annotations": {},
        }

    def private_identity(self) -> dict[str, Any]:
        if self._private_identity_cache is not None:
            return self._private_identity_cache
        with self._private_identity_lock:
            if self._private_identity_cache is not None:
                return self._private_identity_cache
            if not self.private_manifest_path.exists():
                return {}

            manifest = read_json(self.private_manifest_path)
            raw_cases = manifest.get("cases", {})
            if isinstance(raw_cases, dict):
                case_items = raw_cases.items()
            elif isinstance(raw_cases, list):
                case_items = (
                    (str(case.get("case_id", "")), case)
                    for case in raw_cases
                    if isinstance(case, dict)
                )
            else:
                case_items = ()

            result: dict[str, Any] = {}
            for case_id, case in case_items:
                if not case_id or not isinstance(case, dict):
                    continue
                raw_identity = case.get("output_identity")
                if not isinstance(raw_identity, dict):
                    continue
                identity: dict[str, Any] = {}
                for slot in ("A", "B"):
                    raw_slot = raw_identity.get(slot)
                    if isinstance(raw_slot, dict):
                        identity[slot] = {
                            key: raw_slot[key]
                            for key in PRIVATE_IDENTITY_FIELDS
                            if key in raw_slot
                        }
                if identity:
                    result[str(case_id)] = identity
            self._private_identity_cache = result
            return self._private_identity_cache

    def save_annotation(self, payload: dict[str, Any]) -> tuple[dict[str, Any], Path]:
        display, _stem, path = self.annotation_path(payload.get("annotator"))
        manifest = self.public_manifest()
        dataset_id = manifest.get("dataset_id")
        if payload.get("dataset_id") != dataset_id:
            raise ValueError("Annotation dataset_id does not match the loaded dataset")
        annotations = payload.get("annotations")
        if not isinstance(annotations, dict):
            raise ValueError("annotations must be a JSON object")
        validate_annotations(annotations, manifest)
        client_revision = payload.get("client_revision", 0)
        if (not isinstance(client_revision, int) or isinstance(client_revision, bool) or
                client_revision < 0):
            raise ValueError("client_revision must be a non-negative integer")
        client_updated_at = payload.get("client_updated_at")
        if client_updated_at is not None and (
                not isinstance(client_updated_at, str) or len(client_updated_at) > 80):
            raise ValueError("client_updated_at must be a timestamp string")

        with self._write_lock:
            previous: dict[str, Any] = {}
            if path.exists():
                try:
                    previous = read_json(path)
                except (OSError, ValueError, json.JSONDecodeError):
                    previous = {}
            now = utc_now()
            started_at = previous.get("started_at") or payload.get("started_at") or now
            if not isinstance(started_at, str) or len(started_at) > 80:
                started_at = now
            saved = {
                "schema_version": SCHEMA_VERSION,
                "dataset_id": dataset_id,
                "annotator": display,
                "started_at": started_at,
                "updated_at": now,
                "client_revision": client_revision,
                "annotations": annotations,
            }
            if client_updated_at is not None:
                saved["client_updated_at"] = client_updated_at
            saved["_private"] = {
                "model_identity_by_case": self.private_identity(),
                "saved_by": "personacua-human-annotation-server",
            }

            fd, temp_name = tempfile.mkstemp(
                prefix=f".{path.stem}.", suffix=".tmp", dir=self.annotations_root
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(saved, handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, path)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
        return public_annotation(saved), path


def make_handler(store: AnnotationStore):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PersonaCUAAnnotation/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}")

        def send_json(self, status: int, data: Any) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def api_error(self, status: int, message: str) -> None:
            self.send_json(status, {"ok": False, "error": message})

        def do_HEAD(self) -> None:  # noqa: N802
            self.do_GET()

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            if parsed.path == "/api/health":
                self.send_json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "dataset_ready": store.public_manifest_path.is_file(),
                        "time": utc_now(),
                    },
                )
                return
            if parsed.path == "/api/bootstrap":
                query = parse_qs(parsed.query)
                annotator = query.get("annotator", [""])[0]
                try:
                    manifest = store.public_manifest()
                    annotation = store.load_annotation(annotator, str(manifest.get("dataset_id", "")))
                except FileNotFoundError:
                    self.api_error(HTTPStatus.SERVICE_UNAVAILABLE, "Dataset has not been built yet")
                    return
                except (ValueError, OSError, json.JSONDecodeError) as exc:
                    self.api_error(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                self.send_json(
                    HTTPStatus.OK,
                    {"ok": True, "manifest": manifest, "annotation": annotation},
                )
                return
            if parsed.path.startswith("/api/"):
                self.api_error(HTTPStatus.NOT_FOUND, "Unknown API endpoint")
                return
            self.serve_static(parsed.path)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            if parsed.path != "/api/save":
                self.api_error(HTTPStatus.NOT_FOUND, "Unknown API endpoint")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.api_error(HTTPStatus.BAD_REQUEST, "Invalid Content-Length")
                return
            if length <= 0 or length > MAX_BODY_BYTES:
                self.api_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Invalid request body size")
                return
            content_type = self.headers.get_content_type()
            if content_type != "application/json":
                self.api_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type must be application/json")
                return
            try:
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("Request body must be a JSON object")
                saved, path = store.save_annotation(payload)
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                self.api_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except FileNotFoundError:
                self.api_error(HTTPStatus.SERVICE_UNAVAILABLE, "Dataset has not been built yet")
                return
            self.send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "updated_at": saved["updated_at"],
                    "filename": path.name,
                },
            )

        def serve_static(self, request_path: str) -> None:
            decoded = unquote(request_path)
            if decoded == "/data/manifest.public.json":
                try:
                    self.send_json(HTTPStatus.OK, store.public_manifest())
                except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError) as exc:
                    self.api_error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
                return
            if decoded in {"", "/"}:
                base, relative = store.web_root, "index.html"
            elif decoded.startswith("/assets/"):
                base, relative = store.assets_root, decoded.removeprefix("/assets/")
            else:
                base, relative = store.web_root, decoded.lstrip("/")
            try:
                target = (base / relative).resolve()
                target.relative_to(base.resolve())
            except (ValueError, OSError):
                self.api_error(HTTPStatus.FORBIDDEN, "Unsafe path")
                return
            if not target.is_file():
                self.api_error(HTTPStatus.NOT_FOUND, "File not found")
                return
            content_type, _ = mimetypes.guess_type(target.name)
            stat = target.stat()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type or "application/octet-stream")
            self.send_header("Content-Length", str(stat.st_size))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; script-src 'self'; "
                "style-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; "
                "base-uri 'none'",
            )
            self.send_header(
                "Cache-Control",
                "public, max-age=86400" if decoded.startswith("/assets/") else "no-cache",
            )
            self.end_headers()
            if self.command != "HEAD":
                with target.open("rb") as handle:
                    while chunk := handle.read(1024 * 1024):
                        self.wfile.write(chunk)

    return Handler


def create_server(project_root: Path, host: str, port: int) -> ThreadingHTTPServer:
    store = AnnotationStore(project_root)
    return ThreadingHTTPServer((host, port), make_handler(store))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="Listen address")
    parser.add_argument("--port", default=8765, type=int, help="Listen port")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Project directory",
    )
    args = parser.parse_args()
    server = create_server(args.project_root, args.host, args.port)
    print(f"PersonaCUA annotation server: http://{args.host}:{server.server_port}")
    print(f"Annotations: {(args.project_root / 'annotations').resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
