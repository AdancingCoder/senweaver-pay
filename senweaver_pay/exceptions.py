"""
异常定义模块，包含支付过程中可能出现的各种异常
"""


class PayException(Exception):
    """支付基础异常类"""

    pass


class InvalidArgumentException(PayException):
    """无效参数异常"""

    pass


class InvalidConfigException(PayException):
    """无效配置异常"""

    pass


class InvalidSignException(PayException):
    """无效签名异常"""

    pass


class InvalidResponseException(PayException):
    """无效响应异常"""

    pass


class ChannelException(PayException):
    """支付渠道异常基类"""

    pass


class GatewayException(PayException):
    """支付网关异常"""

    def __init__(self, message="", raw=None):
        self.message = message
        self.raw = raw
        super().__init__(message)


class AlipayException(ChannelException):
    """支付宝支付异常"""

    pass


class WechatException(ChannelException):
    """微信支付异常"""

    pass


class DouyinException(ChannelException):
    """抖音支付异常"""

    pass


class UnipayException(ChannelException):
    """银联支付异常"""

    pass
