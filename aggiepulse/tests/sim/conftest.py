import pandas as pd
import pytest


@pytest.fixture
def simulation_inputs():
    days = pd.date_range('2026-09-06','2026-09-20')
    trips, times = [], []
    for direction in [0,1]:
        for hour in range(7,20):
            trip = f't{direction}-{hour}'
            trips.append({'trip_id':trip,'route_id':'r','service_id':'s',
                          'direction_id':str(direction),'trip_headsign':str(direction)})
            for seq in [0,1]:
                times.append({'trip_id':trip,'stop_id':str(seq),'stop_sequence':str(seq),
                              'departure_time':f'{hour:02d}:{seq*10:02d}:00',
                              'shape_dist_traveled':str(seq*3000)})
    gtfs = {'routes':pd.DataFrame([{'route_id':'r','route_short_name':'01','route_long_name':'Bonfire'}]),
            'trips':pd.DataFrame(trips), 'stop_times':pd.DataFrame(times),
            'stops':pd.DataFrame({'stop_id':['0','1'],'stop_lat':['30.61','30.62'],
                                 'stop_lon':['-96.34','-96.34']}),
            'calendar_dates':pd.DataFrame({'service_id':'s','date':days.strftime('%Y%m%d'),'exception_type':'1'})}
    academic = pd.DataFrame({'date':days.strftime('%Y-%m-%d'),'is_class_day':(days.day_of_week<5).astype(int),
                             'is_holiday':0,'is_break':0,'is_reading_day':0,'is_finals':0,
                             'week_of_term':3,'days_to_finals':80,'notes':''})
    periods = pd.DataFrame({'pattern':['MWF','TR'], 'start_time':['08:00','08:00'],
                            'end_time':['09:00','09:00']})
    ntd = pd.DataFrame({'month':pd.to_datetime(['2026-09-01'],utc=True),'upt':[10000],
                        'ntd_id':['60278'],'mode':['MB'],'tos':['DO']})
    return {'gtfs':gtfs,'academic':academic,'periods':periods,'ntd':ntd,
            'events':pd.DataFrame(), 'weather':pd.DataFrame(),
            'start':'2026-09-06','end':'2026-09-15T05:00:00Z'}
