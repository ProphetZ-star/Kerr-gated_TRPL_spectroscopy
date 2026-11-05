"""Fits use observed records only; no access to simulation ground truth."""
import numpy as np
from .drivers.sim import convolved_decay


def fit_records(records, axis, options, cancel=None):
    """Fit a list of frame dicts (delay_ps, exposure_s, spectrum); see fit_frames."""
    from .storage.export import finite_number
    delays=[];spectra=[];exposures=[]
    for r in records:
        if not isinstance(r,dict) or not {'delay_ps','exposure_s','spectrum'}<=r.keys():raise ValueError('Fit record is missing required fields')
        delays.append(finite_number(r['delay_ps'],'delay_ps'));exposures.append(finite_number(r['exposure_s'],'exposure_s',True))
        spectra.append(np.asarray(r['spectrum'],dtype=float))
    if len({s.shape for s in spectra})>1:raise ValueError('Fit spectrum must match the axis and contain only finite counts')
    return fit_frames(np.asarray(delays,dtype=float),np.asarray(spectra,dtype=float).reshape(len(spectra),-1),np.asarray(exposures,dtype=float),axis,options,cancel)


def fit_frames(delays, spectra, exposure, axis, options, cancel=None):
    """Fit plain frame arrays: delays (N,), spectra (N, pixels) and exposure (scalar or (N,)), without the
    run's flags (no saturation, temperature or position checks); see fit_run for a run's frames."""
    from .models import FitRequest
    from .processing import kinetics
    options=FitRequest(**options.model_dump())
    delays=np.asarray(delays,dtype=float)
    block=dict(spectra=np.asarray(spectra),delay_ps=delays,index=np.zeros((len(delays),3),dtype=int),exposure_s=exposure)
    curve=kinetics(block,axis,options)
    return _fit_curve(np.asarray(curve['delays_ps'],dtype=float),np.asarray(curve['values'],dtype=float),options,cancel)


def fit_run(block, axis, options, background=None, full_scale=None, targets_mm=None, cancel=None, response=None):
    """Fit a run's frames (a FrameTable block) with the request's processing options.

    With two or more rounds the fit is repeated for each round on its own; the spread of those results gives a
    between-round standard error for every parameter (round_spread), next to the local Jacobian errors."""
    from .models import FitRequest
    from .processing import kinetics, processing_of
    options=FitRequest(**options.model_dump())
    curve=kinetics(block,axis,options,background,full_scale,targets_mm,response=response,cancel=cancel)
    result=_fit_curve(np.asarray(curve['delays_ps'],dtype=float),np.asarray(curve['values'],dtype=float),options,cancel)
    result.update(processing=dict(processing_of(options),response_corrected=response is not None),rounds_used=curve['rounds_used'],frames_used=curve['counts']['frames_used'],
                  excluded=dict(saturated_points=curve['counts']['saturated_points'] if options.exclude_saturated else 0,
                                unstable_frames=curve['counts']['unstable_frames'] if options.exclude_unstable else 0,
                                off_position_frames=curve['counts']['off_position_frames']))
    spread={};fits=[]
    if len(curve['rounds_used'])>=2:
        for single in curve['per_round']:
            if cancel is not None and cancel.is_set():raise ValueError('Fit cancelled')
            try:fits.append(_fit_curve(np.asarray(single['delays_ps'],dtype=float),np.asarray(single['values'],dtype=float),options,cancel)['parameters'])
            except ValueError as exc:
                if 'cancelled' in str(exc):raise
        if len(fits)>=2:
            spread={name:float(np.std([f[name] for f in fits],ddof=1)/np.sqrt(len(fits))) for name in result['parameters']}
    result.update(round_spread=spread or None,round_fits=len(fits))
    if len(curve['rounds_used'])>=2 and len(fits)<2:
        result['warnings'].append('Too few rounds could be fitted on their own to estimate a between-round error')
    return result


def _fit_curve(t, y, options, cancel=None):
    """Fit the averaged decay y(t) with the request's model (single or double exponential with a Gaussian IRF)."""
    from .numerics import least_squares
    if len(t) < (12 if options.model=='double' else 8) or len(t[t>0])<5:
        raise ValueError('Insufficient distinct delays for this model')
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
    initial=np.clip(initial,np.asarray(lower)+1e-8,np.asarray(upper)-1e-8)
    def objective(p):
        if cancel is not None and cancel.is_set():raise ValueError('Fit cancelled')
        return (predict(p)-y)/scale
    result=least_squares(objective,initial,bounds=(lower,upper),max_nfev=3000)  # Jacobian-scaled, as x_scale='jac'
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
    if options.model=='double' and (values['tau_2_ps']/values['tau_1_ps']<1.5 or min(values['amplitude_1'],values['amplitude_2'])/max(values['amplitude_1']+values['amplitude_2'],1e-12)<.05):
        warnings.append('The two lifetime components are not well separated or one has negligible amplitude; compare with a single-exponential fit')
    if condition>1e8 or np.any(result.active_mask):
        warnings.append('Ill-conditioned fit or parameter at bound; lifetime uncertainty is unreliable')
    return dict(model=options.model,parameters=values,standard_errors=standard_errors,
                uncertainty_method='local Jacobian approximation, heuristic count weights; not a confidence interval',
                irf_source='user-specified Gaussian width; not measured IRF',irf_fwhm_ps=options.irf_fwhm_ps,
                delays_ps=t.tolist(),observed=y.tolist(),predicted=predict(result.x).tolist(),
                residuals=(y-predict(result.x)).tolist(),warnings=warnings,
                converged=True,jacobian_condition=condition if np.isfinite(condition) else None)
