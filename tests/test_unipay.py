"""
测试银联支付
"""

import pytest

from senweaver_pay import Pay
from senweaver_pay.channels.unipay.helper import build_form
from senweaver_pay.constants import (
    UNIPAY_BACKEND_TRANSACTION_URL,
    UNIPAY_BASE_URL,
    UNIPAY_FRONTEND_TRANSACTION_URL,
)
from senweaver_pay.exceptions import InvalidConfigException

# Mock config
MOCK_UNIPAY_CONFIG = {
    "unipay": {
        "default": {
            "mer_id": "123456789012345",
            "mer_private_key_path": "/path/to/private_key.pem",
            "mer_public_cert_path": "/path/to/public_cert.pem",
            "unipay_public_cert_path": "/path/to/unipay_cert.pem",
            "notify_url": "https://example.com/unipay/notify",
            "front_url": "https://example.com/unipay/return",
            "mode": Pay.MODE_NORMAL,
        }
    }
}


def test_unipay_instantiation():
    Pay.config(MOCK_UNIPAY_CONFIG)
    unipay = Pay.unipay()
    assert unipay is not None
    assert unipay.channel == "unipay"


def test_unipay_missing_config():
    with pytest.raises(InvalidConfigException):
        Pay.config({})
        Pay.unipay().web({"order_id": "1234567890", "txn_amt": "100", "order_desc": "test payment"})


def test_unipay_base_url():
    assert UNIPAY_BASE_URL["NORMAL"] == "https://gateway.95516.com"
    assert UNIPAY_BASE_URL["SANDBOX"] == "https://gateway.test.95516.com"


def test_unipay_transaction_url():
    assert UNIPAY_FRONTEND_TRANSACTION_URL == "/gateway/api/frontTransReq.do"
    assert UNIPAY_BACKEND_TRANSACTION_URL == "/gateway/api/backTransReq.do"


def test_unipay_build_form():
    action_url = "https://gateway.95516.com/gateway/api/frontTransReq.do"
    params = {
        "version": "5.1.0",
        "encoding": "UTF-8",
        "txnType": "01",
        "txnSubType": "01",
        "bizType": "000201",
        "channelType": "07",
        "merId": "123456789012345",
        "orderId": "1234567890",
        "txnTime": "20220101120000",
        "txnAmt": "100",
        "currencyCode": "156",
        "orderDesc": "test payment",
        "signature": "mock_signature",
    }

    form = build_form(action_url, params)
    assert 'form id="unipay_payment_form"' in form
    assert 'action="https://gateway.95516.com/gateway/api/frontTransReq.do"' in form
    assert 'name="version" value="5.1.0"' in form
    assert 'name="signature" value="mock_signature"' in form
