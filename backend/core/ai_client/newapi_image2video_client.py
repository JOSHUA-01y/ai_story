#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""New API（one-api 系）网关图生视频执行器。

New API 是什么
--------------
New API 是 One API 的增强分支：一个「模型中转站 / 网关」，把可灵、即梦
（Seedance）、阿里万相、Vidu、Sora 等视频模型统一收敛到一套 OpenAI 风格的
HTTP 接口后面。对本项目来说只需一个 ``base_url`` + 一个 ``sk-`` key，
就能调用这些视频模型，而不必逐个厂商去开通、各写一套鉴权。

它对外提供两套图生视频协议，本执行器都支持（用 ``extra_config['protocol']`` 选择）：

1. ``v1-videos``（默认）—— 官方「OpenAI Video Format」统一任务接口::

       POST {base}/v1/videos                # 提交任务
       GET  {base}/v1/videos/{id}           # 轮询状态
       GET  {base}/v1/videos/{id}/content   # 下载成片（需 Authorization）

2. ``video-generations`` —— 可灵 / 即梦 / Vidu 原生格式::

       POST {base}/v1/video/generations
       GET  {base}/v1/video/generations/{task_id}

图片传参风格由 ``extra_config['image_field_style']`` 决定，默认 ``auto``（按模型名判断）：

- ``seedance``：``metadata.content`` 列表，角色 ``first_frame`` / ``last_frame`` /
  ``reference_image``（豆包 Seedance / 即梦）；
- ``wan``：``metadata.img_url``（阿里万相）；
- ``images``：顶层 ``images`` 列表（Happyhorse 等）；
- ``openai``：顶层 ``image`` 列表（部分中转实现）。

图片一律内联为 base64 data URL，因此本地 ``storage/`` 里的图片**不需要公网可访问**。
"""

import base64
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from core.ai_client.image2video_client import VideoGeneratorClient
from core.utils.http_retry import get_with_retry, post_with_retry

logger = logging.getLogger(__name__)

# 协议标识
PROTOCOL_V1_VIDEOS = 'v1-videos'
PROTOCOL_VIDEO_GENERATIONS = 'video-generations'

_PROTOCOL_ALIASES = {
    'v1-videos': PROTOCOL_V1_VIDEOS,
    'v1videos': PROTOCOL_V1_VIDEOS,
    'videos': PROTOCOL_V1_VIDEOS,
    'unified': PROTOCOL_V1_VIDEOS,
    'seedance': PROTOCOL_V1_VIDEOS,
    'auto': PROTOCOL_V1_VIDEOS,
    'video-generations': PROTOCOL_VIDEO_GENERATIONS,
    'videogenerations': PROTOCOL_VIDEO_GENERATIONS,
    'kling': PROTOCOL_VIDEO_GENERATIONS,
    'jimeng': PROTOCOL_VIDEO_GENERATIONS,
    'vidu': PROTOCOL_VIDEO_GENERATIONS,
}

# 图片字段风格
STYLE_AUTO = 'auto'
STYLE_SEEDANCE = 'seedance'
STYLE_WAN = 'wan'
STYLE_IMAGES = 'images'
STYLE_OPENAI = 'openai'

_STATUS_SUCCESS = frozenset({'succeeded', 'success', 'completed', 'complete', 'finished', 'done'})
_STATUS_FAILED = frozenset({'failed', 'failure', 'error', 'cancelled', 'canceled'})

_P_LEVEL_PATTERN = re.compile(r'^\d{3,4}p$')
_SIZE_PATTERN = re.compile(r'^\d{2,5}\s*[xX*]\s*\d{2,5}$')

_ASPECT_SIZE = {
    '16:9': (1280, 720),
    '9:16': (720, 1280),
    '1:1': (960, 960),
    '4:3': (1024, 768),
    '3:4': (768, 1024),
    '21:9': (1680, 720),
}

_MIME_EXTENSIONS = {
    'image/jpeg': 'jpg',
    'image/jpg': 'jpg',
    'image/png': 'png',
    'image/webp': 'webp',
    'image/bmp': 'bmp',
}


class NewApiImage2VideoClient(VideoGeneratorClient):
    """对接 New API 网关（one-api 系）的图生视频执行器。"""

    #: 允许被 base_url 覆盖的已知后缀（长的放前面，避免 '/v1/videos' 先命中）
    _KNOWN_SUFFIXES = (
        '/v1/videos/generations',
        '/v1/video/generations',
        '/v1/videos',
        '/v1',
    )

    def __init__(
        self,
        api_url: str,
        api_key: Optional[str] = None,
        api_token: Optional[str] = None,
        model_name: Optional[str] = None,
        model: Optional[str] = None,
        protocol: Optional[str] = None,
        request_format: Optional[str] = None,
        image_field_style: Optional[str] = None,
        **kwargs: Any,
    ):
        """初始化 New API 视频客户端。

        Args:
            api_url: 网关地址。可写 ``https://host``、``https://host/v1``，
                或直接写具体端点（``/v1/videos``、``/v1/video/generations``），
                内部会归一化后再拼路径。
            protocol: 中轉协议，``v1-videos``（默认）或 ``video-generations``。
            request_format: ``json``（默认）或 ``multipart``（Sora-2 等需要
                以文件形式上传首帧时使用）。
            image_field_style: 图片字段风格，默认 ``auto``。
        """
        super().__init__(
            api_url=api_url,
            api_token=api_token,
            model=model,
            api_key=api_key,
            model_name=model_name,
            **kwargs,
        )
        self.protocol = self._normalize_protocol(
            protocol or kwargs.get('video_protocol') or PROTOCOL_V1_VIDEOS
        )
        self.request_format = str(
            request_format or kwargs.get('request_format') or 'json'
        ).strip().lower()
        self.image_field_style = str(
            image_field_style or kwargs.get('image_field_style') or STYLE_AUTO
        ).strip().lower()

    # ===== URL 组装 =====

    @staticmethod
    def _normalize_protocol(value: Any) -> str:
        """把协议别名归一到标准标识。"""
        token = str(value or '').strip().lower().replace('_', '-').replace(' ', '')
        return _PROTOCOL_ALIASES.get(token, PROTOCOL_V1_VIDEOS)

    def _api_base(self) -> str:
        """去掉已知端点后缀，得到 ``scheme://host[/prefix]`` 形式的基础地址。"""
        base = (self.base_url or '').strip().rstrip('/')
        for suffix in self._KNOWN_SUFFIXES:
            if base.endswith(suffix):
                return base[: -len(suffix)]
        return base

    def _endpoint(self, path: str) -> str:
        """拼出完整端点地址。"""
        return f'{self._api_base()}{path}'

    def _auth_headers(self) -> Dict[str, str]:
        """仅含鉴权头（multipart 上传时不能带 Content-Type）。"""
        return {'Authorization': self.headers.get('Authorization', '')}

    def _build_create_video_url(self) -> str:
        """构建任务创建地址。"""
        if self.protocol == PROTOCOL_VIDEO_GENERATIONS:
            return self._endpoint('/v1/video/generations')
        return self._endpoint('/v1/videos')

    def _build_task_status_url(self, task_id: str) -> str:
        """构建任务状态查询地址。"""
        if self.protocol == PROTOCOL_VIDEO_GENERATIONS:
            return self._endpoint(f'/v1/video/generations/{task_id}')
        return self._endpoint(f'/v1/videos/{task_id}')

    def _build_task_content_url(self, task_id: str) -> str:
        """构建成片下载地址（仅 v1-videos 协议有）。"""
        return self._endpoint(f'/v1/videos/{task_id}/content')

    # ===== 参数归一 =====

    @staticmethod
    def _resolve_size(resolution: Optional[str]) -> str:
        """把 ``1920*1080`` / ``1280x720`` 归一成 ``1280x720``；非尺寸返回空串。"""
        text = str(resolution or '').strip()
        if not _SIZE_PATTERN.match(text):
            return ''
        return re.sub(r'\s*[xX*]\s*', 'x', text).lower()

    @staticmethod
    def _resolve_p_level(resolution: Optional[str]) -> str:
        """取出 ``720p`` / ``1080P`` 这类档位；否则返回空串。"""
        text = str(resolution or '').strip().lower()
        return text if _P_LEVEL_PATTERN.match(text) else ''

    @staticmethod
    def _resolve_aspect_size(aspect_ratio: Optional[str]) -> str:
        """按画幅比给出默认尺寸（网关未指定分辨率时用）。"""
        text = str(aspect_ratio or '').strip()
        size = _ASPECT_SIZE.get(text)
        return f'{size[0]}x{size[1]}' if size else ''

    @staticmethod
    def _resolve_seconds(duration_seconds: Any, default: int = 5) -> int:
        """把时长归一成正整数秒。"""
        try:
            seconds = int(float(duration_seconds))
        except (TypeError, ValueError):
            seconds = default
        return seconds if seconds > 0 else default

    def _resolve_style(self, model: str) -> str:
        """决定图片字段风格：显式配置优先，否则按模型名猜。"""
        if self.image_field_style and self.image_field_style != STYLE_AUTO:
            return self.image_field_style

        normalized = str(model or '').lower()
        if 'seedance' in normalized or 'doubao' in normalized or 'jimeng' in normalized:
            return STYLE_SEEDANCE
        if 'wan' in normalized:
            return STYLE_WAN
        if 'happyhorse' in normalized:
            return STYLE_IMAGES
        return STYLE_OPENAI

    def _collect_images(
        self,
        kwargs: Dict[str, Any],
        timeout: int,
    ) -> Tuple[List[str], List[str], List[str]]:
        """把入参图片统一解析成 base64 与「可直接传给网关的图片输入」。

        Returns:
            (image_inputs, http_urls, resolved_base64s)：
            ``image_inputs`` 里既有原始 http(s) 链接，也有本地图片转换出来的
            base64 data URL；``resolved_base64s`` 只含 base64 本体（multipart
            上传首帧时要用）。
        """
        image_uris: List[Any] = list(kwargs.get('image_uris') or [])
        image_uri = kwargs.get('image_uri')
        if image_uri and image_uri not in image_uris:
            image_uris.insert(0, image_uri)

        image_base64s: List[str] = list(kwargs.get('image_base64s') or [])
        image_base64 = kwargs.get('image_base64')
        if image_base64 and image_base64 not in image_base64s:
            image_base64s.insert(0, image_base64)

        image_mime_type = kwargs.get('image_mime_type') or 'image/jpeg'
        resolved_base64s = self._resolve_image_base64s(image_uris, image_base64s, timeout)
        image_inputs = self._build_image_inputs_for_openai_videos(
            image_uris,
            resolved_base64s,
            image_mime_type,
        )
        http_urls = [
            item
            for item in image_inputs
            if isinstance(item, str) and item.startswith(('http://', 'https://'))
        ]
        return image_inputs, http_urls, resolved_base64s

    # ===== 请求体构建 =====

    def _build_v1_videos_payload(
        self,
        prompt: str,
        model: str,
        duration_seconds: Any,
        aspect_ratio: Optional[str],
        resolution: Optional[str],
        negative_prompt: Optional[str],
        seed: Optional[Any],
        image_inputs: List[str],
    ) -> Dict[str, Any]:
        """构建 ``/v1/videos``（OpenAI Video 格式）请求体。"""
        style = self._resolve_style(model)
        seconds = self._resolve_seconds(duration_seconds)

        payload: Dict[str, Any] = {
            'model': model,
            'prompt': prompt,
            # 实测 new-api 侧 seconds 必须是字符串
            'seconds': str(seconds),
        }

        size = self._resolve_size(resolution)
        if not size and style in (STYLE_OPENAI, STYLE_IMAGES):
            size = self._resolve_aspect_size(aspect_ratio)
        if size:
            payload['size'] = size

        image_list = [item for item in image_inputs if item]
        metadata: Dict[str, Any] = {}

        if style == STYLE_SEEDANCE:
            # 豆包 Seedance / 即梦：metadata.resolution + metadata.ratio + metadata.content
            p_level = self._resolve_p_level(resolution)
            if p_level:
                metadata['resolution'] = p_level
            if aspect_ratio:
                metadata['ratio'] = str(aspect_ratio).strip()
            if image_list:
                metadata['content'] = self._build_seedance_content(image_list)
        elif style == STYLE_WAN:
            # 阿里万相：首帧走 metadata.img_url
            if image_list:
                metadata['img_url'] = image_list[0]
            p_level = self._resolve_p_level(resolution)
            if p_level:
                metadata['resolution'] = p_level
        elif style == STYLE_IMAGES:
            if image_list:
                payload['images'] = image_list
        else:
            # OpenAI 风格：顶层 image 列表，同时补一份 metadata.img_url 兜底
            if image_list:
                payload['image'] = image_list
                metadata['img_url'] = image_list[0]

        if negative_prompt:
            metadata['negative_prompt'] = str(negative_prompt)
        if seed is not None:
            try:
                metadata['seed'] = int(seed)
            except (TypeError, ValueError):
                logger.warning('忽略非法 seed 参数: %s', seed)

        if metadata:
            payload['metadata'] = metadata
        return payload

    @staticmethod
    def _build_seedance_content(image_list: List[str]) -> List[Dict[str, Any]]:
        """构建 Seedance 的 metadata.content（首帧 / 尾帧 / 参考图）。"""
        roles = ['first_frame', 'last_frame']
        content = []
        for index, image in enumerate(image_list):
            role = roles[index] if index < len(roles) else 'reference_image'
            content.append(
                {
                    'type': 'image_url',
                    'image_url': {'url': image},
                    'role': role,
                }
            )
        return content

    def _build_video_generations_payload(
        self,
        prompt: str,
        model: str,
        duration_seconds: Any,
        aspect_ratio: Optional[str],
        resolution: Optional[str],
        negative_prompt: Optional[str],
        seed: Optional[Any],
        image_inputs: List[str],
        http_urls: List[str],
    ) -> Dict[str, Any]:
        """构建 ``/v1/video/generations``（可灵 / 即梦 / Vidu 格式）请求体。"""
        payload: Dict[str, Any] = {
            'model': model,
            'prompt': prompt,
            'duration': self._resolve_seconds(duration_seconds),
        }

        size = self._resolve_size(resolution)
        if size:
            payload['size'] = size
            width, height = size.split('x')
            payload['width'] = int(width)
            payload['height'] = int(height)

        image_list = [item for item in image_inputs if item]
        if image_list:
            # 该协议文档写明 image 支持 URL 或 Base64，优先用公网 URL
            payload['image'] = http_urls[0] if http_urls else image_list[0]

        metadata: Dict[str, Any] = {}
        if image_list:
            metadata['image_urls'] = image_list
            if len(image_list) > 1:
                metadata['image_tail'] = image_list[1]
        if negative_prompt:
            metadata['negative_prompt'] = str(negative_prompt)
        if aspect_ratio:
            metadata['aspect_ratio'] = str(aspect_ratio).strip()
        p_level = self._resolve_p_level(resolution)
        if p_level:
            metadata['resolution'] = p_level
        if seed is not None:
            try:
                metadata['seed'] = int(seed)
            except (TypeError, ValueError):
                logger.warning('忽略非法 seed 参数: %s', seed)

        if metadata:
            payload['metadata'] = metadata
        return payload

    # ===== 提交任务 =====

    @staticmethod
    def _error_message(response: Any) -> str:
        """从错误响应里提取可读信息。"""
        try:
            body = response.json()
        except ValueError:
            return (response.text or '')[:300]

        if isinstance(body, dict):
            error = body.get('error')
            if isinstance(error, dict):
                return str(error.get('message') or error)
            if error:
                return str(error)
            for key in ('message', 'msg', 'detail'):
                if body.get(key):
                    return str(body[key])
        return str(body)[:300]

    def _ensure_success(self, response: Any, action: str) -> None:
        """状态码 >= 400 时抛出带原始报错的异常（不重试）。"""
        if response.status_code >= 400:
            raise RuntimeError(
                f'{action}失败: HTTP {response.status_code} - {self._error_message(response)}'
            )

    def _post_json(self, url: str, payload: Dict[str, Any], timeout: int) -> Any:
        """发送 JSON 请求（带连接重试）。"""
        response = post_with_retry(url, json=payload, headers=self.headers, timeout=timeout)
        self._ensure_success(response, '创建视频任务')
        return response.json()

    def _post_multipart(
        self,
        url: str,
        payload: Dict[str, Any],
        resolved_base64s: List[str],
        image_mime_type: str,
        timeout: int,
    ) -> Any:
        """以 multipart/form-data 提交（Sora-2 等要求首帧以文件上传）。"""
        data: Dict[str, str] = {}
        for key, value in payload.items():
            if isinstance(value, (dict, list)):
                data[key] = json.dumps(value, ensure_ascii=False)
            elif isinstance(value, bool):
                data[key] = 'true' if value else 'false'
            else:
                data[key] = str(value)

        files = None
        if resolved_base64s:
            extension = _MIME_EXTENSIONS.get(image_mime_type.lower(), 'jpg')
            files = {
                'input_reference': (
                    f'input_reference.{extension}',
                    base64.b64decode(resolved_base64s[0]),
                    image_mime_type,
                )
            }

        response = post_with_retry(
            url,
            data=data,
            files=files,
            headers=self._auth_headers(),
            timeout=timeout,
        )
        self._ensure_success(response, '创建视频任务')
        return response.json()

    def _extract_created_task(self, result: Any) -> Dict[str, Any]:
        """从创建响应里取出任务 id 与（可能的）同步结果视频。"""
        videos: List[Dict[str, Any]] = []
        task_id = ''

        if isinstance(result, list):
            videos = self._normalize_video_items(result)
        elif isinstance(result, dict):
            task_id = str(result.get('id') or result.get('task_id') or '')
            data = result.get('data')
            if isinstance(data, dict):
                task_id = task_id or str(data.get('id') or data.get('task_id') or '')
            videos = self._extract_videos(result, task_id)

        return {'task_id': task_id, 'videos': videos}

    def create_video_task(self, prompt: str, model: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
        """提交视频生成任务。

        Returns:
            dict: ``{'task_id': str, 'videos': list}`` —— 异步任务只有 task_id；
            少数渠道会同步返回成片，此时 ``videos`` 非空。
        """
        timeout = int(kwargs.get('timeout', self.timeout))
        resolved_model = model or kwargs.get('model') or self.model
        negative_prompt = kwargs.get('negative_prompt')
        aspect_ratio = kwargs.get('aspect_ratio')
        resolution = kwargs.get('resolution')
        seed = kwargs.get('seed')
        duration_seconds = kwargs.get('duration_seconds', kwargs.get('duration', 5))
        image_mime_type = kwargs.get('image_mime_type') or 'image/jpeg'

        final_prompt = self._build_prompt_text(
            prompt=prompt,
            negative_prompt=None,
            camera_movement_description=kwargs.get('camera_movement_description'),
        )
        image_inputs, http_urls, resolved_base64s = self._collect_images(kwargs, timeout)

        if self.protocol == PROTOCOL_VIDEO_GENERATIONS:
            payload = self._build_video_generations_payload(
                prompt=final_prompt,
                model=resolved_model,
                duration_seconds=duration_seconds,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                negative_prompt=negative_prompt,
                seed=seed,
                image_inputs=image_inputs,
                http_urls=http_urls,
            )
            result = self._post_json(self._build_create_video_url(), payload, timeout)
        else:
            payload = self._build_v1_videos_payload(
                prompt=final_prompt,
                model=resolved_model,
                duration_seconds=duration_seconds,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                negative_prompt=negative_prompt,
                seed=seed,
                image_inputs=image_inputs,
            )
            url = self._build_create_video_url()
            if self.request_format == 'multipart':
                result = self._post_multipart(
                    url,
                    payload,
                    resolved_base64s,
                    image_mime_type,
                    timeout,
                )
            else:
                result = self._post_json(url, payload, timeout)

        return self._extract_created_task(result)

    def get_task_status(self, task_id: str, timeout: Optional[int] = None) -> Dict[str, Any]:
        """查询任务状态（带连接重试）。"""
        response = get_with_retry(
            self._build_task_status_url(task_id),
            headers=self.headers,
            timeout=int(timeout or self.timeout),
        )
        self._ensure_success(response, '查询任务状态')
        return response.json()

    # ===== 结果解析 =====

    @staticmethod
    def _normalize_task_payload(task_info: Any) -> Tuple[str, Dict[str, Any]]:
        """把不同网关的状态响应归一成 ``(status, payload)``。"""
        payload = task_info if isinstance(task_info, dict) else {}
        if not payload.get('status') and isinstance(payload.get('data'), dict):
            payload = payload['data']
        status = str(payload.get('status') or payload.get('state') or '').strip().lower()
        return status, payload

    @staticmethod
    def _normalize_video_items(items: Any) -> List[Dict[str, Any]]:
        """把 ``['http://x']`` / ``[{'url': ...}]`` 统一成 ``[{'url': ...}]``。"""
        normalized: List[Dict[str, Any]] = []
        for item in items or []:
            if isinstance(item, dict):
                url = item.get('url') or item.get('video_url')
                if url:
                    normalized.append({**item, 'url': url})
            elif item:
                normalized.append({'url': item})
        return normalized

    def _extract_videos(self, task_result: Any, task_id: str) -> List[Dict[str, Any]]:
        """从任务结果里提取视频（并带上带鉴权的下载头）。"""
        if not isinstance(task_result, dict):
            return self._normalize_video_items(task_result)

        found: List[Dict[str, Any]] = []
        for key in ('url', 'video_url'):
            if task_result.get(key):
                found.append({'url': task_result[key]})
        if not found:
            data = task_result.get('data')
            if isinstance(data, dict):
                for key in ('url', 'video_url'):
                    if data.get(key):
                        found.append({'url': data[key]})
                if not found:
                    found = self._normalize_video_items(data.get('videos'))
            elif isinstance(data, list):
                found = self._normalize_video_items(data)

        if not found and self.protocol == PROTOCOL_V1_VIDEOS and task_id:
            # 没有直链时回落到 /v1/videos/{id}/content（需要 Bearer）
            content_url = self._build_task_content_url(task_id)
            found = [{
                'content_url': content_url,
                'original_url': content_url,
            }]

        # 中转站返回的直链往往也需要鉴权，统一带上 Authorization
        return [{**item, 'download_headers': self._auth_headers()} for item in found]

    def _wait_for_task(
        self,
        task_id: str,
        poll_interval: int,
        max_wait_time: int,
        timeout: int,
        max_poll_attempts: int = 240,
        max_consecutive_errors: int = 5,
    ) -> Dict[str, Any]:
        """轮询等待任务完成。"""
        start_time = time.time()
        poll_attempts = 0
        consecutive_errors = 0

        while True:
            if time.time() - start_time > max_wait_time:
                raise TimeoutError(f'任务超时: 超过 {max_wait_time} 秒')
            if poll_attempts >= max_poll_attempts:
                raise TimeoutError(f'任务熔断: 轮询次数超过 {max_poll_attempts} 次')

            try:
                task_info = self.get_task_status(task_id, timeout=timeout)
                consecutive_errors = 0
            except Exception as exc:  # noqa: BLE001 - 连续失败才熔断，单次抖动可容忍
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    raise RuntimeError(
                        f'任务熔断: 连续查询异常达到 {max_consecutive_errors} 次, 最后错误: {exc}'
                    )
                logger.warning(
                    'New API 任务状态查询异常: task_id=%s attempt=%s error=%s',
                    task_id,
                    consecutive_errors,
                    exc,
                )
                time.sleep(poll_interval)
                continue

            poll_attempts += 1
            status, payload = self._normalize_task_payload(task_info)

            if status in _STATUS_SUCCESS:
                return payload
            if status in _STATUS_FAILED:
                message = payload.get('message') or payload.get('error') or '未知错误'
                raise RuntimeError(f'任务失败: {message}')

            time.sleep(poll_interval)

    def _generate_video(
        self,
        prompt: str,
        poll_interval: int = 5,
        max_wait_time: int = 600,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """同步生成视频（提交任务 → 轮询 → 下载到本地 storage）。"""
        start_time = time.time()
        timeout = int(kwargs.get('timeout', self.timeout))
        model = kwargs.get('model') or self.model
        task_id = ''

        try:
            created = self.create_video_task(prompt, model=model, timeout=timeout, **kwargs)
            task_id = created.get('task_id') or ''
            videos = list(created.get('videos') or [])

            if not videos:
                if not task_id:
                    raise RuntimeError('创建视频任务成功，但响应中缺少任务 ID')
                task_result = self._wait_for_task(
                    task_id,
                    poll_interval=poll_interval,
                    max_wait_time=max_wait_time,
                    timeout=timeout,
                )
                videos = self._extract_videos(task_result, task_id)

            if not videos:
                return {
                    'success': False,
                    'data': [],
                    'metadata': self._build_metadata(start_time, model, task_id),
                    'error': '响应中未找到可用视频地址',
                }

            return {
                'success': True,
                'data': self._localize_video_data(videos, timeout),
                'metadata': self._build_metadata(start_time, model, task_id),
            }
        except Exception as exc:  # noqa: BLE001 - 统一转成 AIResponse 风格的结果字典
            logger.error('New API 图生视频失败: %s', exc, exc_info=True)
            return {
                'success': False,
                'data': [],
                'metadata': self._build_metadata(start_time, model, task_id),
                'error': str(exc),
            }

    def _build_metadata(self, start_time: float, model: str, task_id: str) -> Dict[str, Any]:
        """构建与火山执行器一致的 metadata 结构。"""
        return {
            'latency_ms': int((time.time() - start_time) * 1000),
            'model': model,
            'protocol': self.protocol,
            'request_url': self._build_create_video_url(),
            'task_id': task_id,
        }
