"""
SenWeaver Pay - 优雅的支付SDK

一个功能完整、使用优雅的支付SDK，支持支付宝、微信支付、抖音支付、银联支付等多种支付渠道。
"""

__version__ = "0.1.0"

from .constants import MODE_NORMAL, MODE_SANDBOX, MODE_SERVICE
from .exceptions import (
    AlipayException,
    ChannelException,
    DouyinException,
    GatewayException,
    InvalidArgumentException,
    InvalidConfigException,
    InvalidResponseException,
    InvalidSignException,
    PayException,
    UnipayException,
    WechatException,
)
from .pay import Pay
from .types import (
    CancelRequest,
    CancelResponse,
    Config,
    PaymentChannel,
    PaymentMethod,
    PaymentRequest,
    PaymentResponse,
    PaymentStatus,
    QueryRequest,
    QueryResponse,
    RefundRequest,
    RefundResponse,
    RefundStatus,
    Response,
    UnifiedOrder,
)

__all__ = [
    "Pay",
    "MODE_NORMAL",
    "MODE_SANDBOX",
    "MODE_SERVICE",
    "PayException",
    "InvalidArgumentException",
    "InvalidConfigException",
    "InvalidSignException",
    "InvalidResponseException",
    "ChannelException",
    "GatewayException",
    "AlipayException",
    "WechatException",
    "DouyinException",
    "UnipayException",
    "Response",
    "Config",
    # 基础类型
    "PaymentChannel",
    "PaymentMethod",
    "PaymentStatus",
    "RefundStatus",
    # 请求和响应类型
    "UnifiedOrder",
    "PaymentRequest",
    "PaymentResponse",
    "QueryRequest",
    "QueryResponse",
    "RefundRequest",
    "RefundResponse",
    "CancelRequest",
    "CancelResponse",
]
