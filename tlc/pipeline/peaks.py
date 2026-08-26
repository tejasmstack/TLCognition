"""EMG peak fitting seeded by the ensemble (F11; spec 01 §5.1).

f(y) = A * (sigma/tau) * sqrt(pi/2) * exp(sigma^2/(2 tau^2) - (y-mu)/tau)
         * erfc((sigma/tau - (y-mu)/sigma)/sqrt(2)) + baseline
Position = mu (the Gaussian-component centre; build-wide convention D-006).

The covariance "everyone forgets" is corrected: residuals on a smoothed, correlated densitogram
are not white, so Cov = VIF * s^2 (J^T J)^-1 with VIF = 1 + 2 sum_k (1-k/n) rho_k over
K = ceil(3 FWHM) lags. Fit covariance is a COMPONENT of the variance budget, never the interval
(spec 01 §8 anti-pattern 4). A failed fit is tagged, never papered over with invented errors.
"""

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import least_squares
from scipy.special import erfcx

TAIL_RATIO_STREAK = 3.0   # spec 03 §7.3.4 E_STREAK: tau/sigma > 3.0


def emg(y: np.ndarray, amp: float, mu: float, sigma: float, tau: float, baseline: float = 0.0) -> np.ndarray:
    """Unit-peak-normalised EMG shape times amp, plus baseline (stable erfcx form)."""
    sigma = max(sigma, 1e-3)
    tau = max(tau, 1e-3)
    z = (y - mu) / sigma
    k = sigma / tau
    x = (k - z) / math.sqrt(2.0)
    if k > 20.0:  # tau << sigma: the EMG is a Gaussian to float precision; avoid exp overflow
        shape = np.exp(-0.5 * z * z)
    else:
        with np.errstate(over="ignore"):
            shape = np.where(
                x > -20.0,
                erfcx(np.maximum(x, -20.0)) * np.exp(-0.5 * z * z),
                2.0 * np.exp(np.minimum(0.5 * k * k - k * z, 700.0)),
            )
    peak = float(shape.max()) if shape.size else 1.0
    return amp * shape / max(peak, 1e-300) + baseline


@dataclass(frozen=True)
class EMGFit:
    ok: bool
    mu: float
    mode: float                  # argmax of the fitted curve, sub-pixel (D-014 position)
    sigma: float
    tau: float
    amp: float
    baseline: float
    mu_se: float | None          # VIF-corrected; None when the fit failed
    fwhm: float                  # numeric, from the fitted curve
    area: float                  # numeric integral of amp*shape over the window (OD*px)
    residual_rms: float
    vif: float
    n_points: int
    method: str                  # "emg_fit" | "matched_filter_seed" (fallback, tagged)


def _fwhm_numeric(y: np.ndarray, curve: np.ndarray) -> float:
    if curve.size < 3:
        return float("nan")
    half = 0.5 * float(curve.max())
    above = np.nonzero(curve >= half)[0]
    if above.size < 2:
        return float("nan")
    return float(y[above[-1]] - y[above[0]] + 1.0)


def fit_emg(
    profile: np.ndarray,
    seed_row: float,
    seed_fwhm: float,
    seed_amp: float,
    window_half: float | None = None,
) -> EMGFit:
    """Fit one EMG (+ flat baseline) to the profile around a seed. Bounded, deterministic."""
    n = profile.size
    sig0 = max(1.0, seed_fwhm / 2.355)
    half = window_half if window_half is not None else max(6.0, 3.0 * sig0 + 2.0 * sig0)
    lo, hi = int(max(0, math.floor(seed_row - half))), int(min(n, math.ceil(seed_row + half) + 1))
    y = np.arange(lo, hi, dtype=np.float64)
    seg = profile[lo:hi].astype(np.float64)
    if seg.size < 7:
        return EMGFit(False, seed_row, seed_row, sig0, 0.0, seed_amp, 0.0, None, seed_fwhm, float("nan"), float("nan"), 1.0, seg.size, "matched_filter_seed")

    lb = np.array([0.0, lo, 0.5, 1e-3, -abs(seg).max() - 1e-6])
    ub = np.array([max(3.0 * abs(seg).max(), 1e-3), hi - 1, 4.0 * sig0 + 2.0, 6.0 * sig0 + 2.0, abs(seg).max() + 1e-6])

    def resid(p):
        return emg(y, p[0], p[1], p[2], p[3], p[4]) - seg

    # Deterministic multi-start over the tail constant: the EMG cost surface has a symmetric
    # local optimum (small tau, inflated sigma) that swallows a genuinely tailing spot.
    sol = None
    for tau0 in (0.3 * sig0, 1.5 * sig0, 3.0 * sig0):
        p0 = np.clip(np.array([max(seed_amp, 1e-4), seed_row, sig0, tau0, float(np.median(seg))]), lb + 1e-9, ub - 1e-9)
        try:
            cand = least_squares(resid, p0, bounds=(lb, ub), method="trf", max_nfev=400, xtol=1e-10, ftol=1e-10)
        except Exception:
            continue
        if sol is None or cand.cost < sol.cost:
            sol = cand
    if sol is None:
        return EMGFit(False, seed_row, seed_row, sig0, 0.0, seed_amp, 0.0, None, seed_fwhm, float("nan"), float("nan"), 1.0, seg.size, "matched_filter_seed")
    gaussian_limit = False
    if sol.x[3] <= 0.15 * sol.x[2]:
        # tau at its floor: the tail is unresolvable, the 5-parameter Jacobian is degenerate.
        # Refit the 4-parameter Gaussian limit for an honest covariance.
        gaussian_limit = True

        def resid_g(p):
            return emg(y, p[0], p[1], p[2], 1e-3, p[3]) - seg

        pg0 = np.array([sol.x[0], sol.x[1], sol.x[2], sol.x[4]])
        lbg, ubg = lb[[0, 1, 2, 4]], ub[[0, 1, 2, 4]]
        try:
            solg = least_squares(resid_g, np.clip(pg0, lbg + 1e-9, ubg - 1e-9), bounds=(lbg, ubg), method="trf", max_nfev=400, xtol=1e-10, ftol=1e-10)
            solg.x = np.array([solg.x[0], solg.x[1], solg.x[2], 1e-3, solg.x[3]])
            sol = solg
        except Exception:
            pass
    amp, mu, sigma, tau, base = (float(v) for v in sol.x)
    r = sol.fun
    dof = max(seg.size - (4 if gaussian_limit else 5), 1)
    s2 = float((r @ r) / dof)

    # VIF from residual autocorrelation out to K = ceil(3 FWHM) lags (spec 01 §5.1)
    curve = emg(y, amp, mu, sigma, tau, 0.0)
    fwhm = _fwhm_numeric(y, curve)
    # mode: parabolic refinement around the argmax of the fitted curve on a 0.1 px grid
    yf = np.arange(lo, hi - 1 + 1e-9, 0.1)
    cf = emg(yf, amp, mu, sigma, tau, 0.0)
    mode = float(yf[int(np.argmax(cf))]) if cf.size else mu
    k_max = int(min(max(1, math.ceil(3 * (fwhm if np.isfinite(fwhm) else 2.355 * sigma))), seg.size - 2))
    rc = r - r.mean()
    denom = float(rc @ rc) or 1e-18
    vif = 1.0
    for k in range(1, k_max + 1):
        rho = float(rc[:-k] @ rc[k:]) / denom
        vif += 2.0 * (1.0 - k / seg.size) * rho
    vif = float(max(vif, 1.0))

    mu_se = None
    # a fit is only "ok" when there is signal to fit: amplitude above the residual noise and a
    # non-degenerate segment (an all-flat profile must never yield an "ok" peak)
    ok = bool(sol.success and amp > 0 and lo < mu < hi - 1 and (seg.max() - seg.min()) > 1e-9
              and amp >= 2.0 * math.sqrt(s2))
    if ok:
        jac = sol.jac
        try:
            cov = np.linalg.pinv(jac.T @ jac) * s2 * vif
            mu_se = float(math.sqrt(max(cov[1, 1], 0.0)))
            if not np.isfinite(mu_se) or mu_se > (hi - lo):
                ok = False  # degenerate covariance: the position is not pinned by this fit
        except np.linalg.LinAlgError:
            ok = False
    area = float(curve.sum())
    return EMGFit(
        ok=ok, mu=mu, mode=mode if ok else seed_row, sigma=sigma, tau=tau, amp=amp, baseline=base, mu_se=mu_se if ok else None,
        fwhm=fwhm, area=area, residual_rms=float(math.sqrt(s2)), vif=vif, n_points=int(seg.size),
        method=("gaussian_limit_fit" if gaussian_limit else "emg_fit") if ok else "matched_filter_seed",
    )
