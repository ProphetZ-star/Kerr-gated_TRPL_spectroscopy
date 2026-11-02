"""All HDF5 access for a live run occurs in one bounded writer thread."""
from concurrent.futures import Future
from pathlib import Path
from queue import Queue, Empty
from threading import Thread, Lock, Event
import time
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
    """Bounded asynchronous FIFO; futures acknowledge only flushed commit markers."""
    capacity=128
    batch_size=32
    batch_latency=.010

    def __init__(self,path,metadata):
        strict_json(metadata)
        self.path=Path(path);self.partial=self.path.with_suffix('.partial.h5')
        self.tasks=Queue(maxsize=self.capacity);self.ready=Future();self.submit_lock=Lock()
        self.closed=False;self.failure=None;self.finished=Event()
        self.stats=dict(frames=0,batches=0,flushes=0,event_batches=0,max_queue=0,backpressure=0)
        self.thread=Thread(target=self._loop,args=(metadata,),daemon=True,name='hdf-writer')
        self.thread.start()
        try:self.ready.result(timeout=15)
        except Exception:self.thread.join(2);raise

    def submit(self,operation,value=None):
        future=Future()
        with self.submit_lock:
            if self.closed or self.finished.is_set() or not self.thread.is_alive():raise RuntimeError('Experiment writer is closed')
            if operation=='record' and self.failure:
                future.set_exception(RuntimeError(self.failure));return future
            if self.tasks.full():self.stats['backpressure']+=1
            self.tasks.put((operation,value,future),timeout=10)
            if operation=='close':self.closed=True
            if self.finished.is_set() and not future.done():future.set_exception(RuntimeError(self.failure or 'Writer closed'))
            self.stats['max_queue']=max(self.stats['max_queue'],self.tasks.qsize())
        return future

    def call(self,operation,value=None):
        return self.submit(operation,value).result(timeout=30)

    def _loop(self,metadata):
        file=log=None;created=initialized=created_log=False;active=[]
        def flush():
            file.flush();self.stats['flushes']+=1
        def fail(future,exc):
            if not future.done():future.set_exception(exc)
        try:
            file=h5py.File(self.partial,'x');created=True;text=h5py.string_dtype('utf-8')
            for key in ['meta','config','devices','coordinates','calibration','background','simulation']:
                file.create_group(key).attrs['json']=strict_json(metadata.get(key,{}))
            file.create_group('derived');analysis=file.create_group('analysis')
            for task_id,result in metadata.get('analysis',{}).items():analysis.create_dataset(task_id,data=strict_json(result),dtype=text)
            raw=file.create_group('raw')
            spectra=raw.create_dataset('spectra',(0,1024),maxshape=(None,1024),dtype='i4',chunks=(32,1024),compression='lzf',shuffle=True)
            record_meta=raw.create_dataset('record_meta',(0,),maxshape=(None,),dtype=text)
            indices=raw.create_dataset('record_index',(0,4),maxshape=(None,4),dtype='i8')
            committed=raw.create_dataset('committed',(0,),maxshape=(None,),dtype='?')
            events=file.create_dataset('events',(0,),maxshape=(None,),dtype=text)
            log=self.path.with_suffix('.events.jsonl').open('x',encoding='utf-8');created_log=True
            flush();initialized=True;self.ready.set_result(True)
            done=False
            while not done:
                active=[self.tasks.get()];deadline=time.monotonic()+self.batch_latency
                while len(active)<self.batch_size and active[-1][0]!='close':
                    try:active.append(self.tasks.get(timeout=max(.00001,deadline-time.monotonic())))
                    except Empty:break
                    if time.monotonic()>=deadline:break
                cursor=0
                while cursor<len(active):
                    operation,value,future=active[cursor]
                    if operation=='record':
                        batch=[];encoded=[];invalid=None;end=cursor
                        while end<len(active) and active[end][0]=='record':
                            _,record,ack=active[end];end+=1
                            if self.failure:fail(ack,RuntimeError(self.failure));continue
                            try:
                                validate_record(record)
                                if record.get('inject_write_failure'):raise OSError('Injected disk write failure')
                                encoded.append(strict_json({k:v for k,v in record.items() if k not in {'spectrum','inject_write_failure'}}))
                                batch.append((record,ack))
                            except Exception as exc:
                                self.failure=str(exc);invalid=(ack,exc)
                        if batch:
                            offset=len(spectra);size=len(batch)
                            try:
                                for dataset in (spectra,record_meta,indices,committed):dataset.resize(offset+size,axis=0)
                                committed[offset:offset+size]=False
                                spectra[offset:offset+size]=np.stack([r['spectrum'] for r,_ in batch])
                                record_meta[offset:offset+size]=encoded
                                indices[offset:offset+size]=[[r[k] for k in ['record_id','round_index','point_index','frame_index']] for r,_ in batch]
                                flush();committed[offset:offset+size]=True;flush()
                                self.stats['batches']+=1;self.stats['frames']+=size
                                for _,ack in batch:ack.set_result(True)
                            except Exception as exc:
                                self.failure=str(exc)
                                # No failed batch is advertised as committed. Earlier batches remain intact.
                                try:committed[offset:offset+size]=False;flush()
                                except Exception:pass
                                for _,ack in batch:fail(ack,exc)
                        if invalid:fail(*invalid)
                        cursor=end;continue
                    try:
                        if operation=='event':
                            block=[];acks=[]
                            while cursor<len(active) and active[cursor][0]=='event':
                                _,event,ack=active[cursor];block.append(strict_json(event));acks.append(ack);cursor+=1
                            offset=len(events);events.resize(offset+len(block),axis=0);events[offset:]=block
                            log.write('\n'.join(block)+'\n');log.flush();flush();self.stats['event_batches']+=1
                            for ack in acks:ack.set_result(True)
                            continue
                        if operation=='close':
                            if value not in {'completed','stopped','error'}:raise ValueError('Invalid terminal status')
                            terminal='error' if self.failure else value
                            file['meta'].attrs['terminal_status']=terminal;flush();file.close();file=None;log.close();log=None
                            final=self.partial if terminal=='error' else self.path if terminal=='completed' else self.path.with_suffix('.stopped.h5')
                            if final!=self.partial:
                                if final.exists():raise FileExistsError(final)
                                self.partial.rename(final)
                            future.set_result(str(final));done=True
                        else:raise ValueError('Unknown writer operation: '+operation)
                    except Exception as exc:
                        self.failure=str(exc);fail(future,exc)
                        if operation=='event':
                            for _,_,ack in active[cursor:]:fail(ack,exc)
                            raise
                        if operation=='close':done=True
                    cursor+=1
                active=[]
        except Exception as exc:
            self.failure=str(exc)
            if not self.ready.done():self.ready.set_exception(exc)
            for _,_,future in active:fail(future,exc)
        finally:
            self.finished.set()
            for resource in (file,log):
                if resource is not None:
                    try:resource.close()
                    except Exception as exc:self.failure=self.failure or str(exc)
            while True:
                try:_,_,future=self.tasks.get_nowait();fail(future,RuntimeError(self.failure or 'Writer closed'))
                except Empty:break
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
        for start in range(0,len(valid),128):
            rows=valid[start:start+128]
            selection=slice(int(rows[0]),int(rows[-1])+1) if rows[-1]-rows[0]+1==len(rows) else rows
            spectra_batch=f['raw/spectra'][selection];metadata_batch=f['raw/record_meta'][selection]
            for counts,encoded in zip(spectra_batch,metadata_batch):
                meta=parse_json(encoded);meta['spectrum']=counts
                validate_record(meta);records.append(meta)
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
