from collections import defaultdict
from decimal import Decimal
import re
from app.rules import current as current_rules


def normalize(rows, rules=None):
    if rules is None:
        rules = current_rules()
    limit = Decimal(rules['warm_limit'])
    max_age = rules['temp_max_age']
    grouped = defaultdict(lambda: defaultdict(list))
    isolated = []
    for row in rows:
        text = row['celsius']
        if not re.fullmatch(r'-?[0-9]{1,3}(?:\.[0-9]{1,3})?', text) or not -90 <= Decimal(text) <= 80:
            isolated.append(dict(row, code='TEMP_INVALID'))
            continue
        grouped[row['door']][row['time']].append((Decimal(text), row['line']))
    history = {}
    for door, timestamps in sorted(grouped.items()):
        history[door] = []
        for time, group in sorted(timestamps.items()):
            values = {item[0] for item in group}
            lines = [item[1] for item in group]
            if len(values) != 1:
                isolated.append(dict(code='TEMP_CONFLICT', door=door, time=time, lines=lines))
            value = next(iter(values)) if len(values) == 1 else None
            history[door].append(dict(time=time, end=time + max_age, value=str(value) if value is not None else None,
                                      warm=value > limit if value is not None else None, lines=lines))
    return history, isolated
