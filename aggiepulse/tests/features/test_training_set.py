import pandas as pd
import pytest

from aggiepulse.features.build_training_set import build_training_set, weather_features


def test_future_mutation_and_shuffle_do_not_change_features(feature_inputs):
    before = build_training_set(**feature_inputs)
    cutoff = pd.Timestamp('2026-09-12T15:00Z')
    altered = feature_inputs['history'].copy()
    future = altered.window_start >= cutoff
    altered.loc[future,['avg_load_factor','p90_load_factor','max_load_factor','headway_cv','bunching_rate']] = 9.
    feature_inputs['history'] = altered.sample(frac=1,random_state=23)
    after = build_training_set(**feature_inputs)
    features = [c for c in before if not c.startswith(('y_','data_origin'))]
    pd.testing.assert_frame_equal(before.loc[before.t0<=cutoff,features].reset_index(drop=True),
                                  after.loc[after.t0<=cutoff,features].reset_index(drop=True))


def test_labels_and_completed_window_state_alignment(feature_inputs):
    frame = build_training_set(**feature_inputs)
    source = feature_inputs['history'].set_index('window_start')
    row = frame.iloc[100]
    assert row.load_t0==pytest.approx(source.loc[row.t0-pd.Timedelta(minutes=15),'avg_load_factor'])
    assert row.load_lag30==pytest.approx(source.loc[row.t0-pd.Timedelta(minutes=45),'avg_load_factor'])
    for horizon in [15,30,60]:
        target = source.loc[row.t0+pd.Timedelta(minutes=horizon)]
        assert row[f'y_p90load_h{horizon}']==pytest.approx(target.p90_load_factor)
        assert row[f'y_crowd_h{horizon}']==int(target.max_load_factor>=.75)
        assert row[f'y_hwcv_h{horizon}']==.2
    assert pd.isna(frame.iloc[0].load_t0)


def test_observed_holdout_and_no_synthetic_state(feature_inputs):
    observed = feature_inputs['history'].iloc[-8:].copy()
    observed['data_origin']='observed'
    observed['avg_load_factor']=.9
    feature_inputs['history']=pd.concat([feature_inputs['history'],observed],ignore_index=True)
    frame = build_training_set(**feature_inputs)
    measured = frame[frame.data_origin.eq('observed')]
    assert len(measured)>0
    assert not frame.duplicated(['route_short_name','direction_id','t0']).any()
    assert set(measured.split)=={'observed_holdout'}
    assert pd.isna(measured.iloc[0].load_t0)
    assert measured.load_t0.dropna().eq(.9).all()
    for horizon in [15,30,60]:
        assert measured[f'data_origin_h{horizon}'].dropna().eq('observed').all()


def test_seasonal_never_uses_validation_and_split_labels_purged(feature_inputs):
    before = build_training_set(**feature_inputs)
    boundary = pd.Timestamp(feature_inputs['train_end'])
    feature_inputs['history'].loc[feature_inputs['history'].window_start>=boundary,'avg_load_factor']=99.
    after = build_training_set(**feature_inputs)
    pd.testing.assert_series_equal(before.seasonal_slot_mean,after.seasonal_slot_mean)
    train = before[before.split.eq('train')]
    for horizon in [15,30,60]:
        valid = train[f'y_p90load_h{horizon}'].notna()
        assert (train.loc[valid,'t0']+pd.Timedelta(minutes=horizon+15)<=boundary).all()
    assert pd.isna(before.iloc[0].seasonal_slot_mean)


def test_missing_labels_are_not_negative_and_lags_do_not_jump_gaps(feature_inputs):
    source = feature_inputs['history']
    missing = source.window_start.iloc[100]
    feature_inputs['history']=source.drop(index=100)
    frame = build_training_set(**feature_inputs)
    row = frame[frame.t0.eq(missing-pd.Timedelta(minutes=15))].iloc[0]
    assert pd.isna(row.y_crowd_h15)
    row = frame[frame.t0.eq(missing+pd.Timedelta(minutes=15))].iloc[0]
    assert pd.isna(row.load_t0)


def test_forecast_issue_time_and_explicit_observed_proxy():
    t0 = pd.Timestamp('2026-09-10T12:15Z')
    weather = pd.DataFrame({'ts_hour':pd.to_datetime(['2026-09-10T13:00Z']*3),
                            'forecast_issued_ts':pd.to_datetime(['2026-09-10T10:00Z',
                                                               '2026-09-10T13:00Z',
                                                               '2026-11-07T19:00Z']),
                            'is_forecast':[True,True,False], 'precip_mm':[1.,99.,3.]})
    f = weather_features([t0],weather)
    assert f.precip_next_hour.iloc[0]==1
    assert not f.weather_is_observed_proxy.iloc[0]
    f = weather_features([t0],weather[~weather.is_forecast])
    assert f.precip_next_hour.iloc[0]==3
    assert f.weather_is_observed_proxy.iloc[0]
    f = weather_features([t0],weather[~weather.is_forecast],False)
    assert pd.isna(f.precip_next_hour.iloc[0])


def test_schedule_only_observed_scaffold_cannot_replace_simulated_history(feature_inputs):
    before = build_training_set(**feature_inputs)
    scaffold = feature_inputs['history'].copy()
    scaffold['data_origin'] = 'observed'
    for name in ['avg_load_factor','p90_load_factor','max_load_factor','headway_cv']:
        scaffold[name] = float('nan')
    scaffold['n_pings'] = 0
    feature_inputs['history'] = pd.concat([feature_inputs['history'], scaffold],ignore_index=True)
    after = build_training_set(**feature_inputs)
    pd.testing.assert_frame_equal(before,after)
    assert after.y_p90load_h30.notna().sum()>0
    assert not after.data_origin.eq('observed').any()
