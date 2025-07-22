"""
测试通用工具函数
"""

import pytest

from senweaver_pay.exceptions import InvalidArgumentException
from senweaver_pay.helper import generate_random_string, generate_sign_str, rsa_sign, rsa_verify


def test_generate_random_string():
    assert len(generate_random_string()) == 32
    assert len(generate_random_string(16)) == 16
    assert isinstance(generate_random_string(), str)


def test_generate_sign_str():
    params1 = {"a": "1", "b": "2", "c": None, "d": ""}
    assert generate_sign_str(params1) == "a=1&b=2"

    params2 = {"b": "2", "a": "1"}
    assert generate_sign_str(params2) == "a=1&b=2"

    params3 = {"a": "1", "sign": "ignore", "b": "2"}
    assert generate_sign_str(params3) == "a=1&b=2"

    params4 = {"a": "1", "b": {"sub_b": "sb", "sub_a": "sa"}, "c": "3"}
    expected_sign_str = 'a=1&b={"sub_a":"sa","sub_b":"sb"}&c=3'
    assert generate_sign_str(params4) == expected_sign_str


def test_rsa_verify(private_key, public_key):
    # 测试正常的签名和验证
    plain_text = "test content"
    sign = rsa_sign(plain_text, private_key)
    assert rsa_verify(plain_text, sign, public_key) is True

    # 测试使用错误的公钥进行验证
    wrong_plain_text = "wrong content"
    assert rsa_verify(wrong_plain_text, sign, public_key) is False

    # 测试无效的签名类型
    with pytest.raises(InvalidArgumentException):
        rsa_verify(plain_text, sign, public_key, sign_type="RSA1")

    # 测试无效的公钥
    with pytest.raises(InvalidArgumentException):
        rsa_verify(plain_text, sign, "invalid_public_key")
