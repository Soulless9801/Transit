"""Chronological observed split-conformal widening; evaluation remains held out."""
import math

import numpy as np
import pandas as pd

from aggiepulse.common.paths import LOCAL_TZ

MIN_CALIBRATION_N = 100
MIN_OBSERVED_N = 200
MIN_WEEKDAYS = 2


def observed_partition(frame, horizon):
    """Reserve earliest half of distinct t0s for calibration, purge label overlap.

    Evaluation starts after all calibration target windows have completed. Rows
    sharing t0 stay together. Tiny collections stay entirely in evaluation.
    """
    rows = frame[frame.data_origin.eq('observed') & frame.split.eq('observed_holdout')
                 & frame[f'y_p90load_h{horizon}'].notna()].copy()
    times = pd.to_datetime(rows.t0, utc=True)
    unique = sorted(times.unique())
    if len(rows) < MIN_CALIBRATION_N * 2 or len(unique) < 2:
        return rows.iloc[:0], rows
    boundary = unique[len(unique)//2]
    calibration = rows[times < boundary]
    evaluation = rows[times >= boundary + pd.Timedelta(minutes=horizon+15)]
    if len(calibration) < MIN_CALIBRATION_N or len(evaluation) < MIN_CALIBRATION_N:
        return rows.iloc[:0], rows
    return calibration, evaluation


def weekday_count(frame):
    local = pd.to_datetime(frame.t0, utc=True).dt.tz_convert(LOCAL_TZ)
    return int(local[local.dt.day_of_week < 5].dt.date.nunique())


def calibrate(model, frame, horizon):
    """80% finite-sample conformal score max(low-y, y-high, 0), widening only.

    Dependence among route windows limits exchangeability; no coverage guarantee
    is claimed. The evaluation suffix is never used to choose the widening.
    """
    calibration, evaluation = observed_partition(frame, horizon)
    report = {'method': 'observed_split_conformal_widening', 'target_coverage': .8,
              'calibration_n': len(calibration), 'evaluation_n': len(evaluation),
              'widening': 0., 'status': 'insufficient_observed_calibration'}
    model.interval_widening = 0.
    uncalibrated = model.forecast(evaluation, explain=False) if len(evaluation) else None
    if len(calibration) >= MIN_CALIBRATION_N:
        pred = model.forecast(calibration, explain=False)
        y = calibration[f'y_p90load_h{horizon}'].to_numpy(float)
        scores = np.maximum.reduce([pred.p10.to_numpy()-y, y-pred.p90.to_numpy(), np.zeros(len(y))])
        rank = min(len(scores), math.ceil((len(scores)+1)*.8))
        model.interval_widening = float(np.sort(scores)[rank-1])
        report.update(status='calibrated', widening=model.interval_widening,
                      calibration_end=str(pd.to_datetime(calibration.t0, utc=True).max()))
    if len(evaluation):
        y = evaluation[f'y_p90load_h{horizon}'].to_numpy(float)
        pred = model.forecast(evaluation, explain=False)
        report['coverage_before'] = float(((y >= uncalibrated.p10)
                                           & (y <= uncalibrated.p90)).mean())
        report['coverage_after'] = float(((y >= pred.p10) & (y <= pred.p90)).mean())
    model.metadata['calibration'] = report
    return report
