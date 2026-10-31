"""All HDF5 access for a live run occurs in one bounded writer thread."""
from concurrent.futures import Future
from pathlib import Path
from queue import Queue
from threading import Thread
import json
import shutil
import h5py
import numpy as np


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
        self.path = Path(path)
        self.partial = self.path.with_suffix('.partial.h5')
        self.tasks = Queue(maxsize=8)
        self.ready = Future()
        self.thread = Thread(target=self._loop, args=(metadata,), daemon=True, name='hdf-writer')
        self.thread.start()
        self.ready.result(timeout=15)

    def call(self, operation, value=None):
        future = Future()
        self.tasks.put((operation,value,future), timeout=10)
        return future.result(timeout=30)

    def _loop(self, metadata):
        file = None
        log = None
        try:
            file = h5py.File(self.partial, 'x')
            text = h5py.string_dtype('utf-8')
            for key in ['meta','config','devices','coordinates','calibration','background','simulation']:
                group = file.create_group(key)
                group.attrs['json'] = json.dumps(metadata.get(key,{}), ensure_ascii=False)
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
            file.flush()
            self.ready.set_result(True)
            failure = None
            while True:
                operation,value,future = self.tasks.get()
                try:
                    if operation == 'record':
                        if failure:
                            raise RuntimeError(failure)
                        record = value
                        if record.get('inject_write_failure'):
                            raise OSError('Injected disk write failure')
                        i = len(spectra)
                        spectra.resize(i+1,axis=0); record_meta.resize(i+1,axis=0)
                        indices.resize(i+1,axis=0); committed.resize(i+1,axis=0)
                        committed[i] = False
                        spectra[i] = record['spectrum']
                        record_meta[i] = json.dumps({k:v for k,v in record.items() if k not in {'spectrum','inject_write_failure'}})
                        indices[i] = [record[k] for k in ['record_id','round_index','point_index','frame_index']]
                        file.flush()
                        committed[i] = True
                        file.flush()
                    elif operation == 'event':
                        i = len(events); events.resize(i+1,axis=0)
                        line = json.dumps(value,ensure_ascii=False)
                        events[i] = line; log.write(line+'\n'); log.flush(); file.flush()
                    elif operation == 'close':
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


def read_run(path):
    with h5py.File(path, 'r') as f:
        if 'raw/spectra' not in f or 'raw/committed' not in f:
            raise ValueError('Unsupported experiment file')
        if f['raw/spectra'].ndim!=2 or f['raw/spectra'].shape[1]!=1024:
            raise ValueError('This release reads 1024-pixel FVB files only')
        valid = np.flatnonzero(f['raw/committed'][:])
        if len(valid)>25000:
            raise ValueError('File exceeds this release\'s 25,000-record limit')
        records=[]
        for i in valid:
            meta=json.loads(f['raw/record_meta'][i])
            meta['spectrum']=f['raw/spectra'][i]
            records.append(meta)
        metadata={k:json.loads(f[k].attrs['json']) for k in ['meta','config','devices','coordinates','calibration','background','simulation']}
        metadata['analysis']={name:json.loads(value[()]) for name,value in f.get('analysis',{}).items()}
        return metadata,records,str(f['meta'].attrs.get('terminal_status','partial'))


def append_analysis(path,task_id,result):
    """Only used after live acquisition has finalized and closed its writer."""
    with h5py.File(path,'r+') as file:
        file['analysis'].create_dataset(task_id,data=json.dumps(result,allow_nan=False),dtype=h5py.string_dtype('utf-8'))
        file.flush()
