import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'pipelines/core'))
from lib.helpers import apc_stuck


@pytest.mark.parametrize(('seconds', 'lat', 'lon', 'a', 'b', 'expected'), [
    (3599, .01, .01, '1', '2', False),
    (3600, .01, .01, '1', '2', True),
    (7200, 0, 0, '1', '2', False),
    (7200, .01, 0, '1', '1', False),
    (7200, .01, 0, None, None, False),
    (3600, 0, .01, '1', '2', True),
])
def test_stuck_requires_elapsed_motion_and_distinct_stop_evidence(seconds, lat, lon, a, b, expected):
    assert apc_stuck(seconds, lat, lon, a, b) == expected
