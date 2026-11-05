"""From a run's frames to a decay curve: the single place where frames are selected and averaged.

The lifetime fit, the Analysis page's decay plot (through the worker's 'kinetics' command) and the per-round
view all call kinetics(), so what is drawn is exactly what is fitted.

Each frame contributes the mean count of the wavelength window's pixels per second of exposure, optionally
after subtracting the run's recorded background (taken at the same exposure and readout). Frames at the same
delay (repeat rounds, several frames per point) are then averaged. Processing options:
  subtract_background   use the run's background spectrum
  exclude_saturated     drop every (round, delay) point that has a pixel at full scale inside the window
  exclude_unstable      drop frames taken while the CCD temperature was not stabilized
                        (not reached, stabilizing or drifting; a cooler that is off is not flagged)
  position_tolerance_um drop frames whose reported stage position is further than this from the plan
  rounds                use only these repeat rounds (None: all)
"""
import numpy as np

UNSTABLE_STATES = (1, 2, 4)  # frame/temperature_state codes: not_reached, not_stabilized, drift


def window_mask(axis, wavelength_nm, half_window_nm):
    if axis is None:raise ValueError('A valid wavelength calibration is required')
    wavelengths = np.asarray(axis, dtype=float)
    if wavelengths.ndim != 1 or not len(wavelengths) or not np.isfinite(wavelengths).all() or np.any(wavelengths <= 0):
        raise ValueError('Wavelength axis must be positive and finite')
    mask = abs(wavelengths - wavelength_nm) <= half_window_nm
    if not mask.any():raise ValueError('No pixels in the selected wavelength window')
    return mask


def _average(delays, values):
    t, inverse = np.unique(delays, return_inverse=True)
    if not len(t):return t, t.copy(), np.zeros(0, int)
    counts = np.bincount(inverse, minlength=len(t))
    return t, np.bincount(inverse, weights=values, minlength=len(t)) / counts, counts


def _frame_values(block, axis, options, background=None, response=None):
    """Validate one page and calculate its per-frame intensity using the unchanged processing rules."""
    spectra = np.asarray(block['spectra']); delays = np.asarray(block['delay_ps'], dtype=float)
    index = np.asarray(block['index']).reshape(-1, 3); n = len(delays)
    exposure = np.broadcast_to(np.asarray(block['exposure_s'], dtype=float), delays.shape)
    mask = window_mask(axis, options.wavelength_nm, options.half_window_nm)
    if n and spectra.shape[1:] != mask.shape:raise ValueError('Fit spectrum must match the axis and contain only finite counts')
    if not np.isfinite(delays).all():raise ValueError('delay_ps must be finite')
    if not (np.isfinite(exposure).all() and np.all(exposure > 0)):raise ValueError('exposure_s must be finite and > 0')
    window = np.asarray(spectra[:, mask], dtype=float)
    if not np.isfinite(window).all():raise ValueError('Fit spectrum must match the axis and contain only finite counts')
    subtract = bool(getattr(options, 'subtract_background', False))
    if response is not None:
        # Intensity response installed: every pixel is corrected, (raw - B) / R. The offset always comes off first
        # (a corrected offset would be meaningless): the recorded background, or each frame's baseline.
        from .response import baseline
        r = np.asarray(response, dtype=float)
        if r.shape != mask.shape:raise ValueError('The intensity response does not match the spectra')
        good = np.isfinite(r[mask]) & (r[mask] > 0)
        if not good.any():raise ValueError('The intensity response is undefined across this wavelength window')
        if background is not None:
            offset = np.asarray(background, dtype=float)[mask][good]
        else:
            offset = baseline(spectra)[:, None]
        values = ((window[:, good] - offset) / r[mask][good]).mean(axis=1) / exposure
    elif subtract:
        if background is None:raise ValueError('This experiment has no recorded background to subtract')
        background = np.asarray(background, dtype=float)
        if background.shape != mask.shape:raise ValueError('The recorded background does not match the spectra')
        values = (window - background[mask]).mean(axis=1) / exposure
    else:
        values = window.mean(axis=1) / exposure
    if not np.isfinite(values).all():raise ValueError('Exposure-normalized counts are non-finite')
    return spectra, delays, index, values, mask


def kinetics(block, axis, options, background=None, full_scale=None, targets_mm=None, delay_range=True, rounds=None, response=None, cancel=None):
    """Decay and per-round curves from either an array block or a bounded, paged frame snapshot."""
    if hasattr(block, 'iter_blocks'):
        return _stream_kinetics(block,axis,options,background,full_scale,targets_mm,delay_range,rounds,response,cancel)
    spectra, delays, index, values, mask = _frame_values(block,axis,options,background,response)
    n = len(delays)
    keep = np.ones(n, dtype=bool)
    round_index = index[:, 0]
    chosen = rounds if rounds is not None else getattr(options, 'rounds', None)
    if chosen:keep &= np.isin(round_index, np.asarray(list(chosen)))
    # Temperature and position flags are counted whether or not they exclude anything, so the UI can show them.
    state = np.asarray(block.get('temperature_state', np.full(n, -1)))
    unstable = np.isin(state, UNSTABLE_STATES)
    if getattr(options, 'exclude_unstable', False):keep &= ~unstable
    error_um = np.full(n, np.nan)
    if targets_mm is not None and 'stage_reported_mm' in block and n:
        targets = np.asarray(targets_mm, dtype=float); points = index[:, 1]
        valid = (points >= 0) & (points < len(targets))
        error_um[valid] = np.abs(np.asarray(block['stage_reported_mm'], dtype=float)[valid] - targets[points[valid]]) * 1000
    tolerance = getattr(options, 'position_tolerance_um', None)
    off_position = np.zeros(n, dtype=bool) if tolerance is None else np.nan_to_num(error_um, nan=0.0) > tolerance
    if tolerance is not None:keep &= ~off_position
    # Saturation is judged per (round, delay) point: one saturated frame removes that point of that round.
    saturated_point = np.zeros(n, dtype=bool); saturated_points = 0
    if full_scale is not None and n:
        hit = (spectra[:, mask] >= full_scale).any(axis=1)
        if hit.any():
            pairs = np.stack([round_index.astype(float), delays], axis=1)
            unique, inverse = np.unique(pairs, axis=0, return_inverse=True)
            inverse = inverse.reshape(-1)
            point_hit = np.bincount(inverse, weights=hit.astype(float), minlength=len(unique)) > 0
            saturated_point = point_hit[inverse]; saturated_points = int(point_hit.sum())
    excluded_saturated = saturated_point & keep if getattr(options, 'exclude_saturated', True) else np.zeros(n, dtype=bool)
    keep &= ~excluded_saturated
    in_range = np.ones(n, dtype=bool)
    if delay_range:
        if getattr(options, 'min_delay_ps', None) is not None:in_range &= delays >= options.min_delay_ps
        if getattr(options, 'max_delay_ps', None) is not None:in_range &= delays <= options.max_delay_ps
    used = keep & in_range
    t, y, frames = _average(delays[used], values[used])
    per_round = []
    for r in np.unique(round_index[used]).tolist():
        rt, ry, _ = _average(delays[used & (round_index == r)], values[used & (round_index == r)])
        per_round.append(dict(round=int(r), delays_ps=rt.tolist(), values=ry.tolist()))
    st, sy, _ = _average(delays[excluded_saturated & in_range], values[excluded_saturated & in_range])
    finite_error = error_um[np.isfinite(error_um)]
    return dict(delays_ps=t.tolist(), values=y.tolist(), frames=frames.tolist(), per_round=per_round,
                saturated=dict(delays_ps=st.tolist(), values=sy.tolist()),
                counts=dict(frames_total=int(n), frames_used=int(used.sum()), saturated_points=saturated_points,
                            excluded_saturated_frames=int(excluded_saturated.sum()), unstable_frames=int(unstable.sum()),
                            off_position_frames=int(off_position.sum()),
                            position_error_max_um=float(finite_error.max()) if finite_error.size else None),
                rounds_available=[int(r) for r in np.unique(round_index).tolist()],
                rounds_used=[int(r) for r in np.unique(round_index[used]).tolist()],
                background_available=background is not None)


def _stream_kinetics(source, axis, options, background, full_scale, targets_mm, delay_range, rounds, response, cancel):
    """Reduce pages into (round, delay) sums, retaining no per-frame spectra or scalar arrays.

    Saturation is combined across page boundaries before applying exclusions: a late saturated frame must
    reject earlier frames of the same point too. Memory scales with the returned round/delay curves.
    """
    groups = {}
    total = unstable_total = off_total = 0
    max_error = None
    chosen = rounds if rounds is not None else getattr(options, 'rounds', None)
    targets = None if targets_mm is None else np.asarray(targets_mm, dtype=float)
    tolerance = getattr(options, 'position_tolerance_um', None)
    for block in source.iter_blocks(cancel=cancel):
        spectra, delays, index, values, mask = _frame_values(block,axis,options,background,response)
        n = len(delays); total += n
        state = np.asarray(block.get('temperature_state',np.full(n,-1)))
        unstable = np.isin(state,UNSTABLE_STATES); unstable_total += int(unstable.sum())
        error = np.full(n,np.nan)
        if targets is not None and 'stage_reported_mm' in block:
            points = index[:,1]; valid = (points>=0)&(points<len(targets))
            error[valid] = np.abs(np.asarray(block['stage_reported_mm'])[valid]-targets[points[valid]])*1000
        finite = error[np.isfinite(error)]
        if finite.size:max_error = max(float(finite.max()),max_error or 0.)
        off = np.zeros(n,bool) if tolerance is None else np.nan_to_num(error,nan=0.)>tolerance
        off_total += int(off.sum())
        keep = np.ones(n,bool)
        if chosen:keep &= np.isin(index[:,0],np.asarray(list(chosen)))
        if getattr(options,'exclude_unstable',False):keep &= ~unstable
        if tolerance is not None:keep &= ~off
        hit = (spectra[:,mask]>=full_scale).any(axis=1) if full_scale is not None else np.zeros(n,bool)
        pairs,inverse = np.unique(np.stack((index[:,0],delays),axis=1),axis=0,return_inverse=True)
        inverse = inverse.reshape(-1)
        counts = np.bincount(inverse,weights=keep.astype(float),minlength=len(pairs))
        sums = np.bincount(inverse,weights=np.where(keep,values,0.),minlength=len(pairs))
        hits = np.bincount(inverse,weights=hit.astype(float),minlength=len(pairs))>0
        for i,(round_index,delay) in enumerate(pairs):
            key = (int(round_index),float(delay))
            entry = groups.setdefault(key,[0,0.,False])
            entry[0] += int(counts[i]); entry[1] += float(sums[i]); entry[2] |= bool(hits[i])
    if total == 0:
        # Validate the selected window/options even for an empty experiment.
        mask = window_mask(axis,options.wavelength_nm,options.half_window_nm)
        _frame_values(dict(spectra=np.empty((0,len(mask)),dtype='i4'),delay_ps=np.empty(0),
                           index=np.empty((0,3),dtype='i4'),exposure_s=1.),axis,options,background,response)
    used = {}; saturated = {}; per_round = {}
    excluded_frames = saturated_points = frames_used = 0
    available = set()
    def add(mapping,delay,count,value):
        entry = mapping.setdefault(delay,[0,0.]); entry[0] += count; entry[1] += value
    for (round_index,delay),(count,value,hit) in groups.items():
        available.add(round_index); saturated_points += int(hit)
        excluded = hit and getattr(options,'exclude_saturated',True)
        if excluded:excluded_frames += count
        in_range = not delay_range or ((getattr(options,'min_delay_ps',None) is None or delay>=options.min_delay_ps)
                                      and (getattr(options,'max_delay_ps',None) is None or delay<=options.max_delay_ps))
        if not count or not in_range:continue
        if excluded:
            add(saturated,delay,count,value)
        else:
            add(used,delay,count,value); frames_used += count
            add(per_round.setdefault(round_index,{}),delay,count,value)
    def curve(mapping):
        delays = sorted(mapping)
        return dict(delays_ps=delays,values=[mapping[d][1]/mapping[d][0] for d in delays])
    result = curve(used)
    result.update(frames=[used[d][0] for d in result['delays_ps']],
                  per_round=[dict(round=r,**curve(per_round[r])) for r in sorted(per_round)],
                  saturated=curve(saturated),
                  counts=dict(frames_total=total,frames_used=frames_used,saturated_points=saturated_points,
                              excluded_saturated_frames=excluded_frames,unstable_frames=unstable_total,
                              off_position_frames=off_total,position_error_max_um=max_error),
                  rounds_available=sorted(available),rounds_used=sorted(per_round),background_available=background is not None)
    return result


def processing_of(options):
    """The processing choices of a fit request, as stored with the fit."""
    return dict(subtract_background=bool(getattr(options, 'subtract_background', False)),
                exclude_saturated=bool(getattr(options, 'exclude_saturated', True)),
                exclude_unstable=bool(getattr(options, 'exclude_unstable', False)),
                position_tolerance_um=getattr(options, 'position_tolerance_um', None))
