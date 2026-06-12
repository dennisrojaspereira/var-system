"""Tracking da bola e reconstrucao de trajetoria.

A partir da posicao da bola frame a frame, monta a trajetoria, calcula
velocidade (px/s) e detecta mudancas bruscas de direcao - candidatas ao
"momento de contato" com um jogador (chute, cabecada, desvio).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TrackPoint:
    frame: int
    timestamp: float
    x: float
    y: float
    confidence: float


@dataclass
class ContactEvent:
    frame: int
    timestamp: float
    x: float
    y: float
    direction_change_deg: float
    speed_before: float
    speed_after: float


@dataclass
class Trajectory:
    points: list[TrackPoint] = field(default_factory=list)
    contacts: list[ContactEvent] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "points": [p.__dict__ for p in self.points],
            "contacts": [c.__dict__ for c in self.contacts],
            "summary": self.summary(),
        }

    def summary(self) -> dict[str, Any]:
        if len(self.points) < 2:
            return {"tracked_frames": len(self.points)}
        speeds = _speeds(self.points)
        return {
            "tracked_frames": len(self.points),
            "duration_s": round(self.points[-1].timestamp - self.points[0].timestamp, 3),
            "max_speed_px_s": round(max(speeds), 1) if speeds else 0.0,
            "avg_speed_px_s": round(sum(speeds) / len(speeds), 1) if speeds else 0.0,
            "contacts": len(self.contacts),
        }


class BallTracker:
    """Acumula deteccoes da bola e deriva trajetoria + eventos de contato."""

    def __init__(self, direction_change_threshold_deg: float = 35.0,
                 min_speed_px_s: float = 50.0):
        self.points: list[TrackPoint] = []
        self.threshold = direction_change_threshold_deg
        self.min_speed = min_speed_px_s

    def add(self, frame: int, timestamp: float, x: float, y: float, confidence: float) -> None:
        self.points.append(TrackPoint(frame, timestamp, x, y, confidence))

    def build(self) -> Trajectory:
        traj = Trajectory(points=list(self.points))
        traj.contacts = self._detect_contacts()
        return traj

    def _detect_contacts(self) -> list[ContactEvent]:
        contacts: list[ContactEvent] = []
        pts = self.points
        for i in range(1, len(pts) - 1):
            a, b, c = pts[i - 1], pts[i], pts[i + 1]
            v1 = (b.x - a.x, b.y - a.y)
            v2 = (c.x - b.x, c.y - b.y)
            dt1 = max(b.timestamp - a.timestamp, 1e-6)
            dt2 = max(c.timestamp - b.timestamp, 1e-6)
            speed_before = math.hypot(*v1) / dt1
            speed_after = math.hypot(*v2) / dt2
            angle = _angle_between(v1, v2)
            if angle >= self.threshold and max(speed_before, speed_after) >= self.min_speed:
                contacts.append(
                    ContactEvent(
                        frame=b.frame,
                        timestamp=b.timestamp,
                        x=b.x,
                        y=b.y,
                        direction_change_deg=round(angle, 1),
                        speed_before=round(speed_before, 1),
                        speed_after=round(speed_after, 1),
                    )
                )
        return contacts


def _speeds(points: list[TrackPoint]) -> list[float]:
    speeds = []
    for a, b in zip(points, points[1:]):
        dt = max(b.timestamp - a.timestamp, 1e-6)
        speeds.append(math.hypot(b.x - a.x, b.y - a.y) / dt)
    return speeds


def _angle_between(v1: tuple[float, float], v2: tuple[float, float]) -> float:
    m1, m2 = math.hypot(*v1), math.hypot(*v2)
    if m1 < 1e-6 or m2 < 1e-6:
        return 0.0
    cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (m1 * m2)
    cos = max(-1.0, min(1.0, cos))
    return math.degrees(math.acos(cos))
