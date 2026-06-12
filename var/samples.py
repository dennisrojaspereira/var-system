"""Gerador de video sintetico para testar o pipeline sem footage real.

Desenha um gramado, dois "jogadores" e uma bola que se move e muda de direcao
no meio (simulando um contato/chute). Util para validar extracao de frames,
buffer, sync, eventos e API. OBS: YOLO pre-treinado pode nao reconhecer a bola
sintetica como "sports ball" - para deteccao real use footage real ou um modelo
treinado. Para testar o tracker isoladamente, veja tests/test_tracker.py.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .config import PROJECT_ROOT


def generate_sample(output: str = "samples/cam07.mp4", seconds: int = 6,
                    fps: int = 30, size: tuple[int, int] = (1280, 720)) -> Path:
    out_path = Path(output)
    if not out_path.is_absolute():
        out_path = PROJECT_ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    w, h = size
    total = seconds * fps
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(out_path), fourcc, fps, (w, h))

    for i in range(total):
        frame = np.full((h, w, 3), (40, 120, 40), dtype=np.uint8)  # gramado
        cv2.line(frame, (w // 2, 0), (w // 2, h), (230, 230, 230), 2)  # linha central

        # Jogadores (retangulos).
        cv2.rectangle(frame, (300, 300), (340, 420), (20, 20, 200), -1)
        cv2.rectangle(frame, (900, 280), (940, 400), (200, 60, 20), -1)

        # Bola: trajetoria em "V" - muda de direcao no meio (contato simulado).
        t = i / total
        if t < 0.5:
            bx = int(320 + (920 - 320) * (t / 0.5))
            by = int(380 - 120 * (t / 0.5))
        else:
            u = (t - 0.5) / 0.5
            bx = int(920 - (920 - 600) * u)
            by = int(260 + 300 * u)
        cv2.circle(frame, (bx, by), 12, (255, 255, 255), -1)
        cv2.circle(frame, (bx, by), 12, (0, 0, 0), 2)

        writer.write(frame)

    writer.release()
    return out_path
