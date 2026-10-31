"""Fits use observed records only; no access to simulation ground truth."""
import numpy as np
from .drivers.sim import convolved_decay


def fit_records(records, axis, options):
    from scipy.optimize import least_squares
    if axis is None:
        raise ValueError('A valid wavelength calibration is required')
    wavelengths = np.asarray(axis)
    mask = abs(wavelengths-options.wavelength_nm) <= options.half_window_nm
    if not mask.any():
        raise ValueError('No pixels in the selected wavelength window')
    groups = {}
    for r in records:
        if r['saturated_pixels']:
            raise ValueError('Saturated records must be excluded before lifetime fitting')
        t = r['delay_ps']
        if options.min_delay_ps is not None and t < options.min_delay_ps:
            continue
        if options.max_delay_ps is not None and t > options.max_delay_ps:
            continue
        groups.setdefault(t,[]).append(float(np.mean(np.asarray(r['spectrum'])[mask]))/r['exposure_s'])
    t = np.array(sorted(groups))
    if len(t) < (12 if options.model=='double' else 8) or len(t[t>0])<5:
        raise ValueError('Insufficient distinct delays for this model')
    y = np.array([np.mean(groups[x]) for x in t])
    sigma = options.irf_fwhm_ps/2.354820045
    amplitude = max(float(np.ptp(y)),1)
    baseline = float(np.min(y))
    scale = np.sqrt(np.maximum(abs(y),1))
    if options.model=='double':
        initial=[.6*amplitude,.4*amplitude,.6,4,0,baseline]
        lower=[0,0,.01,.02,-1,-1e6];upper=[1e8,1e8,100,100,1,1e6]
        def predict(p):
            a,b,tau1,tau2,t0,c=p
            return a*convolved_decay(t-t0,tau1,sigma)+b*convolved_decay(t-t0,tau2,sigma)+c
        names=['amplitude_1','amplitude_2','tau_1_ps','tau_2_ps','t0_ps','offset_counts_s']
    else:
        initial=[amplitude,2,0,baseline]
        lower=[0,.01,-1,-1e6];upper=[1e8,100,1,1e6]
        def predict(p):
            a,tau,t0,c=p
            return a*convolved_decay(t-t0,tau,sigma)+c
        names=['amplitude','tau_ps','t0_ps','offset_counts_s']
    result=least_squares(lambda p:(predict(p)-y)/scale,initial,bounds=(lower,upper),max_nfev=3000,x_scale='jac')
    if not result.success:
        raise ValueError('Fit did not converge: '+result.message)
    covariance=np.linalg.pinv(result.jac.T@result.jac)*np.sum(result.fun**2)/max(1,len(t)-len(initial))
    errors=np.sqrt(np.maximum(np.diag(covariance),0))
    values=dict(zip(names,map(float,result.x)))
    standard_errors=dict(zip(names,map(float,errors)))
    if options.model=='double' and values['tau_1_ps']>values['tau_2_ps']:
        for a,b in [('tau_1_ps','tau_2_ps'),('amplitude_1','amplitude_2')]:
            values[a],values[b]=values[b],values[a]
            standard_errors[a],standard_errors[b]=standard_errors[b],standard_errors[a]
    condition=float(np.linalg.cond(result.jac))
    warnings=[]
    if condition>1e8 or np.any(result.active_mask):
        warnings.append('Ill-conditioned fit or parameter at bound; lifetime uncertainty is unreliable')
    return dict(model=options.model,parameters=values,standard_errors=standard_errors,
                uncertainty_method='local Jacobian approximation, heuristic count weights; not a confidence interval',
                irf_source='user-specified Gaussian width; not measured IRF',irf_fwhm_ps=options.irf_fwhm_ps,
                processing='raw counts divided by exposure; constant baseline fitted',
                delays_ps=t.tolist(),observed=y.tolist(),predicted=predict(result.x).tolist(),
                residuals=(y-predict(result.x)).tolist(),warnings=warnings,
                converged=True,jacobian_condition=condition if np.isfinite(condition) else None)
