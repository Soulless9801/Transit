import numpy as np
import pandas as pd

from aggiepulse.features.event_exposure import build_event_exposure


def test_nearby_pre_post_phase_far_venues_and_future_publication():
    grid = pd.DataFrame({'route_short_name':['01']*4,'direction_id':[0]*4,
                          'window_start':pd.to_datetime(['2026-09-12T15:15Z','2026-09-12T20:15Z',
                                                        '2026-09-13T15:15Z','2026-09-12T14:15Z'])})
    stops = pd.DataFrame({'route_short_name':['01'],'direction_id':[0],
                           'stop_lat':[30.61],'stop_lon':[-96.34]})
    events = pd.DataFrame({'start_ts':pd.to_datetime(['2026-09-12T16:00Z']),
                            'end_ts':pd.to_datetime(['2026-09-12T20:00Z']),
                            'lat':[30.61],'lon':[-96.34], 'significance':[40.],
                            'is_football_home':[True], 'published_ts':pd.to_datetime(['2026-09-12T15:00Z'])})
    frame = build_event_exposure(grid,events,stops)
    assert frame.event_pre.iloc[0]>0
    assert frame.event_post.iloc[1]>0
    assert frame.event_exposure.iloc[2]==0
    assert frame.event_exposure.iloc[3]==0
    assert frame.minutes_to_next_big_event.iloc[0]==45
    assert frame.minutes_since_last_big_event.iloc[1]==15
    events['lat']=31.
    assert build_event_exposure(grid,events,stops).event_exposure.eq(0).all()
    events['lat']=30.61
    events['significance']=np.nan
    frame = build_event_exposure(grid,events,stops)
    assert frame.event_exposure.eq(0).all()
    assert frame.unknown_size_events.iloc[0]==1


def test_h3_optional_path_has_identical_exact_radius(monkeypatch):
    import aggiepulse.features.event_exposure as module
    grid = pd.DataFrame({'route_short_name':['01'],'direction_id':[0],
                          'window_start':pd.to_datetime(['2026-09-12T15:15Z'])})
    stops = pd.DataFrame({'route_short_name':['01'],'direction_id':[0],
                           'stop_lat':[30.61],'stop_lon':[-96.34]})
    events = pd.DataFrame({'start_ts':pd.to_datetime(['2026-09-12T16:00Z']),
                            'end_ts':pd.to_datetime(['2026-09-12T20:00Z']),
                            'lat':[30.611],'lon':[-96.34],'significance':[40.]})
    h3_frame = module.build_event_exposure(grid,events,stops)
    monkeypatch.setattr(module,'h3',None)
    fallback = module.build_event_exposure(grid,events,stops)
    pd.testing.assert_series_equal(h3_frame.event_exposure,fallback.event_exposure)


def test_hashed_seed_paths(tmp_path):
    from aggiepulse.features.inputs import seed_file
    folder = tmp_path/'academic_days'
    folder.mkdir()
    target = folder/'b232ab21.csv'
    target.write_text('date\n2026-09-06\n')
    assert seed_file(tmp_path,'academic_days.csv') == target
