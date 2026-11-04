'''Per-pixel noise of the live spectrum, drawn on the Live spectrum's right axis.

While frames repeat under the same conditions (Live preview at a fixed delay), the noise is the standard
deviation of each pixel over the last frames: the scatter actually measured, pump fluctuations included.
During a scan every frame is at a new delay, so frame-to-frame scatter would mostly be the signal changing;
there, and until enough repeated frames have arrived, the noise is estimated from the one frame on screen.'''
from collections import deque
import numpy as np

FRAMES = 10          # frames in the rolling standard deviation
MIN_FRAMES = 3       # fewer than this and the single-frame estimate is used
SMOOTH_PIXELS = 5    # running mean over the per-pixel standard deviation (10 frames scatter it by about 24 %)
LOCAL_PIXELS = 31    # neighbourhood of the single-frame estimate (about ±13 % scatter)


def _running_mean(values, width):
    if width <= 1 or len(values) < width:
        return np.asarray(values, dtype=float)
    kernel = np.ones(width) / width
    padded = np.pad(np.asarray(values, dtype=float), (width // 2, width - 1 - width // 2), mode='edge')
    return np.convolve(padded, kernel, mode='valid')


def single_frame_noise(spectrum, width=LOCAL_PIXELS):
    '''Noise of one spectrum from its pixel-to-pixel scatter. The second difference x[i] - (x[i-1] + x[i+1]) / 2
    removes the smooth spectrum (a broad PL band leaves a negligible remainder) and has variance 1.5 sigma^2 for
    independent pixel noise; its local mean square over `width` pixels gives sigma.'''
    x = np.asarray(spectrum, dtype=float)
    if x.size < 3:
        return np.zeros_like(x)
    second = x[1:-1] - (x[:-2] + x[2:]) / 2
    sigma = np.sqrt(_running_mean(second ** 2, width) / 1.5)
    return np.concatenate([sigma[:1], sigma, sigma[-1:]])


class LiveNoise:
    def __init__(self, frames=FRAMES):
        self.frames = deque(maxlen=frames); self.key = None; self.version = None
    def reset(self):
        self.frames.clear(); self.key = None; self.version = None
    def add(self, version, spectrum, key):
        '''A preview frame; `key` holds what must stay the same for frames to be comparable.'''
        if key != self.key:
            self.frames.clear(); self.key = key; self.version = None
        if version is None or version == self.version:
            return
        self.version = version
        self.frames.append(np.asarray(spectrum, dtype=float))
    def sigma(self, spectrum, repeated=True):
        '''(sigma per pixel, the mean spectrum it belongs to, description). repeated is False for scan frames and
        picked run spectra.'''
        if repeated and len(self.frames) >= MIN_FRAMES and self.frames[-1].shape == np.shape(spectrum):
            stack = np.stack(self.frames)
            return (_running_mean(stack.std(axis=0, ddof=1), SMOOTH_PIXELS), stack.mean(axis=0),
                    f'Standard deviation of each pixel over the last {len(stack)} frames ({SMOOTH_PIXELS}-pixel running mean)')
        reason = ('during a scan each frame is at a new delay' if not repeated
                  else f'{len(self.frames)} of {MIN_FRAMES} repeated frames so far')
        return single_frame_noise(spectrum), np.asarray(spectrum, dtype=float), f'Estimated from this frame alone (pixel-to-pixel scatter; {reason})'


SIGNAL_FLOOR = 0.01  # relative noise is drawn only where the signal is at least this fraction of its peak


def relative_noise(sigma, mean, background=None):
    '''(sigma as a percentage of the signal per pixel, the signal in counts, how the signal was taken). The signal
    is the mean counts minus the recorded background, or without one minus the spectrum's baseline (its lowest
    5 % of pixels), so the detector offset does not count as signal. Pixels below SIGNAL_FLOOR of the peak signal
    are left out (NaN): there the ratio grows without bound and says nothing.'''
    mean = np.asarray(mean, dtype=float); sigma = np.asarray(sigma, dtype=float)
    if background is not None and len(background) == len(mean):
        signal = mean - np.asarray(background, dtype=float); basis = 'counts minus the recorded background'
    else:
        signal = mean - np.percentile(mean, 5); basis = 'counts above the spectrum baseline (lowest 5 % of pixels)'
    peak = np.nanmax(signal) if signal.size else 0.0
    if not np.isfinite(peak) or peak <= 0:
        return np.full_like(mean, np.nan), np.full_like(mean, np.nan), basis
    signal = np.where(signal >= SIGNAL_FLOOR * peak, signal, np.nan)
    with np.errstate(divide='ignore', invalid='ignore'):
        percent = 100.0 * sigma / signal
    return percent, signal, basis


def shot_limit(signal, electrons_per_count, read_noise_e=None, frames=1):
    '''The least noise photon statistics allow, in % of the signal: Poisson noise of the signal's photoelectrons
    plus the read noise, sqrt(N + r^2) / N for N = signal x electrons per count, and 1/sqrt(frames) of that for a
    mean of several frames. Light from many emitters cannot be quieter than this; None when the camera does not
    report its sensitivity.'''
    try:
        gain = float(electrons_per_count)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(gain) or gain <= 0:
        return None
    read = float(read_noise_e) if read_noise_e is not None and np.isfinite(float(read_noise_e)) else 0.0
    electrons = np.asarray(signal, dtype=float) * gain
    with np.errstate(divide='ignore', invalid='ignore'):
        return np.where(electrons > 0, 100.0 * np.sqrt(electrons + read ** 2) / electrons, np.nan) / np.sqrt(max(1, int(frames)))
