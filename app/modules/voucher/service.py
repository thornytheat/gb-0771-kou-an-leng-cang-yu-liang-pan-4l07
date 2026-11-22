from collections import Counter, defaultdict
from datetime import datetime, timezone
from app import settings
from app.faults import DockFault
from app.modules.certificate.service import issue as certify
from app.modules.exposure.doors import signals
from app.modules.exposure.service import KINDS, slice_period
from app.modules.exposure.temperature import normalize
from app.rules import for_version
from app.seals import compact, sha

SECTIONS = ('presence', 'moves', 'quarantined', 'slots', 'doors', 'temperatures')


def build(document, lot):
    stages = certify(document)['stages']
    if lot not in stages['registry']['lots']:
        raise DockFault('NO_LOT', '货批不在冷库档案中', 404)
    rules = for_version(document['manifest']['version'])
    presence = [row for row in stages['custody']['periods'] if row['lot'] == lot]
    moves = [row for row in stages['intake']['tables']['moves'] if row['lot'] == lot]
    quarantined = [row for row in stages['intake']['isolated'] + stages['intake']['copies']
                   if row['source'] == 'moves' and (row.get('lot') == lot or lot in row.get('raw', []))]
    slots = {row['line']: row for period in presence for row in stages['registry']['versions'].get(period['slot'], ())}
    doors = {row['door'] for row in slots.values()}
    evidence = dict(presence=presence, moves=moves, quarantined=quarantined, slots=list(slots.values()),
                    doors=[row for row in stages['intake']['tables']['doors'] if row['door'] in doors],
                    temperatures=[row for row in stages['intake']['tables']['temperatures'] if row['door'] in doors])
    rows = [row for row in stages['exposure']['rows'] if row['lot'] == lot]
    counts = Counter({key: 0 for key in KINDS})
    for row in rows:
        counts[row['kind']] += row['seconds']
    conclusion = dict(rows=rows, counts=dict(counts),
                      attended_seconds=sum(row['end'] - row['start'] for row in presence),
                      covered_seconds=sum(row['seconds'] for row in rows),
                      warm_seconds=counts['warm'], warm_minutes=counts['warm'] // 60)
    seals = {name: sha(compact(evidence[name])) for name in SECTIONS}
    result = sha(compact(conclusion))
    receipt = document['manifest']['id']
    manifest = dict(id=sha(compact([receipt, lot, rules, seals, result])), receipt=receipt, lot=lot, rule=rules,
                    window=stages['window'], evidence_sha256=seals, conclusion_sha256=result,
                    issuer=settings.VERSION, generated_at=datetime.now(timezone.utc).isoformat())
    return dict(manifest=manifest, evidence=evidence, conclusion=conclusion)


def issue(store, document, lot):
    stored, reused = store.insert(build(document, lot))
    return dict(brief(stored), reused=reused)


def brief(document):
    manifest, conclusion = document['manifest'], document['conclusion']
    return dict(id=manifest['id'], receipt=manifest['receipt'], lot=manifest['lot'], rule=manifest['rule']['version'],
                attended_seconds=conclusion['attended_seconds'], warm_seconds=conclusion['warm_seconds'],
                warm_minutes=conclusion['warm_minutes'], counts=conclusion['counts'],
                generated_at=manifest['generated_at'])


def verify(document, lookup=None):
    if not isinstance(document, dict):
        raise DockFault('BAD_VOUCHER', '凭证须为 JSON 对象')
    try:
        manifest, evidence, conclusion = (document[key] for key in ('manifest', 'evidence', 'conclusion'))
        periods, rows, rule = evidence['presence'], conclusion['rows'], manifest['rule']
    except (KeyError, TypeError):
        return dict(valid=False, checks=[dict(check='structure', ok=False)])
    checks = [seal_check(document, manifest, evidence, conclusion), coverage_check(manifest, periods, rows),
              classification_check(rule, evidence, rows), totals_check(conclusion, periods, rows)]
    if lookup is not None:
        checks.append(registered_check(document, lookup))
    return dict(valid=all(check['ok'] for check in checks), id=manifest.get('id'), lot=manifest.get('lot'),
                rule=rule.get('version') if isinstance(rule, dict) else None,
                warm_seconds=conclusion.get('warm_seconds') if isinstance(conclusion, dict) else None, checks=checks)


def seal_check(document, manifest, evidence, conclusion):
    try:
        ok = set(evidence) == set(SECTIONS)
        seals = {name: sha(compact(evidence[name])) for name in SECTIONS}
        vid = sha(compact([manifest['receipt'], manifest['lot'], manifest['rule'],
                           manifest['evidence_sha256'], manifest['conclusion_sha256']]))
        ok = ok and seals == manifest['evidence_sha256'] and vid == manifest['id']
        ok = ok and sha(compact(conclusion)) == manifest['conclusion_sha256']
    except (KeyError, TypeError):
        ok = False
    return dict(check='seals', ok=bool(ok))


def coverage_check(manifest, periods, rows):
    detail = {}
    try:
        window = manifest['window']
        ok, attended, claimed, previous = True, 0, set(), None
        for period in sorted(periods, key=lambda row: (row['start'], row['end'])):
            ok &= window['start'] <= period['start'] < period['end'] <= window['end']
            ok &= previous is None or previous <= period['start']
            previous = period['end']
            point = period['start']
            for index, row in enumerate(rows):
                if not (period['start'] <= row['start'] and row['end'] <= period['end']):
                    continue
                claimed.add(index)
                ok &= row['start'] == point and row['seconds'] == row['end'] - row['start'] > 0
                point = row['end']
            ok &= point == period['end']
            attended += period['end'] - period['start']
        covered = sum(row['seconds'] for row in rows)
        ok &= len(claimed) == len(rows) and covered == attended
        detail = dict(attended_seconds=attended, covered_seconds=covered, slices=len(rows))
    except (KeyError, TypeError):
        ok = False
    return dict(check='coverage', ok=bool(ok), **detail)


def classification_check(rule, evidence, rows):
    try:
        versions = defaultdict(list)
        for version in evidence['slots']:
            versions[version['slot']].append(version)
        doors, _ = signals(evidence['doors'], rule['min_door_pulse'])
        temps, _ = normalize(evidence['temperatures'], rule)
        rebuilt = []
        for period in evidence['presence']:
            rebuilt.extend(slice_period(period, versions, doors, temps))
        ok = rebuilt == rows
    except Exception:
        ok = False
    return dict(check='classification', ok=bool(ok), slices=len(rows))


def totals_check(conclusion, periods, rows):
    try:
        counts = Counter({key: 0 for key in KINDS})
        for row in rows:
            counts[row['kind']] += row['seconds']
        ok = (dict(counts) == conclusion['counts'] and
              conclusion['attended_seconds'] == sum(row['end'] - row['start'] for row in periods) and
              conclusion['covered_seconds'] == sum(row['seconds'] for row in rows) and
              conclusion['warm_seconds'] == counts['warm'] and conclusion['warm_minutes'] == counts['warm'] // 60)
    except (KeyError, TypeError):
        ok = False
    return dict(check='totals', ok=bool(ok))


def registered_check(document, lookup):
    try:
        stored = lookup(document['manifest']['id'])
        ok = stored is not None and sha(compact(stored)) == sha(compact(document))
    except (KeyError, TypeError):
        ok = False
    return dict(check='registered', ok=bool(ok))
