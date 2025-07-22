"""
通用工具函数，包含HTTP请求封装、签名计算、字符串处理等功能
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding

from .exceptions import InvalidArgumentException


def generate_nonce_str() -> str:
    """生成随机字符串"""
    return uuid.uuid4().hex.upper()


def generate_sign_str(params: Dict[str, Any], exclude_keys: List[str] | None = None) -> str:
    """
    根据参数字典生成待签名字符串
    :param params: 参数字典
    :param exclude_keys: 需要排除的参数名列表
    :return: 待签名字符串
    """
    if exclude_keys is None:
        exclude_keys = ["sign"]

    # 参数排序
    sorted_keys = sorted(params.keys())

    # 组装待签名字符串
    sign_parts = []
    for key in sorted_keys:
        # 跳过签名字段和值为None的字段
        if key in exclude_keys or params[key] is None or params[key] == "":
            continue
        value = params[key]
        # 字典转为json字符串
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        sign_parts.append(f"{key}={value}")

    # 使用&连接
    return "&".join(sign_parts)


def rsa_sign(plain_text: str, private_key, sign_type: str = "RSA2", use_cached_key: bool = False) -> str:
    """
    使用RSA算法对字符串进行签名
    :param plain_text: 待签名字符串
    :param private_key: 私钥，可以是PEM格式的字符串、文件路径或私钥对象
    :param sign_type: 签名类型，RSA或RSA2
    :param use_cached_key: 是否使用缓存的私钥对象
    :return: 签名字符串的base64编码
    """
    try:
        if use_cached_key:
            # 直接使用传入的私钥对象
            private_key_obj = private_key
        else:
            # 传统方式：从路径或字符串加载
            key_bytes = _get_key_content(private_key)
            private_key_obj = serialization.load_pem_private_key(key_bytes, password=None, backend=default_backend())

        # 选择签名算法
        if sign_type == "RSA":
            hash_algorithm = hashes.SHA1()
        elif sign_type == "RSA2":
            hash_algorithm = hashes.SHA256()
        else:
            raise InvalidArgumentException(f"Unsupported signature type: {sign_type}")

        # 计算签名
        signature = private_key_obj.sign(plain_text.encode("utf-8"), asym_padding.PKCS1v15(), hash_algorithm)

        # base64编码
        return base64.b64encode(signature).decode("utf-8")
    except Exception as e:
        if not use_cached_key:
            # 只有在不使用缓存密钥时才需要安全标识符
            safe_identifier = _get_safe_key_identifier(private_key)
            raise InvalidArgumentException(f"Failed to sign with private key {safe_identifier}: {str(e)}")
        else:
            raise InvalidArgumentException(f"Failed to sign with cached private key: {str(e)}")


def rsa_verify(plain_text: str, sign: str, public_key, sign_type: str = "RSA2", use_cached_key: bool = False) -> bool:
    """
    使用RSA算法验证签名
    :param plain_text: 原始字符串
    :param sign: 签名字符串的base64编码
    :param public_key: 公钥，可以是PEM格式的字符串、文件路径或公钥对象
    :param sign_type: 签名类型，RSA或RSA2
    :param use_cached_key: 是否使用缓存的公钥对象
    :return: 验证结果
    """
    if use_cached_key:
        # 直接使用传入的公钥对象
        public_key_obj = public_key
    else:
        # 传统方式：从路径或字符串加载
        key_bytes = _get_key_content(public_key)

        try:
            # 尝试直接加载公钥
            public_key_obj = serialization.load_pem_public_key(key_bytes, backend=default_backend())
        except ValueError:
            # 如果失败，尝试从证书中提取公钥
            try:
                import warnings
                # 忽略支付宝证书的NULL参数警告，这是已知问题且不影响功能
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", category=DeprecationWarning, 
                                         message=".*NULL parameter value.*")
                    cert = x509.load_pem_x509_certificate(key_bytes, backend=default_backend())
                    public_key_obj = cert.public_key()
            except ValueError:
                # 不暴露具体的公钥内容
                if not use_cached_key:
                    safe_identifier = _get_safe_key_identifier(public_key)
                    raise InvalidArgumentException(f"Invalid public key or certificate: {safe_identifier}")
                else:
                    raise InvalidArgumentException("Invalid cached public key or certificate")

    # 选择验签算法
    if sign_type == "RSA":
        hash_algorithm = hashes.SHA1()
    elif sign_type == "RSA2":
        hash_algorithm = hashes.SHA256()
    else:
        raise InvalidArgumentException(f"Unsupported signature type: {sign_type}")

    # base64解码签名
    signature = base64.b64decode(sign)

    # 验证签名
    try:
        public_key_obj.verify(signature, plain_text.encode("utf-8"), asym_padding.PKCS1v15(), hash_algorithm)
        return True
    except Exception:
        return False


def hmac_sign(plain_text: str, key: str, algorithm: str = "sha256") -> str:
    """
    使用HMAC算法对字符串进行签名
    :param plain_text: 待签名字符串
    :param key: 密钥
    :param algorithm: 哈希算法
    :return: 签名字符串(十六进制)
    """
    key_bytes = key.encode("utf-8")
    message = plain_text.encode("utf-8")

    if algorithm == "sha256":
        signature = hmac.new(key_bytes, message, hashlib.sha256).hexdigest()
    elif algorithm == "sha1":
        signature = hmac.new(key_bytes, message, hashlib.sha1).hexdigest()
    elif algorithm == "md5":
        signature = hmac.new(key_bytes, message, hashlib.md5).hexdigest()
    else:
        raise InvalidArgumentException(f"Unsupported HMAC algorithm: {algorithm}")

    return signature


def sha256_sign(plain_text: str) -> str:
    """计算字符串的SHA256哈希值(十六进制)"""
    return hashlib.sha256(plain_text.encode("utf-8")).hexdigest()


def md5_sign(plain_text: str) -> str:
    """计算字符串的MD5哈希值(十六进制)"""
    return hashlib.md5(plain_text.encode("utf-8")).hexdigest()


def setup_logger(
    name: str, log_file: str, level: str = "info", log_type: str = "single", max_file: int = 30
) -> logging.Logger:
    """
    设置日志记录器
    :param name: 记录器名称
    :param log_file: 日志文件路径
    :param level: 日志级别
    :param log_type: 日志类型(single/daily)
    :param max_file: 当log_type为daily时，保留的最大日志文件数
    :return: 日志记录器
    """
    # 创建日志目录
    log_dir = os.path.dirname(log_file)
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # 设置日志级别
    level_map = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
        "critical": logging.CRITICAL,
    }
    log_level = level_map.get(level.lower(), logging.INFO)

    # 创建日志记录器
    logger = logging.getLogger(name)
    logger.setLevel(log_level)

    # 清除现有处理程序
    if logger.handlers:
        logger.handlers.clear()

    # 处理日志文件名
    if log_type == "daily":
        log_file = f"{log_file}.{datetime.now().strftime('%Y-%m-%d')}"

    # 创建文件处理程序
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(log_level)

    # 设置格式
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(formatter)

    # 添加处理程序
    logger.addHandler(file_handler)

    return logger


# HTTP方法已移至各渠道的 helper.py 中
# 每个渠道根据自己的特殊需求实现专用的 http_get 和 http_post 方法


def _get_key_content(key: str) -> bytes:
    """
    获取密钥内容，可以是文件路径或直接是密钥内容
    :param key: 密钥字符串或文件路径
    :return: 密钥内容的字节串
    """
    key_path = Path(key)
    if key_path.is_file():
        with open(key_path, "rb") as f:
            key_content = f.read()
    else:
        # 如果key本身就是内容，确保它是PEM格式的
        key_content = key.encode("utf-8")
        # 如果不包含BEGIN和END标记，添加它们
        if b"-----BEGIN" not in key_content:
            # 尝试PKCS#8格式（推荐格式）
            key_content = f"-----BEGIN PRIVATE KEY-----\n{key}\n-----END PRIVATE KEY-----".encode("utf-8")

    return key_content


def _is_key_content(key: str) -> bool:
    """
    判断是否为密钥内容而不是文件路径
    :param key: 密钥字符串或文件路径
    :return: True表示是密钥内容，False表示是文件路径
    """
    # 检查是否包含PEM格式的标记
    if "-----BEGIN" in key and "-----END" in key:
        return True

    # 检查是否为文件路径
    key_path = Path(key)
    if key_path.is_file():
        return False

    # 如果不是文件路径且包含换行符，可能是密钥内容
    if "\n" in key or len(key) > 100:
        return True

    return False


def _get_safe_key_identifier(key: str) -> str:
    """
    获取安全的密钥标识符，用于日志和异常消息
    :param key: 密钥字符串或文件路径
    :return: 安全的标识符
    """
    if _is_key_content(key):
        return "[PRIVATE_KEY_CONTENT]"
    else:
        return key
