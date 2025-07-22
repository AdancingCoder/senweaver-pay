"""
支付门面模块，提供统一的支付接口
"""

import importlib
from decimal import Decimal
from typing import Any, Dict, Union

from .base import PayChannel
from .config import config_manager
from .constants import MODE_NORMAL, MODE_SANDBOX, MODE_SERVICE
from .exceptions import InvalidArgumentException, InvalidConfigException
from .types import (
    CancelRequest,
    CancelResponse,
    PaymentChannel,
    PaymentMethod,
    PaymentRequest,
    PaymentResponse,
    QueryRequest,
    QueryResponse,
    RefundRequest,
    RefundResponse,
    UnifiedOrder,
)


class Pay:
    """支付门面类，提供统一的支付接口"""

    # 支付模式常量
    MODE_NORMAL = MODE_NORMAL
    MODE_SANDBOX = MODE_SANDBOX
    MODE_SERVICE = MODE_SERVICE

    # 支持的支付渠道
    _channels = {
        "alipay": ".channels.alipay.client.Alipay",
        "wechat": ".channels.wechat.client.Wechat",
        "douyin": ".channels.douyin.client.Douyin",
        "unipay": ".channels.unipay.client.Unipay",
    }

    # 实例缓存
    _instances = {}

    @classmethod
    def config(cls, config: Dict[str, Any]) -> None:
        """
        设置支付配置
        :param config: 支付配置
        """
        # 清除已缓存的实例
        cls._instances.clear()
        # 加载配置
        config_manager.load_config(config)

    @classmethod
    def alipay(cls, app: str = "default") -> "PayChannel":
        """
        获取支付宝支付实例
        :param app: 应用名称
        :return: 支付宝实例
        """
        return cls.get_channel("alipay", app)

    @classmethod
    def wechat(cls, app: str = "default") -> "PayChannel":
        """
        获取微信支付实例
        :param app: 应用名称
        :return: 微信支付实例
        """
        return cls.get_channel("wechat", app)

    @classmethod
    def douyin(cls, app: str = "default") -> "PayChannel":
        """
        获取抖音支付实例
        :param app: 应用名称
        :return: 抖音支付实例
        """
        return cls.get_channel("douyin", app)

    @classmethod
    def unipay(cls, app: str = "default") -> "PayChannel":
        """
        获取银联支付实例
        :param app: 应用名称
        :return: 银联支付实例
        """
        return cls.get_channel("unipay", app)

    @classmethod
    def get_channel(cls, channel: str, app: str = "default") -> "PayChannel":
        """
        获取指定渠道的支付实例
        :param channel: 渠道名称
        :param app: 应用名称
        :return: 支付实例
        """
        if not config_manager.is_initialized():
            raise InvalidConfigException("Payment config not initialized. Please call Pay.config() first.")

        if channel not in cls._channels:
            raise InvalidArgumentException(f"Unsupported payment channel: {channel}")

        # 检查缓存
        cache_key = f"{channel}.{app}"
        if cache_key in cls._instances:
            return cls._instances[cache_key]

        # 获取渠道配置
        config = config_manager.get_channel_config(channel, app)

        # 动态导入渠道类
        try:
            module_path, class_name = cls._channels[channel].rsplit(".", 1)
            module = importlib.import_module(module_path, package="senweaver_pay")
            channel_class = getattr(module, class_name)
        except (ImportError, AttributeError) as e:
            raise InvalidArgumentException(f"Failed to import payment channel {channel}: {str(e)}") from e

        # 创建实例
        instance = channel_class(config, app)

        # 缓存实例
        cls._instances[cache_key] = instance

        return instance

    @classmethod
    def reload(cls, channel: str = None, app: str = "default", config: Dict[str, Any] = None) -> Dict[str, bool]:
        """
        重新加载渠道资源（证书、配置等）
        :param channel: 渠道名称，如果为None则重新加载所有已缓存的渠道
        :param app: 应用名称
        :param config: 新的配置参数，如果提供则更新配置
        :return: 重新加载结果字典 {channel_app: success}
        """
        results = {}

        if channel:
            # 重新加载指定渠道
            try:
                instance = cls.get_channel(channel, app)
                cache_key = f"{channel}.{app}"
                results[cache_key] = instance.reload(config)
            except Exception as e:
                cache_key = f"{channel}.{app}"
                results[cache_key] = False
        else:
            # 重新加载所有已缓存的渠道
            for cache_key, instance in cls._instances.items():
                try:
                    results[cache_key] = instance.reload(config)
                except Exception as e:
                    results[cache_key] = False

        return results

    # ==================== 类型化的统一支付接口 ====================

    @classmethod
    def create_order(
        cls,
        out_trade_no: str,
        amount: Union[str, Decimal],
        subject: str,
        body: str = None,
        currency: str = "CNY",
        notify_url: str = None,
        return_url: str = None,
        **kwargs,
    ) -> UnifiedOrder:
        """
        创建统一订单对象
        :param out_trade_no: 商户订单号（必填）
        :param amount: 订单金额（必填）
        :param subject: 订单标题（必填）
        :param body: 订单描述
        :param currency: 货币类型，默认CNY
        :param notify_url: 异步通知地址
        :param return_url: 同步返回地址
        :param kwargs: 其他参数
        :return: 统一订单对象

        示例:
        order = Pay.create_order(
            out_trade_no='ORDER_001',
            amount='99.99',
            subject='测试商品',
            body='这是一个测试订单',
            notify_url='http://example.com/notify'
        )
        """
        return UnifiedOrder(
            out_trade_no=out_trade_no,
            amount=Decimal(str(amount)),
            subject=subject,
            body=body,
            currency=currency,
            notify_url=notify_url,
            return_url=return_url,
            **kwargs,
        )

    @classmethod
    def create(cls, request: PaymentRequest) -> PaymentResponse:
        """
        创建支付订单（使用类型化请求）
        :param request: 支付请求对象
        :return: 支付响应对象

        示例:
        request = PaymentRequest(
            channel=PaymentChannel.ALIPAY,
            method=PaymentMethod.SCAN,
            order=order,
            extra_params={'scene': 'offline'}
        )
        response = Pay.create(request)
        """
        try:
            # 使用请求中的 app 参数获取对应租户的渠道实例
            instance = cls.get_channel(request.channel.value, request.app)
            # 直接调用统一的 create 方法
            return instance.create(request)
        except Exception as e:
            return PaymentResponse(success=False, message=str(e), out_trade_no=request.order.out_trade_no)

    @classmethod
    def query(cls, request: QueryRequest) -> QueryResponse:
        """
        查询支付订单（使用类型化请求）
        :param request: 查询请求对象
        :return: 查询响应对象

        示例:
        request = QueryRequest(
            channel=PaymentChannel.ALIPAY,
            out_trade_no='ORDER_001'
        )
        response = Pay.query(request)
        """
        try:
            instance = cls.get_channel(request.channel.value, request.app)

            # 直接调用合并后的统一接口
            return instance.query(request)

        except Exception as e:
            return QueryResponse(
                success=False, message=str(e), out_trade_no=request.out_trade_no, trade_no=request.trade_no
            )

    @classmethod
    def refund(cls, request: RefundRequest) -> RefundResponse:
        """
        申请退款（使用类型化请求）
        :param request: 退款请求对象
        :return: 退款响应对象

        示例:
        request = RefundRequest(
            channel=PaymentChannel.ALIPAY,
            out_trade_no='ORDER_001',
            out_refund_no='REFUND_001',
            refund_amount=Decimal('50.00'),
            total_amount=Decimal('99.99'),
            refund_reason='用户申请退款'
        )
        response = Pay.refund(request)
        """
        try:
            instance = cls.get_channel(request.channel.value, request.app)

            # 直接调用合并后的统一接口
            return instance.refund(request)

        except Exception as e:
            return RefundResponse(
                success=False, message=str(e), out_trade_no=request.out_trade_no, out_refund_no=request.out_refund_no
            )

    @classmethod
    def cancel(cls, request: CancelRequest) -> CancelResponse:
        """
        取消支付订单（使用类型化请求）
        :param request: 取消请求对象
        :return: 取消响应对象

        示例:
        request = CancelRequest(
            channel=PaymentChannel.ALIPAY,
            out_trade_no='ORDER_001'
        )
        response = Pay.cancel(request)
        """
        try:
            instance = cls.get_channel(request.channel.value, request.app)

            # 直接调用合并后的统一接口
            return instance.cancel(request)

        except Exception as e:
            return CancelResponse(
                success=False, message=str(e), out_trade_no=request.out_trade_no, trade_no=request.trade_no
            )

    # ==================== 简化的字符串接口（向后兼容） ====================

    @classmethod
    def pay(cls, channel: str, method: str, app: str = "default", **params) -> PaymentResponse:
        """
        简化的支付接口
        :param channel: 支付渠道字符串
        :param method: 支付方式字符串
        :param app: 租户/应用名称
        :param params: 支付参数
        :return: 支付响应对象
        """
        try:
            # 转换为枚举类型
            channel_enum = PaymentChannel(channel)
            method_enum = PaymentMethod(method)

            # 创建订单对象
            order = UnifiedOrder(
                out_trade_no=params.get("out_trade_no", ""),
                amount=Decimal(str(params.get("total_amount", params.get("amount", 0)))),
                subject=params.get("subject", params.get("description", "")),
                body=params.get("body", ""),
                notify_url=params.get("notify_url", ""),
                return_url=params.get("return_url", ""),
            )

            # 创建请求对象
            request = PaymentRequest(
                channel=channel_enum,
                app=app,
                method=method_enum,
                order=order,
                extra_params={
                    k: v
                    for k, v in params.items()
                    if k
                    not in [
                        "out_trade_no",
                        "total_amount",
                        "amount",
                        "subject",
                        "description",
                        "body",
                        "notify_url",
                        "return_url",
                    ]
                },
            )

            return cls.create(request)

        except Exception as e:
            return PaymentResponse(success=False, message=str(e), out_trade_no=params.get("out_trade_no", ""))
