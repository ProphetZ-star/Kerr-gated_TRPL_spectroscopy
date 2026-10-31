from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _StrictModel(BaseModel):
    """Base model: forbid extra fields, reject NaN/Inf, reject bool for numbers."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def _reject_bool_numbers(cls, data):
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, bool) and cls.model_fields.get(key) is not None and cls.model_fields[key].annotation is not bool:
                    raise ValueError(f"field '{key}' must not be a boolean")
                if isinstance(value, (list, tuple)):
                    for item in value:
                        if isinstance(item, bool):
                            raise ValueError(
                                f"field '{key}' must not contain booleans"
                            )
        return data


class Segment(_StrictModel):
    start: float
    end: float
    step: float = Field(gt=0)

    @model_validator(mode="after")
    def _check_order(self) -> "Segment":
        if not self.end > self.start:
            raise ValueError("end must be greater than start")
        return self


class FaultConfig(_StrictModel):
    kind: Literal[
        "none",
        "move_timeout",
        "camera_disconnect",
        "write_failure",
        "saturation",
        "cosmic_ray",
        "drift",
    ] = "none"
    point_index: int = Field(default=2, ge=0)


class RunRequest(_StrictModel):
    mode: Literal["single", "scan", "repeat"] = "scan"
    segments: list[Segment] = Field(
        default_factory=lambda: [
            Segment(start=-2.0, end=0.0, step=0.1),
            Segment(start=0.0, end=2.0, step=0.05),
            Segment(start=2.0, end=20.0, step=0.5),
        ],
        min_length=1,
        max_length=10,
    )
    repeats: int = Field(default=1, ge=1, le=100)
    exposure_s: float = Field(default=1.0, ge=0.001, le=3600.0)
    frames_per_point: int = Field(default=1, ge=1, le=100)
    save_path: str | None = None
    overwrite_token: str | None = Field(default=None,min_length=64,max_length=64)
    autosave: bool = True
    seed: int = Field(default=42, ge=0, le=2147483647)
    speedup: float = Field(default=50.0, ge=1.0, le=1000.0)
    settle_s: float = Field(default=0.05, ge=0.0, le=10.0)
    fault: FaultConfig = Field(default_factory=FaultConfig)
    request_id: str = Field(min_length=1, max_length=128)


class MoveRequest(_StrictModel):
    delay_ps: float
    relative: bool = False
    request_id: str = Field(min_length=1, max_length=128)


class CameraConfig(_StrictModel):
    exposure_s: float = Field(ge=0.001, le=3600.0)


class BackgroundRequest(_StrictModel):
    kind: Literal["dark", "optical_background"]
    condition: str = Field(min_length=1, max_length=500)


class SpectrographConfig(_StrictModel):
    center_nm: float = Field(default=600.0, gt=0)
    grating: int = Field(default=300, gt=0)
    slit_um: float = Field(default=200.0, gt=0)


class CalibrationRequest(_StrictModel):
    wavelengths_nm: list[float] | None = None
    calibration_id: str = Field(min_length=1, max_length=128)
    source: Literal["synthetic", "measured"] = "measured"

    @field_validator("wavelengths_nm")
    @classmethod
    def _check_wavelengths(cls, value: list[float] | None) -> list[float] | None:
        if value is None:
            return None
        if len(value) != 1024:
            raise ValueError("wavelengths_nm must contain exactly 1024 values")
        if any(not math.isfinite(v) or v<=0 for v in value):
            raise ValueError("wavelengths_nm must contain only positive finite values")
        diffs = [value[i + 1] - value[i] for i in range(len(value) - 1)]
        if all(d > 0 for d in diffs) or all(d < 0 for d in diffs):
            return value
        raise ValueError(
            "wavelengths_nm must be strictly monotonic (increasing or decreasing)"
        )


class FitRequest(_StrictModel):
    wavelength_nm: float = 600.0
    half_window_nm: float = Field(default=5.0, gt=0)
    model: Literal["single", "double"] = "double"
    irf_fwhm_ps: float = Field(default=0.25, ge=0.0001, le=100)
    min_delay_ps: float | None = None
    max_delay_ps: float | None = None

    @model_validator(mode="after")
    def _check_delay_bounds(self) -> "FitRequest":
        if self.min_delay_ps is not None and self.max_delay_ps is not None:
            if not self.max_delay_ps > self.min_delay_ps:
                raise ValueError("max_delay_ps must be greater than min_delay_ps")
        return self

