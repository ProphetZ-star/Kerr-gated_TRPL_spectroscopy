"""Unit conversion and bounded, deterministic scan planning."""
from decimal import Decimal
import hashlib
import json

C_MM_PS = 0.299792458


def delay_to_mm(delay, zero=150.0, sign=1):
    return zero + sign * C_MM_PS * delay / 2


def mm_to_delay(position, zero=150.0, sign=1):
    return sign * 2 * (position - zero) / C_MM_PS


def make_plan(request, current_delay=0.0, zero=150.0, sign=1, revision=0):
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
            if count > 5000:
                raise ValueError('A scan may contain at most 5001 points')
            values = [a + j * step for j in range(count + 1)]
            if values[-1] != b:
                values.append(b)
            if points:
                values = values[1:]
            points.extend(float(v) for v in values)
            if len(points) > 5001:
                raise ValueError('A scan may contain at most 5001 points')
            previous = b
    targets = [delay_to_mm(p, zero, sign) for p in points]
    # Positive-direction approach on every round. The extra travel is part of the plan.
    approach = targets[0] - sign * 0.01 if request.mode != 'single' else targets[0]
    if any(not 0 <= x <= 300 for x in targets + [approach]):
        raise ValueError('Scan or approach position exceeds simulated soft limits 0–300 mm')
    if len(set(round(x, 7) for x in targets)) != len(targets):
        raise ValueError('Different delays map to indistinguishable stage positions')
    rounds = request.repeats if request.mode == 'repeat' else 1
    frames = len(points) * rounds * request.frames_per_point
    # Also bound the Python metadata and browser recovery workload in this first release.
    if frames > 25000:
        raise ValueError('This release supports at most 25,000 frames per run')
    if not request.autosave and frames * (1024 * 4 + 2048) > 128 * 1024**2:
        raise ValueError('Memory-only run exceeds the 128 MiB estimate; enable autosave or reduce frames')
    move_distance = abs(targets[0] - delay_to_mm(current_delay, zero, sign))
    move_distance += sum(abs(b - a) for a, b in zip(targets, targets[1:]))
    seconds = rounds * (move_distance / 6 + len(points) * request.settle_s)
    seconds += frames * (request.exposure_s + 0.03)
    if rounds > 1:
        seconds += (rounds - 1) * (abs(targets[-1] - approach) + 0.01) / 6
    data = dict(delays_ps=points, targets_mm=targets, approach_mm=approach,
                rounds=rounds, total_points=len(points)*rounds, total_frames=frames,
                estimated_seconds=seconds, estimated_bytes=frames * (4096 + 2048),
                revision=revision, zero_mm=zero, delay_sign=sign, path_multiplier=2)
    data['plan_id'] = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:24]
    return data
