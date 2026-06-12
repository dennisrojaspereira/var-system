"""Event bus do VAR.

Eventos (VAR_REVIEW_STARTED, BALL_CONTACT_DETECTED, ANGLE_SWITCHED, etc.) sao
publicados para auditoria, broadcast, analytics e observabilidade. O backend e
Kafka quando disponivel; caso contrario faz fallback transparente para um sink
em arquivo JSONL (auditavel) e/ou console.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import Config, load_config


@dataclass
class VarEvent:
    event_type: str
    match_id: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    camera_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


def make_event(event_type: str, config: Config, camera_id: str | None = None,
               **payload: Any) -> VarEvent:
    return VarEvent(
        event_type=event_type,
        match_id=config.match.get("id", "unknown-match"),
        camera_id=camera_id,
        payload=payload,
    )


class EventBus:
    def __init__(self, config: Config | None = None):
        self.config = config or load_config()
        ev = self.config.section("events")
        self.backend = ev.get("backend", "auto")
        self.topic = ev.get("topic", "var.events")
        self.bootstrap = ev.get("kafka_bootstrap", "localhost:9092")
        self.file_sink = self.config.resolve(ev.get("file_sink", "events/var_events.jsonl"))
        self._producer = None
        self._resolved = self._resolve_backend()

    def _resolve_backend(self) -> str:
        if self.backend in ("file", "console"):
            return self.backend
        if self.backend in ("auto", "kafka"):
            producer = self._try_kafka()
            if producer is not None:
                self._producer = producer
                return "kafka"
            if self.backend == "kafka":
                # Pediram kafka explicitamente mas falhou -> cai para arquivo.
                print("[event_bus] Kafka indisponivel; usando file sink.")
            return "file"
        return "file"

    def _try_kafka(self):
        try:
            from kafka import KafkaProducer  # type: ignore
        except Exception:
            return None
        try:
            return KafkaProducer(
                bootstrap_servers=self.bootstrap,
                value_serializer=lambda v: v.encode("utf-8"),
                request_timeout_ms=3000,
            )
        except Exception:
            return None

    def publish(self, event: VarEvent) -> None:
        data = event.to_json()
        if self._resolved == "kafka" and self._producer is not None:
            self._producer.send(self.topic, value=data)
            self._producer.flush()
        elif self._resolved == "console":
            print(f"[VAR_EVENT] {data}")
        else:  # file (default) - sempre tambem espelha no console p/ visibilidade
            self.file_sink.parent.mkdir(parents=True, exist_ok=True)
            with self.file_sink.open("a", encoding="utf-8") as fh:
                fh.write(data + "\n")
            print(f"[VAR_EVENT] {data}")

    @property
    def backend_in_use(self) -> str:
        return self._resolved

    def close(self) -> None:
        if self._producer is not None:
            self._producer.close()
