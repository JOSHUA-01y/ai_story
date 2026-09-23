"""带连接重试的 HTTP 请求助手。

背景
----
上游网关（实测火山 Ark）会以**约 40% 的概率在 ~19.3 秒处直接掐断连接**，
表现为 `RemoteDisconnected` / `ChunkedEncodingError` / `SSLError`。
这类失败与提示词、模型、鉴权都无关 —— 重试即可成功。

实测数据（2026-09-18，账号 2117348197）：
- 宿主机与容器内表现一致（已排除 Docker/WSL2 网络）
- 短请求同样中招，且失败耗时精确到 19.23~19.28 秒
- 10 次短请求：7 成功 / 3 失败；长流式生成同样约 50% 失败率

因此凡是走外网的模型调用都应带重试。详见 `docs/TODO.md` P1-6。

设计取舍
--------
只重试**连接层异常**（`requests.RequestException`）。
HTTP 4xx/5xx 属于确定性错误（模型未开通、鉴权失败、参数非法等），
**不重试**，直接把 Response 返回给调用方处理。
"""

import logging
import time
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_ATTEMPTS = 3
DEFAULT_BACKOFF = 3.0


def post_with_retry(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    json: Optional[Any] = None,
    data: Optional[Any] = None,
    timeout: int = 60,
    attempts: int = DEFAULT_ATTEMPTS,
    backoff: float = DEFAULT_BACKOFF,
    **kwargs: Any,
) -> requests.Response:
    """发出 POST 请求，仅在连接层异常时重试。

    Args:
        url: 请求地址
        headers: 请求头
        json: JSON 请求体
        data: 原始请求体
        timeout: 超时秒数
        attempts: 最多尝试次数
        backoff: 退避基数，第 n 次重试前等待 backoff * n 秒
        **kwargs: 透传给 requests.post 的其他参数

    Returns:
        requests.Response: 收到响应的那次请求结果（含非 200 状态码）

    Raises:
        requests.RequestException: 所有尝试均因连接层异常失败时，抛最后一次异常
    """
    last_error: Optional[Exception] = None

    for attempt in range(1, attempts + 1):
        try:
            return requests.post(
                url,
                headers=headers,
                json=json,
                data=data,
                timeout=timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= attempts:
                break
            logger.warning(
                '上游连接中断，第 %d/%d 次尝试失败，%.1fs 后重试 (%s): %s',
                attempt, attempts, backoff * attempt, url, exc,
            )
            time.sleep(backoff * attempt)

    logger.error('上游请求重试 %d 次后仍失败 (%s): %s', attempts, url, last_error)
    raise last_error


def get_with_retry(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 60,
    attempts: int = DEFAULT_ATTEMPTS,
    backoff: float = DEFAULT_BACKOFF,
    **kwargs: Any,
) -> requests.Response:
    """发出 GET 请求，仅在连接层异常时重试。

    参数与返回语义同 :func:`post_with_retry`。
    """
    last_error: Optional[Exception] = None

    for attempt in range(1, attempts + 1):
        try:
            return requests.get(
                url,
                headers=headers,
                params=params,
                timeout=timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= attempts:
                break
            logger.warning(
                '上游连接中断，第 %d/%d 次尝试失败，%.1fs 后重试 (%s): %s',
                attempt, attempts, backoff * attempt, url, exc,
            )
            time.sleep(backoff * attempt)

    logger.error('上游请求重试 %d 次后仍失败 (%s): %s', attempts, url, last_error)
    raise last_error
