"""
银联支付特有的工具函数
"""

import base64
from typing import Any, Dict

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from ...exceptions import InvalidConfigException


def encrypt_sensitive_data(data: str, public_key_obj) -> str:
    """
    使用缓存的银联公钥加密敏感信息
    :param data: 待加密数据
    :param public_key_obj: 公钥对象
    :return: Base64编码的加密数据
    """
    # 加密数据
    encrypted_data = public_key_obj.encrypt(data.encode("utf-8"), padding.PKCS1v15())

    # Base64 编码
    return base64.b64encode(encrypted_data).decode("utf-8")


def sign_params(params: Dict[str, Any], private_key_obj) -> str:
    """
    使用缓存的私钥对象对参数进行签名
    :param params: 参数
    :param private_key_obj: 私钥对象
    :return: 签名
    """
    # 按照key排序，拼接成key=value的形式
    sorted_items = sorted(params.items())
    unsigned_string = "&".join([f"{k}={v}" for k, v in sorted_items if v])

    # 对数据进行签名
    signature = private_key_obj.sign(unsigned_string.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())

    # Base64编码签名
    return base64.b64encode(signature).decode("utf-8")


def verify_sign(params: Dict[str, Any], signature: str, public_key_obj) -> bool:
    """
    使用缓存的公钥对象验证签名
    :param params: 参数
    :param signature: 签名
    :param public_key_obj: 公钥对象
    :return: 验证结果
    """
    # 按照key排序，拼接成key=value的形式
    sorted_items = sorted(params.items())
    unsigned_string = "&".join([f"{k}={v}" for k, v in sorted_items if v])

    # Base64解码签名
    signature_bytes = base64.b64decode(signature)

    # 验证签名
    try:
        public_key_obj.verify(signature_bytes, unsigned_string.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:
        return False


def build_form(action_url: str, params: Dict[str, Any]) -> str:
    """
    构建表单
    :param action_url: 表单提交地址
    :param params: 表单参数
    :return: HTML表单
    """
    form_items = []
    for k, v in params.items():
        form_items.append(f'<input type="hidden" name="{k}" value="{v}">')

    form = f"""
    <form id="unipay_payment_form" action="{action_url}" method="post">
        {"".join(form_items)}
    </form>
    <script>document.getElementById('unipay_payment_form').submit();</script>
    """
    return form


def verify_callback(params: Dict[str, Any], config: Dict[str, Any]) -> bool:
    """
    验证回调通知
    :param params: 回调参数
    :param config: 配置
    :return: 验证结果
    """
    # 获取签名和签名类型
    signature = params.pop("signature", "")
    params.pop("signMethod", "")  # 移除但不使用

    if not signature:
        return False

    # 验证签名
    unipay_public_cert_path = config.get("unipay_public_cert_path")
    if not unipay_public_cert_path:
        raise InvalidConfigException("Missing config: unipay_public_cert_path")

    return verify_sign(params, signature, unipay_public_cert_path)


# ==================== 银联专用HTTP方法 ====================

import json
from typing import Optional

import requests

from ...exceptions import UnipayException


def http_get(
    url: str, params: Optional[Dict[str, Any]] = None, config: Optional[Dict[str, Any]] = None, **kwargs
) -> Dict[str, Any]:
    """
    银联专用HTTP GET请求
    :param url: 请求URL
    :param params: 请求参数
    :param config: 配置信息
    :param kwargs: 其他参数
    :return: 响应数据
    """
    config = config or {}
    timeout = config.get("timeout", 30)

    try:
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "User-Agent": "senweaver-pay-unipay/1.0",
            **config.get("headers", {}),
        }

        response = requests.get(url, params=params, headers=headers, timeout=timeout, **kwargs)

        return _process_unipay_response(response, config)

    except requests.RequestException as e:
        raise UnipayException(f"HTTP GET request failed: {e}")
    except Exception as e:
        raise UnipayException(f"Unexpected error in HTTP GET request: {e}")


def http_post(
    url: str, data: Optional[Dict[str, Any]] = None, config: Optional[Dict[str, Any]] = None, **kwargs
) -> Dict[str, Any]:
    """
    银联专用HTTP POST请求
    :param url: 请求URL
    :param data: 请求数据
    :param config: 配置信息
    :param kwargs: 其他参数
    :return: 响应数据
    """
    config = config or {}
    timeout = config.get("timeout", 30)

    try:
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "User-Agent": "senweaver-pay-unipay/1.0",
            **config.get("headers", {}),
        }

        response = requests.post(
            url,
            data=data,  # 银联使用表单数据
            headers=headers,
            timeout=timeout,
            **kwargs,
        )

        return _process_unipay_response(response, config)

    except requests.RequestException as e:
        raise UnipayException(f"HTTP POST request failed: {e}")
    except Exception as e:
        raise UnipayException(f"Unexpected error in HTTP POST request: {e}")


def _process_unipay_response(response: requests.Response, config: Dict[str, Any]) -> Dict[str, Any]:
    """
    处理银联响应
    :param response: HTTP响应对象
    :param config: 配置信息
    :return: 处理后的响应数据
    """
    result = {"status_code": response.status_code, "headers": dict(response.headers), "text": response.text}

    # 检查HTTP状态码
    if response.status_code != 200:
        raise UnipayException(f"HTTP request failed with status {response.status_code}: {response.text}")

    # 银联通常返回表单格式的数据
    try:
        from urllib.parse import parse_qs

        parsed_data = parse_qs(response.text)
        # 将列表值转换为单个值
        response_data = {k: v[0] if len(v) == 1 else v for k, v in parsed_data.items()}
        result["data"] = response_data
    except Exception:
        # 如果解析失败，尝试JSON格式
        try:
            response_data = response.json()
            result["data"] = response_data
        except (ValueError, json.JSONDecodeError):
            # 都失败了，返回原始文本
            result["data"] = {"raw_response": response.text}

    # 银联特定的响应验证
    if config.get("verify_response", True):
        _verify_unipay_response(result, config)

    return result


def _verify_unipay_response(response_data: Dict[str, Any], config: Dict[str, Any]) -> None:
    """
    验证银联响应
    :param response_data: 响应数据
    :param config: 配置信息
    """
    data = response_data.get("data", {})

    # 检查业务错误
    if isinstance(data, dict):
        resp_code = data.get("respCode")
        if resp_code and resp_code != "00":
            error_msg = data.get("respMsg", "Unknown error")
            raise UnipayException(f"Unipay business error: {resp_code} - {error_msg}")
