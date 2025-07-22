"""
抖音支付特有的工具函数
"""

import hashlib
import hmac
from typing import Any, Dict

from ...exceptions import InvalidConfigException, InvalidSignException


def generate_sign(params: Dict[str, Any], salt: str) -> str:
    """
    生成签名
    :param params: 参数
    :param salt: 密钥
    :return: 签名
    """
    # 按照key排序，拼接成key=value的形式
    sign_str = "&".join([f"{k}={params[k]}" for k in sorted(params.keys())])

    # 计算签名
    return hmac.new(salt.encode("utf-8"), sign_str.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_sign(params: Dict[str, Any], salt: str) -> bool:
    """
    验证签名
    :param params: 参数
    :param salt: 密钥
    :return: 验证结果
    """
    if "sign" not in params:
        return False

    # 获取签名
    sign = params.pop("sign")

    # 计算签名
    calculated_sign = generate_sign(params, salt)

    # 比较签名
    return sign == calculated_sign


def verify_callback(params: Dict[str, Any], config: Dict[str, Any]) -> bool:
    """
    验证回调通知
    :param params: 回调参数
    :param config: 配置
    :return: 验证结果
    """
    token = config.get("token")
    if not token:
        raise InvalidConfigException("Missing config: token")

    # 验证签名
    if not verify_sign(params, token):
        raise InvalidSignException("Invalid signature in callback")

    return True


# ==================== 抖音专用HTTP方法 ====================

import json
from typing import Optional

import requests

from ...exceptions import DouyinException


def http_get(
    url: str, params: Optional[Dict[str, Any]] = None, config: Optional[Dict[str, Any]] = None, **kwargs
) -> Dict[str, Any]:
    """
    抖音专用HTTP GET请求
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
            "Content-Type": "application/json",
            "User-Agent": "senweaver-pay-douyin/1.0",
            **config.get("headers", {}),
        }

        response = requests.get(url, params=params, headers=headers, timeout=timeout, **kwargs)

        return _process_douyin_response(response, config)

    except requests.RequestException as e:
        raise DouyinException(f"HTTP GET request failed: {e}")
    except Exception as e:
        raise DouyinException(f"Unexpected error in HTTP GET request: {e}")


def http_post(
    url: str, data: Optional[Dict[str, Any]] = None, config: Optional[Dict[str, Any]] = None, **kwargs
) -> Dict[str, Any]:
    """
    抖音专用HTTP POST请求
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
            "Content-Type": "application/json",
            "User-Agent": "senweaver-pay-douyin/1.0",
            **config.get("headers", {}),
        }

        response = requests.post(url, json=data, headers=headers, timeout=timeout, **kwargs)

        return _process_douyin_response(response, config)

    except requests.RequestException as e:
        raise DouyinException(f"HTTP POST request failed: {e}")
    except Exception as e:
        raise DouyinException(f"Unexpected error in HTTP POST request: {e}")


def _process_douyin_response(response: requests.Response, config: Dict[str, Any]) -> Dict[str, Any]:
    """
    处理抖音响应
    :param response: HTTP响应对象
    :param config: 配置信息
    :return: 处理后的响应数据
    """
    result = {"status_code": response.status_code, "headers": dict(response.headers), "text": response.text}

    # 检查HTTP状态码
    if response.status_code != 200:
        try:
            error_data = response.json()
            error_msg = error_data.get("err_tips", f"HTTP {response.status_code}")
            raise DouyinException(f"Douyin API error: {error_msg}")
        except (ValueError, json.JSONDecodeError):
            raise DouyinException(f"HTTP request failed with status {response.status_code}: {response.text}")

    # 解析JSON响应
    try:
        response_data = response.json()
        result["data"] = response_data
    except (ValueError, json.JSONDecodeError) as e:
        raise DouyinException(f"Failed to parse response JSON: {e}")

    # 抖音特定的响应验证
    if config.get("verify_response", True):
        _verify_douyin_response(result, config)

    return result


def _verify_douyin_response(response_data: Dict[str, Any], config: Dict[str, Any]) -> None:
    """
    验证抖音响应
    :param response_data: 响应数据
    :param config: 配置信息
    """
    data = response_data.get("data", {})

    # 检查业务错误
    if isinstance(data, dict):
        err_no = data.get("err_no")
        if err_no and err_no != "0":
            error_msg = data.get("err_tips", "Unknown error")
            raise DouyinException(f"Douyin business error: {err_no} - {error_msg}")
