"""Synthetic optical Kerr gate and detector; all performance values are illustrative."""
import math
import numpy as np


def convolved_decay(t, tau, sigma):
    from scipy.special import log_ndtr
    try:
        if isinstance(tau,(bool,np.bool_)) or isinstance(sigma,(bool,np.bool_)):raise ValueError()
        t=np.asarray(t,dtype=float);tau=float(tau);sigma=float(sigma)
    except (TypeError,ValueError,OverflowError) as exc:raise ValueError('Time, lifetime and sigma must be finite numeric values') from exc
    if not np.isfinite(t).all() or not math.isfinite(tau) or not math.isfinite(sigma) or tau<=0 or sigma<=0:
        raise ValueError('Time must be finite; lifetime and Gaussian sigma must be finite and positive')
    # Stable analytical Gaussian convolution of H(t)*exp(-t/tau).
    try:
        with np.errstate(over='raise',divide='raise',invalid='raise'):
            logvalue = sigma**2 / (2*tau**2) - t/tau + log_ndtr(t/sigma - sigma/tau)
    except (FloatingPointError,OverflowError,ZeroDivisionError) as exc:raise ValueError('Lifetime or IRF scale is outside the numerical range') from exc
    return np.exp(np.clip(logvalue, -745, 50))


class SimCamera:
    pixels = 1024
    full_scale = 65535
    source_mode = 'simulation'

    def __init__(self):
        self.axis = np.linspace(500, 800, self.pixels)
        self.exposure_s = 1.0

    def capabilities(self):
        return dict(model='Andor Newton DU920P (simulated)', shape=[255,1024],
                    pixels=self.pixels, bit_depth=16, full_scale=self.full_scale,
                    read_modes=['fvb'], source_mode=self.source_mode,
                    temperature_c=-70.0, position_sensor=False)

    def acquire(self, delay_ps, seed, record_id, exposure_s, center_nm=600,
                fault='none', dark=False, optical_background=False):
        try:valid=not isinstance(exposure_s,(bool,np.bool_)) and math.isfinite(exposure_s) and exposure_s>0
        except (TypeError,OverflowError):valid=False
        if not valid:raise ValueError('Exposure must be finite and positive')
        rng = np.random.default_rng(np.random.SeedSequence([seed, record_id]))
        sigma = 0.25 / (2 * math.sqrt(2 * math.log(2)))
        temporal = .62*convolved_decay(delay_ps,.42,sigma)+.38*convolved_decay(delay_ps,3.8,sigma)
        spectral = np.exp(-.5*((self.axis-center_nm)/29)**2)
        signal_rate = 120000 * spectral * temporal
        if dark or optical_background:
            signal_rate = np.zeros_like(spectral)
        optical_rate = np.full(self.pixels, 35.0 if not dark else 0.0)
        if fault == 'drift':
            signal_rate *= 1 + record_id * 0.002
        if fault == 'saturation':
            signal_rate *= 1000
        electrons = rng.poisson(np.maximum(0,(signal_rate+optical_rate+255*.0003)*exposure_s))
        adu = (electrons + rng.normal(0, 5, self.pixels))/2.5+320
        if fault == 'cosmic_ray':
            adu[500] += 45000
        saturated = int(np.count_nonzero(adu >= self.full_scale))
        return np.rint(np.clip(adu,0,self.full_scale)).astype(np.int32), saturated


class SimStage:
    def __init__(self):
        self.position_mm = 150.0
        self.velocity_mm_s = 6.0
        self.acceleration_mm_s2 = 12.0

    def duration(self, target):
        if not 0 <= target <= 300:
            raise ValueError('Stage soft limit exceeded')
        distance = abs(target-self.position_mm)
        ramp = self.velocity_mm_s**2/self.acceleration_mm_s2
        return (2*math.sqrt(distance/self.acceleration_mm_s2) if distance < ramp
                else 2*self.velocity_mm_s/self.acceleration_mm_s2+(distance-ramp)/self.velocity_mm_s)

    def partial_position(self, target, elapsed_s):
        distance=abs(target-self.position_mm)
        duration=self.duration(target)
        t=max(0,min(elapsed_s,duration))
        ramp=min(self.velocity_mm_s/self.acceleration_mm_s2, math.sqrt(distance/self.acceleration_mm_s2))
        cruise=duration-2*ramp
        if t<=ramp:
            travelled=.5*self.acceleration_mm_s2*t*t
        elif t<=ramp+cruise:
            travelled=.5*self.acceleration_mm_s2*ramp*ramp+self.acceleration_mm_s2*ramp*(t-ramp)
        else:
            travelled=distance-.5*self.acceleration_mm_s2*(duration-t)**2
        return self.position_mm+(1 if target>=self.position_mm else -1)*travelled
