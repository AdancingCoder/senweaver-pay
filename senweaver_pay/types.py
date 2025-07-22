"""
类型定义模块，定义各种支付参数的数据结构
不使用 pydantic，而是使用简单的类结构来定义数据类型
"""

import json
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional


class BaseObject:
    """基础对象类，提供属性访问和字典转换功能"""

    def __init__(self, **kwargs):
        """初始化对象并设置属性"""
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __getattr__(self, name):
        """访问不存在的属性时返回None"""
        return None

    def to_dict(self) -> Dict[str, Any]:
        """将对象转换为字典"""
        result = {}
        for key, value in self.__dict__.items():
            if not key.startswith("_"):
                if isinstance(value, BaseObject):
                    result[key] = value.to_dict()
                elif isinstance(value, list):
                    result[key] = [item.to_dict() if isinstance(item, BaseObject) else item for item in value]
                else:
                    result[key] = value
        return result

    def to_json(self) -> str:
        """将对象转换为JSON字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BaseObject":
        """从字典创建对象"""
        return cls(**data)


class Response(BaseObject):
    """API响应对象"""

    def __init__(self, success: bool, data: Any = None, message: str = "", code: str = "", raw_data: Any = None):
        """
        初始化响应对象
        :param success: 是否成功
        :param data: 响应数据
        :param message: 响应消息
        :param code: 响应码
        :param raw: 原始响应数据
        """
        super().__init__(success=success, data=data, message=message, code=code, raw_data=raw_data)


class CallbackResponse(Response):
    """回调响应类型"""

    def __init__(
        self,
        success: bool,
        data: Any = None,
        message: str = "",
        code: str = "",
        raw_data: Any = None,
        out_trade_no: str = None,
        trade_no: str = None,
        amount: str = None,
        pay_time: str = None,
        status: "PaymentStatus" = None,
    ):
        """
        初始化回调响应对象
        :param success: 是否成功（包含签名验证成功）
        :param data: 响应数据
        :param message: 响应消息
        :param code: 响应码
        :param raw_data: 原始响应数据
        :param out_trade_no: 商户订单号
        :param trade_no: 平台交易号
        :param amount: 支付金额
        :param pay_time: 支付时间
        :param status: 支付状态（使用统一的 PaymentStatus 枚举）
        """
        super().__init__(success=success, data=data, message=message, code=code, raw_data=raw_data)
        self.out_trade_no = out_trade_no
        self.trade_no = trade_no
        self.amount = amount
        self.pay_time = pay_time
        self.status = status


class LoggerConfig(BaseObject):
    """日志配置"""

    def __init__(
        self,
        enable: bool = False,
        file: str = "./logs/pay.log",
        level: str = "info",
        type: str = "single",
        max_file: int = 30,
    ):
        super().__init__(enable=enable, file=file, level=level, type=type, max_file=max_file)


class HttpConfig(BaseObject):
    """HTTP请求配置"""

    def __init__(self, timeout: float = 5.0, connect_timeout: float = 5.0, **kwargs):
        super().__init__(timeout=timeout, connect_timeout=connect_timeout, **kwargs)


class Config(BaseObject):
    """配置类，包含所有支付渠道的配置"""

    def __init__(
        self,
        alipay: Optional[Dict[str, Dict[str, Any]]] = None,
        wechat: Optional[Dict[str, Dict[str, Any]]] = None,
        douyin: Optional[Dict[str, Dict[str, Any]]] = None,
        unipay: Optional[Dict[str, Dict[str, Any]]] = None,
        logger: Optional[Dict[str, Any]] = None,
        http: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        """
        初始化支付配置
        :param alipay: 支付宝配置
        :param wechat: 微信支付配置
        :param douyin: 抖音支付配置
        :param unipay: 银联支付配置
        :param logger: 日志配置
        :param http: HTTP配置
        :param kwargs: 其他配置
        """
        self.alipay = alipay or {}
        self.wechat = wechat or {}
        self.douyin = douyin or {}
        self.unipay = unipay or {}
        self.logger = LoggerConfig(**(logger or {}))
        self.http = HttpConfig(**(http or {}))

        for key, value in kwargs.items():
            setattr(self, key, value)


class RequestMethod(Enum):
    GET = "GET"
    POST = "POST"
    PATCH = "PATCH"
    PUT = "PUT"
    DELETE = "DELETE"


# ==================== 统一支付接口数据模型 ====================


class PaymentMethod(Enum):
    """支付方式枚举"""

    WEB = "web"  # 电脑网站支付
    H5 = "h5"  # 手机网站支付
    APP = "app"  # APP支付
    SCAN = "scan"  # 扫码支付
    MINI = "mini"  # 小程序支付
    MP = "mp"  # 公众号支付
    POS = "pos"  # 刷卡支付
    TRANSFER = "transfer"  # 转账


class PaymentChannel(Enum):
    """支付渠道枚举"""

    ALIPAY = "alipay"  # 支付宝
    WECHAT = "wechat"  # 微信支付
    DOUYIN = "douyin"  # 抖音支付
    UNIPAY = "unipay"  # 银联支付


class PaymentStatus(Enum):
    """支付状态枚举"""

    PENDING = "pending"  # 待支付
    SUCCESS = "success"  # 支付成功
    FAILED = "failed"  # 支付失败
    CANCELLED = "cancelled"  # 已取消
    CLOSED = "closed"  # 已关闭
    REFUNDED = "refunded"  # 已退款
    PARTIAL_REFUNDED = "partial_refunded"  # 部分退款
    UNKNOWN = "unknown"  # 未知


class RefundStatus(Enum):
    """退款状态枚举"""

    PENDING = "pending"  # 退款处理中
    SUCCESS = "success"  # 退款成功
    FAILED = "failed"  # 退款失败
    UNKNOWN = "unknown"  # 未知


class UnifiedOrder(BaseObject):
    """统一订单数据模型"""

    def __init__(
        self,
        out_trade_no: str,
        amount: Decimal,
        subject: str,
        body: Optional[str] = None,
        currency: str = "CNY",
        expire_time: Optional[str] = None,
        notify_url: Optional[str] = None,
        return_url: Optional[str] = None,
        attach: Optional[str] = None,
        goods_detail: Optional[List[Dict[str, Any]]] = None,
        **kwargs,
    ):
        """
        统一订单初始化
        :param out_trade_no: 商户订单号
        :param amount: 订单金额（元）
        :param subject: 订单标题
        :param body: 订单描述
        :param currency: 货币类型，默认CNY
        :param expire_time: 过期时间
        :param notify_url: 异步通知地址
        :param return_url: 同步返回地址
        :param attach: 附加数据
        :param goods_detail: 商品详情
        :param kwargs: 其他参数
        """
        super().__init__(
            out_trade_no=out_trade_no,
            amount=amount,
            subject=subject,
            body=body,
            currency=currency,
            expire_time=expire_time,
            notify_url=notify_url,
            return_url=return_url,
            attach=attach,
            goods_detail=goods_detail or [],
            **kwargs,
        )

    def validate_for_payment(self) -> bool:
        """
        验证订单是否适合用于支付
        :return: 验证结果
        """
        if not self.out_trade_no:
            raise ValueError("商户订单号不能为空")

        if not self.amount or self.amount <= 0:
            raise ValueError("订单金额必须大于0")

        if not self.subject:
            raise ValueError("订单标题不能为空")

        # 对于生产环境，建议检查URL
        # if not self.notify_url:
        #     raise ValueError("异步通知地址不能为空")

        return True


class PaymentRequest(BaseObject):
    """统一支付请求数据模型"""

    def __init__(
        self,
        channel: PaymentChannel,
        method: PaymentMethod,
        order: UnifiedOrder,
        extra_params: Optional[Dict[str, Any]] = None,
        app: str = "default",
        **kwargs,
    ):
        """
        支付请求初始化
        :param channel: 支付渠道
        :param method: 支付方式
        :param order: 订单信息
        :param extra_params: 额外参数（如微信的openid等）
        :param app: 租户/应用名称
        :param kwargs: 其他参数
        """
        super().__init__(
            channel=channel,
            app=app,
            method=method,
            order=order,
            extra_params=extra_params or {},
            **kwargs,
        )


class PaymentResponse(Response):
    """统一支付响应数据模型"""

    def __init__(
        self,
        success: bool,
        trade_no: Optional[str] = None,
        out_trade_no: Optional[str] = None,
        pay_url: Optional[str] = None,
        qr_code: Optional[str] = None,
        form_data: Optional[str] = None,
        app_params: Optional[Dict[str, Any]] = None,
        message: Optional[str] = None,
        code: Optional[str] = None,
        raw_data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        """
        支付响应初始化
        :param success: 是否成功
        :param trade_no: 支付平台交易号
        :param out_trade_no: 商户订单号
        :param pay_url: 支付链接
        :param qr_code: 二维码内容
        :param form_data: 表单数据
        :param app_params: APP支付参数
        :param message: 响应消息
        :param code: 响应码
        :param raw_data: 原始响应数据
        :param kwargs: 其他参数
        """
        # 调用父类 Response 的初始化
        super().__init__(
            success=success, data=raw_data or {}, message=message or "", code=code or "", raw_data=raw_data or {}
        )

        # 设置支付特有的属性
        self.trade_no = trade_no
        self.out_trade_no = out_trade_no
        self.pay_url = pay_url
        self.qr_code = qr_code
        self.form_data = form_data
        self.app_params = app_params or {}
        self.raw_data = raw_data or {}

        # 设置其他属性
        for key, value in kwargs.items():
            setattr(self, key, value)


class QueryRequest(BaseObject):
    """统一查询请求数据模型"""

    def __init__(
        self,
        channel: PaymentChannel,
        out_trade_no: Optional[str] = None,
        trade_no: Optional[str] = None,
        app: str = "default",
        **kwargs,
    ):
        """
        查询请求初始化
        :param channel: 支付渠道
        :param out_trade_no: 商户订单号
        :param trade_no: 支付平台交易号
        :param app: 租户/应用名称
        :param kwargs: 其他参数
        """
        if not out_trade_no and not trade_no:
            raise ValueError("out_trade_no or trade_no is required")

        super().__init__(
            channel=channel,
            app=app,
            out_trade_no=out_trade_no,
            trade_no=trade_no,
            **kwargs,
        )


class QueryResponse(Response):
    """统一查询响应数据模型"""

    def __init__(
        self,
        success: bool,
        trade_no: Optional[str] = None,
        out_trade_no: Optional[str] = None,
        status: Optional[PaymentStatus] = None,
        amount: Optional[Decimal] = None,
        paid_amount: Optional[Decimal] = None,
        refund_amount: Optional[Decimal] = None,
        pay_time: Optional[str] = None,
        message: Optional[str] = None,
        code: Optional[str] = None,
        raw_data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        """
        查询响应初始化
        :param success: 是否成功
        :param trade_no: 支付平台交易号
        :param out_trade_no: 商户订单号
        :param status: 支付状态
        :param amount: 订单金额
        :param paid_amount: 已支付金额
        :param refund_amount: 已退款金额
        :param pay_time: 支付时间
        :param message: 响应消息
        :param code: 响应码
        :param raw_data: 原始响应数据
        :param kwargs: 其他参数
        """
        # 调用父类构造函数
        super().__init__(
            success=success,
            message=message,
            code=code,
            raw_data=raw_data or {},
        )

        # 设置查询响应特有的属性
        self.trade_no = trade_no
        self.out_trade_no = out_trade_no
        self.status = status
        self.total_amount = amount
        self.paid_amount = paid_amount
        self.refund_amount = refund_amount
        self.pay_time = pay_time
        self.raw_data = raw_data or {}

        # 设置其他属性
        for key, value in kwargs.items():
            setattr(self, key, value)


class RefundRequest(BaseObject):
    """统一退款请求数据模型"""

    def __init__(
        self,
        channel: PaymentChannel,
        out_trade_no: Optional[str] = None,
        trade_no: Optional[str] = None,
        out_refund_no: Optional[str] = None,
        refund_amount: Optional[Decimal] = None,
        total_amount: Optional[Decimal] = None,
        refund_reason: Optional[str] = None,
        notify_url: Optional[str] = None,
        app: str = "default",
        **kwargs,
    ):
        """
        退款请求初始化
        :param channel: 支付渠道
        :param out_trade_no: 商户订单号
        :param trade_no: 支付平台交易号
        :param out_refund_no: 商户退款单号
        :param refund_amount: 退款金额
        :param total_amount: 订单总金额
        :param refund_reason: 退款原因
        :param notify_url: 退款通知地址
        :param app: 租户/应用名称
        :param kwargs: 其他参数
        """
        if not out_trade_no and not trade_no:
            raise ValueError("out_trade_no or trade_no is required")

        super().__init__(
            channel=channel,
            app=app,
            out_trade_no=out_trade_no,
            trade_no=trade_no,
            out_refund_no=out_refund_no,
            refund_amount=refund_amount,
            total_amount=total_amount,
            refund_reason=refund_reason,
            notify_url=notify_url,
            **kwargs,
        )


class RefundResponse(Response):
    """统一退款响应数据模型"""

    def __init__(
        self,
        success: bool,
        refund_id: Optional[str] = None,
        out_refund_no: Optional[str] = None,
        out_trade_no: Optional[str] = None,
        trade_no: Optional[str] = None,
        refund_amount: Optional[Decimal] = None,
        status: Optional[RefundStatus] = None,
        refund_time: Optional[str] = None,
        message: Optional[str] = None,
        code: Optional[str] = None,
        raw_data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        """
        退款响应初始化
        :param success: 是否成功
        :param refund_id: 退款单号
        :param out_refund_no: 商户退款单号
        :param out_trade_no: 商户订单号
        :param trade_no: 支付平台交易号
        :param refund_amount: 退款金额
        :param status: 退款状态
        :param refund_time: 退款时间
        :param message: 响应消息
        :param code: 响应码
        :param raw_data: 原始响应数据
        :param kwargs: 其他参数
        """
        # 调用父类构造函数
        super().__init__(
            success=success,
            message=message,
            code=code,
            raw_data=raw_data or {},
        )

        # 设置退款响应特有的属性
        self.refund_id = refund_id
        self.out_refund_no = out_refund_no
        self.out_trade_no = out_trade_no
        self.trade_no = trade_no
        self.refund_amount = refund_amount
        self.refund_status = status  # 使用 refund_status 而不是 status
        self.refund_time = refund_time
        self.raw_data = raw_data or {}

        # 设置其他属性
        for key, value in kwargs.items():
            setattr(self, key, value)


class CallbackData(BaseObject):
    """统一回调数据模型"""

    def __init__(
        self,
        channel: PaymentChannel,
        trade_no: Optional[str] = None,
        out_trade_no: Optional[str] = None,
        status: Optional[PaymentStatus] = None,
        amount: Optional[Decimal] = None,
        pay_time: Optional[str] = None,
        attach: Optional[str] = None,
        raw_data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        """
        回调数据初始化
        :param channel: 支付渠道
        :param trade_no: 支付平台交易号
        :param out_trade_no: 商户订单号
        :param status: 支付状态
        :param amount: 支付金额
        :param pay_time: 支付时间
        :param attach: 附加数据
        :param raw_data: 原始回调数据
        :param kwargs: 其他参数
        """
        super().__init__(
            channel=channel,
            trade_no=trade_no,
            out_trade_no=out_trade_no,
            status=status,
            amount=amount,
            pay_time=pay_time,
            attach=attach,
            raw_data=raw_data or {},
            **kwargs,
        )


class CancelRequest(BaseObject):
    """统一取消订单请求数据模型"""

    def __init__(
        self,
        channel: PaymentChannel,
        out_trade_no: Optional[str] = None,
        trade_no: Optional[str] = None,
        app: str = "default",
        **kwargs,
    ):
        """
        取消订单请求初始化
        :param channel: 支付渠道
        :param out_trade_no: 商户订单号
        :param trade_no: 支付平台交易号
        :param app: 租户/应用名称
        :param kwargs: 其他参数
        """
        if not out_trade_no and not trade_no:
            raise ValueError("out_trade_no or trade_no is required")

        super().__init__(
            channel=channel,
            app=app,
            out_trade_no=out_trade_no,
            trade_no=trade_no,
            **kwargs,
        )


class CancelResponse(Response):
    """统一取消订单响应数据模型"""

    def __init__(
        self,
        success: bool,
        out_trade_no: Optional[str] = None,
        trade_no: Optional[str] = None,
        message: Optional[str] = None,
        code: Optional[str] = None,
        raw_data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        """
        取消订单响应初始化
        :param success: 是否成功
        :param out_trade_no: 商户订单号
        :param trade_no: 支付平台交易号
        :param message: 响应消息
        :param code: 响应码
        :param raw_data: 原始响应数据
        :param kwargs: 其他参数
        """
        # 调用父类构造函数
        super().__init__(
            success=success,
            message=message,
            code=code,
            raw_data=raw_data or {},
        )

        # 设置取消响应特有的属性
        self.out_trade_no = out_trade_no
        self.trade_no = trade_no
        self.raw_data = raw_data or {}

        # 设置其他属性
        for key, value in kwargs.items():
            setattr(self, key, value)


class Plugins(BaseObject):
    """插件配置类"""

    def __init__(self, plugins: Optional[List[str]] = None):
        super().__init__(plugins=plugins or [])
