"""Calibration cache and installation lifecycle, owned by the acquisition worker."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import tempfile
import uuid
import time
from threading import Lock,RLock

from .models import CalibrationRequest, SpectrographConfig
from .errors import CommandError

_LOCKS={}
_LOCKS_GUARD=Lock()


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class CalibrationWorkbench:
    def __init__(self):
        self.directory = Path(os.environ.get('KERR_STATE_DIR', Path(__file__).resolve().parents[1] / 'data'))
        self.path = self.directory / 'installed-calibration.json'
        with _LOCKS_GUARD:self.persistence_lock=_LOCKS.setdefault(str(self.path.resolve()),RLock())
        self.frame = None
        self.solutions = {}
        self.warning = None

    def recorded_spectrograph(self):
        """Restore software records of the manual settings; never issue hardware commands."""
        try:
            payload = json.loads(self.path.read_text(encoding='utf-8'))
            if payload['version'] != 1:
                return None
            return SpectrographConfig(**payload['spectrograph']).model_dump()
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def restore(self, spectrograph):
        if not self.path.exists():
            self.warning=None
            return None
        try:
            with self.persistence_lock:payload = json.loads(self.path.read_text(encoding='utf-8'))
            if payload['version'] != 1 or payload['spectrograph'] != spectrograph:
                raise ValueError('Saved calibration does not match the manual spectrograph settings')
            calibration = payload['calibration']
            if not isinstance(calibration,dict) or not {'calibration_id','source','wavelengths_nm'}<=calibration.keys():raise ValueError('Calibration is missing required fields')
            if calibration['wavelengths_nm'] is not None:
                CalibrationRequest(**{k: calibration[k] for k in ('calibration_id', 'source', 'wavelengths_nm')})
            elif (calibration['calibration_id'],calibration['source'])!=(None,None):
                CalibrationRequest(**{k:calibration[k] for k in ('calibration_id','source','wavelengths_nm')})
            self.warning=None
            return calibration
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.warning = f'Saved calibration was not restored: {exc}'
            return dict(calibration_id=None, source=None, wavelengths_nm=None)

    def persist(self, calibration, spectrograph):
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.directory,
                                             prefix='.calibration-', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(dict(version=1, calibration=calibration, spectrograph=spectrograph), stream,
                          ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            with self.persistence_lock:
                for attempt in range(10):
                    try:os.replace(temporary,self.path);break
                    except PermissionError as exc:
                        if attempt==9:raise ValueError('Calibration file is in use. Close the external reader and retry; the previous calibration is retained.') from exc
                        time.sleep(min(.01*2**attempt,.1))
            self.warning = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def invalidate(self):
        self.frame = None
        self.solutions.clear()

    def capture(self, data, spectrograph):
        from .calibration import CaptureRequest, make_frame
        request = CaptureRequest(**data)
        image = make_frame(request.exposure_s, request.seed)
        if request.readout=='fvb':
            import numpy as np
            # Sum photocharge; the synthetic electronic offset is read once.
            image=np.clip((image.astype(np.int64)-75).sum(axis=0,keepdims=True)+75,0,65535).astype(np.int32)
        header = dict(frame_id=uuid.uuid4().hex, shape=list(image.shape), exposure_s=request.exposure_s,
                      seed=request.seed, source_mode='simulation', source='synthetic',
                      readout=request.readout, captured_utc=timestamp(),
                      spectrograph=deepcopy(spectrograph), full_scale=65535)
        self.frame = dict(header=header, image=image)
        self.solutions.clear()
        return self.current_frame()

    def current_frame(self):
        if self.frame is None:
            return None
        return deepcopy(self.frame['header']) | dict(image_counts=self.frame['image'].ravel().tolist())

    def require_frame(self, frame_id, spectrograph):
        if self.frame is None or self.frame['header']['frame_id'] != frame_id:
            raise CommandError('Calibration frame expired; acquire or reload the current frame')
        if self.frame['header']['spectrograph'] != spectrograph:
            raise CommandError('Spectrograph settings changed; acquire a new calibration frame')
        return deepcopy(self.frame)

    def extract(self, data, spectrograph):
        from .calibration import ExtractRequest, extract_profile
        request = ExtractRequest(**data)
        frame = self.require_frame(request.frame_id, spectrograph)
        return dict(frame_id=request.frame_id, source='synthetic') | extract_profile(
            frame['image'], request.row_start, request.row_end)

    def fit(self, data, spectrograph):
        from .calibration import MappingRequest, fit_mapping, extract_profile
        request = MappingRequest(**data)
        frame = self.require_frame(request.frame_id, spectrograph)
        extraction = extract_profile(frame['image'], request.row_start, request.row_end)
        if extraction['saturated_pixels']:
            raise CommandError('Calibration ROI contains saturated pixels; reduce exposure and acquire again')
        solution = fit_mapping(request)
        identifier = uuid.uuid4().hex
        solution.update(solution_id=identifier, calibration_id=f'spectral-{identifier}', source='synthetic',
                        source_mode='simulation', frame_id=request.frame_id, frame=deepcopy(frame['header']),
                        extraction_rows=[request.row_start, request.row_end], created_utc=timestamp(),
                        spectrograph=deepcopy(spectrograph), applied=False)
        solution['warnings'].append('Simulated CCD frame; this is not an experimental wavelength calibration')
        self.solutions[identifier] = solution
        if len(self.solutions) > 8:
            del self.solutions[next(iter(self.solutions))]
        return deepcopy(solution)

    def solution(self, identifier):
        if identifier not in self.solutions:
            raise CommandError('Calibration solution expired; fit the mapping again')
        return deepcopy(self.solutions[identifier])

    def installation(self, identifier, spectrograph):
        solution = self.solution(identifier)
        self.require_frame(solution['frame_id'], spectrograph)
        if solution['spectrograph'] != spectrograph:
            raise ValueError('Calibration settings changed; fit the mapping again')
        calibration = dict(calibration_id=solution['calibration_id'], source=solution['source'],
                           wavelengths_nm=solution['wavelengths_nm'], convention=solution['convention'],
                           installed_utc=timestamp(), provenance=solution | dict(applied=True))
        self.persist(calibration, spectrograph)
        for cached in self.solutions.values():cached['applied']=cached['solution_id']==identifier
        return calibration
