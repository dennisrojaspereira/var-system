"""Camada de persistencia (PostgreSQL + TimescaleDB).

Repositorio unico sobre psycopg3. A conexao e preguicosa e injetavel
(connection_factory) para permitir testes com conexao fake, sem banco real.

Dados de video (segmentos .ts) NAO passam por aqui - ficam em disco/S3.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ..config import Config, load_config

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

UPSERT_MATCH = (
    "INSERT INTO matches (id, name) VALUES (%s, %s) "
    "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name"
)
UPSERT_CAMERA = (
    "INSERT INTO cameras (match_id, id, angle, fps) VALUES (%s, %s, %s, %s) "
    "ON CONFLICT (match_id, id) DO UPDATE SET angle = EXCLUDED.angle, fps = EXCLUDED.fps"
)
INSERT_DETECTION = (
    "INSERT INTO detections (time, match_id, camera_id, frame, label, confidence, x, y) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)
INSERT_EVENT = (
    "INSERT INTO var_events (event_type, match_id, camera_id, ts, payload) "
    "VALUES (%s, %s, %s, %s, %s)"
)
SELECT_TRAJECTORY = (
    "SELECT time, frame, label, confidence, x, y FROM detections "
    "WHERE match_id = %s AND camera_id = %s AND time BETWEEN %s AND %s "
    "ORDER BY time"
)


def event_to_row(evt: dict[str, Any]) -> tuple:
    """Converte um evento do bus (dict do JSON) em parametros de INSERT."""
    return (
        evt["event_type"],
        evt.get("match_id", "unknown-match"),
        evt.get("camera_id"),
        evt["timestamp"],
        json.dumps(evt.get("payload", {}), ensure_ascii=False),
    )


class Storage:
    def __init__(self, config: Config | None = None,
                 connection_factory: Callable[[], Any] | None = None):
        self.config = config or load_config()
        st = self.config.section("storage")
        self.dsn = st.get("dsn", "")
        self._factory = connection_factory
        self._conn: Any = None

    def connect(self) -> Any:
        if self._conn is None:
            if self._factory is not None:
                self._conn = self._factory()
            else:
                import psycopg  # import tardio: opcional fora do Docker
                self._conn = psycopg.connect(self.dsn, autocommit=True,
                                             connect_timeout=3)
        return self._conn

    def available(self) -> bool:
        if not self.dsn and self._factory is None:
            return False
        try:
            self.connect()
            return True
        except Exception:
            return False

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ---- DDL ----

    def ensure_schema(self) -> None:
        sql = SCHEMA_PATH.read_text(encoding="utf-8")
        with self.connect().cursor() as cur:
            cur.execute(sql)

    # ---- Escrita ----

    def upsert_match(self, match_id: str, name: str) -> None:
        with self.connect().cursor() as cur:
            cur.execute(UPSERT_MATCH, (match_id, name))

    def upsert_camera(self, match_id: str, camera_id: str, angle: str | None,
                      fps: int | None) -> None:
        with self.connect().cursor() as cur:
            cur.execute(UPSERT_CAMERA, (match_id, camera_id, angle, fps))

    def insert_detections(self, rows: list[tuple]) -> int:
        """rows: (time, match_id, camera_id, frame, label, confidence, x, y)."""
        if not rows:
            return 0
        with self.connect().cursor() as cur:
            cur.executemany(INSERT_DETECTION, rows)
        return len(rows)

    def insert_event(self, evt: dict[str, Any]) -> None:
        with self.connect().cursor() as cur:
            cur.execute(INSERT_EVENT, event_to_row(evt))

    # ---- Leitura ----

    def trajectory(self, match_id: str, camera_id: str,
                   t0: datetime, t1: datetime) -> list[dict[str, Any]]:
        with self.connect().cursor() as cur:
            cur.execute(SELECT_TRAJECTORY, (match_id, camera_id, t0, t1))
            rows = cur.fetchall()
        return [
            {"time": r[0], "frame": r[1], "label": r[2],
             "confidence": r[3], "x": r[4], "y": r[5]}
            for r in rows
        ]
