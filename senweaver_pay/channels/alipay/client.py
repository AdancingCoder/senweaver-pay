"""
支付宝支付客户端实现
"""

import json
import warnings
from decimal import Decimal
from typing import Any, Dict, Optional, Union

# 全局过滤支付宝证书的NULL参数警告，这是已知问题且不影响功能
warnings.filterwarnings("ignore", category=DeprecationWarning,
                       message=".*NULL parameter value.*")

from ...base import PayChannel
from ...constants import ALIPAY_BASE_URL, MODE_NORMAL
from ...exceptions import (
    AlipayException,
    GatewayException,
    InvalidConfigException,
    InvalidSignException,
)
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
    build_form,
    http_post,
    prepare_public_params,
    sign_params,
    verify_callback,
    verify_response,
)


class Alipay(PayChannel):
    """支付宝支付客户端"""

    def __init__(self, config: Dict[str, Any], app: str = "default"):
        """
        初始化支付宝支付客户端
        :param config: 支付配置
        :param app: 租户应用名称
        """
        super().__init__(config, app)
        self.channel = "alipay"
        self._required_keys = [
            "app_id",
            "app_secret_cert",
            "app_public_cert_path",
            "alipay_public_cert_path",
            "alipay_root_cert_path",
        ]

        # 证书和公钥缓存
        self._public_key_cache = {}
        self._private_key_cache = {}

        # 验证证书文件是否存在
        self._validate_cert_files()

        # 预加载常用的公钥和私钥
        self._preload_keys()

    def _validate_cert_files(self) -> None:
        """
        验证证书文件是否存在
        """
        import os
        from pathlib import Path

        cert_configs = [
            ("app_public_cert_path", "应用公钥证书"),
            ("alipay_public_cert_path", "支付宝公钥证书"),
            ("alipay_root_cert_path", "支付宝根证书")
        ]

        for config_key, desc in cert_configs:
            cert_path = self.config.get(config_key)
            if cert_path:
                # 检查是否为文件路径（而不是证书内容）
                if not cert_path.startswith("-----BEGIN"):
                    cert_file = Path(cert_path)
                    if not cert_file.exists():
                        self._log("error", f"{desc}文件不存在: {cert_path}")
                        raise InvalidConfigException(f"{desc}文件不存在: {cert_path}")
                    elif not cert_file.is_file():
                        self._log("error", f"{desc}路径不是文件: {cert_path}")
                        raise InvalidConfigException(f"{desc}路径不是文件: {cert_path}")
                    else:
                        self._log("debug", f"验证{desc}文件存在: {cert_path}")

    def _preload_keys(self) -> None:
        """
        预加载证书和私钥，避免重复解析
        """
        try:
            # 预加载支付宝公钥
            alipay_public_cert_path = self.config.get("alipay_public_cert_path")
            if alipay_public_cert_path:
                self._get_public_key(alipay_public_cert_path)

            # 预加载应用私钥
            app_secret_cert = self.config.get("app_secret_cert")
            if app_secret_cert:
                self._get_private_key(app_secret_cert)

        except Exception as e:
            self._log("warning", f"Failed to preload keys: {str(e)}")

    def _get_public_key(self, key_path: str):
        """
        获取公钥对象（带缓存）
        :param key_path: 公钥路径或证书路径
        :return: 公钥对象
        """
        if key_path not in self._public_key_cache:
            try:
                import warnings

                from cryptography import x509
                from cryptography.hazmat.backends import default_backend
                from cryptography.hazmat.primitives import serialization

                from ...helper import _get_key_content

                key_bytes = _get_key_content(key_path)
                try:
                    # 尝试直接加载公钥
                    public_key_obj = serialization.load_pem_public_key(key_bytes, backend=default_backend())
                except ValueError:
                    # 如果失败，尝试从证书中提取公钥
                    # 忽略支付宝证书的NULL参数警告，这是已知问题且不影响功能
                    with warnings.catch_warnings():
                        warnings.filterwarnings(
                            "ignore", category=DeprecationWarning, message=".*NULL parameter value.*"
                        )
                        cert = x509.load_pem_x509_certificate(key_bytes, backend=default_backend())
                        public_key_obj = cert.public_key()

                self._public_key_cache[key_path] = public_key_obj
                self._log("debug", f"Loaded and cached public key from: {key_path}")
            except Exception as e:
                # 使用安全标识符记录错误，避免泄露证书内容
                from ...helper import _get_safe_key_identifier
                safe_identifier = _get_safe_key_identifier(key_path)
                self._log("error", f"Failed to load public key from {safe_identifier}: {str(e)}")
                raise InvalidConfigException(f"Failed to load public key from {safe_identifier}")

        return self._public_key_cache[key_path]

    def _get_private_key(self, key_path: str):
        """
        获取私钥对象（带缓存）
        :param key_path: 私钥路径或私钥内容
        :return: 私钥对象
        """
        if key_path not in self._private_key_cache:
            try:
                from cryptography.hazmat.backends import default_backend
                from cryptography.hazmat.primitives import serialization

                from ...helper import _get_key_content, _get_safe_key_identifier

                key_content = _get_key_content(key_path)
                private_key = serialization.load_pem_private_key(key_content, password=None, backend=default_backend())
                self._private_key_cache[key_path] = private_key

                # 使用安全标识符记录日志
                safe_identifier = _get_safe_key_identifier(key_path)
                self._log("debug", f"Loaded and cached private key from: {safe_identifier}")
            except Exception as e:
                # 使用安全标识符记录错误，避免泄露私钥内容
                safe_identifier = _get_safe_key_identifier(key_path)
                self._log("error", f"Failed to load private key from {safe_identifier}: {str(e)}")
                raise InvalidConfigException(f"Failed to load private key from {safe_identifier}")

        return self._private_key_cache[key_path]

    def _get_gateway_url(self) -> str:
        """
        获取支付宝网关地址
        :return: 网关地址
        """
        mode = self.config.get("mode", MODE_NORMAL).upper()
        return ALIPAY_BASE_URL.get(mode, ALIPAY_BASE_URL["NORMAL"])

    def _execute_api(
        self, method: str, biz_content: Dict[str, Any], notify_url: Optional[str] = None, is_async: bool = False
    ) -> Dict[str, Any]:
        """
        执行支付宝API调用
        :param method: 接口名称
        :param biz_content: 业务参数
        :param notify_url: 异步通知地址
        :param is_async: 是否是异步请求
        :return: 接口响应
        """
        # 检查配置
        self._check_config(self._required_keys)
        # 获取配置
        # 准备参数
        params = prepare_public_params(self.config, method, notify_url)

        # 业务参数
        params["biz_content"] = json.dumps(biz_content, ensure_ascii=False)

        # 使用缓存的私钥签名
        app_secret_cert = self.config.get("app_secret_cert")
        private_key = self._get_private_key(app_secret_cert)
        sign_type = params.get("sign_type", "RSA2")
        params = sign_params(params, private_key, sign_type)

        # 请求接口
        if is_async:
            gateway_url = self._get_gateway_url()
            return {"gateway_url": gateway_url, "params": params}
        else:
            try:
                gateway_url_with_charset = f"{self._get_gateway_url()}?charset={params.get('charset')}"
                ret = http_post(gateway_url_with_charset, data=params, config=self.config.get("http", {}))
                raw_string = ret.get("text")
                response = json.loads(raw_string)
                response_type = method.replace(".", "_").lower()
                response_key = f"{response_type}_response"
                alipay_public_cert_path = self.config.get("alipay_public_cert_path")
                public_key = self._get_public_key(alipay_public_cert_path)
                if not verify_response(raw_string, response_key, response, public_key):
                    raise InvalidSignException("Invalid signature in response")

                return response
            except Exception as e:
                self._log("error", f"Alipay API request failed: {str(e)}")

                # 提供更详细的错误信息
                error_msg = str(e)
                if "应用公钥证书不存在" in error_msg:
                    detailed_msg = (
                        f"支付宝API错误: {error_msg}\n"
                        "可能的解决方案:\n"
                        "1. 检查app_public_cert_path配置是否正确\n"
                        "2. 确认证书文件存在且格式正确\n"
                        "3. 登录支付宝开放平台检查应用公钥配置\n"
                        "4. 确认app_id与证书匹配\n"
                        "5. 确认使用正确的环境(沙盒/正式)"
                    )
                    raise GatewayException(detailed_msg)
                else:
                    raise GatewayException(f"Alipay API request failed: {str(e)}")

    def web(self, params: Dict[str, Any]) -> PaymentResponse:
        """
        电脑网站支付
        :param params: 支付参数，必须包含:
            - out_trade_no: 商户订单号
            - total_amount: 订单金额
            - subject: 订单标题
        :return: 支付页面内容
        """
        self._log("info", f"Creating alipay web payment with params: {json.dumps(params, ensure_ascii=False)}")

        # 准备业务参数
        biz_content = {
            "out_trade_no": params.get("out_trade_no", ""),
            "total_amount": params.get("total_amount", ""),
            "subject": params.get("subject", ""),
            "product_code": "FAST_INSTANT_TRADE_PAY",
        }

        # 可选参数
        for key in ["body", "goods_detail", "time_expire", "extend_params", "passback_params"]:
            if key in params:
                biz_content[key] = params[key]

        # 执行API调用
        notify_url = params.get("notify_url") or self.config.get("notify_url")
        api_result = self._execute_api("alipay.trade.page.pay", biz_content, notify_url, True)

        # 构建表单
        gateway_url = api_result["gateway_url"]
        api_params = api_result["params"]
        html = build_form(api_params, gateway_url)

        return PaymentResponse(
            success=True,
            message="Web payment created successfully",
            out_trade_no=params.get("out_trade_no"),
            form_data=html,
            raw_data={"html": html},
        )

    def h5(self, params: Dict[str, Any]) -> PaymentResponse:
        """
        手机网站支付
        :param params: 支付参数，必须包含:
            - out_trade_no: 商户订单号
            - total_amount: 订单金额
            - subject: 订单标题
        :return: 支付页面内容
        """
        self._log("info", f"Creating alipay h5 payment with params: {json.dumps(params, ensure_ascii=False)}")

        # 准备业务参数
        biz_content = {
            "out_trade_no": params.get("out_trade_no", ""),
            "total_amount": params.get("total_amount", ""),
            "subject": params.get("subject", ""),
            "product_code": "QUICK_WAP_WAY",
        }

        # 可选参数
        for key in ["body", "goods_detail", "time_expire", "extend_params", "passback_params"]:
            if key in params:
                biz_content[key] = params[key]

        # 执行API调用
        notify_url = params.get("notify_url") or self.config.get("notify_url")
        api_result = self._execute_api("alipay.trade.wap.pay", biz_content, notify_url, True)

        # 构建表单
        gateway_url = api_result["gateway_url"]
        api_params = api_result["params"]
        html = build_form(api_params, gateway_url)

        return PaymentResponse(
            success=True,
            message="WAP payment created successfully",
            out_trade_no=params.get("out_trade_no"),
            form_data=html,
            raw_data={"html": html},
        )

    def app(self, params: Dict[str, Any]) -> PaymentResponse:
        """
        APP支付
        :param params: 支付参数，必须包含:
            - out_trade_no: 商户订单号
            - total_amount: 订单金额
            - subject: 订单标题
        :return: 支付参数字符串
        """
        self._log("info", f"Creating alipay app payment with params: {json.dumps(params, ensure_ascii=False)}")

        # 准备业务参数
        biz_content = {
            "out_trade_no": params.get("out_trade_no", ""),
            "total_amount": params.get("total_amount", ""),
            "subject": params.get("subject", ""),
            "product_code": "QUICK_MSECURITY_PAY",
        }

        # 可选参数
        for key in ["body", "goods_detail", "time_expire", "extend_params", "passback_params"]:
            if key in params:
                biz_content[key] = params[key]

        # 执行API调用
        notify_url = params.get("notify_url") or self.config.get("notify_url")
        api_result = self._execute_api("alipay.trade.app.pay", biz_content, notify_url, True)

        # 构建URL参数
        api_params = api_result["params"]
        param_str = "&".join([f"{key}={api_params[key]}" for key in api_params])

        return PaymentResponse(
            success=True,
            message="APP payment created successfully",
            out_trade_no=params.get("out_trade_no"),
            app_params={"param_str": param_str},
            raw_data={"param_str": param_str},
        )

    def scan(self, params: Dict[str, Any]) -> PaymentResponse:
        """
        扫码支付
        :param params: 支付参数，必须包含:
            - out_trade_no: 商户订单号
            - total_amount: 订单金额
            - subject: 订单标题
        :return: 支付宝响应，包含二维码链接
        """
        self._log("info", f"Creating alipay scan payment with params: {json.dumps(params, ensure_ascii=False)}")

        # 准备业务参数
        biz_content = {
            "out_trade_no": params.get("out_trade_no", ""),
            "total_amount": params.get("total_amount", ""),
            "subject": params.get("subject", ""),
        }

        # 可选参数
        for key in ["body", "goods_detail", "time_expire", "extend_params", "passback_params"]:
            if key in params:
                biz_content[key] = params[key]

        # 执行API调用
        notify_url = params.get("notify_url") or self.config.get("notify_url")
        response = self._execute_api("alipay.trade.precreate", biz_content, notify_url)

        # 处理响应
        response_data = response.get("alipay_trade_precreate_response", {})
        if response_data.get("code") != "10000":
            return PaymentResponse(
                success=False,
                message=response_data.get("sub_msg") or response_data.get("msg") or "Payment failed",
                code=response_data.get("sub_code") or response_data.get("code"),
                out_trade_no=params.get("out_trade_no"),
                raw_data=response,
            )

        return PaymentResponse(
            success=True,
            message="Scan payment created successfully",
            code=response_data.get("code"),
            out_trade_no=params.get("out_trade_no"),
            qr_code=response_data.get("qr_code"),
            raw_data=response,
        )

    def pos(self, params: Dict[str, Any]) -> PaymentResponse:
        """
        刷卡支付
        :param params: 支付参数，必须包含:
            - out_trade_no: 商户订单号
            - total_amount: 订单金额
            - subject: 订单标题
            - auth_code: 授权码
        :return: 支付宝响应
        """
        self._log("info", f"Creating alipay pos payment with params: {json.dumps(params, ensure_ascii=False)}")

        # 准备业务参数
        biz_content = {
            "out_trade_no": params.get("out_trade_no", ""),
            "total_amount": params.get("total_amount", ""),
            "subject": params.get("subject", ""),
            "auth_code": params.get("auth_code", ""),
            "scene": "bar_code",
        }

        # 可选参数
        for key in ["body", "goods_detail", "time_expire", "extend_params", "passback_params"]:
            if key in params:
                biz_content[key] = params[key]

        # 执行API调用
        notify_url = params.get("notify_url") or self.config.get("notify_url")
        response = self._execute_api("alipay.trade.pay", biz_content, notify_url)

        # 处理响应
        response_data = response.get("alipay_trade_pay_response", {})
        if response_data.get("code") != "10000":
            return PaymentResponse(
                success=False,
                message=response_data.get("sub_msg") or response_data.get("msg") or "Payment failed",
                code=response_data.get("sub_code") or response_data.get("code"),
                out_trade_no=params.get("out_trade_no"),
                raw_data=response,
            )

        return PaymentResponse(
            success=True,
            message="POS payment created successfully",
            code=response_data.get("code"),
            out_trade_no=params.get("out_trade_no"),
            raw_data=response,
        )

    def mini(self, params: Dict[str, Any]) -> PaymentResponse:
        """
        小程序支付
        :param params: 支付参数，必须包含:
            - out_trade_no: 商户订单号
            - total_amount: 订单金额
            - subject: 订单标题
            - buyer_id: 买家的支付宝用户ID
        :return: 支付宝响应
        """
        self._log("info", f"Creating alipay mini payment with params: {json.dumps(params, ensure_ascii=False)}")

        # 准备业务参数
        biz_content = {
            "out_trade_no": params.get("out_trade_no", ""),
            "total_amount": params.get("total_amount", ""),
            "subject": params.get("subject", ""),
            "buyer_id": params.get("buyer_id", ""),
            "product_code": "JSAPI_PAY",
        }

        # 可选参数
        for key in ["body", "goods_detail", "time_expire", "extend_params", "passback_params"]:
            if key in params:
                biz_content[key] = params[key]

        # 执行API调用
        notify_url = params.get("notify_url") or self.config.get("notify_url")
        response = self._execute_api("alipay.trade.create", biz_content, notify_url)

        # 处理响应
        response_data = response.get("alipay_trade_create_response", {})
        if response_data.get("code") != "10000":
            return PaymentResponse(
                success=False,
                message=response_data.get("sub_msg") or response_data.get("msg") or "Payment failed",
                code=response_data.get("sub_code") or response_data.get("code"),
                out_trade_no=params.get("out_trade_no"),
                raw_data=response,
            )

        return PaymentResponse(
            success=True,
            message="Mini payment created successfully",
            code=response_data.get("code"),
            out_trade_no=params.get("out_trade_no"),
            raw_data=response,
        )

    def transfer(self, params: Dict[str, Any]) -> PaymentResponse:
        """
        转账
        :param params: 转账参数，必须包含:
            - out_biz_no: 商户转账唯一订单号
            - trans_amount: 转账金额
            - payee_info: 收款方信息
        :return: 支付宝响应
        """
        self._log("info", f"Creating alipay transfer with params: {json.dumps(params, ensure_ascii=False)}")

        # 准备业务参数
        biz_content = {
            "out_biz_no": params.get("out_biz_no", ""),
            "trans_amount": params.get("trans_amount", ""),
            "product_code": "TRANS_ACCOUNT_NO_PWD",
            "biz_scene": "DIRECT_TRANSFER",
            "payee_info": params.get("payee_info", {}),
        }

        # 可选参数
        for key in ["remark", "order_title"]:
            if key in params:
                biz_content[key] = params[key]

        # 执行API调用
        response = self._execute_api("alipay.fund.trans.uni.transfer", biz_content)

        # 处理响应
        response_data = response.get("alipay_fund_trans_uni_transfer_response", {})
        if response_data.get("code") != "10000":
            return PaymentResponse(
                success=False,
                message=response_data.get("sub_msg") or response_data.get("msg") or "Payment failed",
                code=response_data.get("sub_code") or response_data.get("code"),
                out_trade_no=params.get("out_trade_no"),
                raw_data=response,
            )

        return PaymentResponse(
            success=True,
            message="Payment created successfully",
            code=response_data.get("code"),
            out_trade_no=params.get("out_trade_no"),
            raw_data=response,
        )

    def query(self, params: Union[Dict[str, Any], QueryRequest]) -> QueryResponse:
        """
        查询订单（统一接口，支持传统字典参数和类型化请求）
        :param params: 查询参数，支持两种格式:
            1. 字典格式（传统接口）:
                - out_trade_no或trade_no其中之一
            2. QueryRequest对象（类型化接口）
        :return: QueryResponse 对象
        """
        try:
            # 根据参数类型进行转换
            if isinstance(params, QueryRequest):
                # 类型化请求，转换为字典参数
                converted_params = {}
                if params.out_trade_no:
                    converted_params["out_trade_no"] = params.out_trade_no
                if params.trade_no:
                    converted_params["trade_no"] = params.trade_no
                out_trade_no = params.out_trade_no
                trade_no = params.trade_no
            else:
                # 传统字典参数，直接使用
                converted_params = params
                out_trade_no = params.get("out_trade_no")
                trade_no = params.get("trade_no")

            self._log("info", f"Querying alipay order with params: {json.dumps(converted_params, ensure_ascii=False)}")

            # 准备业务参数
            biz_content = {}
            if "out_trade_no" in converted_params:
                biz_content["out_trade_no"] = converted_params["out_trade_no"]
            elif "trade_no" in converted_params:
                biz_content["trade_no"] = converted_params["trade_no"]
            else:
                raise AlipayException("Missing parameter: either out_trade_no or trade_no is required")

            # 执行API调用
            response = self._execute_api("alipay.trade.query", biz_content)

            # 统一返回类型化响应
            return self._convert_to_query_response(
                response.get("alipay_trade_query_response", {}), out_trade_no, trade_no
            )

        except Exception as e:
            return QueryResponse(
                success=False,
                message=str(e),
                out_trade_no=out_trade_no if "out_trade_no" in locals() else None,
                trade_no=trade_no if "trade_no" in locals() else None,
            )

    def cancel(self, params: Union[Dict[str, Any], CancelRequest]) -> CancelResponse:
        """
        撤销订单（统一接口，支持传统字典参数和类型化请求）
        支付宝的撤销接口用于撤销已支付但可以撤销的订单
        :param params: 撤销参数，支持两种格式:
            1. 字典格式（传统接口）:
                - out_trade_no或trade_no其中之一
            2. CancelRequest对象（类型化接口）
        :return: CancelResponse 对象
        """
        try:
            # 根据参数类型进行转换
            if isinstance(params, CancelRequest):
                # 类型化请求
                out_trade_no = params.out_trade_no
                trade_no = params.trade_no
                # 转换为字典格式调用内部方法
                dict_params = {}
                if out_trade_no:
                    dict_params["out_trade_no"] = out_trade_no
                if trade_no:
                    dict_params["trade_no"] = trade_no
            else:
                # 传统字典参数
                dict_params = params
                out_trade_no = params.get("out_trade_no")
                trade_no = params.get("trade_no")

            # 验证必要参数
            if not out_trade_no and not trade_no:
                raise AlipayException("Missing parameter: either out_trade_no or trade_no is required")

            # 准备业务参数
            biz_content = {}
            if out_trade_no:
                biz_content["out_trade_no"] = out_trade_no
            elif trade_no:
                biz_content["trade_no"] = trade_no

            # 执行API调用 - 使用支付宝的撤销接口
            response = self._execute_api("alipay.trade.cancel", biz_content)

            # 转换响应
            return self._convert_to_cancel_response(
                response.get("alipay_trade_cancel_response", {}), out_trade_no, trade_no
            )

        except Exception as e:
            return CancelResponse(
                success=False,
                message=str(e),
                out_trade_no=out_trade_no if "out_trade_no" in locals() else None,
                trade_no=trade_no if "trade_no" in locals() else None,
            )

    def close(self, params: Union[Dict[str, Any], CancelRequest]) -> CancelResponse:
        """
        关闭订单（统一接口，支持传统字典参数和类型化请求）
        支付宝的关闭接口用于关闭未支付的订单
        :param params: 关闭参数，支持两种格式:
            1. 字典格式（传统接口）:
                - out_trade_no或trade_no其中之一
            2. CancelRequest对象（类型化接口）
        :return: CancelResponse 对象
        """
        try:
            # 根据参数类型进行转换
            if isinstance(params, CancelRequest):
                # 类型化请求
                out_trade_no = params.out_trade_no
                trade_no = params.trade_no
                # 转换为字典格式调用内部方法
                dict_params = {}
                if out_trade_no:
                    dict_params["out_trade_no"] = out_trade_no
                if trade_no:
                    dict_params["trade_no"] = trade_no
            else:
                # 传统字典参数
                dict_params = params
                out_trade_no = params.get("out_trade_no")
                trade_no = params.get("trade_no")

            self._log("info", f"Closing alipay order with params: {json.dumps(dict_params, ensure_ascii=False)}")

            # 验证必要参数
            if not out_trade_no and not trade_no:
                raise AlipayException("Missing parameter: either out_trade_no or trade_no is required")

            # 准备业务参数
            biz_content = {}
            if out_trade_no:
                biz_content["out_trade_no"] = out_trade_no
            elif trade_no:
                biz_content["trade_no"] = trade_no

            # 执行API调用
            response = self._execute_api("alipay.trade.close", biz_content)

            # 转换响应
            return self._convert_to_cancel_response(
                response.get("alipay_trade_close_response", {}), out_trade_no, trade_no
            )

        except Exception as e:
            return CancelResponse(
                success=False,
                message=str(e),
                out_trade_no=out_trade_no if "out_trade_no" in locals() else None,
                trade_no=trade_no if "trade_no" in locals() else None,
            )

    def refund(self, params: Union[Dict[str, Any], RefundRequest]) -> RefundResponse:
        """
        退款（统一接口，支持传统字典参数和类型化请求）
        :param params: 退款参数，支持两种格式:
            1. 字典格式（传统接口）:
                - out_trade_no或trade_no其中之一
                - refund_amount: 退款金额
            2. RefundRequest对象（类型化接口）
        :return: RefundResponse 对象
        """
        try:
            # 根据参数类型进行转换
            if isinstance(params, RefundRequest):
                # 类型化请求，转换为字典参数
                converted_params = self._convert_refund_request(params)
                out_trade_no = params.out_trade_no
                out_refund_no = params.out_refund_no
            else:
                # 传统字典参数，直接使用
                converted_params = params
                out_trade_no = params.get("out_trade_no")
                out_refund_no = params.get("out_request_no")

            self._log("info", f"Refunding alipay order with params: {json.dumps(converted_params, ensure_ascii=False)}")

            # 准备业务参数
            biz_content = {
                "refund_amount": converted_params.get("refund_amount", ""),
            }

            if "out_trade_no" in converted_params:
                biz_content["out_trade_no"] = converted_params["out_trade_no"]
            elif "trade_no" in converted_params:
                biz_content["trade_no"] = converted_params["trade_no"]
            else:
                raise AlipayException("Missing parameter: either out_trade_no or trade_no is required")

            # 可选参数
            for key in ["out_request_no", "refund_reason", "goods_detail"]:
                if key in converted_params:
                    biz_content[key] = converted_params[key]

            # 执行API调用
            response = self._execute_api("alipay.trade.refund", biz_content)

            # 统一返回类型化响应
            return self._convert_to_refund_response(
                response.get("alipay_trade_refund_response", {}), out_trade_no, out_refund_no
            )

        except Exception as e:
            return RefundResponse(
                success=False,
                message=str(e),
                out_trade_no=out_trade_no if "out_trade_no" in locals() else None,
                out_refund_no=out_refund_no if "out_refund_no" in locals() else None,
            )

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
            "subject": order.subject,
            "total_amount": str(order.amount),
            "body": order.body or order.subject,
        }

        # 添加回调地址
        params["notify_url"] = order.notify_url or self.config.get("notify_url")
        params["return_url"] = order.return_url or self.config.get("return_url")

        # 添加额外参数
        if request.extra_params:
            params.update(request.extra_params)

        # 根据支付方式调用对应方法
        if request.method == PaymentMethod.WEB:
            return self.web(params)
        elif request.method == PaymentMethod.H5:
            return self.h5(params)
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
                message=f"Alipay does not support {request.method.value} payment method",
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
        处理支付宝回调
        :param headers: 请求头（支付宝不需要）
        :param raw_body: 原始请求体（支付宝不需要）
        :param form_data: 表单数据（支付宝使用）
        :param query_data: 查询参数（同步回调可能使用）
        :return: 标准化的回调处理结果
        """
        # 支付宝优先使用 form_data，其次是 query_data
        params = form_data or query_data or {}
        self._log("info", f"Processing alipay callback with params: {json.dumps(params, ensure_ascii=False)}")

        # 检查配置
        self._check_config(self._required_keys)

        # 验证签名
        if not verify_callback(params, self.config):
            raise InvalidSignException("Invalid signature in callback")

        # 提取订单信息
        out_trade_no = params.get("out_trade_no")
        trade_no = params.get("trade_no")
        total_amount = params.get("total_amount")
        gmt_payment = params.get("gmt_payment")
        trade_status = params.get("trade_status")

        # 转换支付状态
        from ...types import CallbackResponse, PaymentStatus

        if trade_status == "TRADE_SUCCESS":
            status = PaymentStatus.SUCCESS
        elif trade_status == "TRADE_FINISHED":
            status = PaymentStatus.SUCCESS
        elif trade_status == "TRADE_CLOSED":
            status = PaymentStatus.CANCELLED
        else:
            status = PaymentStatus.FAILED

        # 返回标准化的回调结果
        return CallbackResponse(
            success=True,  # 验签成功才能到这里
            data=params,
            message="Alipay callback processed successfully",
            raw_data=params,
            out_trade_no=out_trade_no,
            trade_no=trade_no,
            amount=total_amount,
            pay_time=gmt_payment,
            status=status,  # 支付状态
        )

    def success(self, params: Optional[Dict[str, Any]] = None) -> str:
        """
        返回成功响应
        :param params: 可选参数
        :return: 支付宝要求的成功响应字符串
        """
        return "success"

    def failure(self, params: Optional[Dict[str, Any]] = None) -> str:
        """
        返回失败响应
        :param params: 可选参数（可包含错误信息）
        :return: 支付宝要求的失败响应字符串
        """
        return "fail"

    def reload(self, config: Dict[str, Any] = None) -> bool:
        """
        重新加载支付宝密钥和配置
        :param config: 新的配置参数，如果提供则更新配置
        :return: 重新加载是否成功
        """
        try:
            # 更新配置
            if config:
                self.config.update(config)

            # 清除缓存的密钥
            self._private_key = None
            self._public_key = None

            # 重新预加载密钥
            self._preload_keys()

            self._log("info", "Alipay keys and config reloaded successfully")
            return True
        except Exception as e:
            self._log("error", f"Failed to reload Alipay keys and config: {str(e)}")
            return False

    def _convert_unified_order(self, order, method, extra_params=None):
        """
        转换统一订单为支付宝特定参数
        :param order: UnifiedOrder 对象
        :param method: PaymentMethod 枚举
        :param extra_params: 额外参数
        :return: 支付宝特定的参数字典
        """
        params = {
            "out_trade_no": order.out_trade_no,
            "subject": order.subject,
            "total_amount": str(order.amount),  # 支付宝使用字符串格式的金额
        }

        # 支付宝特有参数
        if order.body:
            params["body"] = order.body
        if order.notify_url:
            params["notify_url"] = order.notify_url
        if order.return_url:
            params["return_url"] = order.return_url
        if order.attach:
            params["passback_params"] = order.attach  # 支付宝的附加数据字段
        if order.expire_time:
            params["timeout_express"] = order.expire_time  # 支付宝的超时时间字段

        # 根据支付方式设置特定参数
        if method.value == "web":
            params["product_code"] = "FAST_INSTANT_TRADE_PAY"
        elif method.value == "wap":
            params["product_code"] = "QUICK_WAP_WAY"
        elif method.value == "app":
            params["product_code"] = "QUICK_MSECURITY_PAY"
        elif method.value == "scan":
            params["product_code"] = "FACE_TO_FACE_PAYMENT"

        # 处理额外参数
        if extra_params:
            params.update(extra_params)

        return params

    def _convert_refund_request(self, request):
        """
        转换退款请求为支付宝特定参数
        :param request: RefundRequest 对象
        :return: 支付宝特定的参数字典
        """
        params = {}

        # 支付宝退款必需参数
        if request.out_trade_no:
            params["out_trade_no"] = request.out_trade_no
        if request.trade_no:
            params["trade_no"] = request.trade_no
        if request.refund_amount:
            params["refund_amount"] = str(request.refund_amount)  # 支付宝使用字符串格式

        # 支付宝退款可选参数
        if request.out_refund_no:
            params["out_request_no"] = request.out_refund_no  # 支付宝的退款请求号字段
        if request.refund_reason:
            params["refund_reason"] = request.refund_reason
        if request.notify_url:
            params["notify_url"] = request.notify_url

        return params

    # ==================== 支付宝特定的响应转换方法 ====================

    def _convert_to_payment_response(self, raw_response, method, out_trade_no):
        """
        转换支付宝原始响应为统一支付响应
        :param raw_response: 支付宝原始响应
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
        data = getattr(raw_response, "data", "")
        if method in [PaymentMethod.WEB, PaymentMethod.H5]:
            response.pay_url = data  # 支付宝返回的是支付链接
        elif method == PaymentMethod.SCAN:
            response.qr_code = data  # 支付宝返回的是二维码内容
        elif method == PaymentMethod.APP:
            response.app_params = data  # 支付宝返回的是APP调起参数

        return response

    def _convert_to_query_response(self, raw_response, out_trade_no, trade_no):
        """
        转换支付宝查询响应为统一查询响应
        :param raw_response: 支付宝原始响应字典
        :param out_trade_no: 商户订单号
        :param trade_no: 支付宝交易号
        :return: QueryResponse 对象

        支付宝响应示例:
        {
            'code': '10000',
            'msg': 'Success',
            'buyer_logon_id': 'oyw***@sandbox.com',
            'buyer_pay_amount': '0.00',
            'buyer_user_id': '2088722066701854',
            'buyer_user_type': 'PRIVATE',
            'invoice_amount': '0.00',
            'out_trade_no': 'ORDER_1751426958',
            'point_amount': '0.00',
            'receipt_amount': '0.00',
            'send_pay_date': '2025-07-02 11:30:10',
            'total_amount': '0.01',
            'trade_no': '2025070222001401850505989028',
            'trade_status': 'TRADE_SUCCESS'
        }
        """
        # 检查响应是否成功
        if not raw_response or raw_response.get("code") != "10000":
            if raw_response:
                # 构建详细的错误消息
                error_msg = raw_response.get("msg", "Query failed")
                sub_msg = raw_response.get("sub_msg", "")
                if sub_msg:
                    error_msg = f"{error_msg}: {sub_msg}"

                error_code = raw_response.get("code", "")
                sub_code = raw_response.get("sub_code", "")
            else:
                error_msg = "Query failed"
                error_code = ""
                sub_code = ""

            return QueryResponse(
                success=False,
                message=error_msg,
                code=error_code,
                sub_code=sub_code,  # 添加详细错误码
                out_trade_no=out_trade_no,
                trade_no=trade_no,
                raw_data=raw_response or {},
            )

        # 获取支付宝交易状态并转换为统一状态
        alipay_status = raw_response.get("trade_status", "")

        # 支付宝状态映射
        status_mapping = {
            "WAIT_BUYER_PAY": PaymentStatus.PENDING,  # 等待买家付款
            "TRADE_SUCCESS": PaymentStatus.SUCCESS,  # 交易成功
            "TRADE_FINISHED": PaymentStatus.SUCCESS,  # 交易结束
            "TRADE_CLOSED": PaymentStatus.CLOSED,  # 交易关闭
            "TRADE_CANCELLED": PaymentStatus.CANCELLED,  # 交易取消
        }

        payment_status = status_mapping.get(alipay_status, PaymentStatus.PENDING)

        # 转换金额（支付宝返回的是字符串格式）
        total_amount = Decimal(raw_response.get("total_amount", "0")) if raw_response.get("total_amount") else None
        paid_amount = Decimal(raw_response.get("receipt_amount", "0")) if raw_response.get("receipt_amount") else None
        buyer_pay_amount = (
            Decimal(raw_response.get("buyer_pay_amount", "0")) if raw_response.get("buyer_pay_amount") else None
        )

        return QueryResponse(
            success=True,
            message=raw_response.get("msg", "Query successful"),
            code=raw_response.get("code", "10000"),
            out_trade_no=raw_response.get("out_trade_no", out_trade_no),
            trade_no=raw_response.get("trade_no", trade_no),
            status=payment_status,  # 使用 status 参数
            amount=total_amount,  # 使用 amount 参数
            paid_amount=paid_amount or buyer_pay_amount,  # 优先使用 receipt_amount，回退到 buyer_pay_amount
            pay_time=raw_response.get("send_pay_date"),  # 使用 pay_time 参数
            buyer_id=raw_response.get("buyer_user_id"),  # 买家用户ID
            buyer_logon_id=raw_response.get("buyer_logon_id"),  # 买家登录账号
            raw_data=raw_response,
        )

    def _convert_to_refund_response(self, raw_response, out_trade_no, out_refund_no):
        """
        转换支付宝退款响应为统一退款响应
        :param raw_response: 支付宝原始响应字典
        :param out_trade_no: 商户订单号
        :param out_refund_no: 商户退款单号
        :return: RefundResponse 对象

        支付宝退款响应示例:
        {
            'code': '10000',
            'msg': 'Success',
            'buyer_logon_id': 'test***@example.com',
            'buyer_user_id': '2088000000000001',
            'fund_change': 'Y',
            'gmt_refund_pay': '2025-07-02 16:30:45',
            'out_trade_no': 'ORDER_123',
            'refund_fee': '0.01',
            'trade_no': '2025070222001401850505999999'
        }
        """
        # 检查响应是否成功
        if not raw_response or raw_response.get("code") != "10000":
            if raw_response:
                # 构建详细的错误消息
                error_msg = raw_response.get("msg", "Refund failed")
                sub_msg = raw_response.get("sub_msg", "")
                if sub_msg:
                    error_msg = f"{error_msg}: {sub_msg}"

                error_code = raw_response.get("code", "")
                sub_code = raw_response.get("sub_code", "")
            else:
                error_msg = "Refund failed"
                error_code = ""
                sub_code = ""

            return RefundResponse(
                success=False,
                message=error_msg,
                code=error_code,
                sub_code=sub_code,  # 添加详细错误码
                out_trade_no=out_trade_no,
                out_refund_no=out_refund_no,
                raw_data=raw_response or {},
            )

        # 转换退款金额（支付宝返回的是字符串格式）
        refund_amount = Decimal(raw_response.get("refund_fee", "0")) if raw_response.get("refund_fee") else None

        # 判断退款状态
        fund_change = raw_response.get("fund_change", "N")
        refund_status = RefundStatus.SUCCESS if fund_change == "Y" else RefundStatus.PROCESSING

        return RefundResponse(
            success=True,
            message=raw_response.get("msg", "Refund successful"),
            code=raw_response.get("code", "10000"),
            out_trade_no=raw_response.get("out_trade_no", out_trade_no),
            out_refund_no=out_refund_no,  # 支付宝退款响应中通常不返回退款单号
            trade_no=raw_response.get("trade_no"),
            status=refund_status,  # 使用 status 参数
            refund_amount=refund_amount,
            refund_time=raw_response.get("gmt_refund_pay"),  # 退款时间
            buyer_id=raw_response.get("buyer_user_id"),  # 买家用户ID
            buyer_logon_id=raw_response.get("buyer_logon_id"),  # 买家登录账号
            raw_data=raw_response,
        )

    def _convert_to_cancel_response(self, raw_response, out_trade_no, trade_no):
        """
        转换支付宝取消响应为统一取消响应
        :param raw_response: 支付宝原始响应字典
        :param out_trade_no: 商户订单号
        :param trade_no: 支付宝交易号
        :return: CancelResponse 对象

        支付宝取消响应示例:
        {
            'code': '10000',
            'msg': 'Success',
            'out_trade_no': 'ORDER_123',
            'trade_no': '2025070222001401850505999999',
            'retry_flag': 'N',
            'action': 'close'
        }
        """
        # 检查响应是否成功
        if not raw_response or raw_response.get("code") != "10000":
            if raw_response:
                # 构建详细的错误消息
                error_msg = raw_response.get("msg", "Cancel failed")
                sub_msg = raw_response.get("sub_msg", "")
                if sub_msg:
                    error_msg = f"{error_msg}: {sub_msg}"

                error_code = raw_response.get("code", "")
                sub_code = raw_response.get("sub_code", "")
            else:
                error_msg = "Cancel failed"
                error_code = ""
                sub_code = ""

            return CancelResponse(
                success=False,
                message=error_msg,
                code=error_code,
                sub_code=sub_code,  # 添加详细错误码
                out_trade_no=out_trade_no,
                trade_no=trade_no,
                raw_data=raw_response or {},
            )

        # 检查取消操作结果
        action = raw_response.get("action", "")
        retry_flag = raw_response.get("retry_flag", "N")

        # 判断是否需要重试
        need_retry = retry_flag == "Y"

        return CancelResponse(
            success=True,
            message=raw_response.get("msg", "Cancel successful"),
            code=raw_response.get("code", "10000"),
            out_trade_no=raw_response.get("out_trade_no", out_trade_no),
            trade_no=raw_response.get("trade_no", trade_no),
            action=action,  # 取消操作类型（close等）
            retry_flag=retry_flag,  # 是否需要重试
            need_retry=need_retry,  # 便于业务判断
            raw_data=raw_response,
        )
