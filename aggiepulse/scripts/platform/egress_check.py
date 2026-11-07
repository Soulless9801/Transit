# Databricks notebook source
"""One-off serverless HTTPS probe; no TripSpark vehicle calls."""
import json
import urllib.error
import urllib.request

ENDPOINTS = {
    'tripspark': ('GET', 'https://aggiespirit.ts.tamu.edu/RouteMap'),
    'gtfs': ('HEAD', 'https://transport.tamu.edu/webFS/gtfs/google_transit.zip'),
    'events': ('GET', 'https://calendar.tamu.edu/live/json/events/'),
    'openmeteo_archive': ('GET', 'https://archive-api.open-meteo.com/v1/archive?latitude=30.61&longitude=-96.34&start_date=2026-11-01&end_date=2026-11-01&hourly=temperature_2m'),
    'openmeteo_forecast': ('GET', 'https://api.open-meteo.com/v1/forecast?latitude=30.61&longitude=-96.34&hourly=temperature_2m&forecast_days=1'),
    'nws': ('GET', 'https://api.weather.gov/stations/KCLL/observations/latest'),
    'ntd': ('GET', 'https://data.transportation.gov/resource/8bui-9xvu.json?ntd_id=60278&$limit=1'),
}
results = {}
for source, (method, url) in ENDPOINTS.items():
    try:
        req = urllib.request.Request(url, method=method, headers={
            'User-Agent': 'AggiePulse/0.1 (TAMU student transit research)',
        })
        with urllib.request.urlopen(req, timeout=25) as response:
            results[source] = {'status': response.status, 'ok': True,
                               'sample_bytes': len(response.read(1024))}
    except (OSError, ValueError) as exc:
        results[source] = {'ok': False, 'error': str(exc)}
    print(json.dumps({source: results[source]}), flush=True)
try:
    dbutils.notebook.exit(json.dumps(results))
except NameError:
    pass
