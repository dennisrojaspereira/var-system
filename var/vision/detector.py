"""Deteccao de bola e jogadores via YOLOv8 (ultralytics).

Usa pesos pre-treinados COCO (yolov8n.pt por padrao), onde a bola e a classe
"sports ball" e os jogadores sao "person". O modelo e baixado automaticamente
pela ultralytics no primeiro uso.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

import numpy as np

from ..config import Config, load_config


@dataclass
class Detection:
    label: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        cx, cy = self.center
        d["cx"], d["cy"] = cx, cy
        return d


class YoloDetector:
    def __init__(self, config: Config | None = None):
        self.config = config or load_config()
        v = self.config.section("vision")
        self.model_name = v.get("model", "yolov8n.pt")
        self.device = v.get("device", "cpu")
        self.conf_ball = float(v.get("conf_ball", 0.25))
        self.conf_player = float(v.get("conf_player", 0.30))
        self.ball_class = v.get("ball_class", "sports ball")
        self.player_class = v.get("player_class", "person")
        self._model = None  # carregamento preguicoso

    def _ensure_model(self):
        if self._model is None:
            from ultralytics import YOLO  # import tardio: pesado
            self._model = YOLO(self.model_name)
        return self._model

    def detect(self, image: np.ndarray) -> list[Detection]:
        model = self._ensure_model()
        min_conf = min(self.conf_ball, self.conf_player)
        results = model.predict(
            image, device=self.device, conf=min_conf, verbose=False
        )
        out: list[Detection] = []
        for result in results:
            names = result.names
            for box in result.boxes:
                cls_id = int(box.cls[0])
                label = names[cls_id]
                conf = float(box.conf[0])
                if label == self.ball_class and conf < self.conf_ball:
                    continue
                if label == self.player_class and conf < self.conf_player:
                    continue
                if label not in (self.ball_class, self.player_class):
                    continue
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
                out.append(Detection(label, conf, x1, y1, x2, y2))
        return out

    def best_ball(self, detections: list[Detection]) -> Detection | None:
        balls = [d for d in detections if d.label == self.ball_class]
        return max(balls, key=lambda d: d.confidence) if balls else None
