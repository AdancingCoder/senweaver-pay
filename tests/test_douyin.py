"""
测试抖音支付
"""

import pytest

from senweaver_pay import Pay
from senweaver_pay.channels.douyin.helper import generate_sign, verify_sign
from senweaver_pay.exceptions import InvalidConfigException

# Mock config
MOCK_DOUYIN_CONFIG = {
    "douyin": {
        "default": {
            "app_id": "800000000001",
            "app_secret": "1234567890abcdef1234567890abcdef",
            "token": "douyintoken12345",
            "salt": "douyinsalt12345",
            "notify_url": "https://example.com/douyin/notify",
            "mode": Pay.MODE_NORMAL,
        }
    }
}


def test_douyin_instantiation():
    Pay.config(MOCK_DOUYIN_CONFIG)
    douyin = Pay.douyin()
    assert douyin is not None
    assert douyin.channel == "douyin"


def test_douyin_missing_config():
    with pytest.raises(InvalidConfigException):
        Pay.config({})
        Pay.douyin().mini(
            {
                "out_order_no": "1234567890",
                "total_amount": 100,
                "subject": "test payment",
                "body": "test payment description",
            }
        )


def test_douyin_generate_sign():
    params = {
        "app_id": "800000000001",
        "timestamp": "1624266891",
        "out_order_no": "1234567890",
        "total_amount": 100,
        "subject": "test payment",
    }
    salt = "douyinsalt12345"

    signature = generate_sign(params, salt)
    assert signature is not None
    assert len(signature) > 0


def test_douyin_verify_sign():
    params = {
        "app_id": "800000000001",
        "timestamp": "1624266891",
        "out_order_no": "1234567890",
        "total_amount": 100,
        "subject": "test payment",
    }
    salt = "douyinsalt12345"

    # Generate signature
    signature = generate_sign(params, salt)

    # Add signature to params
    params_with_sign = params.copy()
    params_with_sign["sign"] = signature

    # Verify signature
    assert verify_sign(params_with_sign, salt)
