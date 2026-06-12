"""Consumer de eventos do VAR.

Demonstra o lado consumidor do event bus: le o topico Kafka e imprime cada
evento (simulando os sinks de auditoria/broadcast/analytics). Se o Kafka nao
estiver disponivel, faz tail do sink em arquivo JSONL.

Uso:
  python -m var.events.consumer
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..config import load_config


def _run_kafka(bootstrap: str, topic: str) -> bool:
    try:
        from kafka import KafkaConsumer  # type: ignore
    except Exception:
        return False
    try:
        consumer = KafkaConsumer(
            topic,
            bootstrap_servers=bootstrap,
            auto_offset_reset="earliest",
            group_id="var-consumer-demo",
            value_deserializer=lambda v: v.decode("utf-8"),
        )
    except Exception as exc:
        print(f"[consumer] Kafka indisponivel ({exc}); usando file tail.")
        return False

    print(f"[consumer] consumindo de Kafka topic='{topic}' @ {bootstrap}")
    for message in consumer:
        _print_event(message.value)
    return True


def _tail_file(sink: Path) -> None:
    print(f"[consumer] tail do arquivo {sink}")
    sink.parent.mkdir(parents=True, exist_ok=True)
    sink.touch(exist_ok=True)
    with sink.open("r", encoding="utf-8") as fh:
        fh.seek(0, 2)  # vai para o fim
        while True:
            line = fh.readline()
            if not line:
                time.sleep(0.5)
                continue
            _print_event(line.strip())


def _print_event(raw: str) -> None:
    try:
        evt = json.loads(raw)
    except json.JSONDecodeError:
        return
    print(f"  <- {evt.get('event_type'):24} "
          f"cam={evt.get('camera_id')} "
          f"payload={evt.get('payload')}", flush=True)


def main() -> int:
    config = load_config()
    ev = config.section("events")
    bootstrap = ev.get("kafka_bootstrap", "localhost:9092")
    topic = ev.get("topic", "var.events")
    backend = ev.get("backend", "auto")

    if backend in ("auto", "kafka") and _run_kafka(bootstrap, topic):
        return 0
    _tail_file(config.resolve(ev.get("file_sink", "events/var_events.jsonl")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
