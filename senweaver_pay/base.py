"""
支付渠道基类模块，定义所有支付渠道需要实现的接口
"""

import logging
from abc import ABC
from typing import Any, Dict, List, Optional, Union

from .exceptions import InvalidConfigException
from .helper import setup_logger
from .types import (
    CallbackResponse,
    CancelRequest,
    CancelResponse,
    PaymentRequest,
    PaymentResponse,
    QueryRequest,
    QueryResponse,
    RefundRequest,
    RefundResponse,
)


class PayChannel(ABC):
    """支付渠道基类，定义所有支付渠道共有的属性和方法"""

    def __init__(self, config: Optional[Dict[str, Any]] = None, app: str = "default"):
        """
        初始化支付渠道
        :param config: 渠道配置
        :param app: 租户应用名称
        """
        self.config = config or {}
        self.app = app
        self.channel = self.__class__.__name__.lower()
        self.logger = None
        self._setup_logger()

    def _setup_logger(self) -> None:
        """配置日志记录器"""
        if self.config.get("logger", {}).get("enable", False):
            logger_config = self.config.get("logger", {})
            self.logger = setup_logger(
                f"senweaver_pay.{self.channel}",
                logger_config.get("file", f"./logs/{self.channel}.log"),
                logger_config.get("level", "info"),
                logger_config.get("type", "single"),
                logger_config.get("max_file", 30),
            )
        else:
            # 创建一个空的记录器
            self.logger = logging.getLogger(f"senweaver_pay.{self.channel}")
            self.logger.addHandler(logging.NullHandler())

    def _log(self, level: str, message: str, *args, **kwargs) -> None:
        """
        记录日志
        :param level: 日志级别
        :param message: 日志消息
        """
        if not self.logger:
            return

        level_map = {
            "debug": self.logger.debug,
            "info": self.logger.info,
            "warning": self.logger.warning,
            "error": self.logger.error,
            "critical": self.logger.critical,
        }

        log_func = level_map.get(level.lower(), self.logger.info)
        log_func(message, *args, **kwargs)

    def _check_config(self, required_keys: List[str]) -> None:
        """
        检查配置是否包含必要的键
        :param required_keys: 必要的键列表
        :raises InvalidConfigException: 如果缺少必要的键
        """
        config = self.config
        for key in required_keys:
            if key not in config or config[key] is None or config[key] == "":
                raise InvalidConfigException(f"Missing required config key: {key}")

    # ==================== 基础支付方法（子类必须实现） ====================

    def web(self, params: Dict[str, Any]) -> PaymentResponse:
        """网页支付"""
        raise NotImplementedError(f"{self.__class__.__name__} does not support web payment")

    def h5(self, params: Dict[str, Any]) -> PaymentResponse:
        """H5支付"""
        raise NotImplementedError(f"{self.__class__.__name__} does not support h5 payment")

    def app(self, params: Dict[str, Any]) -> PaymentResponse:
        """APP支付"""
        raise NotImplementedError(f"{self.__class__.__name__} does not support app payment")

    def mini(self, params: Dict[str, Any]) -> PaymentResponse:
        """小程序支付"""
        raise NotImplementedError(f"{self.__class__.__name__} does not support mini payment")

    def pos(self, params: Dict[str, Any]) -> PaymentResponse:
        """刷卡支付"""
        raise NotImplementedError(f"{self.__class__.__name__} does not support pos payment")

    def scan(self, params: Dict[str, Any]) -> PaymentResponse:
        """扫码支付"""
        raise NotImplementedError(f"{self.__class__.__name__} does not support scan payment")

    def transfer(self, params: Dict[str, Any]) -> PaymentResponse:
        """账户转账"""
        raise NotImplementedError(f"{self.__class__.__name__} does not support transfer")

    # ==================== 回调和通知方法（子类必须实现） ====================

    def callback(
        self,
        headers: Optional[Dict[str, str]] = None,
        raw_body: Optional[str] = None,
        form_data: Optional[Dict[str, Any]] = None,
        query_data: Optional[Dict[str, Any]] = None,
    ) -> CallbackResponse:
        """
        处理支付回调（统一接口）
        :param headers: 请求头（微信需要）
        :param raw_body: 原始请求体（微信需要）
        :param form_data: 表单数据（支付宝、抖音、银联需要）
        :param query_data: 查询参数（同步回调可能需要）
        :return: 回调处理结果
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support callback")

    def success(self, params: Optional[Dict[str, Any]] = None) -> Any:
        """
        返回成功响应
        :param params: 可选参数
        :return: 成功响应，格式根据渠道要求而定（字符串或字典）
        """
        # 默认返回字典格式，子类可以覆盖为其他格式
        return {"code": "SUCCESS", "message": "SUCCESS"}

    def failure(self, params: Optional[Dict[str, Any]] = None) -> Any:
        """
        返回失败响应
        :param params: 可选参数（可包含错误信息）
        :return: 失败响应，格式根据渠道要求而定（字符串或字典）
        """
        # 默认返回字典格式，子类可以覆盖为其他格式
        error_message = "FAIL"
        if params and "message" in params:
            error_message = params["message"]
        return {"code": "FAIL", "message": error_message}

    def reload(self, config: Dict[str, Any] = None) -> bool:
        """
        重新加载渠道资源（证书、配置等）
        :param config: 新的配置参数，如果提供则更新配置
        :return: 重新加载是否成功
        """
        if config:
            # 更新配置
            self.config.update(config)
        return True

    # ==================== 统一接口（子类必须实现） ====================

    def create(self, request: PaymentRequest) -> PaymentResponse:
        """
        创建支付订单（统一接口）
        子类根据 request.method 处理不同的支付方式
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support create payment")

    def query(self, params: Union[Dict[str, Any], QueryRequest]) -> QueryResponse:
        """
        查询支付订单（统一接口）
        :param params: 查询参数，支持两种格式:
            1. 字典格式（传统接口）
            2. QueryRequest对象（类型化接口）
        :return: QueryResponse 对象
        """
        # 默认实现：不支持查询
        if isinstance(params, QueryRequest):
            out_trade_no = params.out_trade_no
        else:
            out_trade_no = params.get("out_trade_no")

        return QueryResponse(
            success=False,
            message=f"{self.__class__.__name__} does not support query payment",
            out_trade_no=out_trade_no,
        )

    def refund(self, params: Union[Dict[str, Any], RefundRequest]) -> RefundResponse:
        """
        申请退款（统一接口）
        :param params: 退款参数，支持两种格式:
            1. 字典格式（传统接口）
            2. RefundRequest对象（类型化接口）
        :return: RefundResponse 对象
        """
        # 默认实现：不支持退款
        if isinstance(params, RefundRequest):
            out_trade_no = params.out_trade_no
            out_refund_no = params.out_refund_no
        else:
            out_trade_no = params.get("out_trade_no")
            out_refund_no = params.get("out_refund_no")

        return RefundResponse(
            success=False,
            message=f"{self.__class__.__name__} does not support refund payment",
            out_trade_no=out_trade_no,
            out_refund_no=out_refund_no,
        )

    def cancel(self, params: Union[Dict[str, Any], CancelRequest]) -> CancelResponse:
        """
        取消支付（统一接口）
        :param params: 取消参数，支持两种格式:
            1. 字典格式（传统接口）
            2. CancelRequest对象（类型化接口）
        :return: CancelResponse 对象
        """
        # 默认实现：不支持取消
        if isinstance(params, CancelRequest):
            out_trade_no = params.out_trade_no
        else:
            out_trade_no = params.get("out_trade_no")

        return CancelResponse(
            success=False,
            message=f"{self.__class__.__name__} does not support cancel payment",
            out_trade_no=out_trade_no,
        )

    def close(self, params: Union[Dict[str, Any], CancelRequest]) -> CancelResponse:
        """
        关闭订单（统一接口）
        :param params: 关闭参数，支持两种格式:
            1. 字典格式（传统接口）
            2. CancelRequest对象（类型化接口）
        :return: CancelResponse 对象
        """
        # 默认实现：不支持关闭
        if isinstance(params, CancelRequest):
            out_trade_no = params.out_trade_no
        else:
            out_trade_no = params.get("out_trade_no")

        return CancelResponse(
            success=False,
            message=f"{self.__class__.__name__} does not support close order",
            out_trade_no=out_trade_no,
        )
