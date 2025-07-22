#!/usr/bin/env python
"""
使用FastAPI和统一支付网关的示例应用
"""

import datetime
import logging
import os
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Optional

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# 添加项目根目录到Python路径
sys.path.append(str(Path(__file__).parent.parent.parent))

from senweaver_pay import (
    MODE_NORMAL,
    MODE_SANDBOX,
    CancelRequest,
    Pay,
    PayException,
    PaymentChannel,
    PaymentMethod,
    PaymentRequest,
    QueryRequest,
    RefundRequest,
    UnifiedOrder,
)

# 加载环境变量
load_dotenv()

app = FastAPI(title="SenWeaver Pay Demo")

# 配置模板
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

# 配置日志

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# 订单缓存（实际项目中应该使用数据库）
orders_cache = {}

# 删除了不再需要的时间戳辅助函数，统一接口会自动处理

# 获取当前文件所在目录
current_dir = os.path.dirname(os.path.abspath(__file__))

# 初始化支付配置
pay_config = {
    "alipay": {
        "default": {
            "app_id": os.getenv("ALIPAY_APP_ID", "2016********"),
            "app_secret_cert": os.getenv("ALIPAY_APP_SECRET_CERT", "").replace("\\n", "\n"),
            "app_public_cert_path": os.getenv(
                "ALIPAY_APP_PUBLIC_CERT_PATH", os.path.join(current_dir, "certs/alipay/appCertPublicKey.crt")
            ),
            "alipay_public_cert_path": os.getenv(
                "ALIPAY_PUBLIC_CERT_PATH", os.path.join(current_dir, "certs/alipay/alipayCertPublicKey.crt")
            ),
            "alipay_root_cert_path": os.getenv(
                "ALIPAY_ROOT_CERT_PATH", os.path.join(current_dir, "certs/alipay/alipayRootCert.crt")
            ),
            "notify_url": os.getenv("ALIPAY_NOTIFY_URL", "http://localhost:8000/notify/alipay"),
            "return_url": os.getenv("ALIPAY_RETURN_URL", "http://localhost:8000/return/alipay"),
            "mode":  os.getenv("ALIPAY_MODE", MODE_SANDBOX),
        }
    },
    "wechat": {
        "default": {
            "mch_id": os.getenv("WECHAT_MCH_ID", "1600314069"),
            "mch_secret_key": os.getenv("WECHAT_MCH_SECRET_KEY", "e7368d422cfea4b70e91165e522c8fhr"),
            "mch_secret_cert": os.getenv("WECHAT_MCH_SECRET_CERT", "").replace("\\n", "\n"),
            "mch_public_cert_path": os.getenv("WECHAT_MCH_PUBLIC_CERT_PATH", "/path/to/cert.pem"),
            "mp_app_id": os.getenv("WECHAT_MP_APP_ID", "wx55955316af4ef13"),
            "mini_app_id": os.getenv("WECHAT_MINI_APP_ID", "wx55955316af4ef14"),
            "app_id": os.getenv("WECHAT_APP_ID", "wx55955316af4ef15"),
            "notify_url": os.getenv("WECHAT_NOTIFY_URL", "http://localhost:8000/notify/wechat"),
            "serial_number": os.getenv("WECHAT_SERIAL_NUMBER", "12345678"),
            "wechat_public_cert_key": os.getenv("WECHAT_PUBLIC_CERT_KEY", "PUB_KEY_ID_0000xx"),
            "wechat_public_cert_path": os.getenv("WECHAT_PUBLIC_CERT_PATH", "/path/to/private.pem"),
            "mode": MODE_NORMAL,
        }
    },
    "unipay": {
        "default": {
            "mer_id": os.getenv("UNIPAY_MER_ID", "123456789012345"),
            "mer_private_key_path": os.getenv("UNIPAY_PRIVATE_KEY_PATH", "/path/to/private_key.pem"),
            "mer_public_cert_path": os.getenv("UNIPAY_PUBLIC_CERT_PATH", "/path/to/public_cert.pem"),
            "unipay_public_cert_path": os.getenv("UNIPAY_PUBLIC_CERT_PATH", "/path/to/unipay_cert.pem"),
            "notify_url": os.getenv("UNIPAY_NOTIFY_URL", "http://localhost:8000/notify/unipay"),
            "front_url": os.getenv("UNIPAY_FRONT_URL", "http://localhost:8000/return/unipay"),
            "mode": MODE_SANDBOX,
        }
    },
    "douyin": {
        "default": {
            "app_id": os.getenv("DOUYIN_APP_ID", "800000000001"),
            "app_secret": os.getenv("DOUYIN_APP_SECRET", "1234567890abcdef1234567890abcdef"),
            "token": os.getenv("DOUYIN_TOKEN", "douyintoken12345"),
            "salt": os.getenv("DOUYIN_SALT", "douyinsalt12345"),
            "notify_url": os.getenv("DOUYIN_NOTIFY_URL", "http://localhost:8000/notify/douyin"),
            "mode": MODE_SANDBOX,
        }
    },
}

# 初始化统一支付配置
Pay.config(pay_config)


# 定义支付请求模型
class CreatePaymentRequest(BaseModel):
    out_trade_no: str
    amount: float
    subject: str
    body: str
    channel: str  # alipay, wechat, douyin, unipay
    method: str  # web, h5, app, scan, mini, mp, pos
    openid: Optional[str] = None  # 微信支付需要


# 删除复杂的响应处理函数，统一接口会自动处理


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """首页，显示支付表单"""
    # 生成随机订单号
    order_id = f"TEST_ORDER_{int(time.time())}"
    return templates.TemplateResponse(
        "index.html", {"request": request, "title": "SenWeaver Pay Demo", "order_id": order_id}
    )


@app.post("/create_order")
async def create_order(
    out_trade_no: str = Form(...),
    amount: float = Form(...),
    subject: str = Form(...),
    body: str = Form(...),
    channel: str = Form(...),
    method: str = Form(...),
    openid: Optional[str] = Form(None),
):
    """创建订单并返回支付链接或二维码 - 使用统一接口"""
    logger.info(f"创建订单: {out_trade_no}, 支付渠道: {channel}, 支付方式: {method}")

    try:
        # 转换渠道和方法枚举
        try:
            payment_channel = PaymentChannel(channel)
            payment_method = PaymentMethod(method)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"不支持的支付渠道或方式: {str(e)}") from None

        # 创建统一订单
        order = UnifiedOrder(out_trade_no=out_trade_no, amount=Decimal(str(amount)), subject=subject, body=body)

        # 准备额外参数
        extra_params = {}
        if openid:
            extra_params["openid"] = openid

        # 创建支付请求
        payment_request = PaymentRequest(
            channel=payment_channel, method=payment_method, order=order, extra_params=extra_params
        )

        # 调用统一支付接口
        response = Pay.create(payment_request)

        if response.success:
            # 保存订单到缓存
            gateway_names = {"alipay": "支付宝", "wechat": "微信支付", "unipay": "银联支付", "douyin": "抖音支付"}

            order_cache_data = {
                "order_id": out_trade_no,
                "gateway": channel,
                "gateway_name": gateway_names.get(channel, channel),
                "amount": amount,
                "status": "pending",
                "status_name": "待支付",
                "create_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "subject": subject,
                "body": body,
                "payment_method": method,
            }

            orders_cache[out_trade_no] = order_cache_data
            logger.info(f"订单已保存到缓存: {out_trade_no}")

            # 根据支付方式返回不同的响应
            if response.pay_url:
                # 网站支付，返回重定向URL
                return {"type": "redirect", "url": response.pay_url}
            elif response.qr_code:
                # 扫码支付，返回二维码
                return {"type": "qrcode", "code_url": response.qr_code}
            elif response.form_data:
                # 表单支付，返回HTML表单
                return HTMLResponse(content=response.form_data, status_code=200)
            elif response.app_params:
                # APP/小程序支付，返回支付参数
                return {"type": "app_params", "data": response.app_params}
            else:
                return {"type": "success", "message": "支付创建成功"}
        else:
            raise HTTPException(status_code=400, detail=response.message)

    except PayException as e:
        logger.error(f"支付异常: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e)) from None
    except Exception as e:
        logger.error(f"系统异常: {str(e)}")
        raise HTTPException(status_code=500, detail="系统异常，请稍后重试") from None


@app.get("/return/{channel}")
async def payment_return(channel: str, request: Request):
    """处理支付同步通知 - 使用统一接口"""
    try:
        # 转换渠道枚举
        try:
            payment_channel = PaymentChannel(channel)
        except ValueError:
            return HTMLResponse("不支持的支付渠道", status_code=400)

        # 获取渠道实例
        instance = Pay.get_channel(payment_channel.value)

        try:
            # 获取所有可能的数据源
            query_params = dict(request.query_params)
            headers = dict(request.headers)

            # 统一传入所有数据，由具体渠道决定使用哪些参数
            callback_response = instance.callback(
                headers=headers,
                raw_body=None,  # 同步回调通常没有请求体
                form_data=None,  # 同步回调通常没有表单数据
                query_data=query_params,  # 同步回调主要使用查询参数
            )

            # 渠道已经处理好了数据提取，直接使用标准化结果
            if callback_response.out_trade_no and callback_response.out_trade_no in orders_cache:
                orders_cache[callback_response.out_trade_no].update(
                    {
                        "status": "success" if callback_response.success else "failed",
                        "status_name": "支付成功" if callback_response.success else "支付失败",
                        "trade_no": callback_response.trade_no,
                        "pay_time": callback_response.pay_time or datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )

            # 根据支付结果重定向
            if callback_response.success:
                return RedirectResponse(url="/success")
            else:
                return RedirectResponse(url="/error")

        except Exception as e:
            logger.error(f"同步回调处理失败: {str(e)}")
            return RedirectResponse(url="/error")

    except Exception as e:
        logger.error(f"处理同步通知异常: {str(e)}")
        return RedirectResponse(url="/error")


@app.post("/notify/{channel}")
async def payment_notify(channel: str, request: Request):
    """处理支付异步通知 - 使用统一接口"""
    # 转换渠道枚举和获取渠道实例（只执行一次）
    try:
        payment_channel = PaymentChannel(channel)
        instance = Pay.get_channel(payment_channel.value)
    except ValueError:
        return {"return_code": "FAIL", "return_msg": "不支持的支付渠道"}
    except Exception as e:
        logger.error(f"获取支付渠道实例失败: {str(e)}")
        return {"return_code": "FAIL", "return_msg": "获取支付渠道失败"}

    try:
        # 获取所有可能的数据源
        headers = dict(request.headers)
        raw_body = await request.body()
        form_data = await request.form()
        form_dict = dict(form_data)

        # 统一传入所有数据，由具体渠道决定使用哪些参数
        callback_response = instance.callback(
            headers=headers,
            raw_body=raw_body.decode("utf-8") if isinstance(raw_body, bytes) else raw_body,
            form_data=form_dict,
            query_data=None,  # 异步通知通常不使用查询参数
        )

        # 渠道已经处理好了数据提取，直接使用标准化结果
        if callback_response.out_trade_no and callback_response.out_trade_no in orders_cache:
            orders_cache[callback_response.out_trade_no].update(
                {
                    "status": "success" if callback_response.success else "failed",
                    "status_name": "支付成功" if callback_response.success else "支付失败",
                    "trade_no": callback_response.trade_no,
                    "pay_time": callback_response.pay_time or datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            )

        logger.info(f"支付回调处理成功: {callback_response.out_trade_no}, 支付状态: {callback_response.status}")

        # 返回渠道特定的成功响应
        return instance.success()

    except Exception as e:
        logger.error(f"处理异步通知异常: {str(e)}")
        # 返回渠道特定的失败响应（重用已获取的instance）
        return instance.failure({"message": str(e)})


@app.get("/query/{channel}/{order_id}")
async def query_order(channel: str, order_id: str):
    """查询订单 - 使用统一接口"""
    try:
        # 转换渠道枚举
        try:
            payment_channel = PaymentChannel(channel)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"不支持的支付渠道: {channel}") from None

        # 创建查询请求
        query_request = QueryRequest(channel=payment_channel, out_trade_no=order_id)

        # 查询订单
        response = Pay.query(query_request)

        if response.success:
            return {
                "success": True,
                "data": {
                    "out_trade_no": response.out_trade_no,
                    "trade_no": response.trade_no,
                    "status": response.status.value if response.status else None,  # 兼容字段
                    "total_amount": float(response.total_amount) if response.total_amount else None,
                    "paid_amount": float(response.paid_amount) if response.paid_amount else None,
                    "pay_time": response.pay_time,
                    "buyer_id": getattr(response, "buyer_id", None),
                    "buyer_logon_id": getattr(response, "buyer_logon_id", None),
                },
            }
        else:
            return {"success": False, "message": response.message}

    except Exception as e:
        logger.error(f"查询订单异常: {str(e)}")
        raise HTTPException(status_code=500, detail="查询订单失败") from None


@app.get("/cancel/{channel}/{order_id}")
async def cancel_order(channel: str, order_id: str):
    """取消订单 - 使用统一接口"""
    try:
        # 转换渠道枚举
        try:
            payment_channel = PaymentChannel(channel)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"不支持的支付渠道: {channel}") from None

        # 创建取消请求
        cancel_request = CancelRequest(channel=payment_channel, out_trade_no=order_id)

        # 取消订单
        response = Pay.cancel(cancel_request)

        if response.success:
            # 更新缓存中的订单状态
            if order_id in orders_cache:
                orders_cache[order_id]["status"] = "cancelled"
                orders_cache[order_id]["status_name"] = "已取消"

            return {"success": True, "message": "订单已取消"}
        else:
            return {"success": False, "message": response.message}

    except Exception as e:
        logger.error(f"取消订单异常: {str(e)}")
        raise HTTPException(status_code=500, detail="取消订单失败") from None


@app.get("/close/{channel}/{order_id}")
async def close_order(channel: str, order_id: str):
    """关闭订单 - 使用统一接口"""
    try:
        # 转换渠道枚举
        try:
            payment_channel = PaymentChannel(channel)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"不支持的支付渠道: {channel}") from None

        # 获取渠道实例
        instance = Pay.get_channel(payment_channel.value)

        # 创建关闭请求（使用CancelRequest，因为关闭订单和取消订单使用相同的参数结构）
        close_request = CancelRequest(channel=payment_channel, out_trade_no=order_id)

        # 调用关闭订单方法
        response = instance.close(close_request)

        if response.success:
            # 更新缓存中的订单状态
            if order_id in orders_cache:
                orders_cache[order_id]["status"] = "closed"
                orders_cache[order_id]["status_name"] = "已关闭"

            return {"success": True, "message": "订单已关闭"}
        else:
            return {"success": False, "message": response.message}

    except Exception as e:
        logger.error(f"关闭订单异常: {str(e)}")
        raise HTTPException(status_code=500, detail="关闭订单失败") from None


@app.get("/refund/{channel}/{order_id}")
async def refund_order(channel: str, order_id: str, amount: float, refund_id: Optional[str] = None):
    """退款 - 使用统一接口"""
    try:
        # 转换渠道枚举
        try:
            payment_channel = PaymentChannel(channel)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"不支持的支付渠道: {channel}") from None

        # 创建退款请求
        refund_request = RefundRequest(
            channel=payment_channel,
            out_trade_no=order_id,
            out_refund_no=refund_id or f"REFUND_{int(time.time())}",
            refund_amount=Decimal(str(amount)),
            refund_reason="用户申请退款",
        )

        # 申请退款
        response = Pay.refund(refund_request)

        if response.success:
            return {
                "success": True,
                "data": {
                    "refund_id": response.refund_id,
                    "out_refund_no": response.out_refund_no,
                    "refund_amount": float(response.refund_amount) if response.refund_amount else None,
                    "status": response.status.value if response.status else None,
                    "refund_time": response.refund_time,
                },
            }
        else:
            return {"success": False, "message": response.message}

    except Exception as e:
        logger.error(f"退款异常: {str(e)}")
        raise HTTPException(status_code=500, detail="退款失败") from None


@app.get("/orders")
async def get_orders():
    """获取订单列表"""
    try:
        # 从缓存中获取订单数据，按创建时间倒序排列
        orders = list(orders_cache.values())
        orders.sort(key=lambda x: x["create_time"], reverse=True)

        logger.info(f"从缓存获取到 {len(orders)} 个订单")
        return {"success": True, "data": orders}

    except Exception as e:
        logger.error(f"获取订单列表异常: {str(e)}")
        return {"success": False, "message": "获取订单列表失败"}


@app.get("/success", response_class=HTMLResponse)
async def success_page(request: Request):
    """支付成功页面"""
    return templates.TemplateResponse("success.html", {"request": request, "title": "支付成功", "data": {}})


@app.get("/error", response_class=HTMLResponse)
async def error_page(request: Request, message: str = "系统错误"):
    """错误页面"""
    return templates.TemplateResponse("error.html", {"request": request, "title": "系统错误", "error_message": message})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
