'''Plain Python acquisition datasets and grouped spectra.'''
import numpy as np

class SpectrumGroup:
    __slots__=('delay','raw_sum','norm_sum','count')
    def __init__(self,delay,size):
        self.delay=float(delay); self.raw_sum=np.zeros(size,dtype=float); self.norm_sum=np.zeros(size,dtype=float); self.count=0
    @property
    def raw(self):
        return self.raw_sum/max(1,self.count)
    @property
    def normalized(self):
        return self.norm_sum/max(1,self.count)
    @property
    def size(self):
        return int(self.raw_sum.size)
    def add(self,spectrum,exposure):
        self.raw_sum+=spectrum; self.norm_sum+=spectrum/exposure; self.count+=1

class Dataset:
    def __init__(self,run=None,records=None,fit=None,wave=600.0,half_window=5.0):
        self.run=run; self.records=[]; self.fit=fit; self.wave=float(wave); self.half_window=float(half_window)
        self._groups={}; self._seen=set(); self._revision=0;self._sorted_delays=None;self._observed_cache=None
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
    def latest_record(self):
        return self.records[-1] if self.records else None
    def clear_fit(self):
        self.fit=None
    def append(self,record):
        return self.append_record(record)
    def append_record(self,record):
        if not isinstance(record,dict):
            return False
        rid=record.get('record_id')
        if rid is not None:
            try:
                key=('id',rid)
                if key in self._seen:
                    return False
            except Exception:
                key=None
        else:
            key=None
        try:
            delay=float(record.get('delay_ps')); exposure=float(record.get('exposure_s'))
            spectrum=np.asarray(record.get('spectrum'),dtype=float)
        except Exception:
            return False
        if not np.isfinite(delay) or not np.isfinite(exposure) or exposure<=0 or spectrum.ndim!=1 or spectrum.size==0:
            return False
        if not bool(np.all(np.isfinite(spectrum))):
            return False
        group=self._groups.get(delay)
        if group is not None and group.size!=spectrum.size:
            return False
        if group is None:
            group=SpectrumGroup(delay,spectrum.size); self._groups[delay]=group
            self._sorted_delays=None
        group.add(spectrum,exposure)
        if key is not None:
            self._seen.add(key)
        self.records.append(record); self._revision+=1
        return True
    def append_records(self,records):
        added=0
        for record in records or []:
            if self.append_record(record):
                added+=1
        return added
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
