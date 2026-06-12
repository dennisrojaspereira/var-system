"""API de suporte a decisao do VAR (FastAPI).

Expoe a sala do VAR como servico:
  GET  /health                       -> status do sistema e backends
  GET  /cameras                      -> cameras configuradas
  GET  /timeline/{cam}               -> segmentos no buffer (duracao, replay)
  GET  /timeline/{cam}/at?t=...      -> segmento que cobre o instante t
  POST /review                       -> inicia revisao (publica evento)
  POST /analyze                      -> roda visao (YOLO) sobre um video e
                                        retorna trajetoria + contatos detectados
  GET  /sync/align                   -> mapeia um instante entre dois angulos
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ..buffer import TimelineBuffer
from ..config import Config, load_config
from ..events import EventBus, make_event
from ..storage import Storage
from ..sync import SyncManager


def _require_camera(config: Config, camera_id: str) -> None:
    try:
        config.camera(camera_id)
    except KeyError:
        raise HTTPException(404, f"Camera '{camera_id}' nao configurada")


class ReviewRequest(BaseModel):
    camera_id: str
    t_seconds: float
    reason: str = "incident-review"


class AnalyzeRequest(BaseModel):
    video_path: str
    step: int = 3  # subamostragem padrao p/ velocidade
    camera_id: str | None = None  # usada para timestamp absoluto/persistencia


def create_app(config: Config | None = None, storage: Storage | None = None) -> FastAPI:
    config = config or load_config()
    app = FastAPI(title="VAR Decision Support API", version="0.1.0")
    bus = EventBus(config)
    sync = SyncManager(config)
    storage = storage or Storage(config)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "match": config.match,
            "event_backend": bus.backend_in_use,
            "database": storage.available(),
            "cameras": [c.id for c in config.cameras],
        }

    @app.get("/cameras")
    def cameras() -> list[dict[str, Any]]:
        return [
            {"id": c.id, "angle": c.angle, "source": c.source, "fps": c.fps}
            for c in config.cameras
        ]

    @app.get("/timeline/{camera_id}")
    def timeline(camera_id: str) -> dict[str, Any]:
        _require_camera(config, camera_id)
        buf = TimelineBuffer(camera_id, config)
        segs = buf.segments()
        return {
            "camera_id": camera_id,
            "duration_s": buf.duration(),
            "segments": [
                {"index": s.index, "start": s.start, "duration": s.duration,
                 "file": s.path.name}
                for s in segs
            ],
        }

    @app.get("/timeline/{camera_id}/at")
    def timeline_at(camera_id: str, t: float) -> dict[str, Any]:
        _require_camera(config, camera_id)
        seg = TimelineBuffer(camera_id, config).segment_at(t)
        if seg is None:
            raise HTTPException(404, f"Nenhum segmento cobre t={t}s")
        return {
            "camera_id": camera_id,
            "t": t,
            "segment": {"index": seg.index, "start": seg.start,
                        "duration": seg.duration, "file": seg.path.name},
            "absolute_time": sync.absolute_at(camera_id, t).isoformat(),
        }

    @app.get("/sync/align")
    def align(source: str, target: str, t: float) -> dict[str, Any]:
        _require_camera(config, source)
        _require_camera(config, target)
        t_target = sync.align(source, t, target)
        return {
            "source": source, "target": target,
            "t_source": t, "t_target": round(t_target, 4),
            "absolute_time": sync.absolute_at(source, t).isoformat(),
        }

    @app.post("/review")
    def start_review(req: ReviewRequest) -> dict[str, Any]:
        _require_camera(config, req.camera_id)
        absolute = sync.absolute_at(req.camera_id, req.t_seconds).isoformat()
        bus.publish(make_event(
            "VAR_REVIEW_STARTED", config, camera_id=req.camera_id,
            t_seconds=req.t_seconds, reason=req.reason, absolute_time=absolute,
        ))
        return {"status": "review_started", "camera_id": req.camera_id,
                "absolute_time": absolute}

    @app.post("/analyze")
    def analyze(req: AnalyzeRequest) -> dict[str, Any]:
        path = config.resolve(req.video_path)
        if not Path(path).exists():
            raise HTTPException(404, f"Video nao encontrado: {path}")
        camera_id = req.camera_id or config.cameras[0].id
        _require_camera(config, camera_id)
        # Import tardio: ultralytics/torch sao pesados.
        from ..vision import analyze_video
        result = analyze_video(path, config=config, step=req.step)
        traj = result.trajectory
        for contact in traj.contacts:
            bus.publish(make_event(
                "BALL_CONTACT_DETECTED", config, camera_id=camera_id,
                frame=contact.frame, t_seconds=contact.timestamp,
                x=contact.x, y=contact.y,
                direction_change_deg=contact.direction_change_deg,
            ))
        persisted = _persist_trajectory(config, storage, sync, camera_id, traj)
        out = result.to_dict()
        out["persisted_detections"] = persisted
        return out

    @app.get("/trajectory/{camera_id}")
    def stored_trajectory(camera_id: str, t0: str, t1: str) -> dict[str, Any]:
        """Consulta a trajetoria persistida (t0/t1 em ISO-8601 UTC).
        Sempre escopada por match_id + camera_id - o padrao de acesso que
        mantem as queries num unico shard quando distribuido (Citus)."""
        _require_camera(config, camera_id)
        if not storage.available():
            raise HTTPException(503, "Banco de dados indisponivel")
        from datetime import datetime
        match_id = config.match.get("id", "unknown-match")
        points = storage.trajectory(
            match_id, camera_id,
            datetime.fromisoformat(t0), datetime.fromisoformat(t1),
        )
        for p in points:
            p["time"] = p["time"].isoformat() if hasattr(p["time"], "isoformat") else p["time"]
        return {"match_id": match_id, "camera_id": camera_id, "points": points}

    return app


def _persist_trajectory(config: Config, storage: Storage, sync: SyncManager,
                        camera_id: str, traj) -> int:
    """Grava os pontos da bola em detections. Retorna quantos persistiu (0 se
    o banco estiver desligado/indisponivel - a analise nunca falha por isso)."""
    if not config.section("storage").get("persist_detections", True):
        return 0
    if not storage.available():
        return 0
    try:
        match = config.match
        match_id = match.get("id", "unknown-match")
        storage.ensure_schema()
        storage.upsert_match(match_id, match.get("name", match_id))
        cam = config.camera(camera_id)
        storage.upsert_camera(match_id, cam.id, cam.angle, cam.fps)
        rows = [
            (sync.absolute_at(camera_id, p.timestamp), match_id, camera_id,
             p.frame, "sports ball", p.confidence, p.x, p.y)
            for p in traj.points
        ]
        return storage.insert_detections(rows)
    except Exception as exc:
        print(f"[api] persistencia falhou (analise segue valida): {exc}")
        return 0
