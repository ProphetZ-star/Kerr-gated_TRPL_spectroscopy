"""Unit conversion and bounded, deterministic scan planning."""
from decimal import Decimal
import hashlib
import json
import math
from .storage.schema import MAX_POINTS

C_MM_PS = 0.299792458


def delay_to_mm(delay, zero=150.0, sign=1):
    return zero + sign * C_MM_PS * delay / 2


def mm_to_delay(position, zero=150.0, sign=1):
    return sign * 2 * (position - zero) / C_MM_PS


def make_plan(request, current_delay=0.0, zero=150.0, sign=1, revision=0, limits=(0.,300.)):
    if request.mode == 'single':
        points = [current_delay]
    else:
        points = []
        previous = None
        for segment in request.segments:
            a, b, step = (Decimal(str(v)) for v in (segment.start, segment.end, segment.step))
            if previous is not None and a != previous:
                raise ValueError('Segments must be contiguous, ordered and non-overlapping')
            count = int((b - a) // step)
            if count >= MAX_POINTS:
                raise ValueError(f'A scan may contain at most {MAX_POINTS:,} delay points (the TRPL map draws one row per point)')
            values = [a + j * step for j in range(count + 1)]
            if values[-1] != b:
                if abs(b-values[-1])<=abs(step)*Decimal('1e-12'):values[-1]=b
                else:values.append(b)
            if points:
                values = values[1:]
            points.extend(float(v) for v in values)
            if len(points) > MAX_POINTS:
                raise ValueError(f'A scan may contain at most {MAX_POINTS:,} delay points (the TRPL map draws one row per point)')
            previous = b
    targets = [delay_to_mm(p, zero, sign) for p in points]
    # Positive-direction approach on every round. The extra travel is part of the plan.
    approach = targets[0] - sign * 0.01 if request.mode != 'single' else targets[0]
    if any(not limits[0] <= x <= limits[1] for x in targets + [approach]):
        raise ValueError(f'Scan or approach position exceeds stage soft limits {limits[0]}–{limits[1]} mm')
    if len(set(targets)) != len(targets):
        raise ValueError('Different delays map to indistinguishable stage positions')
    until_stopped = request.mode == 'repeat' and request.repeats == 0
    per_round = len(points) * request.frames_per_point
    # No frame limit here: the acquisition stops by itself when memory or disk runs low. A run repeated until
    # stopped has no planned round count (0); its estimates are for one round.
    rounds = 0 if until_stopped else request.repeats if request.mode == 'repeat' else 1
    counted = rounds or 1
    frames = per_round * rounds
    def duration(a,b):
        d=abs(a-b);return 2*math.sqrt(d/12) if d<3 else 1+(d-3)/6
    position=delay_to_mm(current_delay,zero,sign);motion=0
    for _ in range(counted):
        if request.mode!='single':motion+=duration(position,approach);position=approach
        for target in targets:motion+=duration(position,target);position=target
    seconds=motion+counted*len(points)*request.settle_s+per_round*counted*(request.exposure_s+.03)
    data = dict(delays_ps=points, targets_mm=targets, approach_mm=approach,
                rounds=rounds, total_points=len(points)*rounds, total_frames=frames, until_stopped=until_stopped,
                estimated_seconds=seconds, round_seconds=seconds / counted, estimated_bytes=per_round * counted * (4096 + 2048),
                revision=revision, zero_mm=zero, delay_sign=sign, path_multiplier=2)
    identity={k:v for k,v in data.items() if k not in ('estimated_seconds','round_seconds')}
    identity['request']=request.model_dump(exclude={'request_id','save_path','overwrite_token'})
    data['plan_id'] = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
    return data
