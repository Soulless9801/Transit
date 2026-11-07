"""Metrics by horizon x data_origin (and route, hour); observed never pooled with simulated."""
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss, mean_pinball_loss

from aggiepulse.common.paths import LOCAL_TZ

MIN_CONCLUSIVE_N = 100
METRICS = ['mae', 'rmse', 'pinball_p10', 'pinball_p50', 'pinball_p90', 'coverage_p10_p90',
           'pr_auc', 'brier']
SPLITS = [('simulated', 'test'), ('observed', 'observed_holdout')]


def metrics(frame, prediction, horizon):
    y = frame[f'y_p90load_h{horizon}'].astype(float).to_numpy()
    crowd = frame[f'y_crowd_h{horizon}'].astype(float).to_numpy()
    valid, binary = np.isfinite(y), np.isfinite(crowd)
    result = {'n': int(valid.sum()), 'n_crowd': int(binary.sum()),
              'positives': int(np.nansum(crowd[binary])) if binary.any() else 0,
              **{key: None for key in METRICS}}
    if valid.any():
        error = y[valid]-prediction.p50.to_numpy()[valid]
        low, high = prediction.p10.to_numpy()[valid], prediction.p90.to_numpy()[valid]
        result.update(mae=float(np.abs(error).mean()), rmse=float(np.sqrt(np.mean(error**2))),
                      coverage_p10_p90=float(((y[valid] >= low) & (y[valid] <= high)).mean()))
        for q in (10, 50, 90):
            result[f'pinball_p{q}'] = float(mean_pinball_loss(
                y[valid], prediction[f'p{q}'].to_numpy()[valid], alpha=q/100))
    if binary.any():
        p = prediction.p_crowd.to_numpy()[binary]
        result['brier'] = float(brier_score_loss(crowd[binary], p))
        if len(np.unique(crowd[binary])) == 2:
            result['pr_auc'] = float(average_precision_score(crowd[binary], p))
    if result['n'] < MIN_CONCLUSIVE_N:
        result['conclusion'] = f'n={result["n"]}: too small to conclude anything'
    elif frame.data_origin.eq('simulated').all():
        result['conclusion'] = 'simulated-prior evaluation (not evidence about real riders)'
    else:
        result['conclusion'] = 'observed evaluation'
    return result


def evaluate(frame, predictions, horizon, groups=True):
    """``predictions`` maps model name -> frame aligned row-for-row with ``frame``."""
    frame = frame.reset_index(drop=True)
    rows = []
    for origin, split in SPLITS:
        mask = (frame.data_origin.eq(origin) & frame.split.eq(split)).to_numpy()
        subset = frame[mask].reset_index(drop=True)
        hours = pd.to_datetime(subset.t0, utc=True).dt.tz_convert(LOCAL_TZ).dt.hour
        for name, prediction in predictions.items():
            pred = prediction.reset_index(drop=True)[mask].reset_index(drop=True)
            rows.append({'model': name, 'horizon': horizon, 'origin': origin, 'split': split,
                         'group': 'all', **metrics(subset, pred, horizon)})
            if not groups or subset.empty:
                continue
            for kind, values in [('route', subset.route_short_name.astype(str)), ('hour', hours)]:
                for value in sorted(values.unique()):
                    selected = values.eq(value).to_numpy()
                    rows.append({'model': name, 'horizon': horizon, 'origin': origin, 'split': split,
                                 'group': f'{kind}:{value}', **metrics(subset[selected], pred[selected], horizon)})
    return rows


def headline(table):
    """Compact model x horizon x origin table with n, for the report and model card."""
    columns = ['model', 'horizon', 'origin', 'n', 'mae', 'rmse', 'pinball_p50', 'coverage_p10_p90',
               'positives', 'pr_auc', 'brier', 'conclusion']
    return (table[table.group.eq('all')][columns]
            .sort_values(['origin', 'horizon', 'model'], ascending=[False, True, True])
            .reset_index(drop=True))
