from application.tier_service import tier_specs, evaluate_tiers
from application.run_service import run_one
from engine.cost import Cost
from test_contracts import make_bars, client


def test_local_variants_preserve_fixed_params_and_boundaries():
    rows = tier_specs('M4', {'tp': 1, 'sl': 5, 'entry': 'breakout', 'lookback': 37})
    assert rows[0]['error']
    assert rows[1]['params']['tp'] == 1
    assert rows[2]['params']['tp'] == 2
    assert all(r['params']['lookback'] == 37 for r in rows)
    assert all(r['params']['entry'] == 'breakout' for r in rows)
    assert tier_specs('M1', {}) == tier_specs('M2', {}) == []


def test_entry_specific_keys_and_unlimited_holding():
    assert tier_specs('M3', {'entry': 'momentum'})[0]['keys'] == ['mom_n', 'mom_th']
    rows = tier_specs('M10', {'max_hold': None})
    assert all(r['params']['max_hold'] is None for r in rows)
    assert rows[0]['keys'] == ['min_hold']


def test_middle_matches_original_and_best_uses_equity():
    bars, cost = make_bars(120), Cost()
    original = run_one(bars, 'M4', {}, cost, 100000)
    evaluated = evaluate_tiers(bars, 'M4', {}, cost, 100000, base_result=original)
    assert evaluated['rows'][1]['result'] == original
    best = next(r for r in evaluated['rows'] if r['id'] == evaluated['best_id'])
    assert best['result']['equity'][-1] == max(r['result']['equity'][-1] for r in evaluated['rows'])


def test_tier_results_roundtrip(client):
    datasets = client.get('/api/datasets').json()['data']
    request = {'dataset_id': datasets[0]['id'], 'models': [{'id': 'M4', 'params': {}}], 'include_tiers': True}
    response = client.post('/api/runs', json=request)
    assert response.status_code == 200, response.text
    run = response.json()['data']
    saved = client.get('/api/runs/' + run['run_id']).json()['data']
    assert saved['results'][0]['tiers'] == run['results'][0]['tiers']
    assert run['results'][0]['tiers']['rows'][1]['result']['metrics'] == run['results'][0]['metrics']
