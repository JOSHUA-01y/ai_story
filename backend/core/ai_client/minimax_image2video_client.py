#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MiniMax（海螺 / Hailuo）图生视频执行器。

支持 MiniMax 两代视频生成接口，用 ``api_version``（或 ``protocol``）选择，
默认 ``auto``：按 ``api_url`` 路径和模型名自动判断。

1. ``v2``（MiniMax H3 / H3-Max，当前主力）::

       POST {base}/v2/video_generation                  # 提交，返回 task_id
       GET  {base}/v2/query/video_generation/{task_id}  # 轮询，成功时 task.content.url

   - 请求体是 ``content`` 多模态数组：``text`` 必填；图片走 ``image_url`` 并带
     ``role`` —— 1 张按 ``first_frame``、2 张按 ``first_frame`` + ``last_frame``、
     3 张及以上按 ``reference_image``（官方规定首尾帧与参考图两种模式互斥）。
   - ``resolution`` 只接受 ``480P`` / ``768P`` / ``2K``，``duration`` 必须显式传
     且为 4–15 秒（``MiniMax-H3-Max`` 是 5–15 秒且不支持 2K）。
   - 图生视频（带首/尾帧）时 ``ratio`` 由输入图决定，官方固定为 ``adaptive``。

2. ``v1``（旧版 ``MiniMax-Hailuo-2.3`` / ``MiniMax-Hailuo-02`` / ``I2V-01``）::

       POST {base}/v1/video_generation                  # 返回 task_id
       GET  {base}/v1/query/video_generation?task_id=…  # 轮询，成功时给 file_id
       GET  {base}/v1/files/retrieve?file_id=…          # 换限时 download_url（1 小时）

   图片字段是 ``first_frame_image``，接受公网 URL 或 ``data:image/…;base64,…``；
   该代接口只支持 6 / 10 秒（1080P 仅 6 秒），分辨率取值 512P/720P/768P/1080P。

设计约定
--------
- 输入图片一律经基类 :meth:`VideoGeneratorClient._resolve_image_base64s` 读成本地
  base64 再以 Data URL 内联提交，因此项目 ``storage/`` 里的图片**不需要公网可访问**。
- 成片统一交给基类 :meth:`VideoGeneratorClient._localize_video_data` 下载到
  ``storage/video/``，避免 MiniMax 的限时链接过期后拿不到文件。
- ``negative_prompt`` / ``fps`` / ``seed``：MiniMax 两代接口都没有对应字段，故**不发送**；
  ``negative_prompt`` 也不会拼进提示词，避免污染模型指令。
- 失败一律走返回体的 ``base_resp.status_code``（1002 限流 / 1008 余额不足 /
  1026 内容审核 …）或任务态 ``status=failed``，并带上可读的中文原因。

接口文档：
- H3 v2 创建: https://platform.minimax.io/docs/api-reference/video-generation-v2-create
- H3 v2 查询: https://platform.minimax.io/docs/api-reference/video-generation-v2-query
- 旧版图生视频: https://platform.minimax.io/docs/api-reference/video-generation-i2v
"""

import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from core.ai_client.image2video_client import VideoGeneratorClient
from core.utils.http_retry import get_with_retry, post_with_retry

logger = logging.getLogger(__name__)

# ===== 协议标识 =====

PROTOCOL_AUTO = 'auto'
PROTOCOL_V2 = 'v2'
PROTOCOL_V1 = 'v1'

_PROTOCOL_ALIASES = {
    'auto': PROTOCOL_AUTO,
    'v2': PROTOCOL_V2,
    'h3': PROTOCOL_V2,
    'minimax-h3': PROTOCOL_V2,
    'video-generation-v2': PROTOCOL_V2,
    'v1': PROTOCOL_V1,
    'legacy': PROTOCOL_V1,
    'hailuo': PROTOCOL_V1,
    'video-generation': PROTOCOL_V1,
}

#: 国内站；国际站为 ``https://api.minimax.io``
DEFAULT_API_BASE = 'https://api.minimaxi.com'

_CREATE_PATH_V2 = '/v2/video_generation'
_QUERY_PATH_V2 = '/v2/query/video_generation'
_CREATE_PATH_V1 = '/v1/video_generation'
_QUERY_PATH_V1 = '/v1/query/video_generation'
_RETRIEVE_PATH_V1 = '/v1/files/retrieve'

#: 允许被 base_url 覆盖的已知后缀（长的放前面，避免 ``/v2`` 先命中）
_KNOWN_SUFFIXES = (
    '/v2/query/video_generation',
    '/v2/video_generation',
    '/v1/query/video_generation',
    '/v1/video_generation',
    '/v1/files/retrieve',
    '/v2',
    '/v1',
)

_STATUS_SUCCESS = frozenset({'success', 'succeeded', 'completed', 'complete', 'finished', 'done'})
_STATUS_FAILED = frozenset({'fail', 'failed', 'failure', 'error', 'cancelled', 'canceled'})

#: ``base_resp.status_code`` → 人话
_BASE_RESP_MESSAGES = {
    1000: 'MiniMax 未知错误',
    1001: 'MiniMax 请求超时',
    1002: 'MiniMax 触发限流（RPM/TPM），请稍后重试',
    1004: 'MiniMax 鉴权失败，请检查 API Key',
    1008: 'MiniMax 账户余额不足，请先充值',
    1013: 'MiniMax 内部服务错误',
    1026: 'MiniMax 提示词或输入素材命中内容审核',
    1027: 'MiniMax 输出内容命中内容审核',
    1039: 'MiniMax 触发 Token 限流',
    2013: 'MiniMax 参数不合法',
    2304: (
        'MiniMax 提示词被身份/肖像策略拒绝（常见于问候语或指向真人身份的表述），'
        '请改成描述画面与运动的提示词'
    ),
}

#: HTTP 状态码 → 人话（v2 的错误是 OpenAI 风格，真实原因在 body 里）
_HTTP_STATUS_HINTS = {
    400: 'MiniMax 参数不合法',
    401: 'MiniMax 鉴权失败，请检查 API Key',
    402: 'MiniMax 账户余额不足，请先充值',
    422: 'MiniMax 输入命中内容审核',
    429: 'MiniMax 触发限流，请稍后重试',
    500: 'MiniMax 服务端错误',
}

#: 官方 ``[指令]`` 运镜语法，仅旧版 Hailuo / Director 模型支持
_CAMERA_COMMAND_KEYWORDS = (
    ('推进', '[Push in]'),
    ('推近', '[Push in]'),
    ('拉远', '[Pull out]'),
    ('拉出', '[Pull out]'),
    ('左移', '[Truck left]'),
    ('右移', '[Truck right]'),
    ('左摇', '[Pan left]'),
    ('右摇', '[Pan right]'),
    ('上摇', '[Tilt up]'),
    ('下摇', '[Tilt down]'),
    ('上移', '[Pedestal up]'),
    ('下移', '[Pedestal down]'),
    ('放大', '[Zoom in]'),
    ('缩小', '[Zoom out]'),
    ('晃动', '[Shake]'),
    ('抖动', '[Shake]'),
    ('跟随', '[Tracking shot]'),
    ('追踪', '[Tracking shot]'),
    ('固定', '[Static shot]'),
    ('静止', '[Static shot]'),
)

#: v2 要求 ``content`` 里必须有非空 text，提示词为空时兜底
_DEFAULT_PROMPT_FALLBACK = '保持主体自然运动，画面稳定连贯'


class MinimaxImage2VideoClient(VideoGeneratorClient):
    """对接 MiniMax 视频生成 API（H3 v2 / 旧版 v1）的图生视频执行器。"""

    #: v2 允许的分辨率
    _V2_RESOLUTIONS = ('480P', '768P', '2K')
    #: v1 允许的分辨率
    _V1_RESOLUTIONS = ('512P', '720P', '768P', '1080P')

    def __init__(
        self,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        api_token: Optional[str] = None,
        model_name: Optional[str] = None,
        model: Optional[str] = None,
        protocol: Optional[str] = None,
        api_version: Optional[str] = None,
        prompt_optimizer: Optional[bool] = None,
        fast_pretreatment: Optional[bool] = None,
        prompt_expansion_mode: Optional[str] = None,
        callback_url: Optional[str] = None,
        **kwargs: Any,
    ):
        """初始化 MiniMax 视频客户端。

        Args:
            api_url: 可用站点根地址（``https://api.minimaxi.com``）或具体端点
                （``/v2/video_generation``），内部会归一化后再拼路径。
            protocol: ``v2`` / ``v1`` / ``auto``（默认）。``api_version`` 为同义参数。
            prompt_optimizer: 旧版接口的提示词自动优化开关，默认由服务端决定（true）。
            fast_pretreatment: 旧版接口的加速预处理开关，仅 Hailuo 2.3 / 02 有效。
            prompt_expansion_mode: H3-Max 的提示词扩展模式（disabled/balanced/quality）。
            callback_url: 任务状态回调地址，配置后 MiniMax 会主动推送状态。
        """
        super().__init__(
            api_url=api_url or DEFAULT_API_BASE,
            api_token=api_token,
            model=model,
            api_key=api_key,
            model_name=model_name,
            **kwargs,
        )
        self.protocol = self._normalize_protocol(
            protocol or api_version or kwargs.get('video_protocol')
        )
        self.prompt_optimizer = (
            prompt_optimizer if prompt_optimizer is not None else kwargs.get('prompt_optimizer')
        )
        self.fast_pretreatment = (
            fast_pretreatment if fast_pretreatment is not None else kwargs.get('fast_pretreatment')
        )
        self.prompt_expansion_mode = (
            prompt_expansion_mode or kwargs.get('prompt_expansion_mode')
        )
        self.callback_url = callback_url or kwargs.get('callback_url')

    # ===== 地址组装 =====

    @staticmethod
    def _normalize_protocol(value: Any) -> str:
        """把协议别名归一到标准标识。"""
        token = str(value or '').strip().lower().replace('_', '-')
        return _PROTOCOL_ALIASES.get(token, PROTOCOL_AUTO)

    def _api_base(self) -> str:
        """去掉已知端点后缀，得到 ``scheme://host[/prefix]`` 形式的基础地址。"""
        base = (self.base_url or '').strip().rstrip('/')
        if not base:
            return DEFAULT_API_BASE
        for suffix in _KNOWN_SUFFIXES:
            if base.endswith(suffix):
                return base[: -len(suffix)]
        return base

    def _protocol(self) -> str:
        """解析最终使用的接口版本。"""
        if self.protocol != PROTOCOL_AUTO:
            return self.protocol

        path = urlparse(self.base_url or '').path.lower()
        if path.startswith('/v2') or '/v2/' in path:
            return PROTOCOL_V2
        if path.startswith('/v1') or '/v1/' in path:
            return PROTOCOL_V1

        model = (self.model or '').strip().lower()
        if 'h3' in model:
            return PROTOCOL_V2
        legacy_prefixes = ('i2v', 't2v', 's2v')
        if 'hailuo' in model or model.startswith(legacy_prefixes):
            return PROTOCOL_V1
        return PROTOCOL_V2

    def _is_h3_max(self) -> bool:
        """当前模型是否为 MiniMax-H3-Max（不支持 2K）。"""
        return 'h3-max' in (self.model or '').strip().lower()

    def _build_create_video_url(self) -> str:
        """构建任务创建地址。"""
        path = _CREATE_PATH_V2 if self._protocol() == PROTOCOL_V2 else _CREATE_PATH_V1
        return f'{self._api_base()}{path}'

    def _build_task_status_url(self, task_id: str) -> str:
        """构建任务状态查询地址。"""
        if self._protocol() == PROTOCOL_V2:
            return f'{self._api_base()}{_QUERY_PATH_V2}/{task_id}'
        return f'{self._api_base()}{_QUERY_PATH_V1}?task_id={task_id}'

    # ===== 响应校验 =====

    @staticmethod
    def _check_base_resp(payload: Any) -> None:
        """校验 MiniMax 统一响应头 ``base_resp``，非 0 一律抛可读异常。"""
        if not isinstance(payload, dict):
            return

        base_resp = payload.get('base_resp')
        if not isinstance(base_resp, dict):
            return

        status_code = base_resp.get('status_code')
        if status_code in (None, 0, '0'):
            return

        try:
            code = int(status_code)
        except (TypeError, ValueError):
            code = -1

        message = _BASE_RESP_MESSAGES.get(code) or f'MiniMax 接口报错 (status_code={status_code})'
        status_msg = str(base_resp.get('status_msg') or '').strip()
        if status_msg and status_msg.lower() != 'success':
            message = f'{message}: {status_msg}'
        raise RuntimeError(message)

    @staticmethod
    def _raise_for_status(response: Any) -> None:
        """HTTP 4xx/5xx 时把 MiniMax body 里的原因一起抛出来。

        v2 的错误体是 OpenAI 风格 ``{"error": {"message": "… (1008)"}}``，
        直接 ``raise_for_status`` 只会得到 "402 Client Error"，看不出是余额不足。
        """
        status_code = getattr(response, 'status_code', 200) or 200
        if int(status_code) < 400:
            return

        detail = ''
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001 - 错误响应不是 JSON 时退回状态码
            payload = None

        if isinstance(payload, dict):
            error = payload.get('error')
            if isinstance(error, dict):
                detail = str(error.get('message') or '').strip()
            elif error:
                detail = str(error).strip()
            if not detail:
                base_resp = payload.get('base_resp')
                if isinstance(base_resp, dict):
                    detail = str(base_resp.get('status_msg') or '').strip()

        hint = _HTTP_STATUS_HINTS.get(int(status_code))
        parts = [hint or f'MiniMax 接口返回 {status_code}', detail]
        raise RuntimeError(' | '.join(part for part in parts if part))

    @staticmethod
    def _task_payload(task_info: Dict[str, Any]) -> Dict[str, Any]:
        """取出 ``task`` 包裹层（v2 查询结果把任务信息放在 ``task`` 下）。"""
        if not isinstance(task_info, dict):
            return {}
        task = task_info.get('task')
        return task if isinstance(task, dict) else task_info

    def _extract_error_message(self, task_info: Dict[str, Any]) -> str:
        """从失败任务里提取可读错误。"""
        task = self._task_payload(task_info)
        error = task.get('error') or (task_info or {}).get('error')

        if isinstance(error, dict):
            code = error.get('code')
            detail = str(error.get('message') or '').strip()
            try:
                code_int = int(code)
            except (TypeError, ValueError):
                code_int = None
            hint = _BASE_RESP_MESSAGES.get(code_int or -1)
            parts = [item for item in (hint, detail) if item]
            return ' | '.join(parts) or f'MiniMax 任务失败 (code={code})'

        if error:
            return str(error)

        base_resp = (task_info or {}).get('base_resp') or {}
        return str(base_resp.get('status_msg') or '未知错误')

    @staticmethod
    def _resolve_task_status(task_info: Dict[str, Any]) -> str:
        """把 MiniMax 各代任务状态归一到 ``success`` / ``failed`` / ``pending``。"""
        task = MinimaxImage2VideoClient._task_payload(task_info)
        raw = str(task.get('status') or (task_info or {}).get('status') or '').strip().lower()
        if raw in _STATUS_SUCCESS:
            return 'success'
        if raw in _STATUS_FAILED:
            return 'failed'
        return 'pending'

    # ===== 参数归一 =====

    def _resolve_resolution(self, resolution: Optional[str]) -> str:
        """归一分辨率到当前接口允许的档位，越界时回退并告警。"""
        text = str(resolution or '').strip().upper()

        if self._protocol() == PROTOCOL_V2:
            mapping = {
                '': '768P',
                '480P': '480P',
                '512P': '768P',
                '720P': '768P',
                '768P': '768P',
                '1080P': '2K',
                '2K': '2K',
                '4K': '2K',
            }
            resolved = mapping.get(text, text)
            if resolved == '2K' and self._is_h3_max():
                logger.warning('MiniMax-H3-Max 不支持 2K，已回退到 768P')
                resolved = '768P'
            if resolved not in self._V2_RESOLUTIONS:
                logger.warning('MiniMax H3 不接受分辨率 %s，已回退到 768P', text or '(空)')
                resolved = '768P'
            return resolved

        mapping = {
            '': '768P',
            '512P': '512P',
            '720P': '720P',
            '768P': '768P',
            '1080P': '1080P',
            '2K': '1080P',
            '4K': '1080P',
        }
        resolved = mapping.get(text, text)
        if resolved not in self._V1_RESOLUTIONS:
            logger.warning('MiniMax 旧版接口不接受分辨率 %s，已回退到 768P', text or '(空)')
            resolved = '768P'
        return resolved

    def _resolve_duration(self, duration_seconds: Any, resolution: str) -> int:
        """归一时长到当前接口允许的取值。"""
        try:
            duration = int(round(float(duration_seconds)))
        except (TypeError, ValueError):
            duration = 6

        if self._protocol() == PROTOCOL_V2:
            lower, upper = (5, 15) if self._is_h3_max() else (4, 15)
            if duration < lower or duration > upper:
                logger.warning(
                    'MiniMax H3 时长 %s 秒越界，已收敛到 [%s, %s]',
                    duration,
                    lower,
                    upper,
                )
            return max(lower, min(upper, duration))

        # 旧版只有 6 / 10 秒两档，1080P 仅支持 6 秒
        if resolution == '1080P':
            if duration != 6:
                logger.warning('MiniMax 旧版接口 1080P 仅支持 6 秒，已收敛到 6 秒')
            return 6
        resolved = 10 if duration >= 8 else 6
        if resolved != duration:
            logger.info('MiniMax 旧版接口时长只支持 6/10 秒，%s 秒已映射到 %s 秒', duration, resolved)
        return resolved

    def _resolve_ratio(
        self,
        aspect_ratio: Optional[str],
        has_frame_images: bool,
        has_reference_images: bool,
    ) -> Optional[str]:
        """归一画面比例（旧版接口没有该字段，返回 None）。"""
        if self._protocol() != PROTOCOL_V2:
            return None

        requested = str(aspect_ratio or '').strip()
        if has_frame_images:
            # 官方：带首/尾帧时比例由输入图决定，传具体值也会被忽略
            return 'adaptive'
        if has_reference_images:
            return requested or 'adaptive'
        # 文生视频必须有具体比例，不能是 adaptive
        if not requested or requested == 'adaptive':
            return '16:9'
        return requested

    def _resolve_prompt_optimizer(self, kwargs: Dict[str, Any]) -> bool:
        """旧版接口的 ``prompt_optimizer``，默认交给服务端（true）。"""
        value = kwargs.get('prompt_optimizer', self.prompt_optimizer)
        return True if value is None else bool(value)

    @staticmethod
    def _supports_camera_commands(model: str) -> bool:
        """官方 ``[指令]`` 运镜语法只对 Hailuo / Director 系旧模型生效。"""
        normalized = (model or '').strip().lower()
        return 'hailuo' in normalized or 'director' in normalized

    def _extract_camera_commands(self, description: str, model: str) -> List[str]:
        """把中文运镜描述映射成官方 ``[指令]``（最多 3 条）。"""
        if not self._supports_camera_commands(model):
            return []

        commands: List[str] = []
        for keyword, command in _CAMERA_COMMAND_KEYWORDS:
            if keyword in description and command not in commands:
                commands.append(command)
            if len(commands) >= 3:
                break
        return commands

    def _build_prompt(
        self,
        prompt: str,
        camera_movement_description: Optional[str],
        model: str,
    ) -> str:
        """拼接最终提示词（不拼负面提示词，MiniMax 没有该字段）。"""
        parts: List[str] = []
        if prompt and prompt.strip():
            parts.append(prompt.strip())

        description = (camera_movement_description or '').strip()
        if description:
            commands = self._extract_camera_commands(description, model)
            parts.append(' '.join(commands) if commands else description)

        final_prompt = '\n\n'.join(part for part in parts if part)
        if not final_prompt:
            logger.warning('MiniMax 图生视频未收到有效提示词，使用兜底提示词')
            final_prompt = _DEFAULT_PROMPT_FALLBACK
        return final_prompt

    # ===== 请求体 =====

    @staticmethod
    def _build_v2_content(
        prompt_text: str,
        resolved_image_base64s: List[str],
        image_mime_type: str,
    ) -> List[Dict[str, Any]]:
        """构建 v2 的 ``content`` 多模态数组。"""
        content: List[Dict[str, Any]] = [{'type': 'text', 'text': prompt_text}]
        image_count = len(resolved_image_base64s)

        for index, image_base64 in enumerate(resolved_image_base64s):
            if image_count == 1:
                role = 'first_frame'
            elif image_count == 2:
                role = 'first_frame' if index == 0 else 'last_frame'
            else:
                role = 'reference_image'
            content.append(
                {
                    'type': 'image_url',
                    'image_url': {'url': f'data:{image_mime_type};base64,{image_base64}'},
                    'role': role,
                }
            )

        return content

    def _build_payload(self, prompt: str, **kwargs: Any) -> Dict[str, Any]:
        """构建 MiniMax 视频生成请求体。"""
        timeout = int(kwargs.get('timeout', self.timeout))
        model = kwargs.get('model') or self.model
        image_uri = kwargs.get('image_uri')
        image_uris = kwargs.get('image_uris')
        image_base64 = kwargs.get('image_base64')
        image_base64s = kwargs.get('image_base64s')
        image_mime_type = kwargs.get('image_mime_type') or 'image/jpeg'
        camera_movement_description = kwargs.get('camera_movement_description')

        normalized_image_uris = list(image_uris or [])
        if image_uri and image_uri not in normalized_image_uris:
            normalized_image_uris.insert(0, image_uri)

        normalized_image_base64s = list(image_base64s or [])
        if image_base64 and image_base64 not in normalized_image_base64s:
            normalized_image_base64s.insert(0, image_base64)

        resolved_image_base64s = self._resolve_image_base64s(
            normalized_image_uris,
            normalized_image_base64s,
            timeout,
        )

        prompt_text = self._build_prompt(prompt, camera_movement_description, model)
        resolution = self._resolve_resolution(kwargs.get('resolution'))
        duration = self._resolve_duration(
            kwargs.get('duration_seconds', kwargs.get('duration', 6)),
            resolution,
        )
        callback_url = kwargs.get('callback_url') or self.callback_url

        if self._protocol() == PROTOCOL_V2:
            image_count = len(resolved_image_base64s)
            payload: Dict[str, Any] = {
                'model': model,
                'content': self._build_v2_content(
                    prompt_text,
                    resolved_image_base64s,
                    image_mime_type,
                ),
                'resolution': resolution,
                'duration': duration,
                'ratio': self._resolve_ratio(
                    kwargs.get('aspect_ratio'),
                    has_frame_images=0 < image_count <= 2,
                    has_reference_images=image_count > 2,
                ),
            }

            prompt_expansion_mode = (
                kwargs.get('prompt_expansion_mode') or self.prompt_expansion_mode
            )
            if prompt_expansion_mode:
                payload['extra'] = {'prompt_expansion_mode': str(prompt_expansion_mode)}
            if callback_url:
                payload['callback_url'] = callback_url
            return payload

        payload = {
            'model': model,
            'prompt': prompt_text,
            'prompt_optimizer': self._resolve_prompt_optimizer(kwargs),
            'duration': duration,
            'resolution': resolution,
        }
        if resolved_image_base64s:
            payload['first_frame_image'] = (
                f'data:{image_mime_type};base64,{resolved_image_base64s[0]}'
            )

        fast_pretreatment = kwargs.get('fast_pretreatment', self.fast_pretreatment)
        if fast_pretreatment is not None:
            payload['fast_pretreatment'] = bool(fast_pretreatment)
        if callback_url:
            payload['callback_url'] = callback_url
        return payload

    # ===== 任务流转 =====

    def _create_task(self, payload: Dict[str, Any], timeout: int) -> str:
        """提交视频任务并返回 task_id。"""
        response = post_with_retry(
            self._build_create_video_url(),
            json=payload,
            headers=self.headers,
            timeout=timeout,
        )
        self._raise_for_status(response)
        result = response.json()
        self._check_base_resp(result)

        task_id = result.get('task_id') or result.get('id')
        if not task_id and isinstance(result.get('data'), dict):
            data = result['data']
            task_id = data.get('task_id') or data.get('id')
        if not task_id:
            raise ValueError(f'MiniMax 响应缺少 task_id: {result}')
        return str(task_id)

    def _query_task(self, task_id: str, timeout: int) -> Dict[str, Any]:
        """查询单次任务状态。"""
        response = get_with_retry(
            self._build_task_status_url(task_id),
            headers=self.headers,
            timeout=timeout,
        )
        self._raise_for_status(response)
        result = response.json()
        self._check_base_resp(result)
        return result

    def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """查询视频生成任务状态（兼容基类签名）。"""
        return self._query_task(task_id, timeout=int(self.timeout))

    def _retrieve_download_url(self, file_id: Any, timeout: int) -> str:
        """用 file_id 换取限时下载地址（旧版接口出片后必须这一步）。"""
        response = get_with_retry(
            f'{self._api_base()}{_RETRIEVE_PATH_V1}',
            headers=self.headers,
            params={'file_id': file_id},
            timeout=timeout,
        )
        self._raise_for_status(response)
        result = response.json()
        self._check_base_resp(result)

        file_info = result.get('file') if isinstance(result.get('file'), dict) else {}
        download_url = str(file_info.get('download_url') or '').strip()
        if download_url and not download_url.startswith(('http://', 'https://')):
            download_url = f'https://{download_url}'
        return download_url

    def _extract_video_items(self, task_info: Dict[str, Any], timeout: int) -> List[dict]:
        """从任务结果中提取视频条目（含时长/分辨率等元信息）。"""
        task = self._task_payload(task_info)
        items: List[dict] = []

        content = task.get('content')
        if isinstance(content, dict) and (content.get('url') or content.get('video_url')):
            items.append({'url': content.get('url') or content.get('video_url')})
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_url = part.get('url') or part.get('video_url')
                if part_url:
                    items.append({'url': part_url})

        if not items:
            direct_url = task.get('video_url') or task.get('url') or task_info.get('video_url')
            if direct_url:
                items.append({'url': direct_url})

        if not items:
            file_id = task.get('file_id') or task_info.get('file_id')
            if file_id:
                download_url = self._retrieve_download_url(file_id, timeout)
                if download_url:
                    items.append({'url': download_url})

        for item in items:
            item.setdefault('duration', task.get('duration') or 0)
            item.setdefault('resolution', task.get('resolution') or '')
            item.setdefault('ratio', task.get('ratio') or '')

        return items

    def _resolve_max_wait_time(self, max_wait_time: Any) -> int:
        """给异步队列留足时间：H3 单条成片常需数分钟，低于下限会被误杀。"""
        try:
            requested = int(max_wait_time)
        except (TypeError, ValueError):
            requested = 0

        floor = 900 if self._protocol() == PROTOCOL_V2 else 600
        if requested < floor:
            logger.info(
                'MiniMax 视频任务等待上限由 %s 秒提升到 %s 秒（排队+生成通常需要数分钟）',
                requested,
                floor,
            )
            return floor
        return requested

    def _wait_task(
        self,
        task_id: str,
        poll_interval: int = 5,
        max_wait_time: int = 1800,
        timeout: int = 60,
        max_poll_attempts: int = 240,
        max_consecutive_errors: int = 5,
    ) -> Dict[str, Any]:
        """轮询等待任务完成。"""
        start_time = time.time()
        poll_attempts = 0
        consecutive_errors = 0

        while True:
            if time.time() - start_time > max_wait_time:
                raise TimeoutError(f'MiniMax 任务超时: 超过 {max_wait_time} 秒 (task_id={task_id})')

            if poll_attempts >= max_poll_attempts:
                raise TimeoutError(
                    f'MiniMax 任务熔断: 轮询次数超过 {max_poll_attempts} 次 (task_id={task_id})'
                )

            try:
                task_info = self._query_task(task_id, timeout=timeout)
                consecutive_errors = 0
            except Exception as exc:
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    raise RuntimeError(
                        f'MiniMax 任务状态查询连续异常 {max_consecutive_errors} 次, '
                        f'最后错误: {exc}'
                    )
                logger.warning(
                    'MiniMax 任务状态查询异常: task_id=%s attempt=%s error=%s',
                    task_id,
                    consecutive_errors,
                    exc,
                )
                time.sleep(poll_interval)
                continue

            poll_attempts += 1
            status = self._resolve_task_status(task_info)

            if status == 'success':
                return task_info
            if status == 'failed':
                raise RuntimeError(f'MiniMax 任务失败: {self._extract_error_message(task_info)}')

            time.sleep(poll_interval)

    # ===== 对外入口 =====

    def create_video_task(self, prompt: str, model: Optional[str] = None, **kwargs: Any) -> str:
        """创建视频生成任务，返回 task_id。"""
        kwargs.setdefault('model', model or self.model)
        timeout = int(kwargs.get('timeout', self.timeout))
        return self._create_task(self._build_payload(prompt, **kwargs), timeout=timeout)

    def _generate_video(
        self,
        prompt: str,
        poll_interval: int = 5,
        max_wait_time: int = 1800,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """同步生成视频：提交任务 → 轮询 → 下载到本地 storage。"""
        start_time = time.time()
        timeout = int(kwargs.get('timeout', self.timeout))
        model = kwargs.get('model') or self.model
        effective_max_wait = self._resolve_max_wait_time(max_wait_time)
        task_id = ''

        try:
            payload = self._build_payload(prompt, **kwargs)
            task_id = self._create_task(payload, timeout=timeout)
            task_info = self._wait_task(
                task_id,
                poll_interval=poll_interval,
                max_wait_time=effective_max_wait,
                timeout=timeout,
            )
            video_items = self._extract_video_items(task_info, timeout=timeout)
            task = self._task_payload(task_info)

            metadata = {
                'latency_ms': int((time.time() - start_time) * 1000),
                'model': model,
                'request_url': self._build_create_video_url(),
                'task_id': task_id,
                'resolution': task.get('resolution') or payload.get('resolution'),
                'duration': task.get('duration') or payload.get('duration'),
                'usage': task.get('usage') or {},
            }

            if not video_items:
                return {
                    'success': False,
                    'data': [],
                    'metadata': metadata,
                    'error': 'MiniMax 任务成功但响应里没有视频地址',
                }

            return {
                'success': True,
                'data': self._localize_video_data(video_items, timeout),
                'metadata': metadata,
            }

        except Exception as exc:
            logger.error('MiniMax 图生视频失败: %s', exc, exc_info=True)
            return {
                'success': False,
                'data': [],
                'metadata': {
                    'latency_ms': int((time.time() - start_time) * 1000),
                    'model': model,
                    'request_url': self._build_create_video_url(),
                    'task_id': task_id,
                },
                'error': str(exc),
            }
