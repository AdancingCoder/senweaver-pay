"""
测试配置管理
"""

import json
import os
import tempfile

import pytest

from senweaver_pay.config import ConfigManager
from senweaver_pay.exceptions import InvalidConfigException


def test_config_manager_initialization():
    manager = ConfigManager()
    assert not manager.is_initialized()


def test_load_config():
    manager = ConfigManager()
    config = {
        "alipay": {
            "default": {
                "app_id": "2021000000000001",
                "app_secret_cert": "test_secret",
                "app_public_cert_path": "/path/to/app_cert.pem",
                "alipay_public_cert_path": "/path/to/alipay_cert.pem",
                "alipay_root_cert_path": "/path/to/alipay_root_cert.pem",
            }
        }
    }
    manager.load_config(config)
    assert manager.is_initialized()

    loaded_config = manager.get_config()
    assert loaded_config.alipay["default"]["app_id"] == "2021000000000001"


def test_load_from_file():
    manager = ConfigManager()
    config = {
        "alipay": {
            "default": {
                "app_id": "2021000000000001",
                "app_secret_cert": "test_secret",
                "app_public_cert_path": "/path/to/app_cert.pem",
                "alipay_public_cert_path": "/path/to/alipay_cert.pem",
                "alipay_root_cert_path": "/path/to/alipay_root_cert.pem",
            }
        }
    }

    # 创建临时文件
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        json.dump(config, f)
        file_path = f.name

    try:
        manager.load_from_file(file_path)
        assert manager.is_initialized()

        loaded_config = manager.get_config()
        assert loaded_config.alipay["default"]["app_id"] == "2021000000000001"
    finally:
        # 清理临时文件
        os.unlink(file_path)


def test_get_channel_config():
    manager = ConfigManager()
    config = {
        "alipay": {
            "default": {
                "app_id": "2021000000000001",
            },
            "app2": {
                "app_id": "2021000000000002",
            },
        }
    }
    manager.load_config(config)

    default_config = manager.get_channel_config("alipay")
    assert default_config["app_id"] == "2021000000000001"

    app2_config = manager.get_channel_config("alipay", "app2")
    assert app2_config["app_id"] == "2021000000000002"


def test_invalid_config():
    manager = ConfigManager()

    # 测试未初始化异常
    with pytest.raises(InvalidConfigException):
        manager.get_config()

    # 测试渠道不存在异常
    manager.load_config({"alipay": {"default": {"app_id": "123"}}})
    with pytest.raises(InvalidConfigException):
        manager.get_channel_config("wechat")

    # 测试应用不存在异常
    with pytest.raises(InvalidConfigException):
        manager.get_channel_config("alipay", "non_exist_app")


def test_has_channel():
    manager = ConfigManager()
    config = {"alipay": {"default": {"app_id": "123"}}, "wechat": {"default": {"mch_id": "456"}}}
    manager.load_config(config)

    assert manager.has_channel("alipay")
    assert manager.has_channel("wechat")
    assert not manager.has_channel("douyin")
