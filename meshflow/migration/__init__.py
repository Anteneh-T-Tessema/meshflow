"""MeshFlow migration utilities — detect, plan, and transform external agent projects."""

from __future__ import annotations

from meshflow.migration.detector import DetectionResult, ProjectDetector
from meshflow.migration.transformer import Change, CodeTransformer, TransformResult

__all__ = [
    "ProjectDetector",
    "DetectionResult",
    "CodeTransformer",
    "TransformResult",
    "Change",
]
