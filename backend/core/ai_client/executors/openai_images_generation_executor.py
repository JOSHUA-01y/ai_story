"""显式的 OpenAI Images Generations 协议执行器。"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

import requests

from core.ai_client.base import AIResponse, Text2ImageClient as BaseText2ImageClient
from core.ai_client.image_result_utils import (
    ensure_list,
    localize_image_item,
    merge_extra_payload,
    normalize_result_data,
)
from core.ai_client.schemas import Text2ImageRequest
from core.utils.http_retry import post_with_retry


class OpenAIImagesGenerationExecutor(BaseText2ImageClient):
    """适配 `/images/generations` 风格的文生图接口。"""

    def generate_from_text2image_request(self, request: Text2ImageRequest) -> AIResponse:
        return self._run_request(request=request)

    def _generate_image(
        self,
        prompt: str,
        negative_prompt: str = "",
        width: int = 1024,
        height: int = 1024,
        steps: int = 20,
        **kwargs
    ) -> AIResponse:
        request = Text2ImageRequest(
            prompt=prompt,
            negative_prompt=negative_prompt,
            reference_images=ensure_list(kwargs.get('image')),
            width=width,
            height=height,
            aspect_ratio=kwargs.get('ratio', ''),
            sample_count=kwargs.get('sample_count', 1),
            seed=kwargs.get('seed'),
            extra={
                'steps': steps,
                'resolution': kwargs.get('resolution', ''),
                'response_format': kwargs.get('response_format'),
            },
        )
        return self._run_request(
            request=request,
            api_url=kwargs.get('api_url'),
            api_key=kwargs.get('api_key') or kwargs.get('session_id'),
            model_name=kwargs.get('model'),
        )

    def _run_request(
        self,
        request: Text2ImageRequest,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
    ) -> AIResponse:
        start_time = time.time()
        request_url = api_url or self.api_url
        request_key = api_key or self.api_key
        request_model = model_name or self.model_name
        timeout = int(request.extra.get('timeout') or self.config.get('timeout', 180))

        payload: Dict[str, Any] = {
            'model': request_model,
            'prompt': request.prompt.strip(),
            'size': request.size or '1024x1024',
        }
        if request.sample_count > 1:
            payload['n'] = request.sample_count
        if request.reference_images:
            payload['image'] = request.reference_images
        if request.negative_prompt:
            payload['negative_prompt'] = request.negative_prompt
        if request.aspect_ratio:
            payload['aspect_ratio'] = request.aspect_ratio
        if request.seed is not None:
            payload['seed'] = request.seed
        if request.extra.get('resolution'):
            payload['resolution'] = request.extra['resolution']
        if request.extra.get('steps'):
            payload['steps'] = request.extra['steps']
        if request.extra.get('response_format'):
            payload['response_format'] = request.extra['response_format']

        # 水印开关：火山方舟 doubao-seedream 默认会给产物打「AI生成」水印，
        # 短剧/成片素材需要干净画面，因此在 provider 的 extra_config 里加
        # {"watermark": false} 即可关闭（request.extra 里的同名字段优先）。
        watermark = request.extra.get('watermark')
        if watermark is None:
            watermark = self.config.get('watermark')
        if watermark is not None:
            payload['watermark'] = bool(watermark)

        merge_extra_payload(
            payload,
            request.extra,
            reserved_keys={'timeout', 'resolution', 'steps', 'response_format', 'watermark'},
        )

        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {request_key}',
        }

        try:
            # 用带重试的 POST：上游网关会以约 40% 的概率在 ~19.3s 处掐断连接
            # （RemoteDisconnected / SSLError），重试即可成功。详见 core/utils/http_retry.py
            response = post_with_retry(
                request_url,
                headers=headers,
                json=payload,
                timeout=timeout,
            )
            if response.status_code != 200:
                return AIResponse(
                    success=False,
                    error=f'API请求失败: {response.status_code} - {response.text}',
                )

            result = response.json()
            latency_ms = int((time.time() - start_time) * 1000)
            result_data = normalize_result_data(result.get('data'))
            if not result_data:
                return AIResponse(
                    success=False,
                    error='响应格式错误: 缺少data字段或data为空',
                    metadata={
                        'latency_ms': latency_ms,
                        'model': request_model,
                        'request_url': request_url,
                    },
                )

            images_data = []
            image_urls = []
            for item in result_data:
                if not item.get('url') and not item.get('b64_json'):
                    continue
                localized = localize_image_item(
                    item=item,
                    width=request.width or 1024,
                    height=request.height or 1024,
                    timeout=timeout,
                )
                if localized.get('url'):
                    image_urls.append(localized['url'])
                images_data.append(localized)

            if not images_data:
                return AIResponse(
                    success=False,
                    error='响应格式错误: 未从data中解析到有效图片结果',
                    metadata={
                        'latency_ms': latency_ms,
                        'model': request_model,
                        'request_url': request_url,
                    },
                )

            return AIResponse(
                success=True,
                text='\n'.join(image_urls),
                data=images_data,
                metadata={
                    'latency_ms': latency_ms,
                    'model': request_model,
                    'aspect_ratio': request.aspect_ratio,
                    'width': request.width or 1024,
                    'height': request.height or 1024,
                    'sample_count': request.sample_count,
                    'request_url': request_url,
                    'input_image_count': len(request.reference_images),
                    'created': result.get('created'),
                    'usage': result.get('usage', {}),
                },
            )
        except requests.exceptions.RequestException as exc:
            return AIResponse(success=False, error=f'网络请求错误: {str(exc)}')
        except ValueError as exc:
            return AIResponse(success=False, error=f'响应解析错误: {str(exc)}')
        except Exception as exc:
            return AIResponse(success=False, error=f'未知错误: {str(exc)}')

    def validate_config(self) -> bool:
        return bool(self.api_url and self.api_key and self.model_name)
