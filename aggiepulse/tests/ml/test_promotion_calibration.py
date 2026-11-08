import numpy as np
import pandas as pd
import pytest

from aggiepulse.ml.calibration import calibrate, observed_partition
from aggiepulse.ml.train import promotion_gate


def observed(n=800):
    return pd.DataFrame({'t0': pd.date_range('2026-11-09T06:00Z', periods=n, freq='15min'),
                         'data_origin': 'observed', 'split': 'observed_holdout',
                         'y_p90load_h30': .8})


def table(n):
    return pd.DataFrame([{'horizon': 30, 'origin': origin, 'group': 'all', 'model': model,
                          'n': n if origin == 'observed' else 1000, 'mae': mae}
                         for origin in ['simulated', 'observed']
                         for model, mae in [('B0', .15), ('B1', .1), ('LightGBM', .2 if origin == 'observed' else .05)]])


def test_observed_gate_rejects_despite_simulated_improvement():
    frame = observed()
    _, evaluation = observed_partition(frame, 30)
    gate = promotion_gate(table(len(evaluation)), frame)
    assert gate['gate'] == 'observed' and gate['observed_weekdays'] >= 2
    assert not gate['promote']
    assert gate['comparisons']['simulated']['lightgbm_mae'] == .05


def test_simulated_fallback_for_small_or_weekend_only_observation():
    assert promotion_gate(table(20), observed(20))['gate'] == 'simulated'
    frame = observed(400)
    frame['t0'] = pd.date_range('2026-11-07T06:00Z', periods=400, freq='1min')
    assert promotion_gate(table(300), frame)['gate'] == 'simulated'


class FakeModel:
    def __init__(self):
        self.metadata = {}
        self.interval_widening = 0.

    def forecast(self, frame, explain=False):
        w = self.interval_widening
        return pd.DataFrame({'p10': np.full(len(frame), max(0, .2-w)),
                             'p50': .3, 'p90': min(1.5, .4+w)})


def test_chronological_calibration_and_purge():
    frame = observed()
    calibration, evaluation = observed_partition(frame, 30)
    assert calibration.t0.max() + pd.Timedelta(minutes=45) < evaluation.t0.min()
    model = FakeModel()
    report = calibrate(model, frame, 30)
    assert report['status'] == 'calibrated'
    assert report['widening'] == pytest.approx(.4)
    assert report['coverage_before'] == 0 and report['coverage_after'] == 1
    # Changing evaluation outcomes cannot change the fitted widening.
    frame.loc[frame.t0.isin(evaluation.t0), 'y_p90load_h30'] = 1.4
    assert calibrate(FakeModel(), frame, 30)['widening'] == report['widening']


def test_tiny_observed_collection_does_not_fit_intervals():
    report = calibrate(FakeModel(), observed(40), 30)
    assert report['status'] == 'insufficient_observed_calibration' and report['widening'] == 0
    assert report['evaluation_n'] == 40


def test_observed_gate_accepts_only_observed_improvement():
    frame = observed()
    _, evaluation = observed_partition(frame, 30)
    report = table(len(evaluation))
    report.loc[(report.origin == 'observed') & (report.model == 'LightGBM'), 'mae'] = .05
    gate = promotion_gate(report, frame)
    assert gate['gate'] == 'observed' and gate['promote']


def test_gate_threshold_is_inclusive():
    frame = observed()
    assert promotion_gate(table(199), frame)['gate'] == 'simulated'
    assert promotion_gate(table(200), frame)['gate'] == 'observed'


def test_portable_model_widening_and_v1_backward_compatibility():
    from aggiepulse.ml.model import CrowdingModel

    class Quantile:
        def __init__(self, value):
            self.value = value

        def predict(self, frame):
            return np.full(len(frame), self.value)

    model = CrowdingModel(['load_t0'], {})
    model.quantiles = [Quantile(.2), Quantile(.3), Quantile(.4)]
    frame = pd.DataFrame({'load_t0': [.1, .2]})
    # Original pickles do not have interval_widening at all.
    assert model.forecast(frame, explain=False).p10.iloc[0] == .2
    model.interval_widening = .5
    result = model.forecast(frame, explain=False)
    assert result.p10.iloc[0] == 0 and result.p90.iloc[0] == pytest.approx(.9)
    assert result.p50.iloc[0] == .3
