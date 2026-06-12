"""Pipeline de visao: video -> frames -> deteccoes YOLO -> trajetoria da bola.

Orquestra FrameExtractor + YoloDetector + BallTracker e produz um resultado
estruturado pronto para a API de decisao e para o event bus.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..config import Config, load_config
from .detector import Detection, YoloDetector
from .frame_extractor import FrameExtractor
from .tracker import BallTracker, Trajectory


@dataclass
class FrameResult:
    frame: int
    timestamp: float
    detections: list[Detection] = field(default_factory=list)

    @property
    def players(self) -> int:
        return sum(1 for d in self.detections if d.label != "sports ball")


@dataclass
class AnalysisResult:
    video: str
    frame_results: list[FrameResult]
    trajectory: Trajectory

    def to_dict(self) -> dict[str, Any]:
        return {
            "video": self.video,
            "frames_analyzed": len(self.frame_results),
            "trajectory": self.trajectory.to_dict(),
        }


def analyze_video(
    video_path: str | Path,
    config: Config | None = None,
    step: int = 1,
    detector: YoloDetector | None = None,
    on_frame: Callable[[FrameResult], None] | None = None,
) -> AnalysisResult:
    config = config or load_config()
    detector = detector or YoloDetector(config)
    tracker = BallTracker()
    extractor = FrameExtractor(video_path, step=step)

    frame_results: list[FrameResult] = []
    for frame in extractor:
        detections = detector.detect(frame.image)
        ball = detector.best_ball(detections)
        if ball is not None:
            cx, cy = ball.center
            tracker.add(frame.number, frame.timestamp, cx, cy, ball.confidence)
        fr = FrameResult(frame.number, frame.timestamp, detections)
        frame_results.append(fr)
        if on_frame:
            on_frame(fr)

    return AnalysisResult(
        video=str(video_path),
        frame_results=frame_results,
        trajectory=tracker.build(),
    )
