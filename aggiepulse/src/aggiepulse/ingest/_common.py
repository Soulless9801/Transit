"""HTTP and append-only landing primitives shared by batch sources."""
from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from aggiepulse.common.paths import USER_AGENT, landing_dir


def session() -> requests.Session:
    client = requests.Session()
    client.headers['User-Agent'] = USER_AGENT
    retry = Retry(total=4, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    client.mount('https://', HTTPAdapter(max_retries=retry))
    return client


def fetch(url: str, **kwargs) -> requests.Response:
    with session() as client:
        response = client.get(url, timeout=(15, 90), **kwargs)
        response.raise_for_status()
        return response


def stamp() -> datetime:
    return datetime.now(UTC)


def path(source: str, suffix: str, when: datetime) -> Path:
    return landing_dir(source, when) / f'{when:%Y%m%dT%H%M%S.%fZ}_{suffix}'


def land(source: str, payload, *, kind: str = '', query: dict | None = None,
         rows: bool = False) -> Path:
    when = stamp()
    target = path(source, f'{kind + "_" if kind else ""}raw.jsonl.gz' if rows else
                  f'{kind + "_" if kind else ""}raw.json.gz', when)
    metadata = {'ingest_ts': when.isoformat(), **(query or {})}
    if kind:
        metadata['kind'] = kind
    with target.open('xb') as raw, gzip.open(raw, 'wt', encoding='utf-8') as output:
        for item in payload if rows else [payload]:
            output.write(json.dumps({**metadata, 'payload': item}, ensure_ascii=False) + '\n')
    return target
