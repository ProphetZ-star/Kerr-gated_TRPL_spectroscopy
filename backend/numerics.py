"""NumPy-only replacements for the two SciPy routines the backend used.

log_ndtr      -- log of the standard normal CDF, accurate in both tails
least_squares -- bounded nonlinear least squares (projected Levenberg-Marquardt);
                 returns the fields of scipy.optimize.least_squares' result that
                 the lifetime fit reads (x, fun, jac, cost, success, message,
                 active_mask, nfev)
"""
import math
from types import SimpleNamespace

import numpy as np

_ERFC = np.frompyfunc(math.erfc, 1, 1)
_SQRT_HALF = math.sqrt(0.5)
_HALF_LOG_2PI = 0.5 * math.log(2.0 * math.pi)
_EPS = np.finfo(float).eps


def _erfc(values):
    # erfc underflowing to 0 far in the tail is exact enough here; never report it.
    with np.errstate(under='ignore'):
        return _ERFC(values).astype(float)


def log_ndtr(x):
    """log(Phi(x)) without overflow, underflow or floating-point warnings."""
    x = np.asarray(x, dtype=float)
    flat = np.atleast_1d(x).ravel()
    out = np.empty_like(flat)
    upper = flat > 0
    middle = (flat <= 0) & (flat > -30)
    lower = flat <= -30
    if upper.any():
        # Phi(x) = 1 - erfc(x/sqrt2)/2; log1p keeps the tiny tail term.
        out[upper] = np.log1p(-0.5 * _erfc(flat[upper] * _SQRT_HALF))
    if middle.any():
        out[middle] = np.log(0.5 * _erfc(-flat[middle] * _SQRT_HALF))
    if lower.any():
        # Asymptotic series; its first omitted term is below 1e-14 for x <= -30.
        z = flat[lower]
        inv = 1.0 / (z * z)
        series = inv * (-1.0 + inv * (3.0 + inv * (-15.0 + inv * 105.0)))
        out[lower] = -0.5 * z * z - np.log(-z) - _HALF_LOG_2PI + np.log1p(series)
    return out.reshape(x.shape) if x.ndim else float(out[0])


def _jacobian(fun, x, f0, lower, upper):
    # Forward differences ('2-point'), stepping inward when a bound is in the way.
    jac = np.empty((f0.size, x.size))
    for index in range(x.size):
        step = math.sqrt(_EPS) * max(1.0, abs(x[index]))
        if x[index] + step > upper[index]:
            step = -step
        shifted = x.copy()
        shifted[index] += step
        jac[:, index] = (np.asarray(fun(shifted), dtype=float) - f0) / (shifted[index] - x[index])
    return jac


def _active(x, gradient, lower, upper):
    tolerance = 1e-10 * np.maximum(1.0, np.abs(x))
    mask = np.zeros(x.size, dtype=int)
    mask[(x - lower <= tolerance) & (gradient > 0)] = -1
    mask[(upper - x <= tolerance) & (gradient < 0)] = 1
    return mask


def least_squares(fun, x0, bounds, max_nfev=3000, ftol=1e-8, xtol=1e-8, gtol=1e-8):
    """Minimise 0.5*sum(fun(x)**2) subject to bounds = (lower, upper).

    Levenberg-Marquardt with Jacobian-norm variable scaling (as x_scale='jac'),
    steps projected onto the box, and variables pinned at a bound left out of
    the step while the gradient pushes them outward. nfev counts residual
    evaluations only, not those spent on the finite-difference Jacobian."""
    lower = np.asarray(bounds[0], dtype=float)
    upper = np.asarray(bounds[1], dtype=float)
    x = np.clip(np.asarray(x0, dtype=float), lower, upper)
    f = np.asarray(fun(x), dtype=float)
    nfev = 1
    cost = 0.5 * float(f @ f)
    jac = _jacobian(fun, x, f, lower, upper)
    scale = np.maximum(np.linalg.norm(jac, axis=0), _EPS)
    damping = None
    nu = 2.0
    status, message = 0, 'The maximum number of function evaluations is exceeded.'
    while nfev < max_nfev:
        gradient = jac.T @ f
        active = _active(x, gradient, lower, upper)
        free = active == 0
        if np.max(np.abs(gradient[free] / scale[free]), initial=0.0) < gtol * max(1.0, math.sqrt(2.0 * cost)):
            status, message = 1, '`gtol` termination condition is satisfied.'
            break
        hessian = jac.T @ jac
        if damping is None:
            damping = 1e-3 * float(np.max(np.diag(hessian) / scale**2))
        step = np.zeros_like(x)
        system = hessian[np.ix_(free, free)] + damping * np.diag(scale[free] ** 2)
        try:
            step[free] = np.linalg.solve(system, -gradient[free])
        except np.linalg.LinAlgError:
            step[free] = np.linalg.lstsq(system, -gradient[free], rcond=None)[0]
        candidate = np.clip(x + step, lower, upper)
        taken = candidate - x
        if not np.any(taken):
            damping *= nu
            nu *= 2.0
            if damping > 1e16:
                status, message = 2, '`xtol` termination condition is satisfied.'
                break
            continue
        f_new = np.asarray(fun(candidate), dtype=float)
        nfev += 1
        cost_new = 0.5 * float(f_new @ f_new) if np.all(np.isfinite(f_new)) else math.inf
        predicted = -(float(gradient @ taken) + 0.5 * float(taken @ hessian @ taken))
        actual = cost - cost_new
        if actual > 0:
            ratio = actual / predicted if predicted > 0 else 0.0
            x, f, cost = candidate, f_new, cost_new
            jac = _jacobian(fun, x, f, lower, upper)
            scale = np.maximum(scale, np.linalg.norm(jac, axis=0))
            damping *= max(1.0 / 3.0, 1.0 - (2.0 * ratio - 1.0) ** 3)
            nu = 2.0
            if actual < ftol * cost and ratio > 0.25:
                status, message = 2, '`ftol` termination condition is satisfied.'
                break
            if np.linalg.norm(taken * scale) < xtol * (xtol + np.linalg.norm(x * scale)):
                status, message = 3, '`xtol` termination condition is satisfied.'
                break
        else:
            damping *= nu
            nu *= 2.0
            if damping > 1e16:
                status, message = 3, '`xtol` termination condition is satisfied.'
                break
    gradient = jac.T @ f
    return SimpleNamespace(x=x, fun=f, jac=jac, cost=cost, nfev=nfev, status=status, success=status > 0,
                           message=message, active_mask=_active(x, gradient, lower, upper))
