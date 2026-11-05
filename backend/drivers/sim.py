"""Synthetic optical Kerr gate and detector; all performance values are illustrative."""
import math
import threading
import time
import numpy as np
from ..readout import default_readout,normalize_readout,readout_shape,spectrum_full_scale,bin_full_spectrum


def convolved_decay(t, tau, sigma):
    from ..numerics import log_ndtr
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


# Fluctuations of a real pump-probe setup, so repeat rounds differ from each other as they do in the lab.
# They scale the photoluminescence only; the ambient background and the detector noise are unaffected.
LASER_JITTER = 0.04            # rms frame-to-frame pump intensity
LASER_DRIFT = 0.06             # amplitude of the slow pump power drift
DRIFT_PERIODS = (173.0, 541.0) # frames per cycle of the two drift components
TIME_JITTER_PS = 0.03          # rms time-zero jitter per frame


# Detection response of the simulated setup (CCD QE x grating x optics): highest at 650 nm, about a quarter at
# 500 and 800 nm. It shapes every simulated photon; the response correction should take it back out.
SIM_RESPONSE_CENTER_NM = 650.0
SIM_RESPONSE_WIDTH_NM = 90.0
SIM_LAMP_TEMPERATURE_K = 3000.0
SIM_LAMP_RATE = 60000.0  # photoelectrons / s at the lamp's brightest pixel


def sim_response(axis):
    return np.exp(-.5 * ((np.asarray(axis, dtype=float) - SIM_RESPONSE_CENTER_NM) / SIM_RESPONSE_WIDTH_NM) ** 2)


def lamp_spectrum(axis, temperature=SIM_LAMP_TEMPERATURE_K):
    '''Relative spectral radiance of a blackbody lamp (Planck), 1 at its maximum on the axis.'''
    wavelength = np.asarray(axis, dtype=float) * 1e-9
    radiance = 1.0 / (wavelength ** 5 * np.expm1(1.438777e-2 / (wavelength * temperature)))
    return radiance / radiance.max()


def laser_drift(seed, record_id):
    """Slow pump power factor at one frame: two incommensurate cycles with a phase set by the seed."""
    phases = np.random.default_rng(np.random.SeedSequence([seed, 7919])).uniform(0, 2 * math.pi, 2)
    wave = .6 * math.sin(2 * math.pi * record_id / DRIFT_PERIODS[0] + phases[0]) + .4 * math.sin(2 * math.pi * record_id / DRIFT_PERIODS[1] + phases[1])
    return 1 + LASER_DRIFT * wave


class SimCamera:
    pixels = 1024
    full_scale = 65535
    source_mode = 'simulation'

    rows = 255
    temperature_range = (-100, -10)
    # The detector model in acquire(): counts = electrons / electrons_per_count + 320, read noise 5 electrons rms;
    # 2.5 electrons per count at pre-amp gain 1x, fewer at the higher gains (as on the Newton).
    BASE_ELECTRONS_PER_COUNT = 2.5
    preamp_gains = [1.0, 2.0, 4.0]
    # Horizontal readout speeds (MHz) as on the Newton; slower reads with less noise (same ratios as its datasheet,
    # scaled to this model's 5 e- at 3 MHz).
    readout_speeds = [3.0, 1.0, 0.05]
    READ_NOISE_BY_SPEED = (5.0, 4.0, 1.3)
    # Thermal model (illustrative, close to an air-cooled Newton): the cooler pulls the chip down at a steady rate,
    # it reports "not stabilized" for a while after reaching the target, and with the cooler off the chip drifts
    # back to room temperature more slowly. Room temperature to -60 C takes about 4 minutes; -60 C back above
    # -20 C about 4 minutes, and to room temperature about 8.
    AMBIENT_C = 20.0
    COOL_RATE_C_PER_S = 20.0 / 60
    WARM_RATE_C_PER_S = 10.0 / 60
    SETTLE_S = 30.0

    def __init__(self):
        self.axis = np.linspace(500, 800, self.pixels)
        self.exposure_s = 1.0
        self.readout = default_readout(self.rows)
        self.cooler_on = True
        self.target_c = -70
        self.preamp_index = 0
        self.speed_index = 0
        # Starts cooled and stable, as a camera that was left cooling; changes then take time.
        self._thermal_lock = threading.Lock()
        self._temperature = float(self.target_c)
        self._thermal_time = time.monotonic()
        self._reached_at = self._thermal_time - self.SETTLE_S

    def temperature(self):
        '''(temperature_c, state) now, advancing the thermal model from the last reading.'''
        with self._thermal_lock:
            now = time.monotonic(); elapsed = max(0.0, now - self._thermal_time); self._thermal_time = now
            goal = float(self.target_c) if self.cooler_on else self.AMBIENT_C
            value = self._temperature
            if value > goal:
                value = max(goal, value - (self.COOL_RATE_C_PER_S if self.cooler_on else self.WARM_RATE_C_PER_S) * elapsed)
            elif value < goal:
                value = min(goal, value + self.WARM_RATE_C_PER_S * elapsed)
            if value == goal and self._temperature != goal:
                self._reached_at = now
            self._temperature = value
            if not self.cooler_on:
                return value, 'off'
            if value != goal:
                return value, 'not_reached'
            return value, 'stabilized' if now - self._reached_at >= self.SETTLE_S else 'not_stabilized'

    @property
    def electrons_per_count(self):
        return self.BASE_ELECTRONS_PER_COUNT / self.preamp_gains[self.preamp_index]

    @property
    def read_noise_e(self):
        return self.READ_NOISE_BY_SPEED[self.speed_index]

    @property
    def readout_speed_mhz(self):
        return self.readout_speeds[self.speed_index]

    def set_readout_speed(self, index):
        if not 0 <= index < len(self.readout_speeds):
            raise ValueError(f'Readout speed index must be 0…{len(self.readout_speeds)-1}')
        self.speed_index = int(index)
        return dict(index=self.speed_index, mhz=self.readout_speed_mhz, speeds=list(self.readout_speeds),
                    gain=self.preamp_gains[self.preamp_index])

    def set_preamp_gain(self, index):
        if not 0 <= index < len(self.preamp_gains):
            raise ValueError(f'Pre-amp gain index must be 0…{len(self.preamp_gains)-1}')
        self.preamp_index = int(index)
        return dict(index=self.preamp_index, gain=self.preamp_gains[self.preamp_index], gains=list(self.preamp_gains))

    @property
    def temperature_c(self):
        return self.temperature()[0]

    def capabilities(self):
        temperature, state = self.temperature()
        return dict(model='Andor Newton DU920P (simulated)', shape=[255,1024],
                    pixels=readout_shape(self.readout)[1], chip_pixels=self.pixels, detector_rows=self.rows,
                    bit_depth=16, full_scale=spectrum_full_scale(self.readout),
                    read_modes=['fvb'], source_mode=self.source_mode,
                    temperature_c=temperature, temperature_state=state,
                    cooler_on=self.cooler_on, target_temperature_c=self.target_c, temperature_range_c=list(self.temperature_range),
                    temperature_resolution_c=None, serial_number=None,
                    electrons_per_count=self.electrons_per_count, read_noise_e=self.read_noise_e,
                    preamp_gains=list(self.preamp_gains), preamp_gain_index=self.preamp_index,
                    preamp_gain=self.preamp_gains[self.preamp_index], sensitivity_source='model', head_model=None,
                    readout_speeds_mhz=list(self.readout_speeds), readout_speed_index=self.speed_index,
                    readout_speed_mhz=self.readout_speed_mhz,
                    readout=dict(self.readout), position_sensor=False)

    def set_cooling(self, enabled, target_c):
        low, high = self.temperature_range
        target = int(round(max(low, min(high, target_c))))
        self.temperature()  # account for the time spent under the previous setting
        with self._thermal_lock:
            if self.cooler_on and bool(enabled) and target != self.target_c:
                self._reached_at = time.monotonic()
            self.cooler_on = bool(enabled); self.target_c = target
        return dict(enabled=self.cooler_on, target_c=target, requested_c=target_c, clamped=target != round(target_c), range_c=[low, high])

    def set_readout(self, data):
        self.readout, notes = normalize_readout(data, self.rows)
        return self.readout, notes

    def acquire(self, delay_ps, seed, record_id, exposure_s, center_nm=600,
                fault='none', dark=False, optical_background=False, lamp=False):
        try:valid=not isinstance(exposure_s,(bool,np.bool_)) and math.isfinite(exposure_s) and exposure_s>0
        except (TypeError,OverflowError):valid=False
        if not valid:raise ValueError('Exposure must be finite and positive')
        rng = np.random.default_rng(np.random.SeedSequence([seed, record_id]))
        sigma = 0.25 / (2 * math.sqrt(2 * math.log(2)))
        delay = delay_ps + rng.normal(0, TIME_JITTER_PS)
        gain = max(0.0, 1 + rng.normal(0, LASER_JITTER)) * laser_drift(seed, record_id)
        temporal = .62*convolved_decay(delay,.42,sigma)+.38*convolved_decay(delay,3.8,sigma)
        spectral = np.exp(-.5*((self.axis-center_nm)/29)**2)
        signal_rate = 120000 * gain * spectral * temporal
        if dark or optical_background:
            signal_rate = np.zeros_like(spectral)
        if lamp:
            signal_rate = SIM_LAMP_RATE * lamp_spectrum(self.axis)
        optical_rate = np.full(self.pixels, 35.0 if not dark else 0.0)
        # Light (signal, lamp, stray light) passes the detection response; bias and read noise do not.
        response = sim_response(self.axis)
        signal_rate = signal_rate * response; optical_rate = optical_rate * response
        if fault == 'drift':
            signal_rate *= 1 + record_id * 0.002
        if fault == 'saturation':
            signal_rate *= 1000
        electrons = rng.poisson(np.maximum(0,(signal_rate+optical_rate+255*.0003)*exposure_s))
        adu = (electrons + rng.normal(0, self.read_noise_e, self.pixels))/self.electrons_per_count+320
        if fault == 'cosmic_ray':
            adu[500] += 45000
        saturated = int(np.count_nonzero(adu >= self.full_scale))
        counts = bin_full_spectrum(np.rint(np.clip(adu,0,self.full_scale)), self.readout, self.rows)
        return np.clip(counts, 0, spectrum_full_scale(self.readout)).astype(np.int32), saturated


class SimStage:
    def __init__(self):
        self.position_mm = 150.0
        self.velocity_mm_s = 6.0
        self.max_velocity_mm_s = 6.0
        self.acceleration_mm_s2 = 12.0
        self.referenced = True
        self.lower, self.upper = 0.0, 300.0

    def set_velocity(self, requested):
        if not math.isfinite(requested) or requested <= 0:
            raise ValueError('Velocity must be a positive number')
        self.velocity_mm_s = min(requested, self.max_velocity_mm_s)
        return dict(velocity_mm_s=self.velocity_mm_s, requested_mm_s=requested,
                    max_velocity_mm_s=self.max_velocity_mm_s, clamped=self.velocity_mm_s < requested)

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
