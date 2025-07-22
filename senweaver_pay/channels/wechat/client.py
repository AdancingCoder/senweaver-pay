"""
微信支付客户端实现
"""

import base64
import json
import time
from datetime import datetime
from typing import Any, Dict, Optional, Tuple, Union

# requests 已移至 helper.py 中
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding

from ...base import PayChannel
from ...exceptions import GatewayException, InvalidConfigException, InvalidSignException, WechatException
from ...helper import _get_key_content, generate_nonce_str
from ...types import (
    CallbackResponse,
    CancelRequest,
    CancelResponse,
    PaymentMethod,
    PaymentRequest,
    PaymentResponse,
    PaymentStatus,
    QueryRequest,
    QueryResponse,
    RefundRequest,
    RefundResponse,
    RefundStatus,
)
from .helper import (
    aes_decrypt,
    build_authorization,
    generate_client_sign,
    generate_sign,
    get_api_url,
    get_wechatpay_header,
    http_get,
    http_post,
)


class Wechat(PayChannel):
    """微信支付客户端"""

    def __init__(self, config: Dict[str, Any], app: str = "default"):
        """
        初始化微信支付客户端
        :param config: 支付配置
        :param app: 租户应用名称
        """
        super().__init__(config, app)
        self.channel = "wechat"
        self._required_keys = [
            "mch_id",
            "mch_secret_key",
            "mch_secret_cert",
            "mch_public_cert_path",
            "notify_url",
        ]
        # 证书管理
        self._wechat_public_cert_key = self.config.get("wechat_public_cert_key")
        self._certificates = {}  # 证书对象缓存 {serial_no: certificate_object}
        self._cert_update_time = None
        self._wechat_static_public_key = None

        # 如果没有预设证书，则需要动态加载
        if not self._wechat_public_cert_key:
            # 平台证书模式
            self.load_certificates()
        else:
            # 微信公钥模式
            certificate = self.config.get("wechat_public_cert_path")
            cert_bytes = _get_key_content(certificate)
            self._wechat_static_public_key = serialization.load_pem_public_key(cert_bytes, backend=default_backend())

    def _log(self, level, message):
        """简单的日志输出方法"""
        import logging

        logger = logging.getLogger(__name__)
        if level == "debug":
            logger.debug(f"[WeChat Pay] {message}")
        elif level == "info":
            logger.info(f"[WeChat Pay] {message}")
        elif level == "error":
            logger.error(f"[WeChat Pay] {message}")
        else:
            print(f"[WeChat Pay {level.upper()}] {message}")

    def _get_certificate_serial_number(self) -> str:
        """
        获取商户证书序列号
        :return: 证书序列号
        """
        config = self.config

        # 优先从配置中获取序列号
        serial_number = config.get("serial_number", "")
        if serial_number:
            return serial_number

        # 从证书文件中提取序列号
        mch_cert_path = config.get("mch_public_cert_path", "")
        if not mch_cert_path:
            raise InvalidConfigException("Missing config: mch_public_cert_path or serial_number")

        try:
            # 读取证书文件
            cert_content = _get_key_content(mch_cert_path)

            # 解析证书
            certificate = x509.load_pem_x509_certificate(cert_content, default_backend())

            # 获取序列号并转换为大写十六进制字符串
            serial_number = format(certificate.serial_number, "X")

            self._log("debug", f"Extracted certificate serial number: {serial_number}")
            return serial_number

        except Exception as e:
            raise InvalidConfigException(f"Failed to extract serial number from certificate: {str(e)}") from e

    def _prepare_headers(self, method: str, url_path: str, body: str = "") -> Dict[str, str]:
        """
        准备请求头
        :param method: HTTP 方法
        :param url_path: URL 路径
        :param body: 请求体
        :return: 请求头
        """
        config = self.config
        mch_id = config.get("mch_id")
        mch_secret_cert = config.get("mch_secret_cert")

        if not mch_id or not mch_secret_cert:
            raise InvalidConfigException("Missing required config: mch_id or mch_secret_cert")

        # 生成随机串和时间戳
        nonce_str = generate_nonce_str()
        timestamp = str(int(time.time()))

        # 生成签名
        signature = generate_sign(method, url_path, timestamp, nonce_str, body, mch_secret_cert)

        # 获取证书序列号
        serial_no = self._get_certificate_serial_number()

        # 构建认证信息
        authorization = build_authorization(mch_id, nonce_str, timestamp, signature, serial_no)

        # 返回请求头
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": authorization,
            "User-Agent": "senweaver-pay/v1",
        }

        return headers

    def _request(
        self,
        method: str,
        path: str,
        data: Optional[Dict[str, Any]] = None,
        skip_verify: bool = False,
    ) -> Dict[str, Any]:
        """
        发送微信支付API请求
        :param method: HTTP 方法 (GET/POST)
        :param path: 接口路径
        :param data: 请求数据
        :param skip_verify: 是否跳过签名验证
        :return: 响应数据
        :raises GatewayException: 请求失败或签名验证失败
        """
        try:
            # 构建请求参数
            url = get_api_url(path)
            url_path = f"/v3{path}"

            # 构造请求体
            body = data if isinstance(data, str) else json.dumps(data) if data else ""
            # 准备请求头
            headers = self._prepare_headers(method, url_path, body)

            # 发送HTTP请求
            config = {"headers": headers, "timeout": 30}

            if method.upper() == "GET":
                result = http_get(url, config=config)
            else:
                # POST请求需要将body转换为dict
                post_data = json.loads(body) if body else {}
                result = http_post(url, data=post_data, config=config)

            # 处理响应
            status_code = result.get("status_code", 0)
            response_text = result.get("text", "")
            headers = result.get("headers", {})

            # 检查HTTP状态码
            if status_code != 200:
                try:
                    error_data = json.loads(response_text) if response_text else {}
                    error_code = error_data.get("code", "UNKNOWN_ERROR")
                    error_message = error_data.get("message", "Unknown error")
                    self._log("error", f"Wechat API error: {error_code} - {error_message}")
                    raise GatewayException(f"{error_code}: {error_message}", error_data)
                except json.JSONDecodeError:
                    self._log("error", f"Wechat API request failed with status {status_code}: {response_text}")
                    raise GatewayException(f"Wechat API request failed with status {status_code}: {response_text}")

            # 验证响应签名
            if not skip_verify and status_code in range(200, 300):
                try:
                    verify_result = self._verify_signature(headers, response_text)
                    if not verify_result:
                        self._log("error", "Wechat response signature verification failed")
                        raise GatewayException("Invalid response signature")
                except Exception as e:
                    self._log("error", f"Signature verification error: {e}")
                    raise GatewayException(f"Signature verification failed: {str(e)}") from e

            # 解析响应数据
            try:
                return json.loads(response_text) if response_text else {}
            except json.JSONDecodeError as e:
                raise GatewayException(f"Invalid JSON response: {response_text}") from e

        except GatewayException as e:
            raise e  # 直接重新抛出已经处理过的GatewayException
        except Exception as e:
            self._log("error", f"Wechat API request failed: {e}")
            raise GatewayException(f"Wechat API request failed: {str(e)}") from e

    def _verify_signature(self, headers: Dict[str, str], body: str) -> bool:
        """
        验证响应签名，验证失败时自动更新证书重试
        :param headers: 响应头
        :param body: 响应体
        :return: 验证结果
        """
        # 获取请求头中的签名相关信息
        timestamp = get_wechatpay_header(headers, "Timestamp")
        nonce_str = get_wechatpay_header(headers, "Nonce")
        signature = get_wechatpay_header(headers, "Signature")
        serial_no = get_wechatpay_header(headers, "Serial")
        signature_type = get_wechatpay_header(headers, "Signature-Type")

        if signature_type != "WECHATPAY2-SHA256-RSA2048":
            raise InvalidSignException(f"not support this algorithm: {signature_type}")

        # 构造验签字符串
        sign_str = f"{timestamp}\n{nonce_str}\n{body}\n"

        # 尝试验证签名（直接使用证书对象）
        def try_verify():
            public_key = None

            if self._wechat_public_cert_key and serial_no == self._wechat_public_cert_key:
                # 微信支付公钥模式 - 缓存公钥
                public_key = self._wechat_static_public_key
            elif serial_no in self._certificates:
                # 平台证书模式 - 直接从证书对象获取公钥
                cert_obj = self._certificates[serial_no]
                public_key = cert_obj.public_key()
            else:
                return False

            # Base64 解码签名并验证
            try:
                signature_bytes = base64.b64decode(signature)
                public_key.verify(signature_bytes, sign_str.encode("utf-8"), asym_padding.PKCS1v15(), hashes.SHA256())
                return True
            except Exception:
                return False

        # 第一次验证
        if try_verify():
            return True

        # 验证失败，尝试更新证书后重试
        if not self._wechat_public_cert_key and serial_no not in self._certificates:
            self._log("info", f"Certificate {serial_no} not found, updating certificates...")
            self._cert_update_time = None  # 强制更新
            self.load_certificates()

            # 重新验证
            if try_verify():
                self._log("info", "Signature verification succeeded after certificate update")
                return True

        return False

    def _verify_callback(self, headers: Dict[str, str], body: str) -> Tuple[bool, Dict[str, Any]]:
        # 验证签名
        if not self._verify_signature(headers, body):
            raise InvalidSignException("Invalid signature in callback")

        # 解析请求体
        data = json.loads(body)

        # 获取资源数据
        resource = data.get("resource", {})
        if not resource:
            return True, data

        # 解密资源数据
        ciphertext = resource.get("ciphertext", "")
        nonce = resource.get("nonce", "")
        associated_data = resource.get("associated_data", "")

        # 获取API密钥
        mch_secret_key = self.config.get("mch_secret_key", "")
        if not mch_secret_key:
            raise InvalidConfigException("Missing config: mch_secret_key")

        # 解密
        try:
            decrypted_data = aes_decrypt(ciphertext, mch_secret_key, nonce, associated_data)
            resource_data = json.loads(decrypted_data)
            data["resource_data"] = resource_data
        except Exception as e:
            raise InvalidSignException(f"Failed to decrypt callback data: {str(e)}") from e

        return True, data

    def _check_payment_config(self, payment_type: str) -> None:
        """
        检查支付配置
        :param payment_type: 支付类型
        :raises InvalidConfigException: 如果缺少必要的配置
        """
        config = self.config

        # 检查基本配置
        self._check_config(self._required_keys)

        # 检查特定支付类型的配置
        if payment_type == "mp" and not config.get("mp_app_id"):
            raise InvalidConfigException("Missing required config: mp_app_id")
        elif payment_type == "mini" and not config.get("mini_app_id"):
            raise InvalidConfigException("Missing required config: mini_app_id")
        elif payment_type == "app" and not config.get("app_id"):
            raise InvalidConfigException("Missing required config: app_id")

    def _prepare_base_order(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        准备基础订单参数
        :param params: 订单参数
        :return: 处理后的订单参数
        """
        config = self.config

        # 验证必填字段
        if not all(k in params for k in ["out_trade_no", "description", "amount"]):
            raise ValueError("缺少必填参数: out_trade_no, description 或 amount")

        if "total" not in params["amount"]:
            raise ValueError("amount必须包含total字段")

        # 基础参数
        order = {
            "out_trade_no": params["out_trade_no"],
            "description": params["description"],
            "amount": params.get("amount", {}),
            "notify_url": params.get("notify_url") or config.get("notify_url", ""),
        }

        # 可选参数
        for key in ["time_expire", "attach", "goods_tag", "detail", "scene_info"]:
            if key in params:
                order[key] = params[key]

        return order

    def mp(self, params: Dict[str, Any]) -> PaymentResponse:
        """
        公众号支付
        :param params: 支付参数，必须包含:
            - out_trade_no: 商户订单号
            - description: 商品描述
            - amount: 订单金额
                - total: 总金额, 单位为分
            - payer: 支付者
                - openid: 用户标识
        :return: 支付参数
        """
        self._log("info", f"Creating wechat mp payment with params: {json.dumps(params, ensure_ascii=False)}")

        # 检查支付配置
        self._check_payment_config("mp")

        config = self.config
        app_id = config.get("mp_app_id", "")

        # 准备订单参数
        order = self._prepare_base_order(params)

        # 添加支付者信息
        payer = params.get("payer", {})
        if not payer or not payer.get("openid"):
            raise WechatException("Missing required parameter: payer.openid")

        order["payer"] = payer

        # 创建预支付交易
        path = "/pay/transactions/jsapi"
        response = self._request("POST", path, {"appid": app_id, "mchid": config.get("mch_id", ""), **order})

        # 解析响应
        if "prepay_id" not in response:
            return PaymentResponse(
                success=False,
                message=response.get("message") or "Payment failed",
                code=response.get("code", ""),
                out_trade_no=params.get("out_trade_no"),
                raw_data=response,
            )

        prepay_id = response["prepay_id"]

        # 生成支付参数
        timestamp = str(int(time.time()))
        nonce_str = generate_nonce_str()
        package = f"prepay_id={prepay_id}"

        # 计算签名（使用客户端签名算法）
        signature = generate_client_sign(app_id, timestamp, nonce_str, package, config.get("mch_secret_cert", ""))

        # 返回支付参数
        return PaymentResponse(
            success=True,
            message="MP payment created successfully",
            code="",
            out_trade_no=params.get("out_trade_no"),
            app_params={
                "app_id": app_id,
                "time_stamp": timestamp,
                "nonce_str": nonce_str,
                "package": package,
                "sign_type": "RSA",
                "pay_sign": signature,
            },
            raw_data=response,
        )

    def mini(self, params: Dict[str, Any]) -> PaymentResponse:
        """
        小程序支付
        :param params: 支付参数，必须包含:
            - out_trade_no: 商户订单号
            - description: 商品描述
            - amount: 订单金额
                - total: 总金额, 单位为分
            - payer: 支付者
                - openid: 用户标识
        :return: 支付参数
        """
        self._log("info", f"Creating wechat mini payment with params: {json.dumps(params, ensure_ascii=False)}")

        # 检查支付配置
        self._check_payment_config("mini")

        config = self.config
        app_id = config.get("mini_app_id", "")

        # 准备订单参数
        order = self._prepare_base_order(params)

        # 添加支付者信息
        payer = params.get("payer", {})
        if not payer or not payer.get("openid"):
            raise WechatException("Missing required parameter: payer.openid")

        order["payer"] = payer

        # 创建预支付交易
        path = "/pay/transactions/jsapi"
        response = self._request("POST", path, {"appid": app_id, "mchid": config.get("mch_id", ""), **order})

        # 解析响应
        if "prepay_id" not in response:
            return PaymentResponse(
                success=False,
                message=response.get("message") or "Payment failed",
                code=response.get("code", ""),
                out_trade_no=params.get("out_trade_no"),
                raw_data=response,
            )

        prepay_id = response["prepay_id"]

        # 生成支付参数
        timestamp = str(int(time.time()))
        nonce_str = generate_nonce_str()
        package = f"prepay_id={prepay_id}"

        # 计算签名（使用客户端签名算法）
        signature = generate_client_sign(app_id, timestamp, nonce_str, package, config.get("mch_secret_cert", ""))

        # 返回支付参数
        return PaymentResponse(
            success=True,
            message="Mini payment created successfully",
            code="",
            out_trade_no=params.get("out_trade_no"),
            app_params={
                "app_id": app_id,
                "time_stamp": timestamp,
                "nonce_str": nonce_str,
                "package": package,
                "sign_type": "RSA",
                "pay_sign": signature,
            },
            raw_data=response,
        )

    def app(self, params: Dict[str, Any]) -> PaymentResponse:
        """
        APP支付
        :param params: 支付参数，必须包含:
            - out_trade_no: 商户订单号
            - description: 商品描述
            - amount: 订单金额
                - total: 总金额, 单位为分
        :return: 支付参数
        """
        self._log("info", f"Creating wechat app payment with params: {json.dumps(params, ensure_ascii=False)}")

        # 检查支付配置
        self._check_payment_config("app")

        config = self.config
        app_id = config.get("app_id", "")
        mch_id = config.get("mch_id", "")

        # 准备订单参数
        order = self._prepare_base_order(params)

        # 创建预支付交易
        path = "/pay/transactions/app"
        response = self._request("POST", path, {"appid": app_id, "mchid": mch_id, **order})

        # 解析响应
        if "prepay_id" not in response:
            return PaymentResponse(
                success=False,
                message=response.get("message") or "Payment failed",
                code=response.get("code", ""),
                out_trade_no=params.get("out_trade_no"),
                raw_data=response,
            )

        prepay_id = response["prepay_id"]

        # 生成支付参数
        timestamp = str(int(time.time()))
        nonce_str = generate_nonce_str()

        # 计算签名（APP支付使用不同的签名格式）
        # APP支付签名串：appid\npartnerid\nprepayid\npackage\nnoncestr\ntimestamp\n

        # 对于APP支付，package固定为"Sign=WXPay"
        app_sign_str = f"{app_id}\n{mch_id}\n{prepay_id}\nSign=WXPay\n{nonce_str}\n{timestamp}\n"
        key_bytes = _get_key_content(config.get("mch_secret_cert", ""))
        private_key = serialization.load_pem_private_key(key_bytes, password=None, backend=default_backend())
        signature_bytes = private_key.sign(app_sign_str.encode("utf-8"), asym_padding.PKCS1v15(), hashes.SHA256())
        signature = base64.b64encode(signature_bytes).decode("utf-8")

        # 返回支付参数
        return PaymentResponse(
            success=True,
            message="APP payment created successfully",
            code="",
            out_trade_no=params.get("out_trade_no"),
            app_params={
                "app_id": app_id,
                "partner_id": mch_id,
                "prepay_id": prepay_id,
                "package": "Sign=WXPay",
                "nonce_str": nonce_str,
                "time_stamp": timestamp,
                "sign": signature,
            },
            raw_data=response,
        )

    def h5(self, params: Dict[str, Any]) -> PaymentResponse:
        """
        H5支付
        :param params: 支付参数，必须包含:
            - out_trade_no: 商户订单号
            - description: 商品描述
            - amount: 订单金额
                - total: 总金额, 单位为分
            - scene_info: 场景信息
                - payer_client_ip: 用户终端IP
                - h5_info: H5场景信息
                    - type: 场景类型
        :return: 支付链接
        """
        self._log("info", f"Creating wechat wap payment with params: {json.dumps(params, ensure_ascii=False)}")

        config = self.config
        app_id = config.get("mp_app_id", "") or config.get("app_id", "")

        # 准备订单参数
        order = self._prepare_base_order(params)

        # 检查场景信息
        if "scene_info" not in order or not order["scene_info"].get("payer_client_ip"):
            raise WechatException("Missing required parameter: scene_info.payer_client_ip")

        # 创建预支付交易
        path = "/pay/transactions/h5"
        response = self._request("POST", path, {"appid": app_id, "mchid": config.get("mch_id", ""), **order})

        # 解析响应
        if "h5_url" not in response:
            return PaymentResponse(
                success=False,
                message=response.get("message") or "Payment failed",
                code=response.get("code", ""),
                out_trade_no=params.get("out_trade_no"),
                raw_data=response,
            )

        h5_url = response["h5_url"]

        # 如果提供了返回页面，则添加到URL中
        redirect_url = params.get("redirect_url", "")
        if redirect_url:
            h5_url = f"{h5_url}&redirect_url={redirect_url}"

        return PaymentResponse(
            success=True,
            message="H5 payment created successfully",
            code="",
            out_trade_no=params.get("out_trade_no"),
            pay_url=h5_url,
            raw_data=response,
        )

    def scan(self, params: Dict[str, Any]) -> PaymentResponse:
        """
        扫码支付
        :param params: 支付参数，必须包含:
            - out_trade_no: 商户订单号
            - description: 商品描述
            - amount: 订单金额
                - total: 总金额, 单位为分
        :return: 二维码链接
        """
        self._log("info", f"Creating wechat scan payment with params: {json.dumps(params, ensure_ascii=False)}")

        config = self.config
        app_id = config.get("mp_app_id", "") or config.get("app_id", "")

        # 准备订单参数
        order = self._prepare_base_order(params)

        # 创建预支付交易
        path = "/pay/transactions/native"
        response = self._request("POST", path, {"appid": app_id, "mchid": config.get("mch_id", ""), **order})

        # 解析响应
        if "code_url" not in response:
            return PaymentResponse(
                success=False,
                message=response.get("message") or "Payment failed",
                code=response.get("code", ""),
                out_trade_no=params.get("out_trade_no"),
                raw_data=response,
            )

        code_url = response["code_url"]

        return PaymentResponse(
            success=True,
            message="Scan payment created successfully",
            code="",
            out_trade_no=params.get("out_trade_no"),
            qr_code=code_url,
            raw_data=response,
        )

    def pos(self, params: Dict[str, Any]) -> PaymentResponse:
        """
        刷卡支付
        :param params: 支付参数，必须包含:
            - out_trade_no: 商户订单号
            - description: 商品描述
            - amount: 订单金额
                - total: 总金额, 单位为分
            - auth_code: 授权码
        :return: 支付结果
        """
        self._log("info", f"Creating wechat pos payment with params: {json.dumps(params, ensure_ascii=False)}")

        config = self.config
        app_id = config.get("mp_app_id", "") or config.get("app_id", "")

        # 准备订单参数
        order = self._prepare_base_order(params)

        # 检查授权码
        auth_code = params.get("auth_code", "")
        if not auth_code:
            raise WechatException("Missing required parameter: auth_code")

        # 创建支付交易
        path = "/pay/transactions/micropay"
        response = self._request(
            "POST", path, {"appid": app_id, "mchid": config.get("mch_id", ""), "auth_code": auth_code, **order}
        )

        # 处理响应
        if "transaction_id" not in response:
            return PaymentResponse(
                success=False,
                message=response.get("message") or "Payment failed",
                code=response.get("code", ""),
                out_trade_no=params.get("out_trade_no"),
                raw_data=response,
            )

        return PaymentResponse(
            success=True,
            message="POS payment completed successfully",
            code="",
            out_trade_no=params.get("out_trade_no"),
            trade_no=response.get("transaction_id"),
            raw_data=response,
        )

    def query(self, params: Union[Dict[str, Any], QueryRequest]) -> QueryResponse:
        """
        查询订单（统一接口，支持传统字典参数和类型化请求）
        :param params: 查询参数，支持两种格式:
            1. 字典格式（传统接口）:
                - out_trade_no或transaction_id其中之一
            2. QueryRequest对象（类型化接口）
        :return: QueryResponse 对象
        """
        try:
            # 根据参数类型进行转换
            if isinstance(params, QueryRequest):
                # 类型化请求
                out_trade_no = params.out_trade_no
                trade_no = params.trade_no  # 微信的 transaction_id
                # 转换为字典格式
                dict_params = {}
                if out_trade_no:
                    dict_params["out_trade_no"] = out_trade_no
                if trade_no:
                    dict_params["transaction_id"] = trade_no
            else:
                # 传统字典参数
                dict_params = params
                out_trade_no = params.get("out_trade_no")
                trade_no = params.get("transaction_id")

            self._log("info", f"Querying wechat order with params: {json.dumps(dict_params, ensure_ascii=False)}")

            config = self.config
            mch_id = config.get("mch_id", "")

            # 确定查询方式
            if trade_no:
                path = f"/pay/transactions/id/{trade_no}?mchid={mch_id}"
            elif out_trade_no:
                path = f"/pay/transactions/out-trade-no/{out_trade_no}?mchid={mch_id}"
            else:
                raise WechatException("Missing required parameter: either transaction_id or out_trade_no")

            # 发送查询请求
            response = self._request("GET", path)

            # 转换响应
            return self._convert_to_query_response(response, out_trade_no, trade_no)

        except Exception as e:
            return QueryResponse(
                success=False,
                message=str(e),
                out_trade_no=out_trade_no if "out_trade_no" in locals() else None,
                trade_no=trade_no if "trade_no" in locals() else None,
            )

    def cancel(self, params: Union[Dict[str, Any], CancelRequest]) -> CancelResponse:
        """
        取消支付（微信支付不支持取消，使用关闭订单代替）
        :param params: 取消参数，支持两种格式:
            1. 字典格式（传统接口）
            2. CancelRequest对象（类型化接口）
        :return: CancelResponse 对象
        """
        # 微信支付不支持取消，建议使用关闭订单
        if isinstance(params, CancelRequest):
            out_trade_no = params.out_trade_no
        else:
            out_trade_no = params.get("out_trade_no")

        return CancelResponse(
            success=False,
            message="Wechat does not support cancel payment, please use close order instead",
            out_trade_no=out_trade_no,
        )

    def close(self, params: Union[Dict[str, Any], CancelRequest]) -> CancelResponse:
        """
        关闭订单（统一接口，支持传统字典参数和类型化请求）
        :param params: 关闭参数，支持两种格式:
            1. 字典格式（传统接口）:
                - out_trade_no: 商户订单号
            2. CancelRequest对象（类型化接口）
        :return: CancelResponse 对象
        """
        try:
            # 根据参数类型进行转换
            if isinstance(params, CancelRequest):
                # 类型化请求
                out_trade_no = params.out_trade_no
                trade_no = params.trade_no
                # 转换为字典格式
                dict_params = {}
                if out_trade_no:
                    dict_params["out_trade_no"] = out_trade_no
                if trade_no:
                    dict_params["transaction_id"] = trade_no
            else:
                # 传统字典参数
                dict_params = params
                out_trade_no = params.get("out_trade_no")
                trade_no = params.get("transaction_id")

            self._log("info", f"Closing wechat order with params: {json.dumps(dict_params, ensure_ascii=False)}")

            config = self.config
            mch_id = config.get("mch_id", "")

            # 验证必要参数
            if not out_trade_no:
                raise WechatException("Missing required parameter: out_trade_no")

            # 发送关闭请求
            path = f"/pay/transactions/out-trade-no/{out_trade_no}/close"
            response = self._request("POST", path, {"mchid": mch_id})

            # 处理响应
            success = "code" not in response  # 如果没有返回错误码，则认为成功

            if success:
                return CancelResponse(
                    success=True,
                    message="Order closed successfully",
                    code="",
                    out_trade_no=out_trade_no,
                    raw_data=response,
                )
            else:
                return CancelResponse(
                    success=False,
                    message=response.get("message", "Close failed"),
                    code=response.get("code", ""),
                    out_trade_no=out_trade_no,
                    raw_data=response,
                )

        except Exception as e:
            return CancelResponse(
                success=False,
                message=str(e),
                out_trade_no=out_trade_no if "out_trade_no" in locals() else None,
            )

    def refund(self, params: Union[Dict[str, Any], RefundRequest]) -> RefundResponse:
        """
        申请退款（统一接口，支持传统字典参数和类型化请求）
        :param params: 退款参数，支持两种格式:
            1. 字典格式（传统接口）:
                - out_refund_no: 商户退款单号
                - amount: 退款金额信息
                - out_trade_no或transaction_id其中之一
            2. RefundRequest对象（类型化接口）
        :return: RefundResponse 对象
        """
        try:
            # 根据参数类型进行转换
            if isinstance(params, RefundRequest):
                # 类型化请求
                out_trade_no = params.out_trade_no
                trade_no = params.trade_no  # 微信的 transaction_id
                out_refund_no = params.out_refund_no
                refund_amount = params.refund_amount
                refund_reason = params.refund_reason

                # 转换为字典格式
                dict_params = {
                    "out_refund_no": out_refund_no,
                    "amount": {
                        "refund": int(refund_amount * 100),  # 转换为分
                        "total": int(refund_amount * 100),  # 简化处理，实际应该从订单获取
                        "currency": "CNY",
                    },
                }
                if out_trade_no:
                    dict_params["out_trade_no"] = out_trade_no
                if trade_no:
                    dict_params["transaction_id"] = trade_no
                if refund_reason:
                    dict_params["reason"] = refund_reason
            else:
                # 传统字典参数
                dict_params = params
                out_trade_no = params.get("out_trade_no")
                trade_no = params.get("transaction_id")
                out_refund_no = params.get("out_refund_no")

            self._log("info", f"Refunding wechat order with params: {json.dumps(dict_params, ensure_ascii=False)}")

            # 验证必要参数
            if not out_refund_no:
                raise WechatException("Missing required parameter: out_refund_no")
            if not out_trade_no and not trade_no:
                raise WechatException("Missing required parameter: either transaction_id or out_trade_no")

            # 发送退款请求
            path = "/refund/domestic/refunds"
            response = self._request("POST", path, dict_params)

            # 转换响应
            return self._convert_to_refund_response(response, out_trade_no, out_refund_no)

        except Exception as e:
            return RefundResponse(
                success=False,
                message=str(e),
                out_trade_no=out_trade_no if "out_trade_no" in locals() else None,
                out_refund_no=out_refund_no if "out_refund_no" in locals() else None,
            )

    def load_certificates(self) -> Dict[str, Any]:
        """
        加载微信支付平台证书并缓存公钥
        :return: 证书字典 {serial_no: certificate_object}
        """
        # 如果证书缓存有效（6小时内），直接返回
        if self._certificates and self._cert_update_time:
            if (datetime.now() - self._cert_update_time).total_seconds() < 21600:  # 6小时
                return self._certificates

        try:
            # 请求证书列表
            response = self._request("GET", "/certificates", skip_verify=True)
            certificates = {}

            if "data" in response:
                for cert_info in response["data"]:
                    serial_no = cert_info.get("serial_no", "")
                    encrypt_cert = cert_info.get("encrypt_certificate", {})

                    if serial_no and encrypt_cert:
                        try:
                            from .helper import aes_decrypt

                            cert_content = aes_decrypt(
                                encrypt_cert.get("ciphertext", ""),
                                self.config.get("mch_secret_key", ""),
                                encrypt_cert.get("nonce", ""),
                                encrypt_cert.get("associated_data", ""),
                            )

                            # 直接解析证书对象并存储
                            try:
                                cert_bytes = _get_key_content(cert_content)
                                cert_obj = x509.load_pem_x509_certificate(cert_bytes, default_backend())
                                certificates[serial_no] = cert_obj
                            except Exception as e:
                                self._log("error", f"Failed to parse certificate {serial_no}: {str(e)}")

                        except Exception as e:
                            self._log("error", f"Failed to decrypt certificate {serial_no}: {str(e)}")

            if certificates:
                self._certificates = certificates
                self._cert_update_time = datetime.now()
                self._log("info", f"Loaded {len(certificates)} certificate objects")

        except Exception as e:
            self._log("error", f"Failed to load certificates: {str(e)}")

        return self._certificates

    def create(self, request: PaymentRequest) -> PaymentResponse:
        """
        创建支付订单（统一接口）
        根据支付方式调用对应的方法
        """
        # 确保订单有必要的URL
        order = request.order

        # 转换订单参数
        params = {
            "out_trade_no": order.out_trade_no,
            "description": order.subject,
            "amount": {
                "total": int(float(order.amount) * 100),  # 微信金额单位为分
                "currency": "CNY",
            },
        }

        # 添加回调地址
        if order.notify_url:
            params["notify_url"] = order.notify_url

        # 添加额外参数
        if request.extra_params:
            params.update(request.extra_params)

        # 根据支付方式调用对应方法
        if request.method == PaymentMethod.H5:
            return self.h5(params)
        elif request.method == PaymentMethod.MP:
            return self.mp(params)
        elif request.method == PaymentMethod.APP:
            return self.app(params)
        elif request.method == PaymentMethod.MINI:
            return self.mini(params)
        elif request.method == PaymentMethod.POS:
            return self.pos(params)
        elif request.method == PaymentMethod.SCAN:
            return self.scan(params)
        elif request.method == PaymentMethod.TRANSFER:
            return self.transfer(params)
        else:
            return PaymentResponse(
                success=False,
                message=f"Wechat does not support {request.method.value} payment method",
                out_trade_no=request.order.out_trade_no,
            )

    def callback(
        self,
        headers: Optional[Dict[str, str]] = None,
        raw_body: Optional[str] = None,
        form_data: Optional[Dict[str, Any]] = None,
        query_data: Optional[Dict[str, Any]] = None,
    ) -> CallbackResponse:
        """
        处理微信支付回调
        :param headers: 请求头（微信需要）
        :param raw_body: 原始请求体（微信需要）
        :param form_data: 表单数据（微信不需要）
        :param query_data: 查询参数（微信不需要）
        :return: 回调处理结果
        """
        if not headers or not raw_body:
            raise WechatException("Missing headers or raw_body for wechat callback")

        # 验证回调通知
        try:
            verified, data = self._verify_callback(headers, raw_body)
        except Exception as e:
            self._log("error", f"Failed to verify callback: {str(e)}")
            raise InvalidSignException(f"Failed to verify callback: {str(e)}") from e

        if not verified:
            raise InvalidSignException("Wechat callback signature verification failed")

        # 获取资源数据
        resource_data = data.get("resource_data", {})

        # 提取订单信息
        out_trade_no = resource_data.get("out_trade_no")
        transaction_id = resource_data.get("transaction_id")
        amount_total = resource_data.get("amount", {}).get("total")
        success_time = resource_data.get("success_time")
        trade_state = resource_data.get("trade_state")

        # 转换支付状态
        from ...types import CallbackResponse, PaymentStatus

        if trade_state == "SUCCESS":
            status = PaymentStatus.SUCCESS
        elif trade_state == "REFUND":
            status = PaymentStatus.CANCELLED
        elif trade_state == "CLOSED":
            status = PaymentStatus.CANCELLED
        else:
            status = PaymentStatus.FAILED

        # 返回标准化的回调结果
        return CallbackResponse(
            success=True,  # 验签成功才能到这里
            data=resource_data,
            message="Wechat callback processed successfully",
            raw_data=data,  # 原始回调数据
            out_trade_no=out_trade_no,
            trade_no=transaction_id,
            amount=str(amount_total) if amount_total else None,
            pay_time=success_time,
            status=status,  # 支付状态
        )

    def success(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        返回成功响应
        :param params: 可选参数
        :return: 成功响应字典
        """
        return {"code": "SUCCESS", "message": "SUCCESS"}

    def failure(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        返回失败响应
        :param params: 可选参数（可包含错误信息）
        :return: 失败响应字典
        """
        error_message = "FAIL"
        if params and "message" in params:
            error_message = params["message"]
        return {"code": "FAIL", "message": error_message}

    def reload(self, config: Dict[str, Any] = None) -> bool:
        """
        重新加载微信支付证书和配置
        :param config: 新的配置参数，如果提供则更新配置
        :return: 重新加载是否成功
        """
        try:
            # 更新配置
            if config:
                self.config.update(config)

            # 清除证书缓存
            self._certificates = {}
            self._cert_update_time = None
            self._log("info", "Certificate cache cleared")

            # 重新加载证书
            certificates = self.load_certificates()
            success = len(certificates) > 0

            if success:
                self._log("info", f"Successfully reloaded {len(certificates)} certificates and config")
            else:
                self._log("warning", "Failed to reload certificates - no certificates loaded")

            return success
        except Exception as e:
            self._log("error", f"Failed to reload certificates and config: {str(e)}")
            return False

    def _convert_unified_order(self, order, method, extra_params=None):
        """
        转换统一订单为微信特定参数
        :param order: UnifiedOrder 对象
        :param method: PaymentMethod 枚举
        :param extra_params: 额外参数
        :return: 微信特定的参数字典
        """
        params = {
            "out_trade_no": order.out_trade_no,
            "description": order.subject,  # 微信使用 description
            "amount": {"total": int(order.amount * 100)},  # 微信使用分为单位
        }

        # 微信特有参数
        if order.body:
            params["description"] = order.body  # 如果有详细描述，使用详细描述
        if order.notify_url:
            params["notify_url"] = order.notify_url
        if order.attach:
            params["attach"] = order.attach
        if order.expire_time:
            params["time_expire"] = order.expire_time  # 微信的超时时间字段

        # 根据支付方式设置特定参数
        if method.value in ["mp", "mini"]:
            # 公众号和小程序支付需要 openid
            if extra_params and "openid" in extra_params:
                params["payer"] = {"openid": extra_params["openid"]}
        elif method.value == "h5":
            # H5支付需要场景信息
            if extra_params and "scene_info" in extra_params:
                params["scene_info"] = extra_params["scene_info"]
            else:
                # 默认H5场景信息
                params["scene_info"] = {"payer_client_ip": "127.0.0.1", "h5_info": {"type": "Wap"}}
        elif method.value == "pos":
            # 刷卡支付需要授权码
            if extra_params and "auth_code" in extra_params:
                params["auth_code"] = extra_params["auth_code"]

        # 处理其他额外参数
        if extra_params:
            for key, value in extra_params.items():
                if key not in ["openid", "scene_info", "auth_code"]:
                    params[key] = value

        return params

    def _convert_refund_request(self, request):
        """
        转换退款请求为微信特定参数
        :param request: RefundRequest 对象
        :return: 微信特定的参数字典
        """
        params = {}

        # 微信退款必需参数
        if request.out_trade_no:
            params["out_trade_no"] = request.out_trade_no
        if request.trade_no:
            params["transaction_id"] = request.trade_no  # 微信使用 transaction_id
        if request.out_refund_no:
            params["out_refund_no"] = request.out_refund_no

        # 微信退款金额结构
        if request.refund_amount:
            params["amount"] = {
                "refund": int(request.refund_amount * 100),  # 微信使用分
                "total": int(request.total_amount * 100) if request.total_amount else int(request.refund_amount * 100),
                "currency": "CNY",
            }

        # 微信退款可选参数
        if request.refund_reason:
            params["reason"] = request.refund_reason  # 微信使用 reason
        if request.notify_url:
            params["notify_url"] = request.notify_url

        return params

    # ==================== 微信特定的响应转换方法 ====================

    def _convert_to_payment_response(self, raw_response, method, out_trade_no):
        """
        转换微信原始响应为统一支付响应
        :param raw_response: 微信原始响应
        :param method: PaymentMethod 枚举
        :param out_trade_no: 商户订单号
        :return: PaymentResponse 对象
        """
        if not raw_response or not getattr(raw_response, "success", False):
            return PaymentResponse(
                success=False,
                message=getattr(raw_response, "message", "Payment failed"),
                code=getattr(raw_response, "code", ""),
                out_trade_no=out_trade_no,
            )

        response = PaymentResponse(
            success=True,
            message=getattr(raw_response, "message", "Payment created successfully"),
            code=getattr(raw_response, "code", ""),
            out_trade_no=out_trade_no,
            raw_data=getattr(raw_response, "data", {}),
        )

        # 根据支付方式设置特定字段
        data = getattr(raw_response, "data", {})
        if method == PaymentMethod.H5:
            response.pay_url = data  # 微信H5返回的是支付链接
        elif method == PaymentMethod.SCAN:
            response.qr_code = data  # 微信扫码返回的是二维码内容
        elif method in [PaymentMethod.MP, PaymentMethod.MINI, PaymentMethod.APP]:
            response.app_params = data  # 微信返回的是调起参数

        return response

    def _convert_to_query_response(self, raw_response, out_trade_no, trade_no):
        """
        转换微信查询响应为统一查询响应
        :param raw_response: 微信原始响应（字典格式）
        :param out_trade_no: 商户订单号
        :param trade_no: 微信交易号
        :return: QueryResponse 对象
        """
        # raw_response 是从 _request 方法返回的字典
        if not raw_response:
            return QueryResponse(
                success=False,
                message="Query failed - empty response",
                out_trade_no=out_trade_no,
                trade_no=trade_no,
            )

        # 如果是空字典但不是None，可能是某些特殊情况，当作查询失败处理
        if not any(raw_response.values()) if isinstance(raw_response, dict) else False:
            return QueryResponse(
                success=False,
                message="Query failed - empty data",
                out_trade_no=out_trade_no,
                trade_no=trade_no,
                raw_data=raw_response,
            )

        # 检查是否有错误
        if "code" in raw_response and raw_response["code"] != "SUCCESS":
            return QueryResponse(
                success=False,
                message=raw_response.get("message", "Query failed"),
                code=raw_response.get("code", ""),
                out_trade_no=out_trade_no,
                trade_no=trade_no,
                raw_data=raw_response,
            )

        # 获取微信交易状态并转换为统一状态
        wechat_status = raw_response.get("trade_state", "")

        # 微信状态映射
        status_mapping = {
            "SUCCESS": PaymentStatus.SUCCESS,
            "REFUND": PaymentStatus.SUCCESS,  # 转入退款
            "NOTPAY": PaymentStatus.PENDING,
            "CLOSED": PaymentStatus.FAILED,
            "REVOKED": PaymentStatus.FAILED,
            "USERPAYING": PaymentStatus.PENDING,
            "PAYERROR": PaymentStatus.FAILED,
        }

        payment_status = status_mapping.get(wechat_status, PaymentStatus.PENDING)

        # 获取金额信息
        amount_info = raw_response.get("amount", {})
        total_amount = amount_info.get("total", 0)
        payer_total = amount_info.get("payer_total", total_amount)  # 如果没有payer_total，使用total

        return QueryResponse(
            success=True,
            message="Query successful",
            code="SUCCESS",
            out_trade_no=raw_response.get("out_trade_no", out_trade_no),
            trade_no=raw_response.get("transaction_id", trade_no),
            status=payment_status,
            total_amount=total_amount / 100 if total_amount else 0,  # 微信返回分，转换为元
            paid_amount=payer_total / 100 if payer_total else 0,
            trade_state_desc=raw_response.get("trade_state_desc", ""),
            raw_data=raw_response,
        )

    def _convert_to_refund_response(self, raw_response, out_trade_no, out_refund_no):
        """
        转换微信退款响应为统一退款响应
        :param raw_response: 微信原始响应（字典格式）
        :param out_trade_no: 商户订单号
        :param out_refund_no: 商户退款单号
        :return: RefundResponse 对象
        """
        # raw_response 是从 _request 方法返回的字典
        if not raw_response:
            return RefundResponse(
                success=False,
                message="Refund failed - empty response",
                out_trade_no=out_trade_no,
                out_refund_no=out_refund_no,
            )

        # 检查是否有错误
        if "code" in raw_response and raw_response["code"] != "SUCCESS":
            return RefundResponse(
                success=False,
                message=raw_response.get("message", "Refund failed"),
                code=raw_response.get("code", ""),
                out_trade_no=out_trade_no,
                out_refund_no=out_refund_no,
                raw_data=raw_response,
            )

        # 微信退款状态映射
        wechat_status = raw_response.get("status", "PROCESSING")
        status_mapping = {
            "SUCCESS": RefundStatus.SUCCESS,
            "CLOSED": RefundStatus.FAILED,
            "PROCESSING": RefundStatus.PENDING,
            "ABNORMAL": RefundStatus.FAILED,
        }

        refund_status = status_mapping.get(wechat_status, RefundStatus.PENDING)

        # 获取金额信息
        amount_info = raw_response.get("amount", {})
        refund_amount = amount_info.get("refund", 0)

        return RefundResponse(
            success=True,
            message="Refund successful",
            code="SUCCESS",
            out_trade_no=raw_response.get("out_trade_no", out_trade_no),
            out_refund_no=raw_response.get("out_refund_no", out_refund_no),
            refund_status=refund_status,
            refund_amount=refund_amount / 100 if refund_amount else 0,  # 微信返回分，转换为元
            refund_time=raw_response.get("success_time"),
            raw_data=raw_response,
        )

    def _convert_to_cancel_response(self, raw_response, out_trade_no, trade_no):
        """
        转换微信取消响应为统一取消响应
        :param raw_response: 微信原始响应
        :param out_trade_no: 商户订单号
        :param trade_no: 微信交易号
        :return: CancelResponse 对象
        """
        if not raw_response or not getattr(raw_response, "success", False):
            return CancelResponse(
                success=False,
                message=getattr(raw_response, "message", "Cancel failed"),
                code=getattr(raw_response, "code", ""),
                out_trade_no=out_trade_no,
                trade_no=trade_no,
            )

        return CancelResponse(
            success=True,
            message=getattr(raw_response, "message", "Cancel successful"),
            code=getattr(raw_response, "code", ""),
            out_trade_no=out_trade_no,
            trade_no=trade_no,
            raw_data=getattr(raw_response, "data", {}),
        )
