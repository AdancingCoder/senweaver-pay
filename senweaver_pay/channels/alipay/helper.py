"""
支付宝支付特有的工具函数
"""

import hashlib
import html
import json
import time
import urllib.parse
import warnings
from typing import Any, Dict, Optional

from cryptography import x509

from ...constants import ALIPAY_CHARSET, ALIPAY_SIGN_TYPE
from ...exceptions import InvalidConfigException
from ...helper import generate_sign_str, rsa_sign, rsa_verify

# OID到缩写映射
OID_MAP = {
    '2.5.4.3': 'CN',
    '2.5.4.10': 'O',
    '2.5.4.11': 'OU',
    '2.5.4.6': 'C',
}

def get_short_name(attr):
    return OID_MAP.get(attr.oid.dotted_string, attr.oid._name)

def format_issuer(issuer):
    # 反转顺序，缩写
    return ','.join(
        f"{get_short_name(attr)}={attr.value}"
        for attr in reversed(list(issuer))
    )

def format_serial_number(cert):
    # 十进制字符串
    return str(cert.serial_number)

def get_signature_type_ln(cert):
    # 兼容 cryptography 的算法名
    algo = cert.signature_algorithm_oid._name.lower()
    if algo == 'sha1withrsaencryption':
        return 'sha1WithRSAEncryption'
    if algo == 'sha256withrsaencryption':
        return 'sha256WithRSAEncryption'
    return algo

def get_root_cert_sn(cert_path: str,config: Dict[str, Any]) -> str:
    """
    读取支付宝根证书文件，返回 alipay_root_cert_sn
    :param cert_path: 根证书文件路径
    :return: 根证书序列号字符串
    """
    alipay_root_cert_sn = config.get('alipay_root_cert_sn')
    if alipay_root_cert_sn:
        return alipay_root_cert_sn
    with open(cert_path, 'r', encoding='utf-8') as f:
        root_cert_content = f.read()
    certificates = [c + '-----END CERTIFICATE-----' for c in root_cert_content.split('-----END CERTIFICATE-----') if c.strip()]
    sn_list = []
    for cert_content in certificates:
        try:
            # 忽略支付宝证书的NULL参数警告，这是已知问题且不影响功能
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=DeprecationWarning,
                                     message=".*NULL parameter value.*")
                cert = x509.load_pem_x509_certificate(cert_content.encode('utf-8'))
            sig_type = get_signature_type_ln(cert)
            if sig_type not in ['sha1WithRSAEncryption', 'sha256WithRSAEncryption']:
                continue
            issuer_str = format_issuer(cert.issuer)
            serial_number = format_serial_number(cert)
            sn = hashlib.md5((issuer_str + serial_number).encode('utf-8')).hexdigest()
            sn_list.append(sn)
        except Exception:
            continue
    result =  '_'.join(sn_list)
    config['alipay_root_cert_sn'] = result
    return result

def get_app_cert_sn(cert_path: str,config: Dict[str, Any]) -> str:
    """
    读取支付宝应用公钥证书，返回 app_cert_sn
    :param cert_path: 应用公钥证书文件路径
    :return: app_cert_sn 字符串
    """
    app_public_cert_sn = config.get('app_public_cert_sn')
    if app_public_cert_sn:
        return app_public_cert_sn
    with open(cert_path, 'r', encoding='utf-8') as f:
        cert_content = f.read()
    # 忽略支付宝证书的NULL参数警告，这是已知问题且不影响功能
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=DeprecationWarning,
                             message=".*NULL parameter value.*")
        cert = x509.load_pem_x509_certificate(cert_content.encode('utf-8'))
    issuer_str = format_issuer(cert.issuer)
    serial_number = format_serial_number(cert)
    sn = hashlib.md5((issuer_str + serial_number).encode('utf-8')).hexdigest()
    config['app_public_cert_sn'] = sn
    return sn

def prepare_public_params(config: Dict[str, Any], method: str, notify_url: Optional[str] = None) -> Dict[str, Any]:
    """
    准备支付宝API的公共参数
    """
    params = {
        "app_id": config["app_id"],
        "method": method,
        "format": "JSON",
        "charset": "utf-8",
        "sign_type": "RSA2",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": "1.0",
    }

    if notify_url:
        params["notify_url"] = notify_url
    else:
        notify_url_config = config.get("notify_url")
        if notify_url_config:
            params["notify_url"] = notify_url_config

    # 返回地址
    return_url = config.get("return_url")
    if return_url:
        params["return_url"] = return_url
    # 证书模式：添加证书序列号
    app_public_cert_path = config.get("app_public_cert_path")
    alipay_root_cert_path = config.get("alipay_root_cert_path")

    if app_public_cert_path and alipay_root_cert_path:
        # 忽略支付宝证书的NULL参数警告，这是已知问题且不影响功能
        import warnings
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning,
                                 message=".*NULL parameter value.*")
            # 证书模式：添加应用证书序列号
            params["app_cert_sn"] = get_app_cert_sn(app_public_cert_path, config)

            # 证书模式：添加支付宝根证书序列号
            params["alipay_root_cert_sn"] = get_root_cert_sn(alipay_root_cert_path, config)

    # 应用授权令牌
    app_auth_token = config.get("app_auth_token")
    if app_auth_token:
        params["app_auth_token"] = app_auth_token

    return params


def sign_params(params: Dict[str, Any], private_key_obj, sign_type: str = None) -> Dict[str, Any]:
    """
    使用缓存的私钥对象对参数进行签名
    :param params: 参数
    :param private_key_obj: 私钥对象
    :param sign_type: 签名类型，如果为None则从params或默认值获取
    :return: 已签名的参数
    """
    # 生成待签名字符串
    sign_content = generate_sign_str(params)

    # 使用缓存的私钥对象
    actual_sign_type = sign_type or params.get("sign_type", ALIPAY_SIGN_TYPE)
    sign = rsa_sign(sign_content, private_key_obj, actual_sign_type, use_cached_key=True)

    # 添加签名
    params["sign"] = sign

    return params


def build_form(params: Dict[str, Any], gateway_url: str) -> str:
    """
    构建表单
    :param params: 参数
    :param gateway_url: 网关地址
    :return: 表单HTML
    """
    form_params = []
    charset = params.get("charset", ALIPAY_CHARSET)

    for key, value in params.items():
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        # HTML转义并使用指定的charset编码
        if isinstance(value, str):
            value = html.escape(value)
        form_params.append(f'<input type="hidden" name="{key}" value=\'{value}\'>')

    form = f"""
    <form id="alipay_payment_form" action="{gateway_url}?charset={charset}" method="post" accept-charset="{charset}">
        {"".join(form_params)}
    </form>
    <script>document.getElementById('alipay_payment_form').submit();</script>
    """
    return form


def build_url(params: Dict[str, Any], gateway_url: str) -> str:
    """
    构建URL
    :param params: 参数
    :param gateway_url: 网关地址
    :return: URL
    """
    url_params = {}
    charset = params.get("charset", ALIPAY_CHARSET)

    # Prepare parameters for urlencode, keeping values as strings
    for key, value in params.items():
        if isinstance(value, (dict, list)):
            # Ensure complex types are JSON encoded as strings if needed
            value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        # Keep other values as strings (or convert non-strings if necessary)
        if value is not None and not isinstance(value, str):
            value = str(value)
        # Only include non-None parameters
        if value is not None:
            url_params[key] = value

    # Let urlencode handle the encoding based on the specified charset
    query_string = urllib.parse.urlencode(url_params, encoding=charset)
    return f"{gateway_url}?{query_string}"


def get_sign_content(raw_string: str, response_key: str) -> str:
    """
    从支付宝原始响应中提取待验签的JSON内容
    :param raw_string: 支付宝接口返回的原始字符串
    :param response_key: 待提取的响应字段名（如"alipay_trade_precreate_response"）
    :return: 待验签的JSON字符串（包含原始转义字符）
    :raises ValueError: 当字段不存在或JSON格式错误时抛出
    """
    # 定位目标字段起始位置（确保带引号匹配）
    key_pos = raw_string.find(f'"{response_key}":')
    if key_pos == -1:
        raise ValueError(f"未找到响应字段: {response_key}")

    # 定位JSON内容起始大括号
    brace_open_pos = raw_string.find('{', key_pos)
    if brace_open_pos == -1:
        raise ValueError("响应字段后未找到有效JSON内容")

    # 通过栈匹配找到对应闭合大括号
    brace_stack = 1
    current_pos = brace_open_pos + 1

    while current_pos < len(raw_string) and brace_stack > 0:
        if raw_string[current_pos] == '{':
            brace_stack += 1
        elif raw_string[current_pos] == '}':
            brace_stack -= 1
        current_pos += 1

    if brace_stack != 0:
        raise ValueError("JSON括号匹配失败，内容不完整")

    return raw_string[brace_open_pos:current_pos]

def verify_response(
    raw_string: str,
    response_key: str,
    response: Dict[str, Any],
    public_key_obj,
    sign_type: str = ALIPAY_SIGN_TYPE,
) -> bool:
    """
    验证支付宝响应签名
    :param raw_string: 支付宝接口返回的原始字符串
    :param response_key: 待提取的响应字段名（如"alipay_trade_precreate_response"）
    :param response: 解析后的响应数据
    :param public_key_obj: 公钥对象
    :param sign_type: 签名类型
    :return: 验证结果
    """
    # 获取签名
    sign = response.get("sign", None)
    response_sign_type = response.get("sign_type", sign_type)

    if not sign:
        return False

    # 从原始字符串中提取验签内容（保留转义字符）
    try:
        sign_content = get_sign_content(raw_string, response_key)
    except ValueError:
        return False

    # 使用缓存的公钥对象验证签名
    return rsa_verify(sign_content, sign, public_key_obj, response_sign_type, use_cached_key=True)
def verify_callback(params: Dict[str, Any], config: Dict[str, Any]) -> bool:
    """
    验证异步通知签名
    :param params: 通知参数
    :param config: 配置
    :return: 验证结果
    """
    # 获取公钥
    alipay_public_cert_path = config.get("alipay_public_cert_path")
    if not alipay_public_cert_path:
        raise InvalidConfigException("Missing config: alipay_public_cert_path")

    # 获取签名
    sign = params.pop("sign", None)
    sign_type = params.pop("sign_type", ALIPAY_SIGN_TYPE)

    if not sign:
        return False

    # 生成待验签字符串
    sign_content = generate_sign_str(params)

    # 验证签名
    return rsa_verify(sign_content, sign, alipay_public_cert_path, sign_type)


# ==================== 支付宝专用HTTP方法 ====================

import requests

from ...exceptions import AlipayException


def http_get(
    url: str, params: Optional[Dict[str, Any]] = None, config: Optional[Dict[str, Any]] = None, **kwargs
) -> Dict[str, Any]:
    """
    支付宝专用HTTP GET请求
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
            "User-Agent": "senweaver-pay-alipay/1.0",
            **config.get("headers", {}),
        }

        response = requests.get(url, params=params, headers=headers, timeout=timeout, **kwargs)

        # 检查HTTP状态码
        if response.status_code != 200:
            raise AlipayException(f"HTTP GET request failed with status {response.status_code}: {response.text}")

        return _process_alipay_response(response, config)

    except requests.RequestException as e:
        raise AlipayException(f"HTTP GET request failed: {e}")
    except Exception as e:
        raise AlipayException(f"Unexpected error in HTTP GET request: {e}")


def http_post(
    url: str, data: Optional[Dict[str, Any]] = None, config: Optional[Dict[str, Any]] = None, **kwargs
) -> Dict[str, Any]:
    """
    支付宝专用HTTP POST请求
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
            "User-Agent": "senweaver-pay-alipay/1.0",
            **config.get("headers", {}),
        }

        response = requests.post(url, data=data, headers=headers, timeout=timeout, **kwargs)

        # 检查HTTP状态码
        if response.status_code != 200:
            raise AlipayException(f"HTTP POST request failed with status {response.status_code}: {response.text}")

        return _process_alipay_response(response)

    except requests.RequestException as e:
        raise AlipayException(f"HTTP POST request failed: {e}")
    except Exception as e:
        raise AlipayException(f"Unexpected error in HTTP POST request: {e}")


def _process_alipay_response(response: requests.Response) -> Dict[str, Any]:
    """
    处理支付宝响应
    :param response: HTTP响应对象
    :param config: 配置信息
    :return: 处理后的响应数据
    """
    raw_text = response.text

    result = {"status_code": response.status_code, "headers": dict(response.headers), "text": raw_text}

    # 解析JSON响应
    try:
        response_data = json.loads(raw_text)
        result["data"] = response_data
    except json.JSONDecodeError:
        # 支付宝有时返回非JSON格式，这是正常的
        result["data"] = {"raw_response": raw_text}

    return result
