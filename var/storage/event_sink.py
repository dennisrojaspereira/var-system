"""Sink de eventos: Kafka -> Postgres (tabela var_events, auditoria).

Consome o topico do event bus e persiste cada evento. Garante o schema na
partida. Roda como servico no docker-compose (events-sink).

Uso:
  python -m var.storage.event_sink
"""
from __future__ import annotations

import json
import time

from ..config import load_config
from .db import Storage


def main() -> int:
    config = load_config()
    ev = config.section("events")
    bootstrap = ev.get("kafka_bootstrap", "localhost:9092")
    topic = ev.get("topic", "var.events")

    storage = Storage(config)
    while not storage.available():
        print("[event_sink] aguardando Postgres...")
        time.sleep(3)
    storage.ensure_schema()
    print("[event_sink] schema OK")

    from kafka import KafkaConsumer  # type: ignore
    consumer = None
    while consumer is None:
        try:
            consumer = KafkaConsumer(
                topic,
                bootstrap_servers=bootstrap,
                auto_offset_reset="earliest",
                group_id="var-event-sink",
                value_deserializer=lambda v: v.decode("utf-8"),
                api_version_auto_timeout_ms=5000,
            )
        except Exception as exc:
            print(f"[event_sink] aguardando Kafka ({exc})...")
            time.sleep(3)

    print(f"[event_sink] consumindo '{topic}' @ {bootstrap} -> var_events")
    for message in consumer:
        try:
            evt = json.loads(message.value)
        except json.JSONDecodeError:
            continue
        storage.insert_event(evt)
        print(f"[event_sink] persistido: {evt.get('event_type')} "
              f"match={evt.get('match_id')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
