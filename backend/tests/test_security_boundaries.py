# -*- coding: utf-8 -*-
"""Security and resource-boundary regressions for public API inputs."""
import asyncio
import io

from fastapi import UploadFile
from fastapi.testclient import TestClient

from api import datasets
from api.datasets import _inside, import_csv
from main import app


def test_upload_limit_is_enforced_while_streaming(monkeypatch, tmp_path):
    monkeypatch.setattr(datasets, 'MAX_UPLOAD_BYTES', 8)
    monkeypatch.setattr(datasets.state, 'BACKEND_DIR', tmp_path)
    (tmp_path / 'data').mkdir()
    upload = UploadFile(filename='large.csv', file=io.BytesIO(b'123456789'))

    response = asyncio.run(import_csv(upload, 'large'))

    assert response.status_code == 413
    assert not list((tmp_path / 'data').glob('_upload_*.csv'))


def test_seed_boundary_resolves_symlinks(monkeypatch, tmp_path):
    allowed = tmp_path / 'allowed'
    outside = tmp_path / 'outside'
    allowed.mkdir()
    outside.mkdir()
    link = allowed / 'escape'
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        # Some Windows environments disallow creating symlinks.  Mocking realpath
        # still locks in the essential property of the boundary check.
        monkeypatch.setattr(datasets.os.path, 'realpath', lambda p: str(outside / 'x') if p == str(link / 'x') else p)

    assert not _inside(str(link / 'x'), str(allowed))


def test_crawl_rejects_invalid_or_reversed_dates_before_network():
    with TestClient(app) as client:
        invalid = client.post('/api/datasets/crawl', json={
            'codes': ['600186'], 'start': '2026-99-01',
        })
        reversed_range = client.post('/api/datasets/crawl', json={
            'codes': ['600186'], 'start': '2026-11-02', 'end': '2026-11-01',
        })

    assert invalid.status_code == 422
    assert invalid.json()['code'] == 'DATE_INVALID'
    assert reversed_range.status_code == 422
    assert reversed_range.json()['code'] == 'DATE_RANGE_INVALID'


def test_api_rejects_unbounded_work_requests():
    with TestClient(app) as client:
        too_many_codes = client.post('/api/runs', json={
            'dataset_id': 1,
            'codes': [f'{i:06d}' for i in range(51)],
            'models': [{'id': 'M1'}],
        })
        too_many_models = client.post('/api/summary', json={
            'dataset_id': 1,
            'models': [{'id': f'M{i}'} for i in range(21)],
        })
        bad_page_size = client.get('/api/datasets/1/bars?max_bars=-1')

    assert too_many_codes.status_code == 422
    assert too_many_models.status_code == 422
    assert bad_page_size.status_code == 422
