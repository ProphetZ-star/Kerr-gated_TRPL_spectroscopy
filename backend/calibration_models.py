"""Lightweight spectral calibration API contracts (no scientific imports)."""
from __future__ import annotations
from typing import Literal
from pydantic import Field, model_validator, StrictBool
from .models import _StrictModel

WIDTH = 1024
HEIGHT = 255

class CaptureRequest(_StrictModel):
    exposure_s: float = Field(default=1.0, ge=0.001, le=3600.0)
    seed: int = Field(default=42, ge=0, le=2147483647)
    readout: Literal['full_image', 'fvb'] = 'full_image'


class ExtractRequest(_StrictModel):
    frame_id: str = Field(min_length=1, max_length=128)
    row_start: int = Field(default=85, ge=0, le=HEIGHT - 1)
    row_end: int = Field(default=165, ge=0, le=HEIGHT - 1)

    @model_validator(mode='after')
    def _check_rows(self) -> 'ExtractRequest':
        if self.row_end < self.row_start:
            raise ValueError('row_end must be greater than or equal to row_start')
        return self


class LinePair(_StrictModel):
    pixel: float = Field(ge=0.0, le=float(WIDTH - 1))
    wavelength_nm: float = Field(gt=0.0)
    enabled: StrictBool = True


class MappingRequest(ExtractRequest):
    degree: int = Field(default=2, ge=1, le=3)
    pairs: list[LinePair] = Field(min_length=1, max_length=100)
    convention: Literal['air', 'vacuum'] = 'air'
    reference_name: str = Field(
        default='Illustrative demo lines', min_length=1, max_length=200
    )
    reference_source: Literal['synthetic', 'measured'] = 'synthetic'


class ApplyRequest(_StrictModel):
    solution_id: str = Field(min_length=1, max_length=128)
