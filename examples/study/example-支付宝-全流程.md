# senweaver-pay × 支付宝 全链路详解

> 基于 `senweaver_pay` 核心库 + `examples/fastapi_example` 示例，完整梳理支付宝支付的架构设计、前后端交互及数据流。

---

## 目录

1. [整体架构设计](#1-整体架构设计)
2. [证书与密钥体系](#2-证书与密钥体系)
3. [配置初始化](#3-配置初始化)
4. [支付方式全览](#4-支付方式全览)
5. [核心数据模型](#5-核心数据模型)
6. [签名机制详解](#6-签名机制详解)
7. [各场景全链路数据流](#7-各场景全链路数据流)
   - 7.1 网页支付（Web）
   - 7.2 手机网站支付（H5）
   - 7.3 APP 支付
   - 7.4 扫码支付（商家出示码）
   - 7.5 刷卡支付（POS）
   - 7.6 小程序支付（Mini）
8. [异步通知（Notify）全链路](#8-异步通知notify全链路)
9. [同步回调（Return）全链路](#9-同步回调return全链路)
10. [订单查询](#10-订单查询)
11. [订单关闭 & 撤销](#11-订单关闭--撤销)
12. [退款](#12-退款)
13. [多租户（多 App）支持](#13-多租户多-app-支持)
14. [异常体系](#14-异常体系)
15. [关键设计决策](#15-关键设计决策)

---

## 1. 整体架构设计

### 1.1 分层模型

```
┌─────────────────────────────────────────────┐
│              业务层（FastAPI 路由）            │
│  /create_order  /notify  /return  /query    │
│  /cancel  /close  /refund                   │
└──────────────────┬──────────────────────────┘
                   │ 调用
┌──────────────────▼──────────────────────────┐
│              门面层（Pay）                    │
│  Pay.config()  Pay.create()  Pay.query()    │
│  Pay.refund()  Pay.cancel()  Pay.alipay()   │
│  内置实例缓存（_instances），避免重复构造      │
└──────────────────┬──────────────────────────┘
                   │ 动态 importlib
┌──────────────────▼──────────────────────────┐
│           抽象基类层（PayChannel）             │
│  定义协议：create / query / refund / cancel  │
│           callback / success / failure       │
└──────────────────┬──────────────────────────┘
                   │ 继承实现
┌──────────────────▼──────────────────────────┐
│         渠道实现层（Alipay / Wechat / ...）   │
│  senweaver_pay/channels/alipay/client.py    │
│  web / h5 / app / scan / pos / mini / ...   │
└──────────────────┬──────────────────────────┘
                   │ 调用
┌──────────────────▼──────────────────────────┐
│           工具层（helper.py）                 │
│  prepare_public_params  sign_params         │
│  build_form  verify_response                │
│  verify_callback  http_post                 │
│  generate_sign_str  rsa_sign  rsa_verify    │
└──────────────────┬──────────────────────────┘
                   │ HTTPS POST
┌──────────────────▼──────────────────────────┐
│         支付宝开放平台网关                    │
│  正式：https://openapi.alipay.com/gateway.do│
│  沙箱：https://openapi-sandbox.dl.alipaydev.com/gateway.do │
└─────────────────────────────────────────────┘
```

### 1.2 核心类职责

| 类 / 模块 | 文件 | 职责 |
|---|---|---|
| `Pay` | `pay.py` | 门面，统一入口，实例缓存，多渠道路由 |
| `PayChannel` | `base.py` | 抽象基类，定义接口契约，日志骨架 |
| `Alipay` | `channels/alipay/client.py` | 支付宝渠道实现，7种支付方式，回调验签，响应转换 |
| `ConfigManager` | `config.py` | 全局单例，加载/存储/分发配置 |
| helper（alipay） | `channels/alipay/helper.py` | 签名、表单构建、HTTP、证书SN提取、验签 |
| helper（通用） | `helper.py` | RSA签名/验签、HMAC、密钥读取、日志 |
| 类型定义 | `types.py` | 无 pydantic 的纯 Python 数据模型 |
| 常量 | `constants.py` | 网关地址、字符集、签名类型等 |
| 异常 | `exceptions.py` | 分层异常体系 |

---

## 2. 证书与密钥体系

支付宝使用「证书模式」，商户需要准备四个文件：

```
certs/alipay/
├── private_key.pem          # 应用私钥（商户自己生成，绝对保密）
├── appCertPublicKey.crt     # 应用公钥证书（上传到支付宝开放平台）
├── alipayCertPublicKey.crt  # 支付宝公钥证书（从开放平台下载）
└── alipayRootCert.crt       # 支付宝根证书（从开放平台下载）
```

| 文件 | 配置键 | 用途 |
|---|---|---|
| `private_key.pem` | `app_secret_cert` | 请求签名（商户→支付宝），RSA2 |
| `appCertPublicKey.crt` | `app_public_cert_path` | 提取 `app_cert_sn`，作为公共参数上传 |
| `alipayCertPublicKey.crt` | `alipay_public_cert_path` | 验证支付宝响应签名（支付宝→商户） |
| `alipayRootCert.crt` | `alipay_root_cert_path` | 提取 `alipay_root_cert_sn`，作为公共参数上传 |

**证书 SN 的计算方式**（`helper.py: get_app_cert_sn / get_root_cert_sn`）：

```
SN = MD5(issuer_string + serial_number)
```

- `issuer_string`：证书颁发者字段，按 `CN,O,OU,C` 顺序倒转拼接
- `serial_number`：十进制序列号字符串
- 根证书包含多个子证书，只取 `sha1WithRSAEncryption` 和 `sha256WithRSAEncryption` 算法的证书，用 `_` 拼接
- SN 会在首次计算后缓存到 config（`config['app_public_cert_sn']`），避免重复 IO

**密钥缓存**（`client.py: _get_public_key / _get_private_key`）：

```python
# 使用 dict 缓存已解析的密钥对象，避免每次请求重复解析
self._public_key_cache = {}   # key_path → cryptography PublicKey 对象
self._private_key_cache = {}  # key_path → cryptography PrivateKey 对象
```

初始化时即预加载（`_preload_keys`），首次请求无额外 IO 开销。

---

## 3. 配置初始化

### 3.1 配置结构

```python
Pay.config({
    "alipay": {
        "default": {                          # app 名称，支持多租户
            "app_id": "2016xxxxxxx",
            "app_secret_cert": "/path/to/private_key.pem",  # 或 PEM 内容字符串
            "app_public_cert_path": "/path/to/appCertPublicKey.crt",
            "alipay_public_cert_path": "/path/to/alipayCertPublicKey.crt",
            "alipay_root_cert_path": "/path/to/alipayRootCert.crt",
            "notify_url": "https://your-domain.com/notify/alipay",
            "return_url": "https://your-domain.com/return/alipay",
            "mode": "sandbox",   # normal | sandbox | service
        },
        "merchant_b": { ... }   # 第二个租户配置
    },
    "wechat": { ... },
    "logger": {
        "enable": True,
        "file": "./logs/alipay.log",
        "level": "info",
        "type": "daily",   # single | daily
        "max_file": 30
    }
})
```

### 3.2 初始化时序

```
Pay.config(dict)
  └─ config_manager.load_config(dict)
       └─ Config(**dict)  → 各渠道 dict 挂载到 Config 属性
            └─ Pay._instances.clear()  # 清除旧缓存

Pay.alipay()  或  Pay.get_channel("alipay", "default")
  └─ config_manager.get_channel_config("alipay", "default")  → 提取对应 dict
  └─ importlib.import_module(".channels.alipay.client", "senweaver_pay")
  └─ Alipay(config, app="default")
       ├─ _validate_cert_files()   # 检查证书文件是否存在
       └─ _preload_keys()          # 预加载公钥/私钥对象到 cache
  └─ 实例存入 Pay._instances["alipay.default"]
```

---

## 4. 支付方式全览

| 方式 | `PaymentMethod` | 支付宝 API | 返回内容 | 适用场景 |
|---|---|---|---|---|
| 网页支付 | `WEB` | `alipay.trade.page.pay` | HTML 自动提交表单 | PC 浏览器 |
| 手机网站 | `H5` | `alipay.trade.wap.pay` | HTML 自动提交表单 | 手机浏览器 |
| APP 支付 | `APP` | `alipay.trade.app.pay` | URL 参数字符串 | 原生 App |
| 扫码支付 | `SCAN` | `alipay.trade.precreate` | 二维码 URL（`qr_code`）| 线下商户出示码 |
| 刷卡支付 | `POS` | `alipay.trade.pay` | 交易结果 | 线下 POS 机 |
| 小程序支付 | `MINI` | `alipay.trade.create` | 交易号（`trade_no`）| 支付宝小程序 |
| 转账 | `TRANSFER` | `alipay.fund.trans.uni.transfer` | 转账结果 | 企业到个人转账 |

---

## 5. 核心数据模型

> 均继承自 `BaseObject`，无 pydantic 依赖，访问不存在属性时返回 `None`。

### 5.1 UnifiedOrder（统一订单）

```python
UnifiedOrder(
    out_trade_no = "ORDER_1751426958",   # 商户订单号，全局唯一
    amount       = Decimal("99.99"),     # 金额（元），内部保持 Decimal 精度
    subject      = "测试商品",
    body         = "商品描述",           # 可选
    currency     = "CNY",               # 货币类型
    notify_url   = "https://...",       # 异步通知地址
    return_url   = "https://...",       # 同步跳转地址
    attach       = "自定义透传数据",     # → 支付宝的 passback_params
    expire_time  = "30m",              # → 支付宝的 timeout_express
)
```

### 5.2 PaymentRequest / PaymentResponse

```python
# 请求
PaymentRequest(
    channel      = PaymentChannel.ALIPAY,
    method       = PaymentMethod.WEB,
    order        = order,              # UnifiedOrder
    extra_params = {},                 # 渠道特有参数
    app          = "default",          # 租户 ID
)

# 响应（根据支付方式填充不同字段）
PaymentResponse(
    success    = True,
    form_data  = "<form>...</form>",  # WEB/H5
    qr_code    = "https://qr.alipay.com/...",  # SCAN
    app_params = {"param_str": "..."},          # APP
    pay_url    = "https://...",                 # 部分场景
    out_trade_no = "ORDER_xxx",
    raw_data   = { ... },             # 支付宝原始响应
)
```

### 5.3 状态枚举映射

| 支付宝 `trade_status` | 统一 `PaymentStatus` |
|---|---|
| `WAIT_BUYER_PAY` | `PENDING` |
| `TRADE_SUCCESS` | `SUCCESS` |
| `TRADE_FINISHED` | `SUCCESS` |
| `TRADE_CLOSED` | `CLOSED` |
| `TRADE_CANCELLED` | `CANCELLED` |

---

## 6. 签名机制详解

### 6.1 请求签名流程

```
1. 组装公共参数（prepare_public_params）
   ├─ app_id, method, format="JSON", charset="utf-8"
   ├─ sign_type="RSA2", timestamp, version="1.0"
   ├─ notify_url（可选）
   ├─ app_cert_sn（证书模式）← MD5(issuer+serial) of appCertPublicKey.crt
   └─ alipay_root_cert_sn（证书模式）← MD5拼接 of alipayRootCert.crt

2. 填入业务参数（biz_content）
   └─ JSON 序列化后作为字符串整体填入 params["biz_content"]

3. 生成待签名字符串（generate_sign_str）
   ├─ 排除 sign 字段
   ├─ 排除值为 None 或 "" 的字段
   ├─ 按参数名字典序排序
   └─ 拼接格式：key1=val1&key2=val2&...

4. RSA2 签名（rsa_sign）
   ├─ 算法：SHA256withRSA（PKCS1v15 填充）
   ├─ 使用缓存的私钥对象（避免重复解析）
   └─ 输出：Base64 编码的签名字符串 → params["sign"]

5. 发送：HTTP POST 到网关（form-urlencoded）
```

### 6.2 响应验签流程

```
支付宝返回 JSON 原始字符串示例：
{
  "alipay_trade_page_pay_response": { ... },
  "sign": "BASE64...",
  "sign_type": "RSA2"
}

验签步骤（verify_response）：
1. 从原始字符串中提取 "alipay_xxx_response" 的 JSON 片段
   └─ 用栈匹配括号，保留原始转义字符（避免重新序列化破坏签名）
2. 提取响应中的 sign 字段
3. rsa_verify(sign_content, sign, 支付宝公钥对象)
   └─ SHA256withRSA 验证，使用缓存的公钥对象
```

### 6.3 异步通知验签流程

```
支付宝 POST 通知（form-urlencoded）：
out_trade_no=xxx&trade_status=TRADE_SUCCESS&sign=BASE64...&sign_type=RSA2&...

verify_callback 步骤：
1. 从 params 中弹出 sign 和 sign_type
2. 对剩余参数生成待签名字符串（同请求签名规则）
3. rsa_verify(sign_content, sign, 支付宝公钥证书路径)
   └─ 自动从证书文件中提取公钥
```

---

## 7. 各场景全链路数据流

### 7.1 网页支付（Web）

```
用户浏览器           商户服务器（FastAPI）           支付宝开放平台
    │                       │                           │
    │─── POST /create_order ──▶│                           │
    │   channel=alipay        │                           │
    │   method=web            │                           │
    │   amount=99.99          │                           │
    │                        │                           │
    │                        │─── _execute_api ──────────▶│
    │                        │   method: alipay.trade.page.pay
    │                        │   is_async=True（不发 HTTP）│
    │                        │   ↓                        │
    │                        │  build_form(params, gateway_url)
    │                        │  生成 HTML 自动提交表单     │
    │                        │                           │
    │◀── HTMLResponse ────────│                           │
    │   <form action="https://openapi.alipay.com/gateway.do" method="post">
    │   <input name="app_id" ...>                        │
    │   <input name="sign" ...>                          │
    │   <script>form.submit()</script>                   │
    │                        │                           │
    │─── 浏览器自动 POST ──────────────────────────────────▶│
    │                        │                           │  支付宝展示收银台
    │                        │                           │
    │◀────────── 用户支付完成，支付宝跳转 return_url ─────────│
    │─── GET /return/alipay?out_trade_no=...&sign=... ───▶│
    │                        │  instance.callback(query_data=...)
    │                        │  verify_callback → 更新订单
    │◀─── 重定向 /success ────│
```

**`form_data` 字段结构（PaymentResponse）**：

```html
<form id="alipay_payment_form"
      action="https://openapi.alipay.com/gateway.do?charset=UTF-8"
      method="post" accept-charset="UTF-8">
  <input type="hidden" name="app_id" value="2016xxx">
  <input type="hidden" name="method" value="alipay.trade.page.pay">
  <input type="hidden" name="biz_content" value='{"out_trade_no":"ORDER_xxx","total_amount":"99.99","subject":"测试商品","product_code":"FAST_INSTANT_TRADE_PAY"}'>
  <input type="hidden" name="app_cert_sn" value="abcd1234...">
  <input type="hidden" name="alipay_root_cert_sn" value="efgh5678...">
  <input type="hidden" name="sign" value="BASE64_SIGNATURE...">
  ...
</form>
<script>document.getElementById('alipay_payment_form').submit();</script>
```

---

### 7.2 手机网站支付（H5）

流程与 Web 几乎相同，差异点：

| 对比项 | Web | H5 |
|---|---|---|
| API 方法 | `alipay.trade.page.pay` | `alipay.trade.wap.pay` |
| `product_code` | `FAST_INSTANT_TRADE_PAY` | `QUICK_WAP_WAY` |
| 展示入口 | PC 浏览器收银台 | 手机浏览器唤起支付宝 App 或 H5 |
| 返回内容 | HTML Form | HTML Form（相同结构） |

---

### 7.3 APP 支付

```
移动 App              商户服务器（FastAPI）           支付宝开放平台
    │                       │                           │
    │─── POST /create_order ──▶│                           │
    │   channel=alipay        │                           │
    │   method=app            │                           │
    │                        │                           │
    │                        │  _execute_api(is_async=True)
    │                        │  不发 HTTP 请求，本地组装参数
    │                        │  param_str = "&".join(key=value)
    │                        │                           │
    │◀── {"type":"app_params","data":{"param_str":"..."}} │
    │                        │                           │
    │  App 调用支付宝 SDK，传入 param_str                  │
    │─────────────────────────────────────────────────────▶│
    │  支付宝 SDK 调起支付 App                             │
    │◀───── 支付结果回调（App 内） ────────────────────────│
    │                        │                           │
    │  （服务器端异步通知同 Web）                           │
```

**`app_params` 字段结构**：

```python
response.app_params = {
    "param_str": "app_id=2016xxx&biz_content=%7B...%7D&sign=BASE64..."
}
```

`product_code` 固定为 `QUICK_MSECURITY_PAY`。

---

### 7.4 扫码支付（商家出示码）

```
收银员/系统            商户服务器                      支付宝开放平台
    │                       │                           │
    │─── POST /create_order ──▶│                           │
    │   method=scan           │                           │
    │                        │─── alipay.trade.precreate ─▶│
    │                        │   同步 HTTP POST（is_async=False）
    │                        │◀────────────────────────────│
    │                        │  {"alipay_trade_precreate_response":{
    │                        │    "code":"10000",
    │                        │    "qr_code":"https://qr.alipay.com/xxx"}}
    │                        │  verify_response（验证支付宝响应签名）
    │◀── {"type":"qrcode","code_url":"https://qr..."} ────│
    │                        │                           │
    │  将 qr_code 渲染成二维码展示给用户                   │
    │                        │                           │
    │  用户扫码 ───────────────────────────────────────────▶│
    │  用户在支付宝 App 确认支付                            │
    │                        │◀── POST /notify/alipay ────│
    │                        │  trade_status=TRADE_SUCCESS │
    │                        │  verify_callback + 更新订单 │
    │                        │─── "success" ──────────────▶│
```

---

### 7.5 刷卡支付（POS）

```
POS 机               商户服务器                      支付宝开放平台
    │  扫用户付款码    │                           │
    │─── POST /create_order ──▶│                           │
    │   method=pos            │                           │
    │   auth_code=用户付款码   │                           │
    │                        │─── alipay.trade.pay ───────▶│
    │                        │   biz_content.scene="bar_code"
    │                        │◀────────────────────────────│
    │                        │  {"alipay_trade_pay_response":{
    │                        │    "code":"10000",
    │                        │    "trade_no":"xxx"}}
    │◀── 支付结果 ────────────│                           │
```

`auth_code` 为用户支付宝 App 付款码（18 位数字），`scene` 固定为 `bar_code`。

---

### 7.6 小程序支付（Mini）

```
支付宝小程序          商户服务器                      支付宝开放平台
    │                       │                           │
    │  获取 buyer_id（用户 UID）                           │
    │─── POST /create_order ──▶│                           │
    │   method=mini           │                           │
    │   buyer_id=2088xxx      │                           │
    │                        │─── alipay.trade.create ────▶│
    │                        │   product_code="JSAPI_PAY"  │
    │                        │◀────────────────────────────│
    │                        │  {"alipay_trade_create_response":{
    │                        │    "code":"10000",
    │                        │    "trade_no":"xxx"}}
    │◀── {"trade_no":"xxx"} ──│                           │
    │                        │                           │
    │  小程序调用 my.tradePay(tradeNO)                     │
    │  唤起支付宝收银台                                     │
    │                        │◀── POST /notify/alipay ────│
```

---

## 8. 异步通知（Notify）全链路

```
支付宝                         商户服务器（FastAPI）
    │                               │
    │─── POST /notify/alipay ────────▶│
    │   Content-Type: application/x-www-form-urlencoded
    │   Body:                        │
    │   out_trade_no=ORDER_xxx       │
    │   trade_status=TRADE_SUCCESS   │
    │   total_amount=99.99           │
    │   trade_no=2025070222001401850 │
    │   gmt_payment=2025-07-02 11:30:10
    │   sign=BASE64...               │
    │   sign_type=RSA2               │
    │   ...更多字段...               │
    │                               │
    │                               ├── instance.callback(
    │                               │     form_data=dict(form_data),
    │                               │     headers=headers,
    │                               │     raw_body=...
    │                               │   )
    │                               │
    │                               ├── verify_callback(params, config)
    │                               │   1. 弹出 sign, sign_type
    │                               │   2. generate_sign_str（字典序）
    │                               │   3. rsa_verify → True/False
    │                               │   └── 验签失败抛出 InvalidSignException
    │                               │
    │                               ├── 解析 trade_status → PaymentStatus
    │                               │   TRADE_SUCCESS → PaymentStatus.SUCCESS
    │                               │
    │                               ├── 返回 CallbackResponse
    │                               │   .out_trade_no, .trade_no
    │                               │   .amount, .pay_time, .status
    │                               │
    │                               ├── 业务处理（更新订单状态、发货等）
    │                               │
    │◀── "success"（纯文本字符串）────│
    │                               │
    │  如果返回非 "success"，支付宝会 │
    │  间隔 1min/2min/3min... 重复通知│
    │  最多通知 24 小时              │
```

**关键细节**：

- 支付宝通知必须响应纯文本 `"success"`，不是 JSON。`instance.success()` 直接返回字符串 `"success"`。
- FastAPI 路由返回字符串会被包装为 JSON，实际需要注意返回类型。示例中 `return instance.success()` 返回 `"success"` 字符串，FastAPI 会自动处理。
- 通知接收后**必须先验签，再处理业务逻辑**，防止伪造通知。
- 同一通知可能多次送达，业务侧需做幂等处理（检查订单状态，已完成则直接返回 success）。

---

## 9. 同步回调（Return）全链路

```
浏览器                         商户服务器
    │                               │
    │─── GET /return/alipay ─────────▶│
    │   ?out_trade_no=ORDER_xxx      │
    │   &trade_no=2025070222...      │
    │   &total_amount=99.99          │
    │   &sign=BASE64...              │
    │   &sign_type=RSA2              │
    │                               │
    │                               ├── instance.callback(
    │                               │     query_data=dict(request.query_params)
    │                               │   )
    │                               ├── verify_callback → 验签
    │                               ├── 更新本地订单缓存
    │                               │
    │◀── 302 重定向到 /success ───────│
```

**与异步通知的区别**：

| 对比项 | 异步通知（Notify） | 同步回调（Return） |
|---|---|---|
| 请求方 | 支付宝服务器 | 用户浏览器 |
| 方法 | POST | GET |
| 参数位置 | form_data | query_params |
| 可靠性 | 高（重试机制） | 低（网络可能中断） |
| 用途 | **权威支付结果** | 用户体验跳转 |
| 必须响应 | `"success"` | 任意（重定向即可） |

**重要**：不能仅凭同步回调判定支付成功，必须以异步通知或主动查询为准。

---

## 10. 订单查询

```python
# 接口调用
query_request = QueryRequest(
    channel=PaymentChannel.ALIPAY,
    out_trade_no="ORDER_xxx"   # 或 trade_no="2025070222..."
)
response = Pay.query(query_request)
```

**内部流程**：

```
Pay.query(request)
  └─ instance.query(request)
       └─ _execute_api("alipay.trade.query", biz_content)
            ├─ HTTP POST → 支付宝
            ├─ 解析响应 JSON
            ├─ verify_response → 验签
            └─ _convert_to_query_response()
                 ├─ 映射 trade_status → PaymentStatus
                 ├─ 转换金额 str → Decimal
                 └─ 返回 QueryResponse
```

**`QueryResponse` 关键字段**：

```python
response.success        # bool
response.status         # PaymentStatus.SUCCESS / PENDING / CLOSED ...
response.total_amount   # Decimal("99.99")
response.paid_amount    # Decimal 实际付款额（receipt_amount）
response.pay_time       # "2025-07-02 11:30:10"
response.trade_no       # "2025070222001401850505989028"
response.out_trade_no   # "ORDER_xxx"
response.buyer_id       # "2088722066701854"
response.buyer_logon_id # "oyw***@sandbox.com"
response.raw_data       # 支付宝原始响应 dict
```

---

## 11. 订单关闭 & 撤销

### 11.1 关闭订单（close）

适用于：订单已创建但**未支付**，不再需要支付。

```python
close_request = CancelRequest(channel=PaymentChannel.ALIPAY, out_trade_no="ORDER_xxx")
response = instance.close(close_request)
# 调用 alipay.trade.close API
```

### 11.2 撤销订单（cancel）

适用于：已支付但需要撤销（通常用于当日 POS 场景）。

```python
cancel_request = CancelRequest(channel=PaymentChannel.ALIPAY, out_trade_no="ORDER_xxx")
response = Pay.cancel(cancel_request)
# 调用 alipay.trade.cancel API
```

**`CancelResponse` 关键字段**：

```python
response.action      # "close"（关闭）或 "refund"（已付款被撤销退款）
response.retry_flag  # "Y" 表示需要重试，"N" 无需重试
response.need_retry  # bool，便于业务判断
```

---

## 12. 退款

```python
refund_request = RefundRequest(
    channel=PaymentChannel.ALIPAY,
    out_trade_no="ORDER_xxx",
    out_refund_no="REFUND_001",    # → 支付宝的 out_request_no
    refund_amount=Decimal("50.00"),
    refund_reason="用户申请退款",
)
response = Pay.refund(refund_request)
# 调用 alipay.trade.refund API
```

**字段映射**（`_convert_refund_request`）：

| 统一字段 | 支付宝字段 |
|---|---|
| `out_trade_no` | `out_trade_no` |
| `trade_no` | `trade_no` |
| `refund_amount` | `refund_amount`（字符串） |
| `out_refund_no` | `out_request_no`（注意字段名差异） |
| `refund_reason` | `refund_reason` |

**退款状态判断**：

```python
fund_change = raw_response.get("fund_change", "N")
# "Y" → RefundStatus.SUCCESS（资金已退还）
# "N" → RefundStatus.PROCESSING（处理中）
```

**`RefundResponse` 关键字段**：

```python
response.refund_amount   # Decimal，实际退款金额（refund_fee）
response.refund_time     # "2025-07-02 16:30:45"（gmt_refund_pay）
response.refund_status   # RefundStatus.SUCCESS / PROCESSING
response.trade_no        # 支付宝交易号
```

---

## 13. 多租户（多 App）支持

门面层通过 `app` 参数区分租户，实例按 `channel.app` 缓存：

```python
# 配置多个租户
Pay.config({
    "alipay": {
        "default":    { "app_id": "2016xxx", ... },
        "merchant_a": { "app_id": "2016yyy", ... },
        "merchant_b": { "app_id": "2016zzz", ... },
    }
})

# 使用不同租户
alipay_a = Pay.alipay("merchant_a")
alipay_b = Pay.alipay("merchant_b")

# 缓存键：
# Pay._instances["alipay.merchant_a"] → Alipay 实例 A
# Pay._instances["alipay.merchant_b"] → Alipay 实例 B
```

每个 `Alipay` 实例独立持有各自的配置、证书缓存和私钥缓存，互不干扰。

---

## 14. 异常体系

```
Exception
└─ PayException（基础）
   ├─ InvalidArgumentException  参数错误
   ├─ InvalidConfigException    配置错误（缺配置项、证书文件不存在）
   ├─ InvalidSignException      签名验证失败（请求验签/回调验签）
   ├─ InvalidResponseException  响应格式错误
   ├─ GatewayException          网关请求失败（HTTP 错误/支付宝返回错误）
   └─ ChannelException（渠道基类）
      └─ AlipayException        支付宝业务逻辑错误（缺少必要参数等）
```

门面层 `Pay.create/query/refund/cancel` 会捕获所有异常并返回 `success=False` 的响应对象，业务层无需处理底层异常。

---

## 15. 关键设计决策

### 15.1 `is_async` 参数的含义

`_execute_api(..., is_async=True)` 并非异步 IO，而是指「不实际发 HTTP 请求，仅返回待发送参数」。这是因为 WEB/H5/APP 支付实际上是由**浏览器**或**移动 App** 直接向支付宝发请求，商户服务器只需要构造好带签名的参数/表单/URL。

```python
if is_async:
    # 直接返回参数，不发 HTTP
    return {"gateway_url": gateway_url, "params": params}
else:
    # 服务器主动发 HTTP POST，等待响应
    ret = http_post(gateway_url_with_charset, data=params, ...)
```

### 15.2 响应验签为什么要用原始字符串而非解析后的 dict

支付宝的签名是对「原始 JSON 字符串」做的，JSON 序列化后字符顺序、转义方式可能不同。`get_sign_content` 用栈匹配括号从原始字符串中直接截取，保留所有原始转义，避免重新序列化带来的签名不一致。

### 15.3 为什么密钥和证书 SN 都有缓存

- 密钥解析（PEM → cryptography 对象）有 CPU 开销，每次请求解析是浪费。
- 证书 SN 计算需要文件 IO + MD5，结果是固定的，缓存到 config dict 后无需重复计算。
- 初始化时 `_preload_keys()` 即完成预热，首次请求零延迟。

### 15.4 `build_form` 的 HTML 转义

表单中的 `biz_content` 是 JSON 字符串，含有 `{` `}` `"` 等特殊字符。`html.escape()` 将其转义为 HTML 实体，防止破坏 HTML 结构，浏览器提交时会自动还原。

### 15.5 金额精度

内部使用 `Decimal` 保持精度，支付宝 API 要求字符串格式（最多两位小数）：

```python
str(order.amount)  # Decimal("99.99") → "99.99"
```

回调通知中金额以字符串返回，解析时同样转为 `Decimal`：

```python
total_amount = Decimal(raw_response.get("total_amount", "0"))
```

---

## 完整代码示例（网页支付）

```python
from decimal import Decimal
from senweaver_pay import Pay, PaymentChannel, PaymentMethod, PaymentRequest, UnifiedOrder

# 1. 初始化（应用启动时执行一次）
Pay.config({
    "alipay": {
        "default": {
            "app_id": "2016xxxxxxx",
            "app_secret_cert": "/certs/private_key.pem",
            "app_public_cert_path": "/certs/appCertPublicKey.crt",
            "alipay_public_cert_path": "/certs/alipayCertPublicKey.crt",
            "alipay_root_cert_path": "/certs/alipayRootCert.crt",
            "notify_url": "https://example.com/notify/alipay",
            "return_url": "https://example.com/return/alipay",
            "mode": "sandbox",
        }
    }
})

# 2. 创建支付（每次下单）
order = UnifiedOrder(
    out_trade_no="ORDER_20250702_001",
    amount=Decimal("99.99"),
    subject="测试商品",
)
request = PaymentRequest(
    channel=PaymentChannel.ALIPAY,
    method=PaymentMethod.WEB,
    order=order,
)
response = Pay.create(request)

if response.success:
    html_form = response.form_data   # 返回给浏览器，自动跳转支付宝
else:
    print(response.message)          # 失败原因

# 3. 处理异步通知
instance = Pay.alipay()
callback = instance.callback(form_data=notify_params)
if callback.success and callback.status.value == "success":
    # 更新数据库订单状态
    pass
return instance.success()            # 返回 "success"

# 4. 查询订单
from senweaver_pay import QueryRequest
query = QueryRequest(channel=PaymentChannel.ALIPAY, out_trade_no="ORDER_20250702_001")
result = Pay.query(query)
print(result.status, result.paid_amount)

# 5. 退款
from senweaver_pay import RefundRequest
refund = RefundRequest(
    channel=PaymentChannel.ALIPAY,
    out_trade_no="ORDER_20250702_001",
    out_refund_no="REFUND_001",
    refund_amount=Decimal("99.99"),
)
refund_result = Pay.refund(refund)
```
