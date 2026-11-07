import numpy as np
import pandas as pd
import pytest

from aggiepulse.ml.baselines import Baselines, seasonal_keys
from aggiepulse.ml.evaluate import evaluate, metrics


def test_baselines_refuse_non_train_rows(toy_training):
    with pytest.raises(ValueError):
        Baselines(toy_training, 30)


def test_b1_is_train_only_slot_mean(toy_training):
    train = toy_training[toy_training.split.eq('train')]
    baselines = Baselines(train, 30)
    test = toy_training[toy_training.split.eq('test')].reset_index(drop=True)
    prediction = baselines.predict(test, 'B1')
    keys = seasonal_keys(test, 30).iloc[0]
    trained = seasonal_keys(train, 30).assign(y=train.y_p90load_h30.to_numpy())
    expected = trained[(trained.route == keys.route) & (trained.direction == keys.direction)
                       & (trained.slot == keys.slot)].y.mean()
    assert prediction.p50.iloc[0] == pytest.approx(expected)
    # Mutating test labels never changes B1.
    mutated = test.assign(y_p90load_h30=9.)
    pd.testing.assert_frame_equal(prediction, baselines.predict(mutated, 'B1'))


def test_b0_persistence_and_fallback(toy_training):
    baselines = Baselines(toy_training[toy_training.split.eq('train')], 15)
    test = toy_training[toy_training.split.eq('test')].reset_index(drop=True)
    test.loc[0, 'p90_load_t0'] = np.nan
    b0, b1 = baselines.predict(test, 'B0'), baselines.predict(test, 'B1')
    assert b0.p50.iloc[1] == pytest.approx(test.p90_load_t0.iloc[1])
    assert b0.p50.iloc[0] == pytest.approx(b1.p50.iloc[0])
    assert bool(b0.b0_fallback.iloc[0]) and not bool(b0.b0_fallback.iloc[1])
    assert (b0.p10 <= b0.p50).all() and (b0.p50 <= b0.p90).all()


def test_metrics_report_small_observed_n_explicitly(toy_training):
    frame = toy_training[toy_training.split.eq('test')].head(5).copy()
    frame['data_origin'], frame['split'] = 'observed', 'observed_holdout'
    prediction = pd.DataFrame({'p10': 0., 'p50': .3, 'p90': 1., 'p_crowd': .1}, index=range(len(frame)))
    result = metrics(frame.reset_index(drop=True), prediction, 30)
    assert result['n'] == 5 and 'too small' in result['conclusion']
    rows = evaluate(toy_training, {'B1': pd.DataFrame({'p10': 0., 'p50': .3, 'p90': 1., 'p_crowd': .1},
                                                      index=range(len(toy_training)))}, 30, groups=False)
    observed = [r for r in rows if r['origin'] == 'observed']
    assert observed and observed[0]['n'] == 0 and 'too small' in observed[0]['conclusion']
