"""
测试支付宝支付
"""

import pytest

from senweaver_pay import Pay
from senweaver_pay.channels.alipay.helper import prepare_public_params
from senweaver_pay.exceptions import InvalidConfigException

# Mock config
MOCK_ALIPAY_CONFIG = {
    "alipay": {
        "default": {
            "app_id": "2021000000000001",
            "app_secret_cert": (
                "-----BEGIN RSA PRIVATE KEY-----\n...-----END RSA PRIVATE KEY-----\n"
            ),  # Dummy key content
            "app_public_cert_path": "/path/to/appCertPublicKey.crt",
            "alipay_public_cert_path": "/path/to/alipayCertPublicKey_RSA2.crt",
            "alipay_root_cert_path": "/path/to/alipayRootCert.crt",
            "notify_url": "https://example.com/notify",
            "return_url": "https://example.com/return",
            "mode": Pay.MODE_NORMAL,
        }
    }
}


def test_alipay_instantiation():
    Pay.config(MOCK_ALIPAY_CONFIG)
    alipay = Pay.alipay()
    assert alipay is not None
    assert alipay.channel == "alipay"


def test_alipay_missing_config():
    with pytest.raises(InvalidConfigException):
        Pay.config({})
        Pay.alipay().web({"out_trade_no": "123", "total_amount": "1", "subject": "Test"})  # Should raise error


def test_alipay_prepare_public_params():
    from unittest.mock import patch
    
    config = MOCK_ALIPAY_CONFIG["alipay"]["default"]
    method = "alipay.trade.page.pay"
    
    # Mock证书序列号提取，避免实际读取证书文件
    with patch('senweaver_pay.channels.alipay.helper.extract_cert_serial_number') as mock_extract_cert, \
         patch('senweaver_pay.channels.alipay.helper.extract_root_cert_serial_numbers') as mock_extract_root:
        
        mock_extract_cert.return_value = "MOCK_APP_CERT_SN"
        mock_extract_root.return_value = "MOCK_ROOT_CERT_SN"
        
        params = prepare_public_params(config, method)
        
        assert params["app_id"] == config["app_id"]
        assert params["method"] == method
        assert params["notify_url"] == config["notify_url"]
        assert params["return_url"] == config["return_url"]
        assert "timestamp" in params
        assert params["sign_type"] == "RSA2"
        
        # 验证证书序列号参数
        assert params["app_cert_sn"] == "MOCK_APP_CERT_SN"
        assert params["alipay_root_cert_sn"] == "MOCK_ROOT_CERT_SN"
