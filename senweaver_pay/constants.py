"""
常量定义模块，包含各个支付渠道 API 地址等常量
"""

# 支付宝相关常量
ALIPAY_BASE_URL = {
    "NORMAL": "https://openapi.alipay.com/gateway.do",
    "SANDBOX": "https://openapi-sandbox.dl.alipaydev.com/gateway.do",
}
ALIPAY_FORMAT = "json"
ALIPAY_CHARSET = "UTF-8"
ALIPAY_SIGN_TYPE = "RSA2"
ALIPAY_VERSION = "1.0"

# 微信支付相关常量
WECHAT_BASE_URL = {
    "NORMAL": "https://api.mch.weixin.qq.com",
    "SANDBOX": "https://api.mch.weixin.qq.com/sandboxnew",
}
WECHAT_API_V3_URL = "https://api.mch.weixin.qq.com/v3"
WECHAT_PAY_DOMAIN = "api.mch.weixin.qq.com"

# 抖音支付相关常量
DOUYIN_API_BASE = "https://developer.toutiao.com/api"
DOUYIN_MINI_PAYMENT_URL = f"{DOUYIN_API_BASE}/apps/ecpay/v1/create_order"

# 银联支付相关常量
UNIPAY_BASE_URL = {
    "NORMAL": "https://gateway.95516.com",
    "SANDBOX": "https://gateway.test.95516.com",
}
UNIPAY_FRONTEND_TRANSACTION_URL = "/gateway/api/frontTransReq.do"
UNIPAY_BACKEND_TRANSACTION_URL = "/gateway/api/backTransReq.do"
UNIPAY_CARD_TRANSACTION_URL = "/gateway/api/cardTransReq.do"
UNIPAY_APPLET_TRANSACTION_URL = "/gateway/api/appTransReq.do"
UNIPAY_QR_TRANSACTION_URL = "/gateway/api/qrcBackTransReq.do"

# 支付模式常量
MODE_NORMAL = "normal"
MODE_SANDBOX = "sandbox"
MODE_SERVICE = "service"
