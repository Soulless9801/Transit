"""Curated seed integrity checks. Sources retrieved 2026-11-07: ../../seeds/README.md.
Data source: https://catalog.tamu.edu/undergraduate/academic-calendar/
"""
from pathlib import Path
import pandas as pd
import pytest

SEEDS = Path(__file__).resolve().parents[2] / 'seeds'
SCHEMAS = {
 'academic_days.csv': 'date term is_class_day is_holiday is_break is_reading_day is_finals is_commencement week_of_term days_to_finals notes',
 'class_periods.csv': 'pattern start_time end_time source',
 'venues.csv': 'venue_key display_name aliases lat lon capacity venue_type campus',
 'football_home.csv': 'date kickoff_local opponent home_away_neutral venue_key is_conference notes source',
 'basketball_home.csv': 'date kickoff_local opponent team home_away_neutral venue_key is_conference notes source',
}
@pytest.mark.parametrize('filename,columns', SCHEMAS.items())
def test_schema(filename, columns):
 df = pd.read_csv(SEEDS / filename)
 assert set(columns.split()) <= set(df.columns)
 assert not df.empty

def test_academic_continuity_and_flags():
 df = pd.read_csv(SEEDS / 'academic_days.csv').set_index('date')
 assert list(df.index) == pd.date_range('2026-08-01', '2026-12-31').strftime('%Y-%m-%d').tolist()
 for col in ['is_class_day','is_holiday','is_break','is_reading_day','is_finals','is_commencement']:
  assert set(df[col]) <= {0,1}
 assert df.loc['2026-08-24','week_of_term'] == 1
 assert df.loc['2026-12-01','is_class_day'] == 1
 assert 'Friday' in df.loc['2026-12-01','notes']
 assert df.loc['2026-12-03','is_class_day'] == 1
 assert df.loc['2026-11-25','is_reading_day'] == 1
 assert df.loc['2026-11-26':'2026-11-27','is_holiday'].eq(1).all()
 assert df.loc['2026-12-07':'2026-12-10','is_finals'].eq(1).all()
 assert df.loc['2026-12-07','days_to_finals'] == 0
 assert not (df.is_class_day.astype(bool) & (df.is_holiday | df.is_reading_day | df.is_finals).astype(bool)).any()

def test_venues():
 df = pd.read_csv(SEEDS / 'venues.csv')
 assert df.venue_key.is_unique
 assert df.lat.isna().equals(df.lon.isna())
 known = df.dropna(subset=['lat','lon'])
 assert known.lat.between(30.50,30.80).all()
 assert known.lon.between(-96.60,-96.20).all()
 assert df.loc[df.lat.isna(),'notes'].str.contains('UNVERIFIED').all()
 assert df.capacity.dropna().gt(0).all()
 aliases = df.aliases.str.split('|',regex=False).explode().str.casefold()
 assert not aliases.duplicated().any()

def test_periods_and_games():
 periods = pd.read_csv(SEEDS / 'class_periods.csv')
 assert not periods.duplicated(['pattern','start_time','end_time','campus_region']).any()
 assert periods.start_time.lt(periods.end_time).all()
 keys = set(pd.read_csv(SEEDS / 'venues.csv').venue_key)
 for filename,pk in [('football_home.csv',['date']),('basketball_home.csv',['date','team','opponent'])]:
  df = pd.read_csv(SEEDS / filename)
  assert not df.duplicated(pk).any()
  assert df.opponent.str.len().lt(60).all()
  assert not df.opponent.str.contains(r'^(?:at |vs\.)',regex=True).any()
  assert df.kickoff_local.dropna().str.fullmatch(r'\d{2}:\d{2}').all()
  assert set(df.loc[df.home_away_neutral.eq('home'),'venue_key']) <= keys
 football = pd.read_csv(SEEDS / 'football_home.csv').set_index('date')
 assert len(football) == 12
 assert football.loc['2026-11-14','opponent'] == 'Tennessee'
 assert football.loc['2026-11-14','venue_key'] == 'kyle_field'
