"""Testes de regressao da camada de storage (PostgreSQL + TimescaleDB).

Usam uma conexao fake injetada (connection_factory) que captura o SQL e os
parametros executados - validam o contrato do repositorio sem banco real.
O teste de integracao real roda apenas se VAR_DB_DSN apontar para um Postgres
acessivel (ex: a stack do docker-compose).
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from var.config import load_config
from var.storage import Storage, event_to_row, SCHEMA_PATH


# ---- Fakes ----

class FakeCursor:
    def __init__(self, log, rows=None):
        self.log = log
        self.rows = rows or []

    def execute(self, sql, params=None):
        self.log.append(("execute", sql, params))

    def executemany(self, sql, seq):
        self.log.append(("executemany", sql, list(seq)))

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, rows=None):
        self.log = []
        self.rows = rows
        self.closed = False

    def cursor(self):
        return FakeCursor(self.log, self.rows)

    def close(self):
        self.closed = True


def make_storage(rows=None):
    cfg = load_config()
    conn = FakeConnection(rows)
    return Storage(cfg, connection_factory=lambda: conn), conn


# ---- Schema ----

def test_schema_file_defines_expected_objects():
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    assert "CREATE EXTENSION IF NOT EXISTS timescaledb" in sql
    assert "create_hypertable('detections', 'time'" in sql
    for table in ("matches", "cameras", "detections", "var_events"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql, f"tabela {table} ausente"
    # Citus-ready: tabelas de volume precisam carregar a chave de distribuicao.
    assert sql.count("match_id") >= 4


def test_ensure_schema_executes_schema_sql():
    storage, conn = make_storage()
    storage.ensure_schema()
    assert len(conn.log) == 1
    op, sql, _ = conn.log[0]
    assert op == "execute" and "create_hypertable" in sql


# ---- Escrita ----

def test_upsert_match_and_camera_are_idempotent_sql():
    storage, conn = make_storage()
    storage.upsert_match("m1", "Final")
    storage.upsert_camera("m1", "cam-07", "goal-line", 50)
    (_, sql_match, p_match), (_, sql_cam, p_cam) = conn.log
    assert "ON CONFLICT (id) DO UPDATE" in sql_match
    assert p_match == ("m1", "Final")
    assert "ON CONFLICT (match_id, id) DO UPDATE" in sql_cam
    assert p_cam == ("m1", "cam-07", "goal-line", 50)


def test_insert_detections_batches_rows():
    storage, conn = make_storage()
    t = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)
    rows = [
        (t, "m1", "cam-07", 10, "sports ball", 0.94, 542.0, 312.0),
        (t, "m1", "cam-07", 11, "sports ball", 0.91, 550.0, 308.0),
    ]
    assert storage.insert_detections(rows) == 2
    op, sql, params = conn.log[0]
    assert op == "executemany"
    assert "INSERT INTO detections" in sql
    assert params == rows
    # Lista vazia nao toca o banco.
    assert storage.insert_detections([]) == 0
    assert len(conn.log) == 1


def test_insert_event_maps_bus_event_to_row():
    storage, conn = make_storage()
    evt = {
        "event_type": "VAR_REVIEW_STARTED",
        "match_id": "world-cup-final-2026",
        "camera_id": "cam-07",
        "timestamp": "2026-06-12T00:45:20.772190+00:00",
        "payload": {"t_seconds": 12.5, "reason": "penalty-check"},
    }
    storage.insert_event(evt)
    _, sql, params = conn.log[0]
    assert "INSERT INTO var_events" in sql
    assert params[0] == "VAR_REVIEW_STARTED"
    assert params[2] == "cam-07"
    assert json.loads(params[4]) == {"t_seconds": 12.5, "reason": "penalty-check"}


def test_event_to_row_defaults():
    row = event_to_row({"event_type": "X", "timestamp": "2026-01-01T00:00:00+00:00"})
    assert row[1] == "unknown-match" and row[2] is None
    assert json.loads(row[4]) == {}


# ---- Leitura ----

def test_trajectory_query_is_match_scoped():
    t0 = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 7, 19, 12, 0, 10, tzinfo=timezone.utc)
    rows = [(t0, 10, "sports ball", 0.94, 542.0, 312.0)]
    storage, conn = make_storage(rows=rows)
    points = storage.trajectory("m1", "cam-07", t0, t1)
    _, sql, params = conn.log[0]
    # Sempre escopada por match_id + camera_id (padrao single-shard no Citus).
    assert "match_id = %s AND camera_id = %s" in sql
    assert params == ("m1", "cam-07", t0, t1)
    assert points == [{"time": t0, "frame": 10, "label": "sports ball",
                       "confidence": 0.94, "x": 542.0, "y": 312.0}]


# ---- Disponibilidade ----

def test_available_false_without_dsn_or_factory():
    cfg = load_config()
    cfg.raw["storage"] = {"dsn": ""}
    assert Storage(cfg).available() is False


def test_available_true_with_factory():
    storage, _ = make_storage()
    assert storage.available() is True


# ---- API: persistencia nao quebra a analise ----

def test_api_health_reports_database_flag():
    from fastapi.testclient import TestClient
    from var.api import create_app
    cfg = load_config()
    cfg.raw["storage"] = {"dsn": ""}
    client = TestClient(create_app(cfg))
    body = client.get("/health").json()
    assert body["database"] is False
    assert body["status"] == "ok"


def test_api_trajectory_503_when_db_off():
    from fastapi.testclient import TestClient
    from var.api import create_app
    cfg = load_config()
    cfg.raw["storage"] = {"dsn": ""}
    client = TestClient(create_app(cfg))
    r = client.get("/trajectory/cam-07", params={
        "t0": "2026-01-01T00:00:00+00:00", "t1": "2026-01-01T01:00:00+00:00"})
    assert r.status_code == 503


# ---- Integracao real (so com Postgres acessivel) ----

def test_integration_roundtrip_if_db_available():
    dsn = os.environ.get("VAR_DB_DSN", "")
    if not dsn:
        print("  (integracao pulada: VAR_DB_DSN nao definido)")
        return
    cfg = load_config()
    cfg.raw["storage"] = {"dsn": dsn}
    storage = Storage(cfg)
    if not storage.available():
        print("  (integracao pulada: banco inacessivel)")
        return
    storage.ensure_schema()
    storage.upsert_match("test-match", "Teste Regressao")
    storage.upsert_camera("test-match", "cam-test", "test", 30)
    t = datetime.now(timezone.utc)
    storage.insert_detections([(t, "test-match", "cam-test", 1, "sports ball",
                                0.9, 100.0, 200.0)])
    points = storage.trajectory("test-match", "cam-test", t, t)
    assert any(p["frame"] == 1 for p in points)
    storage.close()
    print("  (integracao executada contra Postgres real)")


if __name__ == "__main__":
    test_schema_file_defines_expected_objects()
    test_ensure_schema_executes_schema_sql()
    test_upsert_match_and_camera_are_idempotent_sql()
    test_insert_detections_batches_rows()
    test_insert_event_maps_bus_event_to_row()
    test_event_to_row_defaults()
    test_trajectory_query_is_match_scoped()
    test_available_false_without_dsn_or_factory()
    test_available_true_with_factory()
    test_api_health_reports_database_flag()
    test_api_trajectory_503_when_db_off()
    test_integration_roundtrip_if_db_available()
    print("OK: testes de storage passaram")
