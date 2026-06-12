"""Renderiza um video anotado com as deteccoes do pipeline.

Desenha sobre cada frame: caixa da bola (amarelo), jogadores (ciano), trilha
da trajetoria, marcador de contato e um HUD com tempo/velocidade. O resultado
e um MP4 pronto para revisao ou divulgacao.
"""
from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np

COLOR_BALL = (0, 215, 255)      # amarelo (BGR)
COLOR_PLAYER = (255, 220, 0)    # ciano
COLOR_TRAIL = (80, 255, 120)    # verde claro
COLOR_CONTACT = (60, 60, 255)   # vermelho
COLOR_HUD = (255, 255, 255)

CONTACT_FLASH_S = 0.6


def render_annotated(video_path: str | Path, result, output_path: str | Path) -> Path:
    """Reproduz o video original desenhando as deteccoes de `result`
    (um AnalysisResult de pipeline.analyze_video)."""
    video_path = Path(video_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise FileNotFoundError(f"video nao encontrado: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    writer = cv2.VideoWriter(
        str(output_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h)
    )

    by_frame = {fr.frame: fr for fr in result.frame_results}
    points = result.trajectory.points
    contacts = result.trajectory.contacts

    last_detections = []
    frame_no = 0
    while True:
        ok, img = cap.read()
        if not ok:
            break
        t = frame_no / fps

        fr = by_frame.get(frame_no)
        if fr is not None:
            last_detections = fr.detections

        _draw_trail(img, points, t)
        _draw_detections(img, last_detections)
        _draw_contacts(img, contacts, t)
        _draw_hud(img, points, contacts, t, frame_no)

        writer.write(img)
        frame_no += 1

    cap.release()
    writer.release()
    return output_path


def _draw_detections(img, detections) -> None:
    for d in detections:
        p1 = (int(d.x1), int(d.y1))
        p2 = (int(d.x2), int(d.y2))
        if d.label == "sports ball":
            cv2.rectangle(img, p1, p2, COLOR_BALL, 2)
            cx, cy = (int(v) for v in d.center)
            cv2.drawMarker(img, (cx, cy), COLOR_BALL,
                           cv2.MARKER_CROSS, 14, 1)
            _label(img, f"BOLA {d.confidence:.2f}", p1, COLOR_BALL)
        else:
            cv2.rectangle(img, p1, p2, COLOR_PLAYER, 2)
            _label(img, f"JOGADOR {d.confidence:.2f}", p1, COLOR_PLAYER)


def _draw_trail(img, points, t: float) -> None:
    past = [(int(p.x), int(p.y)) for p in points if p.timestamp <= t]
    if len(past) >= 2:
        cv2.polylines(img, [np.array(past, dtype=np.int32)],
                      False, COLOR_TRAIL, 2, cv2.LINE_AA)
    for xy in past[-12:]:
        cv2.circle(img, xy, 3, COLOR_TRAIL, -1, cv2.LINE_AA)


def _draw_contacts(img, contacts, t: float) -> None:
    for c in contacts:
        if c.timestamp <= t:
            xy = (int(c.x), int(c.y))
            cv2.circle(img, xy, 16, COLOR_CONTACT, 2, cv2.LINE_AA)
            if t - c.timestamp <= CONTACT_FLASH_S:
                pulse = 16 + int(10 * math.sin((t - c.timestamp) * 20))
                cv2.circle(img, xy, pulse, COLOR_CONTACT, 2, cv2.LINE_AA)
                _label(img, f"CONTATO {c.direction_change_deg:.0f}graus",
                       (xy[0] + 20, xy[1] - 10), COLOR_CONTACT)


def _draw_hud(img, points, contacts, t: float, frame_no: int) -> None:
    speed = _speed_at(points, t)
    n_contacts = sum(1 for c in contacts if c.timestamp <= t)
    lines = [
        f"VAR | t={t:6.2f}s  frame={frame_no}",
        f"bola: {speed:6.0f} px/s   contatos: {n_contacts}",
    ]
    overlay = img.copy()
    cv2.rectangle(overlay, (8, 8), (320, 64), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.55, img, 0.45, 0, img)
    for i, text in enumerate(lines):
        cv2.putText(img, text, (16, 32 + i * 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, COLOR_HUD, 1, cv2.LINE_AA)


def _speed_at(points, t: float) -> float:
    past = [p for p in points if p.timestamp <= t]
    if len(past) < 2:
        return 0.0
    a, b = past[-2], past[-1]
    dt = max(b.timestamp - a.timestamp, 1e-6)
    return math.hypot(b.x - a.x, b.y - a.y) / dt


def _label(img, text: str, origin: tuple[int, int], color) -> None:
    x, y = origin
    y = max(y - 6, 14)
    cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX,
                0.5, color, 1, cv2.LINE_AA)
