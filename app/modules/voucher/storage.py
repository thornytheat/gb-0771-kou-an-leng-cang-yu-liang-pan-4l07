from contextlib import closing
import json
from pathlib import Path
import sqlite3
from app.faults import DockFault
from app.seals import compact


class Vouchers:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS vouchers '
                       '(id TEXT PRIMARY KEY, receipt TEXT NOT NULL, lot TEXT NOT NULL, rule TEXT NOT NULL, document TEXT NOT NULL)')

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def read(self, key):
        with closing(self.connect()) as db:
            row = db.execute('SELECT document FROM vouchers WHERE id=?', (key,)).fetchone()
        if row is None:
            raise DockFault('NO_VOUCHER', '超温凭证不存在', 404)
        return json.loads(row[0])

    def get(self, key):
        try:
            return self.read(key)
        except DockFault:
            return None

    def insert(self, document):
        manifest = document['manifest']
        with closing(self.connect()) as db, db:
            cursor = db.execute('INSERT OR IGNORE INTO vouchers VALUES (?,?,?,?,?)',
                                (manifest['id'], manifest['receipt'], manifest['lot'],
                                 manifest['rule']['version'], compact(document)))
            inserted = cursor.rowcount == 1
        return self.read(manifest['id']), not inserted

    def for_receipt(self, receipt):
        with closing(self.connect()) as db:
            return [json.loads(row[0]) for row in
                    db.execute('SELECT document FROM vouchers WHERE receipt=? ORDER BY rowid', (receipt,))]
