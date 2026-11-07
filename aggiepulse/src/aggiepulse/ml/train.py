"""Fit per-horizon LightGBM models with forward (chronological) selection.

Hyperparameters and the number of trees are chosen on the validation split
(which follows the training split in time); final models are then fit on the
training split only, the same data the baselines see. Test and observed rows
never fit or select anything.
"""
import os
import warnings

import lightgbm as lgb
import mlflow
import numpy as np
import optuna
import pandas as pd

from aggiepulse.common.paths import LOCAL_TZ
from aggiepulse.ml.baselines import Baselines
from aggiepulse.ml.evaluate import evaluate
from aggiepulse.ml.model import CATEGORICAL, CrowdingModel, feature_columns

HORIZONS = (15, 30, 60)
QUANTILES = (.1, .5, .9)
MAX_TREES = 800
MAX_POS_WEIGHT = 25.
BASE = {'verbosity': -1, 'random_state': 42, 'n_jobs': min(8, os.cpu_count() or 4),
        'force_col_wise': True, 'subsample_freq': 1}


def split_rows(frame, split, horizon):
    target = f'y_p90load_h{horizon}'
    return frame[frame.split.eq(split) & frame.data_origin.eq('simulated')
                 & frame[target].notna()].reset_index(drop=True)


def _params(trial):
    return {'learning_rate': trial.suggest_float('learning_rate', .04, .15, log=True),
            'num_leaves': trial.suggest_int('num_leaves', 15, 127, log=True),
            'min_child_samples': trial.suggest_int('min_child_samples', 20, 400, log=True),
            'reg_lambda': trial.suggest_float('reg_lambda', .01, 30, log=True),
            'colsample_bytree': trial.suggest_float('colsample_bytree', .5, 1.),
            'subsample': trial.suggest_float('subsample', .6, 1.)}


def _fit(estimator, x, y, xv, yv):
    # The estimator's own ``metric`` (quantile loss or average precision) drives early stopping.
    warnings.filterwarnings('ignore', message=".*'eval_set' is deprecated.*")
    estimator.fit(x, y, eval_set=[(xv, yv)], callbacks=[lgb.early_stopping(50, verbose=False)])
    return estimator


def fit_horizon(frame, horizon, trials=15, metadata=None, season=None, log=True):
    target, crowd = f'y_p90load_h{horizon}', f'y_crowd_h{horizon}'
    train, valid = split_rows(frame, 'train', horizon), split_rows(frame, 'validation', horizon)
    if train.empty or valid.empty:
        raise ValueError(f'Need chronological train and validation labels for h{horizon}')
    columns = feature_columns(frame)
    categories = {c: sorted(train[c].dropna().astype('string').unique().tolist())
                  for c in columns if c in CATEGORICAL}
    if 'direction_id' in categories:
        categories['direction_id'] = sorted({str(int(float(v))) for v in categories['direction_id']})
    model = CrowdingModel(columns, categories, horizon, metadata)
    model.season = season
    x, xv = model.encode(train), model.encode(valid)
    y, yv = train[target].astype(float), valid[target].astype(float)

    def objective(trial):
        params = _params(trial)
        estimator = _fit(lgb.LGBMRegressor(objective='quantile', alpha=.5, n_estimators=MAX_TREES,
                                           metric='quantile', **BASE, **params), x, y, xv, yv)
        mae = float(np.mean(np.abs(yv-estimator.predict(xv))))
        trial.set_user_attr('best_iteration', int(estimator.best_iteration_ or MAX_TREES))
        if log:
            with mlflow.start_run(run_name=f'h{horizon}_trial_{trial.number:02d}', nested=True):
                mlflow.log_params({'horizon': horizon, **params})
                mlflow.log_metrics({'validation_mae': mae,
                                    'best_iteration': trial.user_attrs['best_iteration']})
        return mae

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(direction='minimize', sampler=optuna.samplers.TPESampler(seed=42))
    study.optimize(objective, n_trials=max(1, min(20, trials)))
    best = dict(study.best_params)
    trees = study.best_trial.user_attrs['best_iteration']
    for alpha in QUANTILES:
        # Tree count per quantile is chosen on validation, then fit on train only.
        probe = _fit(lgb.LGBMRegressor(objective='quantile', alpha=alpha, n_estimators=MAX_TREES,
                                       metric='quantile', **BASE, **best), x, y, xv, yv)
        n = int(probe.best_iteration_ or trees)
        model.quantiles.append(lgb.LGBMRegressor(objective='quantile', alpha=alpha, n_estimators=n,
                                                 **BASE, **best).fit(x, y))
    label, vlabel = train[crowd].astype(float), valid[crowd].astype(float)
    positives = int(label.sum())
    if label.nunique() < 2:
        model.classifier = float(label.mean())
    else:
        # Bounded positive up-weighting for the rare class, undone at inference.
        model.probability_scale = float(min(MAX_POS_WEIGHT, max(1., (len(label)-positives)/positives)))
        settings = dict(objective='binary', scale_pos_weight=model.probability_scale, **BASE, **best)
        # Weighted training inflates probabilities, so stop on ranking (AP), not log loss.
        probe = _fit(lgb.LGBMClassifier(n_estimators=MAX_TREES, metric='average_precision', **settings),
                     x, label, xv, vlabel)
        model.classifier = lgb.LGBMClassifier(n_estimators=int(probe.best_iteration_ or trees),
                                              **settings).fit(x, label)
    summary = {'best_params': best, 'best_validation_mae': float(study.best_value),
               'quantile_trees': [m.n_estimators for m in model.quantiles],
               'classifier_trees': getattr(model.classifier, 'n_estimators', None),
               'train_n': len(train), 'validation_n': len(valid), 'train_positives': positives,
               'validation_positives': int(vlabel.sum()), 'scale_pos_weight': model.probability_scale,
               'features': len(columns), 'trials': len(study.trials)}
    model.metadata.update(training_summary=summary)
    return model, Baselines(frame[frame.split.eq('train') & frame.data_origin.eq('simulated')], horizon), summary


def holdout(frame):
    return frame[frame.split.isin(['test', 'observed_holdout'])].reset_index(drop=True)


def predict_all(frame, model, baselines):
    return {'B0': baselines.predict(frame, 'B0'), 'B1': baselines.predict(frame, 'B1'),
            'LightGBM': model.forecast(frame, explain=False)}


def evaluate_models(frame, models, baselines):
    rows = []
    test = holdout(frame)
    for horizon, model in models.items():
        rows.extend(evaluate(test, predict_all(test, model, baselines[horizon]), horizon))
    return pd.DataFrame(rows)


def promotion_gate(table, horizon=30):
    """LightGBM is champion only if it beats B1 on simulated-test MAE at 30 min."""
    gate = table[table.horizon.eq(horizon) & table.origin.eq('simulated') & table.group.eq('all')]
    gate = gate.set_index('model')
    lgbm, b1 = float(gate.loc['LightGBM', 'mae']), float(gate.loc['B1', 'mae'])
    return {'promote': bool(lgbm < b1), 'lightgbm_mae': lgbm, 'b1_mae': b1,
            'b0_mae': float(gate.loc['B0', 'mae']), 'n': int(gate.loc['LightGBM', 'n']),
            'rule': f'LightGBM simulated-test MAE < B1 at h{horizon}'}


def log_plots(frame, model, horizon):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    target, crowd = f'y_p90load_h{horizon}', f'y_crowd_h{horizon}'
    test = split_rows(frame, 'test', horizon)
    if test.empty:
        return
    prediction = model.forecast(test, explain=False)
    hour = pd.to_datetime(test.t0, utc=True).dt.tz_convert(LOCAL_TZ).dt.hour
    residual = pd.DataFrame({'hour': hour, 'residual': test[target].to_numpy()-prediction.p50.to_numpy()})
    fig, ax = plt.subplots(figsize=(8, 4))
    stats = residual.groupby('hour').residual.agg(['mean', lambda r: r.abs().mean()])
    stats.columns = ['mean residual (bias)', 'MAE']
    stats.plot.bar(ax=ax)
    ax.axhline(0, color='black', lw=.8)
    ax.set(xlabel='local hour of t0', ylabel='load factor',
           title=f'Simulated test residuals by hour · h{horizon} (actual − p50)')
    fig.tight_layout()
    mlflow.log_figure(fig, f'plots/h{horizon}_residuals_by_hour.png')
    plt.close(fig)

    sample = test.sample(min(len(test), 3000), random_state=0)
    contrib = model.quantiles[1].booster_.predict(model.encode(sample), pred_contrib=True)[:, :-1]
    importance = pd.Series(np.abs(contrib).mean(axis=0), index=model.columns).nlargest(20).sort_values()
    fig, ax = plt.subplots(figsize=(7, 6))
    importance.plot.barh(ax=ax)
    ax.set(xlabel='mean |SHAP contribution| (load factor)', title=f'SHAP summary, p50 model · h{horizon}')
    fig.tight_layout()
    mlflow.log_figure(fig, f'plots/h{horizon}_shap_summary.png')
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(5, 5))
    ax.plot([0, 1], [0, 1], ls='--', color='grey')
    for origin, split in [('simulated', 'test'), ('observed', 'observed_holdout')]:
        rows = frame[frame.split.eq(split) & frame.data_origin.eq(origin) & frame[crowd].notna()]
        if rows.empty:
            ax.plot([], [], label=f'{origin} n=0 (no labels)')
            continue
        p = model.forecast(rows, explain=False).p_crowd.to_numpy()
        bins = pd.DataFrame({'p': p, 'y': rows[crowd].astype(float).to_numpy(),
                             'bin': np.minimum((p*10).astype(int), 9)})
        curve = bins.groupby('bin')[['p', 'y']].mean()
        ax.plot(curve.p, curve.y, marker='o', label=f'{origin} n={len(rows)}')
    ax.set(xlabel='predicted P(crowded)', ylabel='observed crowded fraction',
           title=f'Calibration · h{horizon}')
    ax.legend()
    fig.tight_layout()
    mlflow.log_figure(fig, f'plots/h{horizon}_calibration.png')
    plt.close(fig)
