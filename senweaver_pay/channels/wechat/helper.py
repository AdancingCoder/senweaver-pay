"""
微信支付特有的工具函数
"""

import base64
import json
from typing import Any, Dict, Optional

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ...constants import WECHAT_API_V3_URL
from ...exceptions import InvalidConfigException
from ...helper import _get_key_content


def get_api_url(path: str) -> str:
    """
    获取微信支付 API URL
    :param path: API 路径
    :return: API URL
    """
    return f"{WECHAT_API_V3_URL}{path}"


def generate_sign(method: str, url_path: str, timestamp: str, nonce_str: str, body: str, mch_private_key: str) -> str:
    """
    生成请求签名
    :param method: HTTP 方法
    :param url_path: URL 路径
    :param timestamp: 时间戳
    :param nonce_str: 随机字符串
    :param body: 请求体
    :param mch_private_key: 商户私钥或私钥文件路径
    :return: 签名
    """
    try:
        # 构造签名串
        sign_str = f"{method}\n{url_path}\n{timestamp}\n{nonce_str}\n{body}\n"

        # 加载私钥
        key_bytes = _get_key_content(mch_private_key)
        # 微信支付V3推荐使用PKCS#8格式
        private_key = serialization.load_pem_private_key(key_bytes, password=None, backend=default_backend())
        # 计算签名
        signature = private_key.sign(sign_str.encode("utf-8"), asym_padding.PKCS1v15(), hashes.SHA256())

        # Base64 编码
        return base64.b64encode(signature).decode("utf-8")
    except Exception as e:
        from ...helper import _get_safe_key_identifier
        safe_identifier = _get_safe_key_identifier(mch_private_key)
        raise InvalidConfigException(f"Failed to generate signature with key {safe_identifier}: {str(e)}")


def build_authorization(mch_id: str, nonce_str: str, timestamp: str, signature: str, serial_no: str = "") -> str:
    """
    构建请求头中的 Authorization
    :param mch_id: 商户号
    :param nonce_str: 随机字符串
    :param timestamp: 时间戳
    :param signature: 签名
    :param serial_no: 证书序列号
    :return: Authorization 字符串
    """
    # 验证必要参数
    if not mch_id or not nonce_str or not timestamp or not signature:
        raise InvalidConfigException(
            f"Missing required parameters for authorization: mch_id={mch_id}, nonce_str={nonce_str}, timestamp={timestamp}, signature={'***' if signature else 'None'}"
        )

    auth_parts = [
        f'mchid="{mch_id}"',
        f'nonce_str="{nonce_str}"',
        f'signature="{signature}"',
        f'timestamp="{timestamp}"',
    ]

    if serial_no:
        auth_parts.append(f'serial_no="{serial_no}"')

    return f"WECHATPAY2-SHA256-RSA2048 {','.join(auth_parts)}"


def generate_client_sign(app_id: str, timestamp: str, nonce_str: str, package: str, mch_private_key: str) -> str:
    """
    生成客户端支付签名（用于小程序、公众号等前端调起支付）
    :param app_id: 应用ID
    :param timestamp: 时间戳
    :param nonce_str: 随机字符串
    :param package: 订单详情扩展字符串
    :param mch_private_key: 商户私钥或私钥文件路径
    :return: 签名
    """
    try:
        # 构造签名串（客户端签名格式）
        sign_str = f"{app_id}\n{timestamp}\n{nonce_str}\n{package}\n"

        # 加载私钥
        key_bytes = _get_key_content(mch_private_key)
        private_key = serialization.load_pem_private_key(key_bytes, password=None, backend=default_backend())

        # 计算签名
        signature = private_key.sign(sign_str.encode("utf-8"), asym_padding.PKCS1v15(), hashes.SHA256())

        # Base64 编码
        return base64.b64encode(signature).decode("utf-8")
    except Exception as e:
        from ...helper import _get_safe_key_identifier
        safe_identifier = _get_safe_key_identifier(mch_private_key)
        raise InvalidConfigException(f"Failed to generate client signature with key {safe_identifier}: {str(e)}")


def get_wechatpay_header(headers, key):
    """
    智能获取微信支付头部，兼容不同框架的命名方式：
    - 标准格式：Wechatpay-Signature
    - Django：HTTP_WECHATPAY_SIGNATURE
    - FastAPI：wechatpay-signature
    - 其他可能变种：wechatpay_signature, WECHATPAY-SIGNATURE 等
    """
    # 可能的头部名称变种（按优先级检查）
    header_variants = [
        f"Wechatpay-{key}",  # 标准
        f"HTTP_WECHATPAY_{key.upper()}",  # Django
        f"wechatpay-{key.lower()}",  # FastAPI
        f"wechatpay_{key.lower()}",  # Nginx 可能转换
        f"WECHATPAY-{key.upper()}",  # 全大写
    ]

    for variant in header_variants:
        if variant in headers:
            return headers.get(variant)

    return ""


def rsa_encrypt(message: str, certificate: str) -> str:
    """
    RSA 加密
    :param message: 明文
    :param certificate: 证书内容或路径
    :return: Base64 编码的密文
    """
    # 加载证书
    cert_bytes = _get_key_content(certificate)
    cert = x509.load_pem_x509_certificate(cert_bytes, default_backend())
    public_key = cert.public_key()

    # 加密
    ciphertext = public_key.encrypt(
        message.encode("utf-8"),
        asym_padding.OAEP(mgf=asym_padding.MGF1(algorithm=hashes.SHA1()), algorithm=hashes.SHA1(), label=None),
    )

    # Base64 编码
    return base64.b64encode(ciphertext).decode("utf-8")


def aes_decrypt(ciphertext: str, key: str, nonce: str, associated_data: str) -> str:
    """
    AES-GCM 解密
    :param ciphertext: Base64 编码的密文
    :param key: 密钥
    :param nonce: 随机串
    :param associated_data: 附加数据
    :return: 明文
    """
    # Base64 解码密文
    data = base64.b64decode(ciphertext)

    # 使用 AES-GCM 解密
    cipher = Cipher(
        algorithms.AES(key.encode("utf-8")), modes.GCM(nonce.encode("utf-8"), tag=data[-16:]), backend=default_backend()
    )
    decryptor = cipher.decryptor()

    # 添加附加数据
    decryptor.authenticate_additional_data(associated_data.encode("utf-8"))

    # 解密
    plaintext = decryptor.update(data[:-16]) + decryptor.finalize()

    return plaintext.decode("utf-8")


# ==================== 微信专用HTTP方法 ====================

import requests

from ...exceptions import WechatException


def http_get(
    url: str, params: Optional[Dict[str, Any]] = None, config: Optional[Dict[str, Any]] = None, **kwargs
) -> Dict[str, Any]:
    """
    微信专用HTTP GET请求
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
            "Accept": "application/json",
            "User-Agent": "senweaver-pay-wechat/1.0",
            **config.get("headers", {}),
        }

        response = requests.get(url, params=params, headers=headers, timeout=timeout, **kwargs)

        return _process_wechat_response(response, config)

    except requests.RequestException as e:
        raise WechatException(f"HTTP GET request failed: {e}")
    except Exception as e:
        raise WechatException(f"Unexpected error in HTTP GET request: {e}")


def http_post(
    url: str, data: Optional[Dict[str, Any]] = None, config: Optional[Dict[str, Any]] = None, **kwargs
) -> Dict[str, Any]:
    """
    微信专用HTTP POST请求
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
            "Accept": "application/json",
            "User-Agent": "senweaver-pay-wechat/1.0",
            **config.get("headers", {}),
        }
        response = requests.post(url, json=data, headers=headers, timeout=timeout, **kwargs)

        return _process_wechat_response(response, config)

    except requests.RequestException as e:
        raise WechatException(f"HTTP POST request failed: {e}")
    except Exception as e:
        raise WechatException(f"Unexpected error in HTTP POST request: {e}")


def _process_wechat_response(response: requests.Response, config: Dict[str, Any]) -> Dict[str, Any]:
    """
    处理微信响应
    :param response: HTTP响应对象
    :param config: 配置信息
    :return: 处理后的响应数据
    """
    result = {"status_code": response.status_code, "headers": dict(response.headers), "text": response.text}

    # 微信特定的状态码处理
    if response.status_code == 204:
        # 微信某些接口返回204表示成功
        result["data"] = {"success": True}
        return result

    # 对于非200状态码，不在这里抛出异常，让调用方处理
    # 这样可以保持错误处理逻辑的一致性
    if response.status_code == 200:
        # 只有200状态码才尝试解析JSON
        try:
            response_data = response.json()
            result["data"] = response_data
        except (ValueError, json.JSONDecodeError):
            # 如果无法解析JSON，保持text内容，让调用方处理
            pass

    return result
