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


def create_app(config: Config | None = None) -> FastAPI:
    config = config or load_config()
    app = FastAPI(title="VAR Decision Support API", version="0.1.0")
    bus = EventBus(config)
    sync = SyncManager(config)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "match": config.match,
            "event_backend": bus.backend_in_use,
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
        # Import tardio: ultralytics/torch sao pesados.
        from ..vision import analyze_video
        result = analyze_video(path, config=config, step=req.step)
        traj = result.trajectory
        for contact in traj.contacts:
            bus.publish(make_event(
                "BALL_CONTACT_DETECTED", config,
                frame=contact.frame, t_seconds=contact.timestamp,
                x=contact.x, y=contact.y,
                direction_change_deg=contact.direction_change_deg,
            ))
        return result.to_dict()

    return app
