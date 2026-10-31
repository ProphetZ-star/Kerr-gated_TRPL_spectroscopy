"""All HDF5 access for a live run occurs in one bounded writer thread."""
from concurrent.futures import Future
from pathlib import Path
from queue import Queue
from threading import Thread
import json
import shutil
import heapq
import h5py
import numpy as np


def strict_json(value):
    def numpy_value(item):
        if isinstance(item,np.generic):return item.item()
        if isinstance(item,np.ndarray):return item.tolist()
        raise TypeError(f'Unsupported metadata type: {type(item).__name__}')
    return json.dumps(value,ensure_ascii=False,allow_nan=False,default=numpy_value)


def parse_json(value):
    def nonfinite(token):raise ValueError(f'Non-finite JSON value: {token}')
    return json.loads(value,parse_constant=nonfinite)


def validate_record(record):
    from .export import finite_number
    if not isinstance(record,dict) or not {'spectrum','record_id','round_index','point_index','frame_index','delay_ps','exposure_s'}<=record.keys():
        raise ValueError('Experiment record is missing required fields')
    spectrum=np.asarray(record['spectrum'])
    if spectrum.shape!=(1024,) or spectrum.dtype.kind not in 'iu' or np.any(spectrum<np.iinfo(np.int32).min) or np.any(spectrum>np.iinfo(np.int32).max):
        raise ValueError('Raw spectrum must contain exactly 1024 int32-compatible integer counts')
    for key in ('record_id','round_index','point_index','frame_index'):
        if isinstance(record[key],(bool,np.bool_)) or not isinstance(record[key],(int,np.integer)) or record[key]<0:
            raise ValueError(f'{key} must be a nonnegative integer')
    finite_number(record['delay_ps'],'delay_ps');finite_number(record['exposure_s'],'exposure_s',True)


def choose_path(path, run_id, estimate=0):
    target = Path(path).expanduser() if path else Path(__file__).resolve().parents[2]/'data'/f'{run_id}.h5'
    if target.suffix.lower() != '.h5':
        raise ValueError('Output filename must end in .h5')
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if any(p.exists() for p in [target,target.with_suffix('.partial.h5'),target.with_suffix('.stopped.h5'),target.with_suffix('.events.jsonl')]):
        raise ValueError('Output file already exists; choose a new filename')
    if shutil.disk_usage(target.parent).free < estimate + 20*1024**2:
        raise ValueError('Insufficient free disk space')
    return target


class HDFWriter:
    def __init__(self, path, metadata):
        strict_json(metadata)  # Validate before creating an output file.
        self.path = Path(path)
        self.partial = self.path.with_suffix('.partial.h5')
        self.tasks = Queue(maxsize=8)
        self.ready = Future()
        self.thread = Thread(target=self._loop, args=(metadata,), daemon=True, name='hdf-writer')
        self.thread.start()
        try:self.ready.result(timeout=15)
        except Exception:
            self.thread.join(2)
            raise

    def call(self, operation, value=None):
        future = Future()
        self.tasks.put((operation,value,future), timeout=10)
        return future.result(timeout=30)

    def _loop(self, metadata):
        file = None
        log = None
        created=False;initialized=False;created_log=False
        try:
            file = h5py.File(self.partial, 'x')
            created=True
            text = h5py.string_dtype('utf-8')
            for key in ['meta','config','devices','coordinates','calibration','background','simulation']:
                group = file.create_group(key)
                group.attrs['json'] = strict_json(metadata.get(key,{}))
            file.create_group('derived')
            analysis=file.create_group('analysis')
            for task_id,result in metadata.get('analysis',{}).items():
                analysis.create_dataset(task_id,data=json.dumps(result,allow_nan=False),dtype=text)
            raw = file.create_group('raw')
            spectra = raw.create_dataset('spectra',(0,1024),maxshape=(None,1024),dtype='i4',chunks=(1,1024),compression='gzip')
            record_meta = raw.create_dataset('record_meta',(0,),maxshape=(None,),dtype=text)
            indices = raw.create_dataset('record_index',(0,4),maxshape=(None,4),dtype='i8')
            committed = raw.create_dataset('committed',(0,),maxshape=(None,),dtype='?')
            events = file.create_dataset('events',(0,),maxshape=(None,),dtype=text)
            log = self.path.with_suffix('.events.jsonl').open('x',encoding='utf-8')
            created_log=True
            file.flush()
            initialized=True
            self.ready.set_result(True)
            failure = None
            while True:
                operation,value,future = self.tasks.get()
                try:
                    if operation == 'record':
                        if failure:
                            raise RuntimeError(failure)
                        record = value
                        validate_record(record)
                        record_json=strict_json({k:v for k,v in record.items() if k not in {'spectrum','inject_write_failure'}})
                        if record.get('inject_write_failure'):
                            raise OSError('Injected disk write failure')
                        i = len(spectra)
                        spectra.resize(i+1,axis=0); record_meta.resize(i+1,axis=0)
                        indices.resize(i+1,axis=0); committed.resize(i+1,axis=0)
                        committed[i] = False
                        spectra[i] = record['spectrum']
                        record_meta[i] = record_json
                        indices[i] = [record[k] for k in ['record_id','round_index','point_index','frame_index']]
                        file.flush()
                        committed[i] = True
                        file.flush()
                    elif operation == 'event':
                        line=strict_json(value)
                        i = len(events); events.resize(i+1,axis=0)
                        events[i] = line; log.write(line+'\n'); log.flush(); file.flush()
                    elif operation == 'close':
                        if value not in {'completed','stopped','error'}:raise ValueError('Invalid terminal status')
                        file['meta'].attrs['terminal_status'] = value
                        file.flush(); file.close(); file=None
                        log.close(); log=None
                        final = self.path if value=='completed' else self.path.with_suffix(f'.{value}.h5')
                        if value == 'error':
                            final = self.partial
                        elif final.exists():
                            raise FileExistsError(final)
                        else:
                            self.partial.rename(final)
                        future.set_result(str(final))
                        break
                    future.set_result(True)
                except Exception as exc:
                    failure = str(exc)
                    future.set_exception(exc)
                    if operation=='close':
                        break
        except Exception as exc:
            if not self.ready.done():
                self.ready.set_exception(exc)
        finally:
            if file is not None:
                file.close()
            if log is not None:
                log.close()
            if created and not initialized:
                self.partial.unlink(missing_ok=True)
                if created_log:self.path.with_suffix('.events.jsonl').unlink(missing_ok=True)


def _read_run(path):
    with h5py.File(path, 'r') as f:
        if 'raw/spectra' not in f or 'raw/committed' not in f:
            raise ValueError('Unsupported experiment file')
        if f['raw/spectra'].ndim!=2 or f['raw/spectra'].shape[1]!=1024:
            raise ValueError('This release reads 1024-pixel FVB files only')
        if f['raw/spectra'].dtype.kind not in 'iu':raise ValueError('Raw spectra must have an integer dtype')
        if f['raw/committed'].ndim!=1 or f['raw/record_meta'].ndim!=1:raise ValueError('Invalid raw record dimensions')
        valid = np.flatnonzero(f['raw/committed'][:])
        if len(valid)>25000:
            raise ValueError('File exceeds this release\'s 25,000-record limit')
        if len(valid) and valid[-1]>=min(len(f['raw/spectra']),len(f['raw/record_meta'])):raise ValueError('Committed record exceeds available spectra/metadata')
        records=[]
        for i in valid:
            meta=parse_json(f['raw/record_meta'][i])
            meta['spectrum']=f['raw/spectra'][i]
            validate_record(meta)
            records.append(meta)
        metadata={k:parse_json(f[k].attrs['json']) for k in ['meta','config','devices','coordinates','calibration','background','simulation']}
        if any(not isinstance(metadata[k],dict) for k in ['meta','config','devices','coordinates','calibration','simulation']):raise ValueError('Invalid experiment metadata shape')
        from ..models import RunRequest
        RunRequest(**metadata['config']['request'])
        if not isinstance(metadata['meta']['run_id'],str) or not metadata['meta']['run_id'] or not metadata['meta']['created_utc']:raise ValueError('Invalid experiment identity')
        if not isinstance(metadata['config']['plan'],dict):raise ValueError('Missing experiment scan plan')
        if not {'rounds','total_points','total_frames','delays_ps'}<=metadata['config']['plan'].keys():raise ValueError('Incomplete experiment scan plan')
        recent=[]
        for name,value in f.get('analysis',{}).items():
            result=parse_json(value[()])
            if not isinstance(result,dict):raise ValueError('Invalid analysis result')
            heapq.heappush(recent,(str(result.get('finished_utc','')),name,result))
            if len(recent)>64:heapq.heappop(recent)
        metadata['analysis']={name:result for _,name,result in sorted(recent)}
        terminal=str(f['meta'].attrs.get('terminal_status','partial'))
        if terminal not in {'completed','stopped','error','partial'}:raise ValueError('Invalid experiment terminal status')
        return metadata,records,terminal


def read_run(path):
    if not Path(path).is_file():raise FileNotFoundError('Experiment file does not exist')
    try:return _read_run(path)
    except (OSError,KeyError,IndexError,TypeError,OverflowError,ValueError) as exc:
        raise ValueError(f'Invalid or damaged experiment file: {exc}') from exc


def append_analysis(path,task_id,result):
    """Only used after live acquisition has finalized and closed its writer."""
    with h5py.File(path,'r+') as file:
        if task_id in file['analysis']:raise ValueError('Analysis task already exists in this experiment')
        file['analysis'].create_dataset(task_id,data=json.dumps(result,allow_nan=False),dtype=h5py.string_dtype('utf-8'))
        file.flush()
