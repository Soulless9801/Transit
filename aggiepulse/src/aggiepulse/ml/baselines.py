"""Required baselines, fit on the simulated training split only.

B0 persistence: the target (p90 load of the window t0+h) equals the p90 load
of the last completed window [t0-15m, t0). Rows with no completed window
(service start, missing APC) fall back to B1 and are counted.
B1 seasonal profile: route x direction x day-type x 15-min slot (of the target
window) mean of the training labels; unseen keys fall back to route x direction
x slot, then the training mean.
"""
import numpy as np
import pandas as pd

from aggiepulse.common.paths import LOCAL_TZ

PROFILE_KEYS = ['route', 'direction', 'day_type', 'slot']


def seasonal_keys(frame, horizon):
    # Day type is the t0 day's; a target slot just past local midnight keeps the prior day type.
    target = pd.to_datetime(frame.t0, utc=True).dt.tz_convert(LOCAL_TZ)+pd.Timedelta(minutes=horizon)
    return pd.DataFrame({'route': frame.route_short_name.astype(str).to_numpy(),
                         'direction': pd.to_numeric(frame.direction_id).astype(int).to_numpy(),
                         'day_type': frame.day_type.astype(str).to_numpy(),
                         'slot': (target.dt.hour*4+target.dt.minute//15).to_numpy()})


def _risk_bins(load):
    return np.floor(np.nan_to_num(np.asarray(load, dtype=float), nan=-.1)*20).astype(int)


class Baselines:
    def __init__(self, training, horizon):
        if not (training.split.eq('train').all() and training.data_origin.eq('simulated').all()):
            raise ValueError('Baselines fit the simulated train split only')
        self.horizon = horizon
        y = training[f'y_p90load_h{horizon}'].astype(float).to_numpy()
        crowd = training[f'y_crowd_h{horizon}'].astype(float).to_numpy()
        keep = np.isfinite(y)
        training, y, crowd = training[keep], y[keep], crowd[keep]
        self.mean, self.prior = float(y.mean()), float(np.nanmean(crowd))
        keys = seasonal_keys(training, horizon).assign(y=y, crowd=crowd)
        def profile(by):
            return keys.groupby(by).agg(p50=('y', 'mean'), p10=('y', lambda v: v.quantile(.1)),
                                        p90=('y', lambda v: v.quantile(.9)),
                                        p_crowd=('crowd', 'mean')).reset_index()
        self.profile = profile(PROFILE_KEYS)
        self.coarse = profile(['route', 'direction', 'slot'])
        # Persistence interval and crowd risk come from training residuals only.
        state = training.p90_load_t0.astype(float).to_numpy()
        has_state = np.isfinite(state)
        self.residual = np.quantile(y[has_state]-state[has_state], [.1, .9])
        bins = _risk_bins(state)
        self.risk = {int(b): (float(crowd[bins == b].sum())+20*self.prior)/(int((bins == b).sum())+20)
                     for b in np.unique(bins)}

    def b1(self, frame):
        keys = seasonal_keys(frame, self.horizon)
        fine = keys.merge(self.profile, on=PROFILE_KEYS, how='left')
        coarse = keys.merge(self.coarse, on=['route', 'direction', 'slot'], how='left')
        columns = ['p10', 'p50', 'p90', 'p_crowd']
        result = fine[columns].fillna(coarse[columns])
        return result.fillna({'p10': self.mean, 'p50': self.mean, 'p90': self.mean,
                              'p_crowd': self.prior}).reset_index(drop=True)

    def b0(self, frame):
        state = frame.p90_load_t0.astype(float).to_numpy()
        fallback = self.b1(frame)
        has_state = np.isfinite(state)
        p50 = np.where(has_state, state, fallback.p50)
        risk = np.array([self.risk.get(int(b), self.prior) for b in _risk_bins(state)])
        # Residual quantiles need not straddle zero; the interval must still contain p50.
        low = np.minimum(np.clip(state+self.residual[0], 0, 1.5), state)
        high = np.maximum(np.clip(state+self.residual[1], 0, 1.5), state)
        return pd.DataFrame({
            'p10': np.where(has_state, low, fallback.p10),
            'p50': p50,
            'p90': np.where(has_state, high, fallback.p90),
            'p_crowd': np.where(has_state, risk, fallback.p_crowd),
            'b0_fallback': ~has_state})

    def predict(self, frame, name):
        return self.b0(frame) if name == 'B0' else self.b1(frame)
