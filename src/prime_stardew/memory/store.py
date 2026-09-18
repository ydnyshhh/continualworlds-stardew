"""SQLite-backed append-only external memory with deterministic retrieval."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .models import (
    BeliefPrediction, CalibrationReport, MemoryExport, MemoryKind, MemoryQuery,
    MemoryAccessStats, MemoryRecord, MemoryStatus, MemoryStatusEvent, PredictionOutcome,
    RankFeatures, RetrievalCandidate, RetrievalResult,
)


class MemoryStoreError(RuntimeError):
    pass


class MemoryStore:
    """Stores immutable records; lifecycle changes are separate append-only events."""

    def __init__(self, path: Path, *, store_id: str = "prime-stardew-memory") -> None:
        self.path = path.resolve()
        self.store_id = store_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = DELETE")
        self._create_schema()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def add(self, record: MemoryRecord) -> MemoryRecord:
        encoded = _record_json(record)
        existing = self._connection.execute(
            "SELECT record_json FROM memories WHERE memory_id = ?", (record.memory_id,),
        ).fetchone()
        if existing is not None:
            if existing["record_json"] != encoded:
                raise MemoryStoreError(f"Memory ID already exists with different content: {record.memory_id}")
            return record
        if record.supersedes_id:
            parent = self.get(record.supersedes_id, include_inactive=True)
            if parent is None:
                raise MemoryStoreError(f"Superseded memory does not exist: {record.supersedes_id}")
            if record.version != parent.version + 1:
                raise MemoryStoreError("Superseding memory version must increment by one")
        with self._connection:
            self._connection.execute(
                "INSERT INTO memories(memory_id, kind, text, created_at, confidence, season, "
                "map_name, task_domain, valid_from_day, valid_to_day, supersedes_id, version, record_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.memory_id, record.kind.value, record.text,
                    record.created_at.astimezone(UTC).isoformat(), record.confidence,
                    record.season, record.map_name, record.task_domain,
                    record.valid_from_day, record.valid_to_day, record.supersedes_id,
                    record.version, encoded,
                ),
            )
            self._append_status(record.memory_id, MemoryStatus.ACTIVE, "created")
            if record.supersedes_id:
                self._append_status(record.supersedes_id, MemoryStatus.SUPERSEDED, record.memory_id)
        return record

    def add_text(
        self,
        text: str,
        *,
        kind: MemoryKind = MemoryKind.SEMANTIC,
        memory_id: str | None = None,
        source_event_ids: tuple[str, ...] = (),
        **metadata: object,
    ) -> MemoryRecord:
        record = MemoryRecord(
            memory_id=memory_id or f"mem-{uuid4()}", kind=kind, text=text,
            source_event_ids=source_event_ids, **metadata,
        )
        return self.add(record)

    def delete(self, memory_id: str, *, reason: str) -> None:
        if self.get(memory_id, include_inactive=True) is None:
            raise MemoryStoreError(f"Memory does not exist: {memory_id}")
        with self._connection:
            self._append_status(memory_id, MemoryStatus.DELETED, reason)

    def get(self, memory_id: str, *, include_inactive: bool = False) -> MemoryRecord | None:
        row = self._connection.execute(
            "SELECT record_json FROM memories WHERE memory_id = ?", (memory_id,),
        ).fetchone()
        if row is None:
            return None
        if not include_inactive and self.status(memory_id) is not MemoryStatus.ACTIVE:
            return None
        return MemoryRecord.model_validate_json(row["record_json"])

    def status(self, memory_id: str) -> MemoryStatus:
        row = self._connection.execute(
            "SELECT status FROM memory_status_events WHERE memory_id = ? "
            "ORDER BY sequence DESC LIMIT 1", (memory_id,),
        ).fetchone()
        if row is None:
            raise MemoryStoreError(f"Memory does not exist: {memory_id}")
        return MemoryStatus(row["status"])

    def records(self, *, active_only: bool = True) -> tuple[MemoryRecord, ...]:
        rows = self._connection.execute(
            "SELECT record_json FROM memories ORDER BY created_at, memory_id"
        ).fetchall()
        result = tuple(MemoryRecord.model_validate_json(row["record_json"]) for row in rows)
        if active_only:
            result = tuple(record for record in result if self.status(record.memory_id) is MemoryStatus.ACTIVE)
        return result

    def recent(self, *, limit: int, token_budget: int) -> RetrievalResult:
        query = MemoryQuery(text="", limit=limit, token_budget=token_budget)
        records = list(reversed(self.records()))
        result = self._select(query, [(record, _rank(record, query)) for record in records])
        self._record_retrievals(result)
        return result

    def retrieve(self, query: MemoryQuery) -> RetrievalResult:
        eligible = [record for record in self.records() if _eligible(record, query)]
        ranked = [(record, _rank(record, query)) for record in eligible]
        ranked.sort(
            key=lambda item: (
                -item[1].total_score,
                -item[0].created_at.timestamp(),
                item[0].memory_id,
            )
        )
        result = self._select(query, ranked)
        self._record_retrievals(result)
        return result

    def deactivate(self, memory_id: str, *, reason: str) -> None:
        if self.get(memory_id) is None:
            raise MemoryStoreError(f"Active memory does not exist: {memory_id}")
        if not reason.strip():
            raise MemoryStoreError("Deactivation reason is required")
        with self._connection:
            self._append_status(memory_id, MemoryStatus.DEACTIVATED, reason)

    def status_history(self, memory_id: str | None = None) -> tuple[MemoryStatusEvent, ...]:
        if memory_id is None:
            rows = self._connection.execute(
                "SELECT memory_id,sequence,status,reason,created_at FROM memory_status_events "
                "ORDER BY memory_id,sequence"
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT memory_id,sequence,status,reason,created_at FROM memory_status_events "
                "WHERE memory_id=? ORDER BY sequence", (memory_id,),
            ).fetchall()
        return tuple(MemoryStatusEvent(
            memory_id=row["memory_id"], sequence=row["sequence"],
            status=MemoryStatus(row["status"]), reason=row["reason"],
            created_at=datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")),
        ) for row in rows)

    def access_stats(self, memory_id: str) -> MemoryAccessStats:
        row = self._connection.execute(
            "SELECT COUNT(*) AS count, MAX(created_at) AS latest FROM memory_access_events "
            "WHERE memory_id=? AND access_type='retrieved'", (memory_id,),
        ).fetchone()
        latest = row["latest"]
        return MemoryAccessStats(
            memory_id=memory_id, retrieval_count=int(row["count"]),
            last_retrieved_at=(
                datetime.fromisoformat(latest.replace("Z", "+00:00")) if latest else None
            ),
        )

    def export(self, path: Path, *, provenance: dict[str, str] | None = None) -> MemoryExport:
        records = self.records(active_only=False)
        statuses = {record.memory_id: self.status(record.memory_id) for record in records}
        status_events = self.status_history()
        content = _export_content(
            self.store_id, records, statuses, provenance or {}, status_events=status_events,
        )
        bundle = MemoryExport(
            schema_version=2,
            export_id=f"memory-export-{uuid4()}", created_at=datetime.now(UTC),
            source_store_id=self.store_id, provenance=provenance or {}, records=records,
            statuses=statuses, status_events=status_events,
            content_sha256=_sha256_json(content),
        )
        _atomic_json(path.resolve(), bundle.model_dump(mode="json"))
        return bundle

    def import_bundle(self, path: Path) -> tuple[str, ...]:
        bundle = MemoryExport.model_validate_json(path.read_text(encoding="utf-8"))
        content = _export_content(
            bundle.source_store_id, bundle.records, bundle.statuses, bundle.provenance,
            status_events=bundle.status_events if bundle.schema_version == 2 else None,
        )
        if _sha256_json(content) != bundle.content_sha256:
            raise MemoryStoreError("Memory export content hash mismatch")
        imported: list[str] = []
        for record in bundle.records:
            self.add(record)
            target = bundle.statuses[record.memory_id]
            if target is MemoryStatus.DELETED:
                self.delete(record.memory_id, reason=f"import:{bundle.export_id}")
            imported.append(record.memory_id)
        if bundle.schema_version == 2:
            imported_ids = tuple(record.memory_id for record in bundle.records)
            with self._connection:
                self._connection.executemany(
                    "DELETE FROM memory_status_events WHERE memory_id=?",
                    ((memory_id,) for memory_id in imported_ids),
                )
                self._connection.executemany(
                    "INSERT INTO memory_status_events(memory_id,sequence,status,reason,created_at) "
                    "VALUES(?,?,?,?,?)",
                    ((event.memory_id, event.sequence, event.status.value, event.reason,
                      event.created_at.astimezone(UTC).isoformat())
                     for event in bundle.status_events),
                )
        return tuple(imported)

    def snapshot(self, destination: Path) -> Path:
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        pending = destination.with_name(f".{destination.name}.tmp-{uuid4().hex}")
        backup = sqlite3.connect(pending)
        try:
            self._connection.backup(backup)
        finally:
            backup.close()
        os.replace(pending, destination)
        return destination

    def record_prediction(self, prediction: BeliefPrediction) -> BeliefPrediction:
        belief = self.get(prediction.belief_id, include_inactive=True)
        if belief is None or belief.kind is not MemoryKind.BELIEF:
            raise MemoryStoreError(f"Prediction belief does not exist: {prediction.belief_id}")
        encoded = json.dumps(prediction.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        existing = self._connection.execute(
            "SELECT prediction_json FROM belief_predictions WHERE prediction_id = ?",
            (prediction.prediction_id,),
        ).fetchone()
        if existing is not None:
            if existing["prediction_json"] != encoded:
                raise MemoryStoreError("Prediction ID was reused with different content")
            return prediction
        with self._connection:
            self._connection.execute(
                "INSERT INTO belief_predictions(prediction_id, belief_id, probability, "
                "prediction_json) VALUES (?, ?, ?, ?)",
                (prediction.prediction_id, prediction.belief_id, prediction.probability, encoded),
            )
        return prediction

    def record_outcome(self, outcome: PredictionOutcome) -> PredictionOutcome:
        if self._connection.execute(
            "SELECT 1 FROM belief_predictions WHERE prediction_id = ?", (outcome.prediction_id,),
        ).fetchone() is None:
            raise MemoryStoreError(f"Prediction does not exist: {outcome.prediction_id}")
        encoded = json.dumps(outcome.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        existing = self._connection.execute(
            "SELECT outcome_json FROM belief_outcomes WHERE prediction_id = ?",
            (outcome.prediction_id,),
        ).fetchone()
        if existing is not None:
            if existing["outcome_json"] != encoded:
                raise MemoryStoreError("Prediction outcome was reused with different content")
            return outcome
        with self._connection:
            self._connection.execute(
                "INSERT INTO belief_outcomes(prediction_id, occurred, outcome_json) "
                "VALUES (?, ?, ?)",
                (outcome.prediction_id, int(outcome.occurred), encoded),
            )
        return outcome

    def calibration(self) -> CalibrationReport:
        rows = self._connection.execute(
            "SELECT p.probability, o.occurred FROM belief_predictions p "
            "JOIN belief_outcomes o USING(prediction_id) ORDER BY p.prediction_id"
        ).fetchall()
        if not rows:
            return CalibrationReport(scored_predictions=0)
        probabilities = [float(row["probability"]) for row in rows]
        outcomes = [int(row["occurred"]) for row in rows]
        return CalibrationReport(
            scored_predictions=len(rows),
            brier_score=sum((probability - outcome) ** 2 for probability, outcome in zip(probabilities, outcomes)) / len(rows),
            mean_confidence=sum(probabilities) / len(rows),
            observed_rate=sum(outcomes) / len(rows),
        )

    def _select(
        self, query: MemoryQuery, ranked: list[tuple[MemoryRecord, RankFeatures]],
    ) -> RetrievalResult:
        selected: list[MemoryRecord] = []
        candidates: list[RetrievalCandidate] = []
        used = 0
        for record, features in ranked:
            tokens = _estimate_tokens(record.text)
            choose = len(selected) < query.limit and used + tokens <= query.token_budget
            if choose:
                selected.append(record)
                used += tokens
            candidates.append(RetrievalCandidate(
                memory_id=record.memory_id, features=features,
                estimated_tokens=tokens, selected=choose,
            ))
        return RetrievalResult(
            query=query, candidates=tuple(candidates), selected=tuple(selected),
            selected_tokens=used,
        )

    def _append_status(self, memory_id: str, status: MemoryStatus, reason: str) -> None:
        row = self._connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS sequence FROM memory_status_events "
            "WHERE memory_id = ?", (memory_id,),
        ).fetchone()
        self._connection.execute(
            "INSERT INTO memory_status_events(memory_id, sequence, status, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (memory_id, int(row["sequence"]) + 1, status.value, reason, datetime.now(UTC).isoformat()),
        )

    def _record_retrievals(self, result: RetrievalResult) -> None:
        if not result.selected:
            return
        with self._connection:
            self._connection.executemany(
                "INSERT INTO memory_access_events(memory_id,access_type,created_at) VALUES(?,?,?)",
                ((record.memory_id, "retrieved", datetime.now(UTC).isoformat())
                 for record in result.selected),
            )

    def _create_schema(self) -> None:
        with self._connection:
            self._connection.executescript("""
                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    season TEXT,
                    map_name TEXT,
                    task_domain TEXT,
                    valid_from_day INTEGER,
                    valid_to_day INTEGER,
                    supersedes_id TEXT REFERENCES memories(memory_id),
                    version INTEGER NOT NULL,
                    record_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_status_events (
                    memory_id TEXT NOT NULL REFERENCES memories(memory_id),
                    sequence INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(memory_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS idx_memory_metadata
                    ON memories(season, map_name, task_domain, confidence);
                CREATE TABLE IF NOT EXISTS memory_access_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id TEXT NOT NULL REFERENCES memories(memory_id),
                    access_type TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_access
                    ON memory_access_events(memory_id,access_type,sequence);
                CREATE TABLE IF NOT EXISTS belief_predictions (
                    prediction_id TEXT PRIMARY KEY,
                    belief_id TEXT NOT NULL REFERENCES memories(memory_id),
                    probability REAL NOT NULL,
                    prediction_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS belief_outcomes (
                    prediction_id TEXT PRIMARY KEY REFERENCES belief_predictions(prediction_id),
                    occurred INTEGER NOT NULL,
                    outcome_json TEXT NOT NULL
                );
            """)


def _eligible(record: MemoryRecord, query: MemoryQuery) -> bool:
    if record.confidence < query.min_confidence:
        return False
    for wanted, actual in (
        (query.season, record.season),
        (query.map_name, record.map_name),
        (query.task_domain, record.task_domain),
    ):
        if wanted is not None and actual not in {None, wanted}:
            return False
    if query.game_day is not None:
        if record.valid_from_day is not None and query.game_day < record.valid_from_day:
            return False
        if record.valid_to_day is not None and query.game_day > record.valid_to_day:
            return False
    return True


def _rank(record: MemoryRecord, query: MemoryQuery) -> RankFeatures:
    query_terms = _terms(query.text)
    memory_terms = _terms(record.text + " " + " ".join(record.tags))
    overlap = len(query_terms & memory_terms)
    keyword_score = overlap / max(1, len(query_terms))
    metadata_score = sum(
        0.25 for wanted, actual in (
            (query.season, record.season),
            (query.map_name, record.map_name),
            (query.task_domain, record.task_domain),
        ) if wanted is not None and wanted == actual
    )
    total = keyword_score * 4 + metadata_score + record.confidence
    return RankFeatures(
        keyword_overlap=overlap, keyword_score=keyword_score,
        metadata_score=metadata_score, confidence=record.confidence,
        total_score=total,
    )


def _terms(value: str) -> set[str]:
    return {term for term in re.findall(r"[a-z0-9]+", value.lower()) if len(term) > 1}


def _estimate_tokens(value: str) -> int:
    return max(1, (len(value.encode("utf-8")) + 3) // 4)


def estimate_memory_tokens(record: MemoryRecord) -> int:
    return _estimate_tokens(record.text)


def _record_json(record: MemoryRecord) -> str:
    return json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def _export_content(
    store_id: str,
    records: tuple[MemoryRecord, ...],
    statuses: dict[str, MemoryStatus],
    provenance: dict[str, str],
    *,
    status_events: tuple[MemoryStatusEvent, ...] | None = None,
) -> dict[str, object]:
    content: dict[str, object] = {
        "source_store_id": store_id,
        "provenance": provenance,
        "records": [record.model_dump(mode="json") for record in records],
        "statuses": {key: value.value for key, value in sorted(statuses.items())},
    }
    if status_events is not None:
        content["status_events"] = [event.model_dump(mode="json") for event in status_events]
    return content


def _sha256_json(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    pending.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(pending, path)
