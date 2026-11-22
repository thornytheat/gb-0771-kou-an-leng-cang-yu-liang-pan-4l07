from flask import Blueprint, current_app, jsonify, request
from app.faults import DockFault
from app.modules.history.routes import receipts
from .service import brief, issue, verify

voucher = Blueprint('voucher', __name__)


def vouchers():
    return current_app.extensions['ice_vouchers']


@voucher.post('/api/receipts/<key>/vouchers')
def publish(key):
    body = request.get_json(silent=True)
    lot = body.get('lot') if isinstance(body, dict) else None
    if not isinstance(lot, str) or not lot:
        raise DockFault('BAD_PACK', '签发需指明货批')
    return jsonify(issue(vouchers(), receipts().read(key), lot))


@voucher.get('/api/receipts/<key>/vouchers')
def listing(key):
    receipts().read(key)
    rows = [brief(document) for document in vouchers().for_receipt(key)]
    return jsonify(rows=rows, count=len(rows))


@voucher.get('/api/vouchers/<key>')
def detail(key):
    document = vouchers().read(key)
    return jsonify(manifest=document['manifest'], **brief(document))


@voucher.get('/api/vouchers/<key>/export')
def export(key):
    document = vouchers().read(key)
    response = jsonify(document)
    lot = document['manifest']['lot']
    response.headers['Content-Disposition'] = f'attachment; filename="voucher-{lot}-{key[:12]}.json"'
    return response


@voucher.get('/api/vouchers/<key>/verify')
def check_stored(key):
    return jsonify(verify(vouchers().read(key), lookup=vouchers().get))


@voucher.post('/api/vouchers/verify')
def check_posted():
    return jsonify(verify(request.get_json(silent=True), lookup=vouchers().get))
