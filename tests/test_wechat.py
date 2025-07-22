"""
测试微信支付
"""

import pytest

from senweaver_pay import Pay
from senweaver_pay.channels.wechat.helper import build_authorization, get_api_url
from senweaver_pay.exceptions import InvalidConfigException

# Mock config
MOCK_WECHAT_CONFIG = {
    "wechat": {
        "default": {
            "mch_id": "1600314069",
            "mch_secret_key": "e7368d422cfea4b70e91165e522c8fhr",
            "mch_secret_cert": ("-----BEGIN PRIVATE KEY-----\n...-----END PRIVATE KEY-----\n"),
            "mch_public_cert_path": "/path/to/cert.pem",
            "mp_app_id": "wx55955316af4ef13",
            "mini_app_id": "wx55955316af4ef14",
            "app_id": "wx55955316af4ef15",
            "notify_url": "https://example.com/wechat/notify",
            "serial_number": "12345678",  # Mock serial number
            "mode": Pay.MODE_NORMAL,
        }
    }
}


def test_wechat_instantiation():
    Pay.config(MOCK_WECHAT_CONFIG)
    wechat = Pay.wechat()
    assert wechat is not None
    assert wechat.channel == "wechat"


def test_wechat_missing_config():
    with pytest.raises(InvalidConfigException):
        Pay.config({})
        Pay.wechat().mp(
            {
                "out_trade_no": "1234567890",
                "description": "test payment",
                "amount": {"total": 100},
                "payer": {"openid": "test_openid"},
            }
        )


def test_wechat_api_url():
    url = get_api_url("/pay/transactions/jsapi")
    assert url == "https://api.mch.weixin.qq.com/v3/pay/transactions/jsapi"


def test_wechat_build_authorization():
    auth = build_authorization(
        mch_id="1600314069",
        nonce_str="1234567890",
        timestamp="1624266891",
        signature="abcdef1234567890",
        serial_no="12345678",
    )
    assert "WECHATPAY2-SHA256-RSA2048" in auth
    assert 'mchid="1600314069"' in auth
    assert 'serial_no="12345678"' in auth
