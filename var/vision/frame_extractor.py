"""Extracao de frames de um video/segmento usando OpenCV.

Itera sobre os frames de um arquivo (.mp4 ou segmento .ts) entregando, alem do
pixel buffer, o numero do frame e o timestamp em segundos derivado do FPS.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np


@dataclass
class Frame:
    number: int
    timestamp: float       # segundos desde o inicio do video
    image: np.ndarray      # BGR (HxWx3)

    @property
    def shape(self) -> tuple[int, ...]:
        return self.image.shape


class FrameExtractor:
    def __init__(self, video_path: str | Path, step: int = 1):
        """step: processa 1 a cada `step` frames (subamostragem para velocidade)."""
        self.video_path = Path(video_path)
        self.step = max(1, step)

    def __iter__(self) -> Iterator[Frame]:
        if not self.video_path.exists():
            raise FileNotFoundError(f"Video nao encontrado: {self.video_path}")
        cap = cv2.VideoCapture(str(self.video_path))
        if not cap.isOpened():
            raise RuntimeError(f"Falha ao abrir o video: {self.video_path}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        number = 0
        try:
            while True:
                ok, image = cap.read()
                if not ok:
                    break
                if number % self.step == 0:
                    yield Frame(number=number, timestamp=number / fps, image=image)
                number += 1
        finally:
            cap.release()

    def info(self) -> dict:
        cap = cv2.VideoCapture(str(self.video_path))
        try:
            return {
                "fps": cap.get(cv2.CAP_PROP_FPS),
                "frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
                "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            }
        finally:
            cap.release()
