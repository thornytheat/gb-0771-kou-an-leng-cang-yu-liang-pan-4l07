import pytest
from app.modules.voucher.service import build, verify
from app.parcels import load_example
from app.pipeline import review
from app.rules import RULES
from app.seals import compact, input_seals, sha
from app.settings import VERSION


def document():
    files = load_example('dirty')
    stages = review(files)
    manifest = dict(id='test', version=VERSION, inputs=input_seals(files), result_sha256=sha(compact(stages)))
    return dict(manifest=manifest, files=files, stages=stages)


def issued(client, receipt, lot):
    return client.post(f"/api/receipts/{receipt}/vouchers", json={'lot': lot}).json


def checks(report):
    return {row['check']: row['ok'] for row in report['checks']}


def test_conclusion_matches_pipeline_rows():
    doc = document()
    for lot in doc['stages']['registry']['lots']:
        voucher = build(doc, lot)
        assert voucher['conclusion']['rows'] == [row for row in doc['stages']['exposure']['rows'] if row['lot'] == lot]
        report = verify(voucher)
        assert report['valid'] and all(checks(report).values())


def test_issue_scopes_evidence_to_lot(client, archived):
    voucher = issued(client, archived['id'], 'F03')
    assert voucher['counts']['missing_slot'] == 900 and voucher['reused'] is False
    evidence = client.get(f"/api/vouchers/{voucher['id']}/export").json['evidence']
    assert {row['lot'] for row in evidence['moves']} == {'F03'}
    assert {row['door'] for row in evidence['doors']} == {row['door'] for row in evidence['temperatures']} == {'EAST'}
    assert {row['slot'] for row in evidence['slots']} == {'A1'}
    text = str(evidence)
    for leaked in ('F01', 'F02', 'F04', 'F05', 'WEST'):
        assert leaked not in text


def test_quarantined_rows_follow_their_lot(client, archived):
    voucher = issued(client, archived['id'], 'F05')
    evidence = client.get(f"/api/vouchers/{voucher['id']}/export").json['evidence']
    codes = {row['code'] for row in evidence['quarantined']}
    assert {'MISSING', 'EVENT_CONFLICT', 'DUP_PACKET'} <= codes
    assert all(row.get('lot') in (None, 'F05') for row in evidence['quarantined'])


def test_warm_minutes_for_overtemp_lot(client, archived):
    voucher = issued(client, archived['id'], 'F01')
    assert (voucher['warm_seconds'], voucher['warm_minutes']) == (600, 10)


def test_export_verify_roundtrip(client, archived):
    voucher = issued(client, archived['id'], 'F04')
    exported = client.get(f"/api/vouchers/{voucher['id']}/export").json
    assert 'attachment;' in client.get(f"/api/vouchers/{voucher['id']}/export").headers['Content-Disposition']
    report = client.post('/api/vouchers/verify', json=exported).json
    assert report['valid'] and checks(report) == dict.fromkeys(
        ('seals', 'coverage', 'classification', 'totals', 'registered'), True)


def test_stored_verify_endpoint(client, archived):
    voucher = issued(client, archived['id'], 'F02')
    report = client.get(f"/api/vouchers/{voucher['id']}/verify").json
    assert report['valid'] and report['warm_seconds'] == 600


def test_verify_detects_tampered_conclusion(client, archived):
    voucher = issued(client, archived['id'], 'F01')
    exported = client.get(f"/api/vouchers/{voucher['id']}/export").json
    exported['conclusion']['warm_seconds'] += 60
    report = client.post('/api/vouchers/verify', json=exported).json
    assert not report['valid']
    assert checks(report)['seals'] is False and checks(report)['totals'] is False


def test_verify_detects_dropped_slice(client, archived):
    voucher = issued(client, archived['id'], 'F01')
    exported = client.get(f"/api/vouchers/{voucher['id']}/export").json
    warm = next(row for row in exported['conclusion']['rows'] if row['kind'] == 'warm')
    exported['conclusion']['rows'].remove(warm)
    report = client.post('/api/vouchers/verify', json=exported).json
    assert not report['valid'] and checks(report)['coverage'] is False


def test_verify_detects_tampered_evidence(client, archived):
    voucher = issued(client, archived['id'], 'F01')
    exported = client.get(f"/api/vouchers/{voucher['id']}/export").json
    exported['evidence']['temperatures'][0]['celsius'] = '1.0'
    report = client.post('/api/vouchers/verify', json=exported).json
    assert not report['valid']
    assert checks(report)['seals'] is False and checks(report)['classification'] is False


def test_verify_rejects_unregistered_forgery(client, archived):
    forgery = build(document(), 'F03')
    report = client.post('/api/vouchers/verify', json=forgery).json
    assert not report['valid'] and checks(report)['registered'] is False
    assert all(ok for name, ok in checks(report).items() if name != 'registered')


def test_issue_idempotent_retry(client, archived):
    first = issued(client, archived['id'], 'F04')
    second = issued(client, archived['id'], 'F04')
    assert second['id'] == first['id'] and second['reused'] is True
    assert client.get(f"/api/receipts/{archived['id']}/vouchers").json['count'] == 1


def test_rule_update_issues_new_voucher_old_still_verifies(client, archived, monkeypatch):
    first = issued(client, archived['id'], 'F01')
    assert first['rule'] == 'ice-stock/1' and first['warm_seconds'] == 600
    monkeypatch.setitem(RULES, 'ice-stock/2', dict(warm_limit='3.5', temp_max_age=600, min_door_pulse=30))
    monkeypatch.setattr('app.settings.VERSION', 'ice-stock/2')
    monkeypatch.setattr('app.modules.history.service.VERSION', 'ice-stock/2')
    refreshed = client.post(f"/api/receipts/{archived['id']}/refresh").json
    assert refreshed['id'] != archived['id'] and refreshed['reused'] is False
    second = issued(client, refreshed['id'], 'F01')
    assert second['rule'] == 'ice-stock/2' and second['warm_seconds'] == 1380 and second['id'] != first['id']
    old = client.get(f"/api/vouchers/{first['id']}/verify").json
    assert old['valid'] and old['rule'] == 'ice-stock/1' and old['warm_seconds'] == 600
    assert client.get(f"/api/receipts/{archived['id']}/vouchers").json['count'] == 1
    assert client.get(f"/api/receipts/{refreshed['id']}/vouchers").json['count'] == 1


def test_unknown_lot(client, archived):
    response = client.post(f"/api/receipts/{archived['id']}/vouchers", json={'lot': 'F99'})
    assert (response.status_code, response.json['code']) == (404, 'NO_LOT')


def test_unknown_receipt(client):
    response = client.post('/api/receipts/unknown/vouchers', json={'lot': 'F01'})
    assert (response.status_code, response.json['code']) == (404, 'NO_RECEIPT')


def test_bad_issue_body(client, archived):
    response = client.post(f"/api/receipts/{archived['id']}/vouchers", json={})
    assert (response.status_code, response.json['code']) == (422, 'BAD_PACK')


def test_bad_verify_body(client):
    response = client.post('/api/vouchers/verify', json=['not-a-voucher'])
    assert (response.status_code, response.json['code']) == (422, 'BAD_VOUCHER')


def test_unknown_voucher(client):
    assert client.get('/api/vouchers/unknown').status_code == 404
    assert client.get('/api/vouchers/unknown/verify').status_code == 404
