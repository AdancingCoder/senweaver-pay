"""
抖音支付客户端实现
"""

import json
import time
from typing import Any, Dict, Optional, Union

from ...base import PayChannel
from ...constants import DOUYIN_API_BASE, DOUYIN_MINI_PAYMENT_URL
from ...exceptions import DouyinException, GatewayException, InvalidConfigException, InvalidSignException
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
from .helper import generate_sign, http_post, verify_callback


class Douyin(PayChannel):
    """抖音支付客户端"""

    def __init__(self, config: Dict[str, Any], app: str = "default"):
        """
        初始化抖音支付客户端
        :param config: 支付配置
        :param app: 租户应用名称
        """
        super().__init__(config, app)
        self.channel = "douyin"
        self._required_keys = [
            "app_id",
            "app_secret",
            "token",
            "salt",
            "notify_url",
        ]

    def _prepare_base_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        准备基础参数
        :param params: 参数
        :return: 处理后的参数
        """
        config = self.config
        app_id = config.get("app_id")

        # 基础参数
        base_params = {
            "app_id": app_id,
            "timestamp": str(int(time.time())),
            "sign_method": "hmac_sha256",
        }

        # 添加通知地址
        notify_url = params.get("notify_url") or config.get("notify_url")
        if notify_url:
            base_params["notify_url"] = notify_url

        return base_params

    def _add_sign(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        添加签名
        :param params: 参数
        :return: 添加签名后的参数
        """
        config = self.config
        salt = config.get("salt")

        if not salt:
            raise InvalidConfigException("Missing config: salt")

        # 生成签名
        params["sign"] = generate_sign(params, salt)

        return params

    def _request(self, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        发送请求
        :param url: 请求URL
        :param params: 请求参数
        :return: 响应
        """
        self._log("debug", f"Requesting douyin API: {url} with params: {json.dumps(params, ensure_ascii=False)}")

        try:
            response = http_post(url, json_data=params, config=self.config.get("http", {}))

            self._log("debug", f"Douyin API response: {json.dumps(response, ensure_ascii=False)}")

            return response
        except Exception as e:
            self._log("error", f"Douyin API request failed: {str(e)}")
            raise GatewayException(f"Douyin API request failed: {str(e)}")

    def mini(self, params: Dict[str, Any]) -> PaymentResponse:
        """
        小程序支付
        :param params: 支付参数，必须包含:
            - out_order_no: 商户订单号
            - total_amount: 订单金额(分)
            - subject: 商品标题
            - body: 商品详情
            - valid_time: 订单有效时间（秒）
        :return: 支付参数
        """
        self._log("info", f"Creating douyin mini payment with params: {json.dumps(params, ensure_ascii=False)}")

        # 检查配置
        self._check_config(self._required_keys)

        # 准备参数
        base_params = self._prepare_base_params(params)

        # 业务参数
        order_params = {
            "out_order_no": params.get("out_order_no", ""),
            "total_amount": params.get("total_amount", 0),
            "subject": params.get("subject", ""),
            "body": params.get("body", ""),
            "valid_time": params.get("valid_time", 7200),  # 默认2小时
        }

        # 合并参数
        request_params = {**base_params, **order_params}

        # 添加签名
        request_params = self._add_sign(request_params)

        # 发送请求
        response = self._request(DOUYIN_MINI_PAYMENT_URL, request_params)

        # 处理响应
        if response.get("err_no") != 0:
            return PaymentResponse(
                success=False,
                message=response.get("err_tips") or "Payment failed",
                code=str(response.get("err_no", "")),
                out_trade_no=params.get("out_trade_no"),
                raw_data=response,
            )

        # 获取订单信息
        order_info = response.get("data", {})

        return PaymentResponse(
            success=True,
            message="Payment created successfully",
            out_trade_no=params.get("out_trade_no"),
            raw_data={
                "order_id": order_info.get("order_id", ""),
                "order_token": order_info.get("order_token", ""),
                **order_info,
            },
        )

    def query(self, params: Union[Dict[str, Any], QueryRequest]) -> QueryResponse:
        """
        查询订单（统一接口，支持传统字典参数和类型化请求）
        :param params: 查询参数，支持两种格式:
            1. 字典格式（传统接口）:
                - out_order_no: 商户订单号
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
                    dict_params["out_order_no"] = out_trade_no  # 抖音使用 out_order_no
                if trade_no:
                    dict_params["order_id"] = trade_no  # 抖音使用 order_id
            else:
                # 传统字典参数
                dict_params = params
                out_trade_no = params.get("out_order_no")
                trade_no = params.get("order_id")

            self._log("info", f"Querying douyin order with params: {json.dumps(dict_params, ensure_ascii=False)}")

            # 检查配置
            self._check_config(self._required_keys)

            # 准备参数
            base_params = self._prepare_base_params(dict_params)

            # 业务参数
            query_params = {
                "out_order_no": dict_params.get("out_order_no", ""),
            }

            # 合并参数
            request_params = {**base_params, **query_params}

            # 添加签名
            request_params = self._add_sign(request_params)

            # 发送请求
            url = f"{DOUYIN_API_BASE}/apps/ecpay/v1/query_order"
            response = self._request(url, request_params)

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
        取消支付（抖音支付不支持取消，使用关闭订单代替）
        :param params: 取消参数，支持两种格式:
            1. 字典格式（传统接口）
            2. CancelRequest对象（类型化接口）
        :return: CancelResponse 对象
        """
        # 抖音支付不支持取消，建议使用关闭订单
        if isinstance(params, CancelRequest):
            out_trade_no = params.out_trade_no
        else:
            out_trade_no = params.get("out_trade_no")

        return CancelResponse(
            success=False,
            message="Douyin does not support cancel payment, please use close order instead",
            out_trade_no=out_trade_no,
        )

    def close(self, params: Union[Dict[str, Any], CancelRequest]) -> CancelResponse:
        """
        取消订单（统一接口，支持传统字典参数和类型化请求）
        :param params: 取消参数，支持两种格式:
            1. 字典格式（传统接口）:
                - out_order_no: 商户订单号
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
                    dict_params["out_order_no"] = out_trade_no  # 抖音使用 out_order_no
                if trade_no:
                    dict_params["order_id"] = trade_no  # 抖音使用 order_id
            else:
                # 传统字典参数
                dict_params = params
                out_trade_no = params.get("out_order_no")
                trade_no = params.get("order_id")

            self._log("info", f"Canceling douyin order with params: {json.dumps(dict_params, ensure_ascii=False)}")

            # 检查配置
            self._check_config(self._required_keys)

            # 准备参数
            base_params = self._prepare_base_params(dict_params)

            # 业务参数
            cancel_params = {
                "out_order_no": dict_params.get("out_order_no", ""),
            }

            # 合并参数
            request_params = {**base_params, **cancel_params}

            # 添加签名
            request_params = self._add_sign(request_params)

            # 发送请求
            url = f"{DOUYIN_API_BASE}/apps/ecpay/v1/cancel_order"
            response = self._request(url, request_params)

            # 转换响应
            return self._convert_to_cancel_response(response, out_trade_no, trade_no)

        except Exception as e:
            return CancelResponse(
                success=False,
                message=str(e),
                out_trade_no=out_trade_no if "out_trade_no" in locals() else None,
                trade_no=trade_no if "trade_no" in locals() else None,
            )

    def refund(self, params: Union[Dict[str, Any], RefundRequest]) -> RefundResponse:
        """
        申请退款（抖音支付不支持退款）
        :param params: 退款参数，支持两种格式:
            1. 字典格式（传统接口）
            2. RefundRequest对象（类型化接口）
        :return: RefundResponse 对象
        """
        # 抖音支付不支持退款
        if isinstance(params, RefundRequest):
            out_trade_no = params.out_trade_no
            out_refund_no = params.out_refund_no
        else:
            out_trade_no = params.get("out_trade_no")
            out_refund_no = params.get("out_refund_no")

        return RefundResponse(
            success=False,
            message="Douyin does not support refund payment",
            out_trade_no=out_trade_no,
            out_refund_no=out_refund_no,
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
            "cp_orderno": order.out_trade_no,
            "cp_extra": order.subject,
            "total_amount": int(float(order.amount) * 100),  # 抖音金额单位为分
            "subject": order.subject,
            "body": order.body or order.subject,
        }

        # 添加回调地址
        if order.notify_url:
            params["notify_url"] = order.notify_url

        # 添加额外参数
        if request.extra_params:
            params.update(request.extra_params)

        # 根据支付方式调用对应方法
        if request.method == PaymentMethod.MINI:
            return self.mini(params)
        else:
            return PaymentResponse(
                success=False,
                message=f"Douyin does not support {request.method.value} payment method",
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
        处理抖音支付回调
        :param headers: 请求头（抖音不需要）
        :param raw_body: 原始请求体（抖音不需要）
        :param form_data: 表单数据（抖音使用）
        :param query_data: 查询参数（抖音不需要）
        :return: 回调处理结果
        """
        params = form_data or {}
        self._log("info", f"Processing douyin callback with params: {json.dumps(params, ensure_ascii=False)}")

        # 检查配置
        self._check_config(self._required_keys)

        # 验证回调参数
        if not params:
            raise DouyinException("Missing callback parameters")

        # 验证签名
        if not verify_callback(params, self.config):
            raise InvalidSignException("Invalid signature in douyin callback")

        # 提取订单信息
        cp_orderno = params.get("cp_orderno")
        order_id = params.get("order_id")
        total_amount = params.get("total_amount")
        pay_time = params.get("pay_time")
        err_no = params.get("err_no")

        # 转换支付状态
        from ...types import CallbackResponse, PaymentStatus

        if err_no == "0":
            status = PaymentStatus.SUCCESS
        else:
            status = PaymentStatus.FAILED

        # 返回标准化的回调结果
        return CallbackResponse(
            success=True,  # 验签成功才能到这里
            data=params,
            message="Douyin callback processed successfully",
            raw_data=params,
            out_trade_no=cp_orderno,
            trade_no=order_id,
            amount=total_amount,
            pay_time=pay_time,
            status=status,  # 支付状态
        )

    def success(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        返回成功响应
        :param params: 可选参数
        :return: 抖音要求的成功响应字典
        """
        return {"err_no": 0, "err_tips": "success"}

    def failure(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        返回失败响应
        :param params: 可选参数（可包含错误信息）
        :return: 抖音要求的失败响应字典
        """
        error_message = "fail"
        if params and "message" in params:
            error_message = params["message"]
        return {"err_no": 1, "err_tips": error_message}

    def reload(self, config: Dict[str, Any] = None) -> bool:
        """
        重新加载抖音支付配置
        :param config: 新的配置参数，如果提供则更新配置
        :return: 重新加载是否成功
        """
        try:
            # 更新配置
            if config:
                self.config.update(config)

            # 验证必要的配置参数
            required_keys = ["app_id", "app_secret", "token", "salt"]
            for key in required_keys:
                if not self.config.get(key):
                    self._log("error", f"Missing required config key: {key}")
                    return False

            self._log("info", "Successfully reloaded Douyin payment configuration")
            return True

        except Exception as e:
            self._log("error", f"Failed to reload Douyin configuration: {str(e)}")
            return False

    def _convert_unified_order(self, order, method, extra_params=None):
        """
        转换统一订单为抖音特定参数
        :param order: UnifiedOrder 对象
        :param method: PaymentMethod 枚举
        :param extra_params: 额外参数
        :return: 抖音特定的参数字典
        """
        params = {
            "out_order_no": order.out_trade_no,  # 抖音使用 out_order_no
            "total_amount": int(order.amount * 100),  # 抖音使用分为单位
            "subject": order.subject,
        }

        # 抖音特有参数
        if order.body:
            params["body"] = order.body
        if order.notify_url:
            params["notify_url"] = order.notify_url
        if order.attach:
            params["cp_extra"] = order.attach  # 抖音的附加数据字段
        if order.expire_time:
            params["valid_time"] = order.expire_time  # 抖音的超时时间字段

        # 根据支付方式设置特定参数
        if method.value == "mini":
            # 小程序支付需要 thirdparty_id
            if extra_params and "thirdparty_id" in extra_params:
                params["thirdparty_id"] = extra_params["thirdparty_id"]

        # 处理其他额外参数
        if extra_params:
            for key, value in extra_params.items():
                if key not in ["thirdparty_id"]:
                    params[key] = value

        return params

    def _convert_refund_request(self, request):
        """
        转换退款请求为抖音特定参数
        :param request: RefundRequest 对象
        :return: 抖音特定的参数字典
        """
        params = {}

        # 抖音退款必需参数
        if request.out_trade_no:
            params["out_order_no"] = request.out_trade_no  # 抖音使用 out_order_no
        if request.trade_no:
            params["order_id"] = request.trade_no  # 抖音使用 order_id
        if request.out_refund_no:
            params["out_refund_no"] = request.out_refund_no
        if request.refund_amount:
            params["refund_amount"] = int(request.refund_amount * 100)  # 抖音使用分

        # 抖音退款可选参数
        if request.refund_reason:
            params["reason"] = request.refund_reason  # 抖音使用 reason
        if request.notify_url:
            params["notify_url"] = request.notify_url

        return params

    # ==================== 抖音特定的响应转换方法 ====================

    def _convert_to_payment_response(self, raw_response, method, out_trade_no):
        """
        转换抖音原始响应为统一支付响应
        :param raw_response: 抖音原始响应
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
        if method == PaymentMethod.MINI:
            response.app_params = data  # 抖音小程序返回的是调起参数

        return response

    def _convert_to_query_response(self, raw_response, out_trade_no, trade_no):
        """
        转换抖音查询响应为统一查询响应
        :param raw_response: 抖音原始响应
        :param out_trade_no: 商户订单号
        :param trade_no: 抖音交易号
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

        # 获取抖音交易状态并转换为统一状态
        raw_data = getattr(raw_response, "data", {})
        douyin_status = raw_data.get("order_status", "")

        # 抖音状态映射
        status_mapping = {
            "SUCCESS": PaymentStatus.SUCCESS,
            "PROCESSING": PaymentStatus.PENDING,
            "FAIL": PaymentStatus.FAILED,
            "TIMEOUT": PaymentStatus.FAILED,
        }

        payment_status = status_mapping.get(douyin_status, PaymentStatus.PENDING)

        return QueryResponse(
            success=True,
            message=getattr(raw_response, "message", "Query successful"),
            code=getattr(raw_response, "code", ""),
            out_trade_no=out_trade_no,
            trade_no=raw_data.get("order_id", trade_no),
            payment_status=payment_status,
            total_amount=raw_data.get("total_amount", 0) / 100,  # 抖音返回分，转换为元
            paid_amount=raw_data.get("total_amount", 0) / 100,
            raw_data=raw_data,
        )

    def _convert_to_refund_response(self, raw_response, out_trade_no, out_refund_no):
        """
        转换抖音退款响应为统一退款响应
        :param raw_response: 抖音原始响应
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

        # 抖音退款状态映射
        douyin_status = raw_data.get("refund_status", "PROCESSING")
        status_mapping = {
            "SUCCESS": RefundStatus.SUCCESS,
            "FAIL": RefundStatus.FAILED,
            "PROCESSING": RefundStatus.PENDING,
        }

        refund_status = status_mapping.get(douyin_status, RefundStatus.PENDING)

        return RefundResponse(
            success=True,
            message=getattr(raw_response, "message", "Refund successful"),
            code=getattr(raw_response, "code", ""),
            out_trade_no=out_trade_no,
            out_refund_no=out_refund_no,
            refund_status=refund_status,
            refund_amount=raw_data.get("refund_amount", 0) / 100,  # 抖音返回分，转换为元
            refund_time=raw_data.get("refund_time"),
            raw_data=raw_data,
        )

    def _convert_to_cancel_response(self, raw_response, out_trade_no, trade_no):
        """
        转换抖音取消响应为统一取消响应
        :param raw_response: 抖音原始响应
        :param out_trade_no: 商户订单号
        :param trade_no: 抖音交易号
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
