"""Portable LightGBM pyfunc with a frozen feature/category vocabulary.

One CrowdingModel per horizon: quantile regressors (alpha .1/.5/.9) for the
p90 load factor of the target window, plus a binary crowding classifier.
It also carries the frozen train-only seasonal profile so inference builds
exactly the features the model was trained on.
"""
import json

import mlflow.pyfunc
import numpy as np
import pandas as pd

from aggiepulse.ml.explanations import neutralise_events, top_factors

CATEGORICAL = ('route_short_name', 'direction_id', 'day_type', 'venue_type')
# Metadata, provenance flags, and nullable hooks without history are never inputs.
EXCLUDE = {'split', 'data_origin', 't0', 'direction_name', 'event_geo_method',
           'weather_forecast_issued_ts', 'feature_staleness_min', 'scoring_ts', 'date', 'notes',
           'weather_is_observed_proxy', 'weather_now_is_observed_proxy',
           'event_schedule_is_hindsight', 'event_timing_is_assumed', 'state_origin',
           'active_alert', 'closed_stop_count', 'upcoming_alert_60m', 'class_wave'}
LOAD_BOUNDS = (0., 1.5)


def feature_columns(frame):
    return [c for c in frame if c not in EXCLUDE and not c.startswith(('y_', 'data_origin', 'sim_'))
            and not pd.api.types.is_datetime64_any_dtype(frame[c])]


class CrowdingModel(mlflow.pyfunc.PythonModel):
    def __init__(self, columns, categories, horizon=None, metadata=None):
        self.columns, self.categories = list(columns), dict(categories)
        self.horizon, self.metadata = horizon, dict(metadata or {})
        self.quantiles, self.classifier, self.probability_scale = [], None, 1.
        self.season = None

    def encode(self, frame):
        result = frame.reindex(columns=self.columns).copy()
        for c in self.columns:
            if c in self.categories:
                values = result[c].astype('string')
                if c == 'direction_id':  # 0, 0.0 and '0' are the same direction
                    values = pd.to_numeric(values, errors='coerce').astype('Int64').astype('string')
                result[c] = pd.Categorical(values, categories=self.categories[c])
            else:
                result[c] = pd.to_numeric(result[c], errors='coerce').astype(float)
        return result

    def quantile_predictions(self, frame):
        x = self.encode(frame)
        q = np.column_stack([m.predict(x) for m in self.quantiles])
        # p50 stays the fitted median (so SHAP explains it); p10/p90 are not allowed to cross it.
        mid = np.clip(q[:, 1], *LOAD_BOUNDS)
        low = np.minimum(np.clip(q[:, 0], *LOAD_BOUNDS), mid)
        high = np.maximum(np.clip(q[:, 2], *LOAD_BOUNDS), mid)
        return x, low, mid, high, q[:, 1]

    def crowd_probability(self, x):
        if isinstance(self.classifier, float) or self.classifier is None:
            return np.full(len(x), float(self.classifier or 0.))
        raw = np.clip(self.classifier.predict_proba(x)[:, 1], 1e-8, 1-1e-8)
        # Undo positive-class up-weighting so probabilities return to the training prior odds.
        return raw/(self.probability_scale*(1-raw)+raw)

    def forecast(self, frame, explain=True):
        frame = frame.reset_index(drop=True)
        x, low, mid, high, raw_mid = self.quantile_predictions(frame)
        neutral = self.quantile_predictions(neutralise_events(frame))[4]
        output = pd.DataFrame({'p10': low, 'p50': mid, 'p90': high,
                               'p_crowd': self.crowd_probability(x),
                               'event_uplift': raw_mid-neutral})
        if explain:
            contrib = self.quantiles[1].booster_.predict(x, pred_contrib=True)
            output['top_factors'] = top_factors(frame, contrib, self.columns)
        return output

    def predict(self, context, model_input, params=None):
        output = self.forecast(model_input)
        # JSON keeps the registry signature portable; the scorer writes the typed array.
        output['top_factors'] = output.top_factors.map(json.dumps)
        return output
