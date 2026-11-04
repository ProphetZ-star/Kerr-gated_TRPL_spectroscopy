'''Acquisition datasets on the GUI side: per-delay sums of the run's frames plus its latest frame.

Frames arrive from the worker in blocks of arrays (see backend/storage/frames.py FrameTable.block); the GUI
keeps per-delay sums, not every spectrum, so a long run costs memory per delay rather than per frame.'''
import numpy as np

FULL_SCALE=65535.0

class SpectrumGroup:
    __slots__=('delay','raw_sum','norm_sum','count','saturated')
    def __init__(self,delay,size):
        self.delay=float(delay); self.raw_sum=np.zeros(size,dtype=float); self.norm_sum=np.zeros(size,dtype=float); self.count=0
        self.saturated=np.zeros(size,dtype=bool)  # pixels that hit full scale in any frame of this delay
    @property
    def raw(self):
        return self.raw_sum/max(1,self.count)
    @property
    def normalized(self):
        return self.norm_sum/max(1,self.count)
    @property
    def size(self):
        return int(self.raw_sum.size)
    def add(self,spectrum,exposure,full_scale=FULL_SCALE):
        self.add_many(np.asarray(spectrum,dtype=float)[None,:],exposure,full_scale)
    def add_many(self,spectra,exposure,full_scale=FULL_SCALE):
        total=spectra.sum(axis=0)
        self.raw_sum+=total; self.norm_sum+=total/exposure; self.count+=len(spectra)
        self.saturated|=(spectra>=full_scale).any(axis=0)

class FrameCount:
    '''Stands in for the old list of frame dicts: len() and truth are the frames received; [-1] is the latest.'''
    __slots__=('count','latest')
    def __init__(self):
        self.count=0; self.latest=None
    def __len__(self):
        return self.count
    def __bool__(self):
        return self.count>0
    def __getitem__(self,index):
        if index in (-1,self.count-1) and self.latest is not None:
            return self.latest
        raise IndexError('Only the latest frame is kept on the GUI side')

class Dataset:
    def __init__(self,run=None,records=None,fit=None,wave=600.0,half_window=5.0):
        self.run=run; self.records=FrameCount(); self.fit=fit; self.wave=float(wave); self.half_window=float(half_window)
        self._groups={}; self._revision=0;self._sorted_delays=None;self._observed_cache=None
        self.quality={}
        if records:
            self.append_records(records)
    @property
    def revision(self):
        return self._revision
    @property
    def groups(self):
        return self._groups
    @property
    def delays(self):
        if self._sorted_delays is None:self._sorted_delays=sorted(self._groups)
        return self._sorted_delays
    @property
    def raw_by_delay(self):
        return {d:g.raw.copy() for d,g in self._groups.items()}
    @property
    def normalized_by_delay(self):
        return {d:g.normalized.copy() for d,g in self._groups.items()}
    @property
    def spectra(self):
        return self.normalized_by_delay
    @property
    def frames_by_delay(self):
        return {d:g.count for d,g in self._groups.items()}
    @property
    def axis(self):
        if not self.run:
            return None
        try:
            axis=self.run.get('metadata',{}).get('calibration',{}).get('wavelengths_nm')
        except Exception:
            axis=None
        if not axis:
            axis=self.run.get('live_axis')
        if isinstance(axis,list) and axis and all(isinstance(v,(int,float)) for v in axis):
            return [float(v) for v in axis]
        return None
    @property
    def full_scale(self):
        try:
            value=float(self.run['metadata']['instrument']['camera']['full_scale'])
        except Exception:
            return FULL_SCALE
        return value if np.isfinite(value) and value>0 else FULL_SCALE
    @property
    def latest_record(self):
        return self.records.latest
    def clear_fit(self):
        self.fit=None
    def append_block(self,block):
        '''Add rows offset.. of the run. Rows already received are skipped; a block that would leave a gap, or
        whose values are invalid, is refused whole. Returns the number of new frames.'''
        try:
            offset=int(block['offset']); exposure=float(block['exposure_s'])
            spectra=np.asarray(block['spectra'],dtype=float); delays=np.asarray(block['delay_ps'],dtype=float)
            index=np.asarray(block['index']); saturated=np.asarray(block['saturated_pixels'])
        except Exception:
            return 0
        if offset>self.records.count or spectra.ndim!=2 or spectra.shape[1]==0 or delays.shape!=(len(spectra),) or index.shape!=(len(spectra),3):
            return 0
        skip=self.records.count-offset
        spectra,delays,index,saturated=spectra[skip:],delays[skip:],index[skip:],saturated[skip:]
        if not len(spectra) or not np.isfinite(exposure) or exposure<=0 or not np.isfinite(delays).all() or not np.isfinite(spectra).all():
            return 0
        unique,inverse=np.unique(delays,return_inverse=True)
        if any(d in self._groups and self._groups[d].size!=spectra.shape[1] for d in unique.tolist()):
            return 0
        full_scale=self.full_scale
        for position,delay in enumerate(unique.tolist()):
            group=self._groups.get(delay)
            if group is None:
                group=SpectrumGroup(delay,spectra.shape[1]); self._groups[delay]=group; self._sorted_delays=None
            group.add_many(spectra[inverse==position],exposure,full_scale)
        self._add_quality(block,skip)
        last=len(spectra)-1
        self.records.latest=dict(record_id=offset+skip+last,round_index=int(index[last,0]),point_index=int(index[last,1]),frame_index=int(index[last,2]),
                                 delay_ps=float(delays[last]),exposure_s=exposure,source_mode=block.get('source_mode'),
                                 saturated_pixels=int(saturated[last]),spectrum=spectra[last].tolist())
        self.records.count+=len(spectra); self._revision+=1
        return len(spectra)
    def _add_quality(self,block,skip):
        '''Running summary of the per-frame columns for the experiment details: CCD temperature range and state,
        saturated frames, stage position error against the plan, and the span of frame times.'''
        def column(name):
            value=block.get(name)
            return None if value is None else np.asarray(value)[skip:]
        q=self.quality
        temperature=column('temperature_c')
        if temperature is not None:
            finite=temperature[np.isfinite(temperature)]
            if finite.size:
                q['temperature_min']=min(q.get('temperature_min',np.inf),float(finite.min()))
                q['temperature_max']=max(q.get('temperature_max',-np.inf),float(finite.max()))
        state=column('temperature_state')
        if state is not None:
            q['unstable_frames']=q.get('unstable_frames',0)+int(np.count_nonzero((state>=0)&(state!=3)))
            q['unknown_state_frames']=q.get('unknown_state_frames',0)+int(np.count_nonzero(state<0))
        saturated=column('saturated_pixels')
        if saturated is not None:
            q['saturated_frames']=q.get('saturated_frames',0)+int(np.count_nonzero(saturated>0))
        reported=column('stage_reported_mm'); index=column('index')
        try:targets=np.asarray(self.run['metadata']['acquisition']['targets_mm'],dtype=float)
        except Exception:targets=None
        if reported is not None and index is not None and targets is not None and targets.size:
            points=index[:,1]; valid=np.isfinite(reported)&(points>=0)&(points<targets.size)
            if valid.any():
                error=float(np.max(np.abs(reported[valid]-targets[points[valid]])))
                q['position_error_max_mm']=max(q.get('position_error_max_mm',0.0),error)
        start=column('t_start_s'); end=column('t_end_s')
        if start is not None and end is not None:
            finite=np.isfinite(start)&np.isfinite(end)
            if finite.any():
                q['first_start_s']=min(q.get('first_start_s',np.inf),float(start[finite].min()))
                q['last_end_s']=max(q.get('last_end_s',-np.inf),float(end[finite].max()))
                q['exposure_span_s']=q.get('exposure_span_s',0.0)+float(np.sum(end[finite]-start[finite]))
    def append_records(self,records):
        '''Frame dicts (delay_ps, exposure_s, spectrum, ...) in run order, e.g. from tests or older callers.'''
        added=0
        for record in records or []:
            try:
                block=dict(offset=self.records.count,exposure_s=record['exposure_s'],source_mode=record.get('source_mode'),
                           spectra=np.asarray(record['spectrum'],dtype=float)[None,:],delay_ps=np.asarray([record['delay_ps']],dtype=float),
                           index=np.asarray([[record.get('round_index',0),record.get('point_index',0),record.get('frame_index',0)]]),
                           saturated_pixels=np.asarray([record.get('saturated_pixels') or 0]))
            except Exception:
                continue
            added+=self.append_block(block)
        return added
    def append_record(self,record):
        return self.append_records([record])==1
    def append(self,record):
        return self.append_record(record)
    def frames_for_delay(self,delay):
        group=self._groups.get(float(delay))
        return group.count if group else 0
    def spectrum_for_delay(self,delay):
        group=self._groups.get(float(delay))
        return group.normalized.copy() if group else None
    def raw_spectrum_for_delay(self,delay):
        group=self._groups.get(float(delay))
        return group.raw.copy() if group else None
    def observed(self,wavelength=None,half_window=None):
        axis=self.axis
        if not axis:
            return []
        wave=self.wave if wavelength is None else float(wavelength)
        half=self.half_window if half_window is None else float(half_window)
        key=(self._revision,wave,half,id(self.run.get('metadata',{}).get('calibration',{}).get('wavelengths_nm')))
        if self._observed_cache is not None and self._observed_cache[0]==key:return self._observed_cache[1]
        ids=[i for i,v in enumerate(axis) if abs(v-wave)<=half]
        if not ids:
            return []
        out=[]
        for delay in self.delays:
            group=self._groups[delay]
            if any(i>=group.size for i in ids):
                continue
            out.append((delay,float(np.mean(group.norm_sum[ids]))/group.count))
        self._observed_cache=(key,out)
        return out
    def integrated_at(self,delay,wave=None,half_window=None):
        axis=self.axis
        group=self._groups.get(float(delay))
        if group is None:
            return None
        if not axis:
            return float(np.mean(group.normalized))
        wave=self.wave if wave is None else float(wave); half=self.half_window if half_window is None else float(half_window)
        ids=[i for i,v in enumerate(axis) if abs(v-wave)<=half and i<group.size]
        if not ids:
            return None
        return float(np.mean(group.normalized[ids]))

def delay_fraction(delay,minimum,maximum):
    return (delay-minimum)/(maximum-minimum) if maximum>minimum else 0.5

def finite_number(value):
    try:
        value=float(value)
    except Exception:
        return None
    return value if np.isfinite(value) else None
