"""SQLite registry for immutable skill versions and append-only lifecycle events."""

from __future__ import annotations

import json
import hashlib
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .models import SkillDefinition, SkillExport, SkillUseMetrics, SkillValidation, ValidationStage


class SkillStoreError(RuntimeError):
    pass


class SkillStore:
    def __init__(self, path: Path, *, store_id: str = "prime-stardew-skills") -> None:
        self.path = path.resolve()
        self.store_id = store_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.row_factory = sqlite3.Row
        self._initialize()

    def close(self) -> None:
        self._db.close()

    def add(self, skill: SkillDefinition) -> SkillDefinition:
        previous = self.get(skill.skill_id, skill.version - 1) if skill.version > 1 else None
        if skill.version > 1 and previous is None:
            raise SkillStoreError("A new version requires its immediate predecessor")
        encoded = json.dumps(skill.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        existing = self._db.execute(
            "SELECT content_sha256 FROM skills WHERE skill_id=? AND version=?",
            (skill.skill_id, skill.version),
        ).fetchone()
        if existing:
            if existing["content_sha256"] != skill.content_sha256():
                raise SkillStoreError("Skill version is immutable and already has different content")
            return skill
        with self._db:
            self._db.execute(
                "INSERT INTO skills(skill_id,version,definition_json,content_sha256,created_at) VALUES(?,?,?,?,?)",
                (skill.skill_id, skill.version, encoded, skill.content_sha256(), _now()),
            )
            self._event(skill.skill_id, skill.version, "created", {"content_sha256": skill.content_sha256()})
        return skill

    def get(self, skill_id: str, version: int) -> SkillDefinition | None:
        row = self._db.execute(
            "SELECT definition_json FROM skills WHERE skill_id=? AND version=?",
            (skill_id, version),
        ).fetchone()
        return SkillDefinition.model_validate_json(row["definition_json"]) if row else None

    def record_validation(self, validation: SkillValidation) -> None:
        if self.get(validation.skill_id, validation.version) is None:
            raise SkillStoreError("Cannot validate an unknown skill version")
        with self._db:
            existing = self._db.execute(
                "SELECT payload_json FROM validations WHERE validation_id=?",
                (validation.validation_id,),
            ).fetchone()
            encoded = json.dumps(validation.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            if existing and existing["payload_json"] != encoded:
                raise SkillStoreError("Validation ID was reused with different content")
            if not existing:
                self._db.execute(
                    "INSERT INTO validations(validation_id,skill_id,version,stage,success,payload_json,created_at) VALUES(?,?,?,?,?,?,?)",
                    (validation.validation_id, validation.skill_id, validation.version,
                     validation.stage.value, int(validation.success), encoded, _now()),
                )
                self._event(validation.skill_id, validation.version, "validated", {
                    "validation_id": validation.validation_id,
                    "stage": validation.stage.value, "success": validation.success,
                })

    def activate(self, skill_id: str, version: int) -> SkillDefinition:
        skill = self.get(skill_id, version)
        if skill is None:
            raise SkillStoreError("Cannot activate an unknown skill version")
        stages = {
            ValidationStage(row["stage"])
            for row in self._db.execute(
                "SELECT stage FROM validations WHERE skill_id=? AND version=? AND success=1",
                (skill_id, version),
            )
        }
        required = {ValidationStage.REPLAY, ValidationStage.DISPOSABLE}
        if not required <= stages:
            raise SkillStoreError("Activation requires passing replay and disposable validation")
        with self._db:
            self._event(skill_id, version, "activated", {})
        return skill

    def rollback(self, skill_id: str, version: int, *, reason: str) -> SkillDefinition:
        target = self.get(skill_id, version)
        if target is None:
            raise SkillStoreError("Rollback target does not exist")
        if not reason.strip():
            raise SkillStoreError("Rollback reason is required")
        with self._db:
            self._event(skill_id, version, "rolled_back", {"reason": reason})
        return target

    def active(self, skill_id: str) -> SkillDefinition | None:
        row = self._db.execute(
            "SELECT version FROM skill_events WHERE skill_id=? AND event_type IN ('activated','rolled_back') ORDER BY sequence DESC LIMIT 1",
            (skill_id,),
        ).fetchone()
        return self.get(skill_id, int(row["version"])) if row else None

    def record_use(self, metrics: SkillUseMetrics) -> None:
        if self.get(metrics.skill_id, metrics.version) is None:
            raise SkillStoreError("Cannot record use of an unknown skill version")
        encoded = json.dumps(metrics.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        with self._db:
            existing = self._db.execute(
                "SELECT payload_json FROM skill_uses WHERE use_id=?", (metrics.use_id,),
            ).fetchone()
            if existing and existing["payload_json"] != encoded:
                raise SkillStoreError("Skill use ID was reused with different content")
            if not existing:
                self._db.execute(
                    "INSERT INTO skill_uses(use_id,skill_id,version,payload_json,created_at) VALUES(?,?,?,?,?)",
                    (metrics.use_id, metrics.skill_id, metrics.version, encoded, _now()),
                )
                self._event(metrics.skill_id, metrics.version, "used", metrics.model_dump(mode="json"))

    def uses(self, skill_id: str) -> tuple[SkillUseMetrics, ...]:
        rows = self._db.execute(
            "SELECT payload_json FROM skill_uses WHERE skill_id=? ORDER BY created_at,use_id",
            (skill_id,),
        )
        return tuple(SkillUseMetrics.model_validate_json(row["payload_json"]) for row in rows)

    def history(self, skill_id: str) -> tuple[dict[str, object], ...]:
        rows = self._db.execute(
            "SELECT sequence,version,event_type,payload_json,created_at FROM skill_events WHERE skill_id=? ORDER BY sequence",
            (skill_id,),
        )
        return tuple({
            "sequence": row["sequence"], "version": row["version"],
            "event_type": row["event_type"], "payload": json.loads(row["payload_json"]),
            "created_at": row["created_at"],
        } for row in rows)

    def export(self, path: Path, *, provenance: dict[str, str] | None = None) -> SkillExport:
        skill_rows = self._db.execute(
            "SELECT definition_json FROM skills ORDER BY skill_id,version"
        ).fetchall()
        skills = tuple(SkillDefinition.model_validate_json(row["definition_json"]) for row in skill_rows)
        validation_rows = self._db.execute(
            "SELECT payload_json FROM validations ORDER BY validation_id"
        ).fetchall()
        validations = tuple(
            SkillValidation.model_validate_json(row["payload_json"]) for row in validation_rows
        )
        active_versions: dict[str, int] = {}
        for skill_id in sorted({skill.skill_id for skill in skills}):
            active = self.active(skill_id)
            if active is not None:
                active_versions[skill_id] = active.version
        provenance_value = provenance or {}
        content = _export_content(
            self.store_id, skills, validations, active_versions, provenance_value,
        )
        bundle = SkillExport(
            export_id=f"skill-export-{uuid4()}", created_at=datetime.now(UTC),
            source_store_id=self.store_id, provenance=provenance_value,
            skills=skills, validations=validations, active_versions=active_versions,
            content_sha256=_sha256_json(content),
        )
        _atomic_json(path.resolve(), bundle.model_dump(mode="json"))
        return bundle

    def import_bundle(self, path: Path) -> tuple[tuple[str, int], ...]:
        bundle = SkillExport.model_validate_json(path.read_text(encoding="utf-8"))
        content = _export_content(
            bundle.source_store_id, bundle.skills, bundle.validations,
            bundle.active_versions, bundle.provenance,
        )
        if _sha256_json(content) != bundle.content_sha256:
            raise SkillStoreError("Skill export content hash mismatch")
        imported: list[tuple[str, int]] = []
        for skill in sorted(bundle.skills, key=lambda item: (item.skill_id, item.version)):
            self.add(skill)
            imported.append((skill.skill_id, skill.version))
        for validation in bundle.validations:
            self.record_validation(validation)
        for skill_id, version in sorted(bundle.active_versions.items()):
            self.activate(skill_id, version)
        return tuple(imported)

    def _event(self, skill_id: str, version: int, event_type: str, payload: dict[str, object]) -> None:
        self._db.execute(
            "INSERT INTO skill_events(skill_id,version,event_type,payload_json,created_at) VALUES(?,?,?,?,?)",
            (skill_id, version, event_type,
             json.dumps(payload, sort_keys=True, separators=(",", ":")), _now()),
        )

    def _initialize(self) -> None:
        with self._db:
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS skills(
                    skill_id TEXT NOT NULL, version INTEGER NOT NULL,
                    definition_json TEXT NOT NULL, content_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL, PRIMARY KEY(skill_id,version));
                CREATE TABLE IF NOT EXISTS validations(
                    validation_id TEXT PRIMARY KEY, skill_id TEXT NOT NULL,
                    version INTEGER NOT NULL, stage TEXT NOT NULL, success INTEGER NOT NULL,
                    payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS skill_uses(
                    use_id TEXT PRIMARY KEY, skill_id TEXT NOT NULL, version INTEGER NOT NULL,
                    payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS skill_events(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, skill_id TEXT NOT NULL,
                    version INTEGER NOT NULL, event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
            """)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _export_content(
    source_store_id: str,
    skills: tuple[SkillDefinition, ...],
    validations: tuple[SkillValidation, ...],
    active_versions: dict[str, int],
    provenance: dict[str, str],
) -> dict[str, object]:
    return {
        "source_store_id": source_store_id,
        "skills": [skill.model_dump(mode="json") for skill in skills],
        "validations": [validation.model_dump(mode="json") for validation in validations],
        "active_versions": active_versions,
        "provenance": provenance,
    }


def _sha256_json(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    pending.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(pending, path)
