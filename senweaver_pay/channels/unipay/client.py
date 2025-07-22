"""
银联支付客户端实现
"""

import json
import time
from typing import Any, Dict, Optional, Union

from ...base import PayChannel
from ...constants import (
    MODE_NORMAL,
    UNIPAY_BACKEND_TRANSACTION_URL,
    UNIPAY_BASE_URL,
    UNIPAY_CARD_TRANSACTION_URL,
    UNIPAY_FRONTEND_TRANSACTION_URL,
    UNIPAY_QR_TRANSACTION_URL,
)
from ...exceptions import (
    GatewayException,
    InvalidConfigException,
    InvalidSignException,
    UnipayException,
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
from .helper import encrypt_sensitive_data, http_post, sign_params, verify_callback, verify_sign


class Unipay(PayChannel):
    """银联支付客户端"""

    def __init__(self, config: Dict[str, Any], app: str = "default"):
        """
        初始化银联支付客户端
        :param config: 支付配置
        :param app: 租户应用名称
        """
        super().__init__(config, app)
        self.channel = "unipay"
        self._required_keys = [
            "mer_id",
            "mer_private_key_path",
            "mer_public_cert_path",
            "unipay_public_cert_path",
            "notify_url",
        ]

        # 证书和公钥缓存
        self._public_key_cache = {}
        self._private_key_cache = {}

        # 预加载常用的公钥和私钥
        self._preload_keys()

    def _preload_keys(self) -> None:
        """
        预加载证书和私钥，避免重复解析
        """
        try:
            # 预加载银联公钥
            unipay_public_cert_path = self.config.get("unipay_public_cert_path")
            if unipay_public_cert_path:
                self._get_public_key(unipay_public_cert_path)

            # 预加载商户私钥
            mer_private_key_path = self.config.get("mer_private_key_path")
            if mer_private_key_path:
                self._get_private_key(mer_private_key_path)

        except Exception as e:
            self._log("warning", f"Failed to preload keys: {str(e)}")

    def _get_public_key(self, cert_path: str):
        """
        获取公钥对象（带缓存）
        :param cert_path: 证书路径
        :return: 公钥对象
        """
        if cert_path not in self._public_key_cache:
            try:
                from cryptography import x509
                from cryptography.hazmat.backends import default_backend

                from ...helper import _get_key_content

                cert_content = _get_key_content(cert_path)
                cert = x509.load_pem_x509_certificate(cert_content, default_backend())
                self._public_key_cache[cert_path] = cert.public_key()
                self._log("debug", f"Loaded and cached public key from: {cert_path}")
            except Exception as e:
                # 使用安全标识符记录错误，避免泄露证书内容
                from ...helper import _get_safe_key_identifier
                safe_identifier = _get_safe_key_identifier(cert_path)
                self._log("error", f"Failed to load public key from {safe_identifier}: {str(e)}")
                raise InvalidConfigException(f"Failed to load public key from {safe_identifier}")

        return self._public_key_cache[cert_path]

    def _get_private_key(self, key_path: str):
        """
        获取私钥对象（带缓存）
        :param key_path: 私钥路径
        :return: 私钥对象
        """
        if key_path not in self._private_key_cache:
            try:
                from cryptography.hazmat.backends import default_backend
                from cryptography.hazmat.primitives import serialization

                from ...helper import _get_key_content

                key_content = _get_key_content(key_path)
                private_key = serialization.load_pem_private_key(key_content, password=None, backend=default_backend())
                self._private_key_cache[key_path] = private_key
                self._log("debug", f"Loaded and cached private key from: {key_path}")
            except Exception as e:
                # 使用安全标识符记录错误，避免泄露私钥内容
                from ...helper import _get_safe_key_identifier
                safe_identifier = _get_safe_key_identifier(key_path)
                self._log("error", f"Failed to load private key from {safe_identifier}: {str(e)}")
                raise InvalidConfigException(f"Failed to load private key from {safe_identifier}")

        return self._private_key_cache[key_path]

    def _get_base_url(self) -> str:
        """
        获取银联API基础URL
        :return: 基础URL
        """
        mode = self.config.get("mode", MODE_NORMAL).upper()
        return UNIPAY_BASE_URL.get(mode, UNIPAY_BASE_URL["NORMAL"])

    def _get_transaction_url(self, transaction_type: str) -> str:
        """
        获取交易URL
        :param transaction_type: 交易类型
        :return: 交易URL
        """
        base_url = self._get_base_url()

        if transaction_type == "frontend":
            return base_url + UNIPAY_FRONTEND_TRANSACTION_URL
        elif transaction_type == "backend":
            return base_url + UNIPAY_BACKEND_TRANSACTION_URL
        elif transaction_type == "card":
            return base_url + UNIPAY_CARD_TRANSACTION_URL
        elif transaction_type == "qr":
            return base_url + UNIPAY_QR_TRANSACTION_URL
        else:
            raise UnipayException(f"Unsupported transaction type: {transaction_type}")

    def _prepare_base_params(self, transaction_type: str) -> Dict[str, Any]:
        """
        准备基础参数
        :param transaction_type: 交易类型
        :return: 基础参数
        """
        config = self.config

        # 获取商户号
        mer_id = config.get("mer_id")
        if not mer_id:
            raise InvalidConfigException("Missing config: mer_id")

        # 当前时间
        curr_time = time.strftime("%Y%m%d%H%M%S")

        # 基础参数
        params = {
            "version": "5.1.0",  # 版本号
            "encoding": "UTF-8",  # 编码方式
            "signMethod": "01",  # 签名方法，01表示RSA
            "txnType": "01",  # 交易类型，01表示消费
            "txnSubType": "01",  # 交易子类型，01表示自助消费
            "bizType": "000201",  # 业务类型，000201表示B2C网关支付
            "accessType": "0",  # 接入类型，0表示商户直连
            "merId": mer_id,  # 商户代码
            "txnTime": curr_time,  # 订单发送时间
            "currencyCode": "156",  # 交易币种，156表示人民币
        }

        # 添加通知地址
        notify_url = config.get("notify_url")
        if notify_url:
            params["backUrl"] = notify_url

        # 添加前台通知地址
        front_url = config.get("front_url")
        if front_url:
            params["frontUrl"] = front_url

        return params

    def _add_sign(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        添加签名
        :param params: 参数
        :return: 添加签名后的参数
        """
        config = self.config

        # 获取私钥路径
        mer_private_key_path = config.get("mer_private_key_path")
        if not mer_private_key_path:
            raise InvalidConfigException("Missing config: mer_private_key_path")

        # 使用缓存的私钥生成签名
        private_key = self._get_private_key(mer_private_key_path)
        params["signature"] = sign_params(params, private_key)

        return params

    def _request(self, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        发送请求
        :param url: 请求URL
        :param params: 请求参数
        :return: 响应
        """
        self._log("debug", f"Requesting unipay API: {url} with params: {json.dumps(params, ensure_ascii=False)}")

        try:
            response = http_post(url, data=params, config=self.config.get("http", {}))

            self._log("debug", f"Unipay API response: {json.dumps(response, ensure_ascii=False)}")

            # 检查响应签名
            config = self.config
            signature = response.pop("signature", "")

            if not signature:
                raise InvalidSignException("Missing signature in response")

            # 使用缓存的公钥验证签名
            unipay_public_cert_path = config.get("unipay_public_cert_path")
            public_key = self._get_public_key(unipay_public_cert_path)
            if not verify_sign(response, signature, public_key):
                raise InvalidSignException("Invalid signature in response")

            return response
        except Exception as e:
            self._log("error", f"Unipay API request failed: {str(e)}")
            raise GatewayException(f"Unipay API request failed: {str(e)}")

    def web(self, params: Dict[str, Any]) -> PaymentResponse:
        """
        电脑网站支付
        :param params: 支付参数，必须包含:
            - order_id: 商户订单号
            - txn_amt: 交易金额(分)
            - order_desc: 订单描述
        :return: 支付表单
        """
        self._log("info", f"Creating unipay web payment with params: {json.dumps(params, ensure_ascii=False)}")

        # 检查配置
        self._check_config(self._required_keys)

        # 准备参数
        base_params = self._prepare_base_params("frontend")

        # 业务参数
        trans_params = {
            "channelType": "07",  # 渠道类型，07表示互联网
            "orderId": params.get("order_id", ""),  # 商户订单号
            "txnAmt": params.get("txn_amt", ""),  # 交易金额，单位分
            "orderDesc": params.get("order_desc", ""),  # 订单描述
        }

        # 合并参数
        request_params = {**base_params, **trans_params}

        # 添加签名
        request_params = self._add_sign(request_params)

        # 构建表单（暂时不使用）
        # form_html = build_form(self._get_transaction_url("frontend"), request_params)

        return PaymentResponse(
            success=True,
            message="Web payment created successfully",
            out_trade_no=params.get("out_trade_no"),
            raw_data={"response": "success"},
        )

    def h5(self, params: Dict[str, Any]) -> PaymentResponse:
        """
        手机网站支付
        :param params: 支付参数，必须包含:
            - order_id: 商户订单号
            - txn_amt: 交易金额(分)
            - order_desc: 订单描述
        :return: 支付表单
        """
        self._log("info", f"Creating unipay wap payment with params: {json.dumps(params, ensure_ascii=False)}")

        # 检查配置
        self._check_config(self._required_keys)

        # 准备参数
        base_params = self._prepare_base_params("frontend")

        # 业务参数
        trans_params = {
            "channelType": "08",  # 渠道类型，08表示移动
            "orderId": params.get("order_id", ""),  # 商户订单号
            "txnAmt": params.get("txn_amt", ""),  # 交易金额，单位分
            "orderDesc": params.get("order_desc", ""),  # 订单描述
        }

        # 合并参数
        request_params = {**base_params, **trans_params}

        # 添加签名
        request_params = self._add_sign(request_params)

        # 构建表单（暂时不使用）
        # form = build_form(self._get_transaction_url("frontend"), request_params)

        return PaymentResponse(
            success=True,
            message="Wap payment created successfully",
            out_trade_no=params.get("out_trade_no"),
            raw_data={"response": "success"},
        )

    def scan(self, params: Dict[str, Any]) -> PaymentResponse:
        """
        扫码支付
        :param params: 支付参数，必须包含:
            - order_id: 商户订单号
            - txn_amt: 交易金额(分)
            - order_desc: 订单描述
        :return: 二维码链接
        """
        self._log("info", f"Creating unipay scan payment with params: {json.dumps(params, ensure_ascii=False)}")

        # 检查配置
        self._check_config(self._required_keys)

        # 准备参数
        base_params = self._prepare_base_params("backend")

        # 业务参数
        trans_params = {
            "channelType": "07",  # 渠道类型，07表示互联网
            "orderId": params.get("order_id", ""),  # 商户订单号
            "txnAmt": params.get("txn_amt", ""),  # 交易金额，单位分
            "orderDesc": params.get("order_desc", ""),  # 订单描述
            "txnSubType": "07",  # 交易子类型，07表示申请消费二维码
        }

        # 合并参数
        request_params = {**base_params, **trans_params}

        # 添加签名
        request_params = self._add_sign(request_params)

        # 发送请求
        response = self._request(self._get_transaction_url("qr"), request_params)

        # 处理响应
        if response.get("respCode") != "00":
            return PaymentResponse(
                success=False,
                message=response.get("respMsg") or "Payment failed",
                code=response.get("respCode", ""),
                out_trade_no=params.get("out_trade_no"),
                raw_data=response,
            )

        qr_code = response.get("qrCode", "")

        return PaymentResponse(
            success=True,
            message="Scan payment created successfully",
            out_trade_no=params.get("out_trade_no"),
            qr_code=qr_code,
            raw_data=response,
        )

    def pos(self, params: Dict[str, Any]) -> PaymentResponse:
        """
        刷卡支付
        :param params: 支付参数，必须包含:
            - order_id: 商户订单号
            - txn_amt: 交易金额(分)
            - order_desc: 订单描述
            - card_no: 卡号
        :return: 支付结果
        """
        self._log("info", f"Creating unipay pos payment with params: {json.dumps(params, ensure_ascii=False)}")

        # 检查配置
        self._check_config(self._required_keys)

        # 准备参数
        base_params = self._prepare_base_params("backend")

        # 获取卡号
        card_no = params.get("card_no", "")
        if not card_no:
            raise UnipayException("Missing required parameter: card_no")

        # 使用缓存的公钥加密卡号
        config = self.config
        unipay_public_cert_path = config.get("unipay_public_cert_path")
        public_key = self._get_public_key(unipay_public_cert_path)
        encrypted_card_no = encrypt_sensitive_data(card_no, public_key)

        # 业务参数
        trans_params = {
            "channelType": "01",  # 渠道类型，01表示银行卡
            "orderId": params.get("order_id", ""),  # 商户订单号
            "txnAmt": params.get("txn_amt", ""),  # 交易金额，单位分
            "orderDesc": params.get("order_desc", ""),  # 订单描述
            "txnSubType": "01",  # 交易子类型，01表示自助消费
            "cardTransData": encrypted_card_no,  # 加密卡号
        }

        # 合并参数
        request_params = {**base_params, **trans_params}

        # 添加签名
        request_params = self._add_sign(request_params)

        # 发送请求
        response = self._request(self._get_transaction_url("card"), request_params)

        # 处理响应
        if response.get("respCode") != "00":
            return PaymentResponse(
                success=False,
                message=response.get("respMsg") or "Payment failed",
                code=response.get("respCode", ""),
                out_trade_no=params.get("out_trade_no"),
                raw_data=response,
            )

        return PaymentResponse(
            success=True,
            message="POS payment created successfully",
            code=response.get("respCode", ""),
            out_trade_no=params.get("out_trade_no"),
            raw_data=response,
        )

    def query(self, params: Union[Dict[str, Any], QueryRequest]) -> QueryResponse:
        """
        查询订单（统一接口，支持传统字典参数和类型化请求）
        :param params: 查询参数，支持两种格式:
            1. 字典格式（传统接口）:
                - order_id: 商户订单号
            2. QueryRequest对象（类型化接口）
        :return: QueryResponse 对象
        """
        try:
            # 根据参数类型进行转换
            if isinstance(params, QueryRequest):
                # 类型化请求
                out_trade_no = params.out_trade_no
                trade_no = params.trade_no
                # 转换为字典格式
                dict_params = {}
                if out_trade_no:
                    dict_params["order_id"] = out_trade_no  # 银联使用 order_id
                if trade_no:
                    dict_params["query_id"] = trade_no  # 银联使用 query_id
            else:
                # 传统字典参数
                dict_params = params
                out_trade_no = params.get("order_id")
                trade_no = params.get("query_id")

            self._log("info", f"Querying unipay order with params: {json.dumps(dict_params, ensure_ascii=False)}")

            # 检查配置
            self._check_config(self._required_keys)

            # 准备参数
            base_params = self._prepare_base_params("backend")
            base_params["txnType"] = "00"  # 交易类型，00表示查询
            base_params["txnSubType"] = "00"  # 交易子类型，00表示查询

            # 业务参数
            query_params = {
                "orderId": dict_params.get("order_id", ""),  # 商户订单号
            }

            # 合并参数
            request_params = {**base_params, **query_params}

            # 添加签名
            request_params = self._add_sign(request_params)

            # 发送请求
            response = self._request(self._get_transaction_url("backend"), request_params)

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
        取消支付（银联支付不支持取消）
        :param params: 取消参数，支持两种格式:
            1. 字典格式（传统接口）
            2. CancelRequest对象（类型化接口）
        :return: CancelResponse 对象
        """
        # 银联支付不支持取消
        if isinstance(params, CancelRequest):
            out_trade_no = params.out_trade_no
        else:
            out_trade_no = params.get("out_trade_no")

        return CancelResponse(
            success=False,
            message="Unipay does not support cancel payment",
            out_trade_no=out_trade_no,
        )

    def close(self, params: Union[Dict[str, Any], CancelRequest]) -> CancelResponse:
        """
        关闭订单（银联支付不支持关闭订单）
        :param params: 关闭参数，支持两种格式:
            1. 字典格式（传统接口）
            2. CancelRequest对象（类型化接口）
        :return: CancelResponse 对象
        """
        # 银联支付不支持关闭订单
        if isinstance(params, CancelRequest):
            out_trade_no = params.out_trade_no
        else:
            out_trade_no = params.get("out_trade_no")

        return CancelResponse(
            success=False,
            message="Unipay does not support close order",
            out_trade_no=out_trade_no,
        )

    def refund(self, params: Union[Dict[str, Any], RefundRequest]) -> RefundResponse:
        """
        申请退款（统一接口，支持传统字典参数和类型化请求）
        :param params: 退款参数，支持两种格式:
            1. 字典格式（传统接口）:
                - order_id: 商户订单号
                - txn_amt: 退款金额(分)
                - orig_order_id: 原交易订单号
                - orig_txn_time: 原交易时间
            2. RefundRequest对象（类型化接口）
        :return: RefundResponse 对象
        """
        try:
            # 根据参数类型进行转换
            if isinstance(params, RefundRequest):
                # 类型化请求
                out_trade_no = params.out_trade_no
                out_refund_no = params.out_refund_no
                refund_amount = params.refund_amount
                # 转换为字典格式
                dict_params = {
                    "order_id": out_trade_no,
                    "out_refund_no": out_refund_no,
                    "txn_amt": int(float(refund_amount) * 100),  # 转换为分
                }
            else:
                # 传统字典参数
                dict_params = params
                out_trade_no = params.get("order_id")
                out_refund_no = params.get("out_refund_no")

            self._log("info", f"Refunding unipay order with params: {json.dumps(dict_params, ensure_ascii=False)}")

            # 检查配置
            self._check_config(self._required_keys)

            # 准备参数
            base_params = self._prepare_base_params("backend")
            base_params["txnType"] = "04"  # 交易类型，04表示退货

            # 业务参数
            refund_params = {
                "orderId": dict_params.get("order_id", ""),  # 商户订单号
                "txnAmt": dict_params.get("txn_amt", ""),  # 退款金额，单位分
                "origQryId": dict_params.get("orig_query_id", ""),  # 原交易查询流水号
            }

            # 如果没有原交易查询流水号，则需要提供原交易订单号和原交易时间
            if not refund_params["origQryId"]:
                refund_params["origOrderId"] = dict_params.get("orig_order_id", "")  # 原交易订单号
                refund_params["origTxnTime"] = dict_params.get("orig_txn_time", "")  # 原交易时间

            # 合并参数
            request_params = {**base_params, **refund_params}

            # 添加签名
            request_params = self._add_sign(request_params)

            # 发送请求
            response = self._request(self._get_transaction_url("backend"), request_params)

            # 转换响应
            return self._convert_to_refund_response(response, out_trade_no, out_refund_no)

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
            "orderId": order.out_trade_no,
            "txnAmt": str(int(float(order.amount) * 100)).zfill(12),  # 银联金额单位为分，12位
            "orderDesc": order.subject,
        }

        # 添加回调地址
        if order.notify_url:
            params["backUrl"] = order.notify_url
        if order.return_url:
            params["frontUrl"] = order.return_url

        # 添加额外参数
        if request.extra_params:
            params.update(request.extra_params)

        # 根据支付方式调用对应方法
        if request.method == PaymentMethod.WEB:
            return self.web(params)
        elif request.method == PaymentMethod.H5:
            return self.h5(params)
        elif request.method == PaymentMethod.POS:
            return self.pos(params)
        elif request.method == PaymentMethod.SCAN:
            return self.scan(params)
        else:
            return PaymentResponse(
                success=False,
                message=f"Unipay does not support {request.method.value} payment method",
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
        处理银联支付回调
        :param headers: 请求头（银联不需要）
        :param raw_body: 原始请求体（银联不需要）
        :param form_data: 表单数据（银联使用）
        :param query_data: 查询参数（银联不需要）
        :return: 回调处理结果
        """
        params = form_data or {}
        self._log("info", f"Processing unipay callback with params: {json.dumps(params, ensure_ascii=False)}")

        # 检查配置
        self._check_config(self._required_keys)

        # 验证回调参数
        if not params:
            raise UnipayException("Missing callback parameters")

        # 验证签名
        if not verify_callback(params, self.config):
            raise InvalidSignException("Invalid signature in unipay callback")

        # 提取订单信息
        order_id = params.get("orderId")
        query_id = params.get("queryId")
        txn_amt = params.get("txnAmt")
        txn_time = params.get("txnTime")
        resp_code = params.get("respCode")

        # 转换支付状态
        from ...types import CallbackResponse, PaymentStatus

        if resp_code == "00":
            status = PaymentStatus.SUCCESS
        else:
            status = PaymentStatus.FAILED

        # 返回标准化的回调结果
        return CallbackResponse(
            success=True,  # 验签成功才能到这里
            data=params,
            message="Unipay callback processed successfully",
            raw_data=params,
            out_trade_no=order_id,
            trade_no=query_id,
            amount=txn_amt,
            pay_time=txn_time,
            status=status,  # 支付状态
        )

    def success(self, params: Optional[Dict[str, Any]] = None) -> str:
        """
        返回成功响应
        :param params: 可选参数
        :return: 银联要求的成功响应字符串
        """
        return "success"

    def failure(self, params: Optional[Dict[str, Any]] = None) -> str:
        """
        返回失败响应
        :param params: 可选参数（可包含错误信息）
        :return: 银联要求的失败响应字符串
        """
        return "fail"

    def reload(self, config: Dict[str, Any] = None) -> bool:
        """
        重新加载银联证书和密钥
        :param config: 新的配置参数，如果提供则更新配置
        :return: 重新加载是否成功
        """
        try:
            # 更新配置
            if config:
                self.config.update(config)

            # 清除密钥缓存
            self._private_key = None
            self._public_key = None
            self._unipay_public_key = None

            # 重新预加载密钥
            self._preload_keys()

            # 检查是否成功加载
            success = (
                self._private_key is not None and self._public_key is not None and self._unipay_public_key is not None
            )

            if success:
                self._log("info", "Successfully reloaded Unipay certificates and keys")
            else:
                self._log("warning", "Failed to reload some Unipay certificates or keys")

            return success
        except Exception as e:
            self._log("error", f"Failed to reload Unipay certificates: {str(e)}")
            return False

    def _convert_unified_order(self, order, method, extra_params=None):
        """
        转换统一订单为银联特定参数
        :param order: UnifiedOrder 对象
        :param method: PaymentMethod 枚举
        :param extra_params: 额外参数
        :return: 银联特定的参数字典
        """
        params = {
            "orderId": order.out_trade_no,  # 银联使用 orderId
            "txnAmt": str(int(order.amount * 100)),  # 银联使用分为单位的字符串
            "orderDesc": order.subject,
        }

        # 银联特有参数
        if order.body:
            params["orderDesc"] = order.body  # 如果有详细描述，使用详细描述
        if order.notify_url:
            params["backUrl"] = order.notify_url  # 银联使用 backUrl
        if order.return_url:
            params["frontUrl"] = order.return_url  # 银联使用 frontUrl
        if order.attach:
            params["reqReserved"] = order.attach  # 银联的附加数据字段
        if order.expire_time:
            params["payTimeout"] = order.expire_time  # 银联的超时时间字段

        # 根据支付方式设置特定参数
        if method.value == "web":
            params["bizType"] = "000201"  # 银联网关支付
            params["txnType"] = "01"  # 消费
            params["txnSubType"] = "01"  # 自助消费
            params["channelType"] = "07"  # PC
        elif method.value == "wap":
            params["bizType"] = "000201"  # 银联网关支付
            params["txnType"] = "01"  # 消费
            params["txnSubType"] = "01"  # 自助消费
            params["channelType"] = "08"  # 手机
        elif method.value == "scan":
            params["bizType"] = "000000"  # 银联二维码支付
            params["txnType"] = "01"  # 消费
            params["txnSubType"] = "07"  # 二维码消费
            params["channelType"] = "08"  # 手机

        # 处理其他额外参数
        if extra_params:
            params.update(extra_params)

        return params

    def _convert_refund_request(self, request):
        """
        转换退款请求为银联特定参数
        :param request: RefundRequest 对象
        :return: 银联特定的参数字典
        """
        params = {}

        # 银联退款必需参数
        if request.out_trade_no:
            params["origQryId"] = request.out_trade_no  # 银联使用 origQryId
        if request.trade_no:
            params["origQryId"] = request.trade_no
        if request.out_refund_no:
            params["orderId"] = request.out_refund_no  # 银联退款订单号
        if request.refund_amount:
            params["txnAmt"] = str(int(request.refund_amount * 100))  # 银联使用分为单位的字符串

        # 银联退款固定参数
        params["bizType"] = "000201"  # 银联网关支付
        params["txnType"] = "04"  # 退货
        params["txnSubType"] = "00"  # 全额退货
        params["channelType"] = "07"  # PC

        # 银联退款可选参数
        if request.refund_reason:
            params["orderDesc"] = request.refund_reason
        if request.notify_url:
            params["backUrl"] = request.notify_url

        return params

    # ==================== 银联特定的响应转换方法 ====================

    def _convert_to_payment_response(self, raw_response, method, out_trade_no):
        """
        转换银联原始响应为统一支付响应
        :param raw_response: 银联原始响应
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
            response.pay_url = data  # 银联返回的是支付表单或链接
        elif method == PaymentMethod.SCAN:
            response.qr_code = data  # 银联返回的是二维码内容

        return response

    def _convert_to_query_response(self, raw_response, out_trade_no, trade_no):
        """
        转换银联查询响应为统一查询响应
        :param raw_response: 银联原始响应
        :param out_trade_no: 商户订单号
        :param trade_no: 银联交易号
        :return: QueryResponse 对象
        """
        if not raw_response or not getattr(raw_response, "success", False):
            return QueryResponse(
                success=False,
                message=getattr(raw_response, "message", "Query failed"),
                code=getattr(raw_response, "code", ""),
                out_trade_no=out_trade_no,
                trade_no=trade_no,
            )

        # 获取银联交易状态并转换为统一状态
        raw_data = getattr(raw_response, "data", {})
        unipay_status = raw_data.get("origRespCode", "")

        # 银联状态映射
        status_mapping = {
            "00": PaymentStatus.SUCCESS,  # 成功
            "03": PaymentStatus.PENDING,  # 处理中
            "05": PaymentStatus.FAILED,  # 失败
        }

        payment_status = status_mapping.get(unipay_status, PaymentStatus.PENDING)

        return QueryResponse(
            success=True,
            message=getattr(raw_response, "message", "Query successful"),
            code=getattr(raw_response, "code", ""),
            out_trade_no=out_trade_no,
            trade_no=raw_data.get("queryId", trade_no),
            payment_status=payment_status,
            total_amount=int(raw_data.get("txnAmt", 0)) / 100,  # 银联返回分，转换为元
            paid_amount=int(raw_data.get("txnAmt", 0)) / 100,
            raw_data=raw_data,
        )

    def _convert_to_refund_response(self, raw_response, out_trade_no, out_refund_no):
        """
        转换银联退款响应为统一退款响应
        :param raw_response: 银联原始响应
        :param out_trade_no: 商户订单号
        :param out_refund_no: 商户退款单号
        :return: RefundResponse 对象
        """
        if not raw_response or not getattr(raw_response, "success", False):
            return RefundResponse(
                success=False,
                message=getattr(raw_response, "message", "Refund failed"),
                code=getattr(raw_response, "code", ""),
                out_trade_no=out_trade_no,
                out_refund_no=out_refund_no,
            )

        raw_data = getattr(raw_response, "data", {})

        # 银联退款状态映射
        unipay_status = raw_data.get("respCode", "00")
        status_mapping = {
            "00": RefundStatus.SUCCESS,
            "03": RefundStatus.PENDING,
            "05": RefundStatus.FAILED,
        }

        refund_status = status_mapping.get(unipay_status, RefundStatus.PENDING)

        return RefundResponse(
            success=True,
            message=getattr(raw_response, "message", "Refund successful"),
            code=getattr(raw_response, "code", ""),
            out_trade_no=out_trade_no,
            out_refund_no=out_refund_no,
            refund_status=refund_status,
            refund_amount=int(raw_data.get("txnAmt", 0)) / 100,  # 银联返回分，转换为元
            refund_time=raw_data.get("txnTime"),
            raw_data=raw_data,
        )

    def _convert_to_cancel_response(self, raw_response, out_trade_no, trade_no):
        """
        转换银联取消响应为统一取消响应
        :param raw_response: 银联原始响应
        :param out_trade_no: 商户订单号
        :param trade_no: 银联交易号
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
