-- Schema do var-system (PostgreSQL + TimescaleDB).
--
-- Desenho Citus-ready: todas as tabelas de volume carregam match_id, que seria
-- a chave de distribuicao num cluster Citus (cada partida vive inteira num
-- shard; queries de revisao nunca cruzam shards). Num único Postgres, o
-- particionamento por tempo do TimescaleDB (hypertable) cobre o crescimento.

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ---- Dados relacionais pequenos ----

CREATE TABLE IF NOT EXISTS matches (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cameras (
    match_id    TEXT NOT NULL REFERENCES matches(id),
    id          TEXT NOT NULL,
    angle       TEXT,
    fps         INT,
    PRIMARY KEY (match_id, id)
);

-- ---- Deteccoes: time series de alto volume (hypertable) ----
-- 50fps x N cameras x 90min. Sem PK proprio: hypertables exigem que indices
-- unicos incluam a coluna de tempo.

CREATE TABLE IF NOT EXISTS detections (
    time        TIMESTAMPTZ NOT NULL,
    match_id    TEXT NOT NULL,
    camera_id   TEXT NOT NULL,
    frame       INT  NOT NULL,
    label       TEXT NOT NULL,
    confidence  REAL NOT NULL,
    x           REAL NOT NULL,
    y           REAL NOT NULL
);

SELECT create_hypertable('detections', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_detections_match_cam_time
    ON detections (match_id, camera_id, time DESC);

-- ---- Eventos VAR: sink de auditoria do Kafka (append-only) ----

CREATE TABLE IF NOT EXISTS var_events (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_type  TEXT NOT NULL,
    match_id    TEXT NOT NULL,
    camera_id   TEXT,
    ts          TIMESTAMPTZ NOT NULL,
    payload     JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_var_events_match_ts
    ON var_events (match_id, ts DESC);
