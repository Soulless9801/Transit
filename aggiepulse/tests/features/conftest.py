import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def feature_inputs():
    ts = pd.date_range('2026-09-06T05:00Z',periods=16*96,freq='15min')
    load = .3+.1*np.sin(np.arange(len(ts))/10)
    history = pd.DataFrame({'route_short_name':'01','direction_id':0,'window_start':ts,
                            'avg_load_factor':load,'p90_load_factor':load+.1,
                            'max_load_factor':load+.2,'active_vehicles':2,'sched_trips':2,
                            'service_ratio':1.,'headway_cv':.2,'bunching_rate':.1,
                            'data_origin':'simulated'})
    days = pd.date_range('2026-09-01','2026-10-01')
    academic = pd.DataFrame({'date':days.strftime('%Y-%m-%d'),'is_class_day':(days.day_of_week<5).astype(int),
                             'is_holiday':0,'is_break':0,'is_reading_day':0,'is_finals':0,
                             'week_of_term':3,'days_to_finals':80,'notes':''})
    periods = pd.DataFrame({'pattern':['MWF','TR'],'start_time':['08:00']*2,'end_time':['09:00']*2})
    return {'history':history,'academic':academic,'periods':periods,
            'event_exposure':pd.DataFrame(), 'weather':pd.DataFrame(),
            'train_end':'2026-09-16T05:00Z', 'validation_end':'2026-09-19T05:00Z'}
