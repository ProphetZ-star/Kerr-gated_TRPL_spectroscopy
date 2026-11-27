"""Calibration cache and installation lifecycle, owned by the acquisition worker."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import json
import numpy as np
import os
import tempfile
import uuid
import time
from threading import Lock,RLock

from .models import CalibrationRequest, SpectrographConfig
from .errors import CommandError

_LOCKS={}
LIBRARY_SIZE=64
_LOCKS_GUARD=Lock()


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class CalibrationWorkbench:
    def __init__(self):
        self.instrument=dict(driver='oriel_77400_manual',model='Oriel 77400 / MS125')
        self.directory = Path(os.environ.get('KERR_STATE_DIR', Path(__file__).resolve().parents[1] / 'data'))
        self.path = self.directory / 'installed-calibration.json'
        self.response_path = self.directory / 'installed-response.json'
        with _LOCKS_GUARD:self.persistence_lock=_LOCKS.setdefault(str(self.path.resolve()),RLock())
        self.frame = None
        self.solutions = {}
        self.warning = None

    @property
    def library_path(self):
        '''Every calibration installed, one per spectrometer, centre wavelength and grating (installed-calibration.json
        holds only the one in use): returning to a setting brings its calibration back.'''
        return self.path.with_name('calibration-library.json' if self.path.name == 'installed-calibration.json' else 'simulation-calibration-library.json')

    def library_key(self, spectrograph):
        # The slit does not move wavelengths on the CCD, so it is not part of the key.
        return f"{self.instrument['driver']}|{self.instrument.get('model', '')}|{float(spectrograph['center_nm']):.4f}|{int(spectrograph['grating'])}"

    def _read_library(self):
        try:
            payload = json.loads(self.library_path.read_text(encoding='utf-8'))
            return payload['entries'] if payload.get('version') == 1 and isinstance(payload.get('entries'), dict) else {}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return {}

    def remember(self, calibration, spectrograph):
        '''Keep a copy of an installed calibration for its setting (newest per setting; at most LIBRARY_SIZE settings).'''
        if not calibration or calibration.get('wavelengths_nm') is None:
            return
        with self.persistence_lock:
            entries = self._read_library()
            key = self.library_key(spectrograph)
            previous = entries.get(key) or {}
            same = (previous.get('calibration') or {}).get('calibration_id') == calibration.get('calibration_id')
            entries[key] = dict(calibration=calibration, center_nm=float(spectrograph['center_nm']), grating=int(spectrograph['grating']),
                                instrument=self.instrument, saved_utc=previous.get('saved_utc') if same and previous.get('saved_utc') else timestamp())
            if len(entries) > LIBRARY_SIZE:
                for old in sorted(entries, key=lambda k: entries[k].get('saved_utc') or '')[:len(entries) - LIBRARY_SIZE]:
                    del entries[old]
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = self.library_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(dict(version=1, entries=entries), ensure_ascii=False, allow_nan=False), encoding='utf-8')
            os.replace(temporary, self.library_path)
            self._hint_cache = None

    def dispersion_hint(self, spectrograph):
        """What the saved calibrations for this spectrometer and grating say about the pixel -> wavelength shape at this
        centre wavelength: dict(dispersion_nm_per_px (signed, at the chip centre), curvature_nm (x^2 coefficient, x =
        (pixel - 511.5) / 511.5), range (relative search range for the dispersion), calibrations), or None.
        One saved calibration: its own values, dispersion within +-25 %. Two or more at different centre wavelengths:
        the dispersion interpolated (or extrapolated) linearly in centre wavelength, within +-6 %; the curvature is
        the median. Line pairing uses this. Cached until the saved calibrations change."""
        key = (self.library_path, self.instrument.get('driver'), self.instrument.get('model'), int(spectrograph['grating']), float(spectrograph['center_nm']))
        cache = getattr(self, '_hint_cache', None)
        if cache and cache[0] == key:
            return deepcopy(cache[1])
        shapes = []
        for entry in self._read_library().values():
            try:
                if entry.get('instrument') != self.instrument or int(entry['grating']) != int(spectrograph['grating']):
                    continue
                axis = np.asarray(entry['calibration']['wavelengths_nm'], float)
                x = (np.arange(len(axis)) - (len(axis) - 1) / 2) / ((len(axis) - 1) / 2)
                q, s, _ = np.polyfit(x, axis, 2)
                if np.isfinite(q) and np.isfinite(s) and s:
                    shapes.append((float(entry['center_nm']), float(s) / ((len(axis) - 1) / 2), float(q)))
            except (KeyError, TypeError, ValueError, IndexError):
                continue
        hint = None
        if shapes:
            centre = float(spectrograph['center_nm'])
            centres = sorted({c for c, _, _ in shapes})
            if len(centres) >= 2:
                a, b = np.polyfit([c for c, _, _ in shapes], [d for _, d, _ in shapes], 1)
                dispersion, spread = float(a * centre + b), 0.06
            else:
                dispersion, spread = shapes[0][1], 0.25
            hint = dict(dispersion_nm_per_px=dispersion, curvature_nm=float(np.median([q for _, _, q in shapes])), range=spread,
                        calibrations=len(shapes))
        self._hint_cache = (key, hint)
        return deepcopy(hint)

    def recall(self, spectrograph):
        '''The calibration saved for this spectrometer, centre wavelength and grating, with when it was installed; or None.'''
        entry = self._read_library().get(self.library_key(spectrograph))
        try:
            calibration = entry['calibration']
            CalibrationRequest(**{k: calibration[k] for k in ('calibration_id', 'source', 'wavelengths_nm')})
            return dict(calibration=deepcopy(calibration), saved_utc=entry.get('saved_utc'))
        except (TypeError, KeyError, ValueError):
            return None

    def set_mode(self,mode):
        self.path=self.directory/('installed-calibration.json' if mode=='hardware' else 'installed-simulation-calibration.json')
        self.response_path=self.directory/('installed-response.json' if mode=='hardware' else 'installed-simulation-response.json')
        with _LOCKS_GUARD:self.persistence_lock=_LOCKS.setdefault(str(self.path.resolve()),RLock())
        self.warning=None

    def set_instrument(self,config):
        self.instrument=dict(driver=config['driver'],model=config.get('model','Oriel 77400 / MS125'))

    def instrument_matches(self,payload):
        # Legacy files predate instrument selection and belonged to the original Oriel.
        saved=payload.get('instrument',dict(driver='oriel_77400_manual',model='Oriel 77400 / MS125'))
        return saved==self.instrument

    def recorded_spectrograph(self):
        """Restore software records of the manual settings; never issue hardware commands."""
        try:
            payload = json.loads(self.path.read_text(encoding='utf-8'))
            if payload['version'] != 1 or not self.instrument_matches(payload):
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
            if payload['version'] != 1 or payload['spectrograph'] != spectrograph or not self.instrument_matches(payload):
                raise ValueError('Saved calibration does not match the selected spectrometer or optical settings')
            calibration = payload['calibration']
            if not isinstance(calibration,dict) or not {'calibration_id','source','wavelengths_nm'}<=calibration.keys():raise ValueError('Calibration is missing required fields')
            if calibration['wavelengths_nm'] is not None:
                CalibrationRequest(**{k: calibration[k] for k in ('calibration_id', 'source', 'wavelengths_nm')})
            elif (calibration['calibration_id'],calibration['source'])!=(None,None):
                CalibrationRequest(**{k:calibration[k] for k in ('calibration_id','source','wavelengths_nm')})
            self.warning=None
            try:self.remember(calibration, spectrograph)  # one installed before the library existed
            except (OSError, ValueError):pass
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
                json.dump(dict(version=1, calibration=calibration, spectrograph=spectrograph,instrument=self.instrument), stream,
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
        try:
            self.remember(calibration, spectrograph)
        except (OSError, ValueError) as exc:
            self.warning = f'Calibration installed, but not added to the saved calibrations: {exc}'

    def persist_response(self, response, spectrograph):
        '''Save the installed intensity response (or an empty one) with the settings it was made for.'''
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = dict(version=1, response=response, spectrograph=spectrograph, instrument=self.instrument)
        temporary = self.response_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        os.replace(temporary, self.response_path)

    def restore_response(self, spectrograph):
        '''The saved response if it still applies: same spectrometer; a measured one also needs the same settings
        (it carries the setup's pixel-dependent throughput), an imported one depends on wavelength only.'''
        try:
            payload = json.loads(self.response_path.read_text(encoding='utf-8'))
            response = payload['response']
            if payload['version'] != 1 or not self.instrument_matches(payload) or not response or not response.get('values'):
                return None
            if response.get('source') == 'measured' and payload['spectrograph'] != spectrograph:
                return None
            return response
        except (OSError, ValueError, KeyError, TypeError):
            return None

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

    def record_hardware_frame(self,image,exposure,readout,spectrograph,frame_id):
        import numpy as np
        image=np.asarray(image,dtype=np.int32)
        if image.shape!=((1,1024) if readout=='fvb' else (255,1024)):
            raise ValueError('Unexpected hardware CCD frame dimensions')
        header=dict(frame_id=frame_id,shape=list(image.shape),exposure_s=exposure,source_mode='hardware',source='measured',
                    readout=readout,captured_utc=timestamp(),spectrograph=deepcopy(spectrograph),full_scale=65535)
        self.frame=dict(header=header,image=image.copy());self.solutions.clear()
        return self.current_frame()

    def require_frame(self, frame_id, spectrograph):
        if self.frame is None or self.frame['header']['frame_id'] != frame_id:
            raise CommandError('Calibration frame expired; acquire or reload the current frame')
        # The slit width does not move wavelengths on the CCD (the worker keeps the calibration for a slit-only change).
        optics = lambda setting: {k: v for k, v in (setting or {}).items() if k != 'slit_um'}
        if optics(self.frame['header']['spectrograph']) != optics(spectrograph):
            raise CommandError('Centre wavelength or grating changed; acquire a new calibration frame')
        return deepcopy(self.frame)

    def extract(self, data, spectrograph):
        from .calibration import ExtractRequest, extract_profile
        request = ExtractRequest(**data)
        frame = self.require_frame(request.frame_id, spectrograph)
        return dict(frame_id=request.frame_id, source=frame['header']['source']) | extract_profile(
            frame['image'], request.row_start, request.row_end)

    def fit(self, data, spectrograph):
        from .calibration import MappingRequest, fit_mapping, extract_profile
        request = MappingRequest(**data)
        frame = self.require_frame(request.frame_id, spectrograph)
        if frame['header']['source_mode']=='hardware' and request.reference_source!='measured':
            raise ValueError('Import measured reference wavelengths before fitting a real CCD frame; demo lines cannot calibrate hardware')
        extraction = extract_profile(frame['image'], request.row_start, request.row_end)
        if extraction['saturated_pixels']:
            raise CommandError('Calibration ROI contains saturated pixels; reduce exposure and acquire again')
        solution = fit_mapping(request)
        identifier = uuid.uuid4().hex
        solution.update(solution_id=identifier, calibration_id=f'spectral-{identifier}', source=frame['header']['source'],
                        source_mode=frame['header']['source_mode'], frame_id=request.frame_id, frame=deepcopy(frame['header']),
                        extraction_rows=[request.row_start, request.row_end], created_utc=timestamp(),
                        spectrograph=deepcopy(spectrograph), applied=False)
        if frame['header']['source_mode']=='simulation':solution['warnings'].append('Simulated CCD frame; this is not an experimental wavelength calibration')
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
