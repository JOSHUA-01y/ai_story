"""
OpenAI兼容的LLM客户端实现
支持OpenAI API和兼容接口
"""

import codecs
import logging
import requests
import json
import time
from typing import Dict, Any, Generator
from core.utils.http_retry import post_with_retry
from .base import LLMClient, AIResponse

logger = logging.getLogger(__name__)


class OpenAIClient(LLMClient):
    """
    OpenAI客户端实现
    兼容OpenAI API和类似接口
    支持流式和非流式生成
    """

    def _generate_text(
        self,
        prompt: str,
        max_tokens: int = None,
        temperature: float = None,
        **kwargs
    ) -> AIResponse:
        """生成文本(非流式) 废弃"""

        start_time = time.time()

        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }

        payload = {
            'model': self.model_name,
            'messages': [
                {'role': 'user', 'content': prompt}
            ],
            'max_tokens': max_tokens or self.config.get("max_tokens", 4096),
            'temperature': temperature or self.config.get("temperature", 0.7),
            **kwargs
        }

        try:
            timeout = self.config.get('timeout', 60)

            # 带连接重试：上游网关约 40% 概率在 ~19.3s 处掐断连接。
            # 详见 core/utils/http_retry.py
            response = post_with_retry(
                f'{self.api_url}',
                headers=headers,
                json=payload,
                timeout=timeout,
            )

            if response.status_code == 200:
                result = response.json()
                latency_ms = int((time.time() - start_time) * 1000)

                return AIResponse(
                    success=True,
                    text=result['choices'][0]['message']['content'],
                    metadata={
                        'tokens_used': result.get('usage', {}).get('total_tokens', 0),
                        'latency_ms': latency_ms,
                        'model': self.model_name,
                    }
                )
            else:
                return AIResponse(
                    success=False,
                    error=f'API请求失败: {response.status_code} - {response.text}'
                )

        except requests.RequestException as e:
            return AIResponse(
                success=False,
                error=f'网络请求错误: {str(e)}'
            )
        except Exception as e:
            return AIResponse(
                success=False,
                error=f'未知错误: {str(e)}'
            )

    # 判定为「可重试」的错误特征（连接层问题）。
    # 上游网关（实测火山 Ark）会以约 40% 的概率在 ~19.3 秒处直接掐断连接，
    # 表现为 RemoteDisconnected / ChunkedEncodingError / SSLError 等，
    # 与提示词、模型、鉴权均无关 —— 重试即可成功。
    # 确定性错误（如 404 模型未开通、401 鉴权失败）不含这些特征，不重试。
    RETRYABLE_ERROR_MARKERS = (
        '网络请求错误',
        'Connection',
        'ChunkedEncoding',
        'RemoteDisconnected',
        'SSL',
        'timed out',
        'Timeout',
        '意外中断',
    )

    def generate_stream(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int = 2000,
        temperature: float = 0.7,
        **kwargs
    ) -> Generator[Dict[str, Any], None, None]:
        """带连接级重试的流式生成。

        真正的请求逻辑在 _generate_stream_once()；这里只负责在「连接层失败」时重试。
        重试会重新开始一次完整生成，因此调用方每次收到 token 都应使用 chunk 里
        累积的 full_text（本项目 llm_stage 正是这么做的），不会拼出重复内容。

        Yields:
            Dict包含: type (token/done/error), content, metadata
        """
        max_attempts = int(self.config.get('stream_retry_attempts') or 3)
        backoff = float(self.config.get('stream_retry_backoff') or 3)

        for attempt in range(1, max_attempts + 1):
            error_chunk = None
            saw_done = False

            for chunk in self._generate_stream_once(
                prompt=prompt,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                **kwargs
            ):
                if chunk.get('type') == 'error':
                    error_chunk = chunk
                    break
                if chunk.get('type') == 'done':
                    saw_done = True
                yield chunk

            # 正常结束（收到了完成标记且没有报错）
            if error_chunk is None and saw_done:
                return

            # 既没报错也没收到完成标记 —— 流被静默截断，按可重试错误处理
            if error_chunk is None:
                error_chunk = {
                    'type': 'error',
                    'error': '网络请求错误: 流式响应意外中断（未收到完成标记）',
                }

            error_text = str(error_chunk.get('error') or '')
            retryable = any(marker in error_text for marker in self.RETRYABLE_ERROR_MARKERS)

            if not retryable or attempt >= max_attempts:
                if retryable:
                    logger.warning(
                        '流式生成重试 %d 次后仍失败 (model=%s): %s',
                        max_attempts, self.model_name, error_text,
                    )
                    error_chunk = dict(
                        error_chunk,
                        error=f'{error_text}（已重试 {max_attempts} 次仍失败）',
                    )
                yield error_chunk
                return

            logger.warning(
                '流式生成连接中断，第 %d/%d 次尝试失败，%.1fs 后重试 (model=%s): %s',
                attempt, max_attempts, backoff * attempt, self.model_name, error_text,
            )
            time.sleep(backoff * attempt)

    def _generate_stream_once(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int = 2000,
        temperature: float = 0.7,
        **kwargs
    ) -> Generator[Dict[str, Any], None, None]:
        """
        流式生成文本（单次尝试，不含重试）

        Args:
            prompt: 输入提示词
            max_tokens: 最大token数
            temperature: 温度参数
            **kwargs: 其他参数

        Yields:
            Dict包含: type (token/done/error), content, metadata
        """

        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }
        messages = []
        if system_prompt:
            messages.append({'role': 'system', 'content': system_prompt})
        messages.append({'role': 'user', 'content': prompt})
        payload = {
            'model': self.model_name,
            'messages': messages,
            'max_tokens': max_tokens,
            'temperature': temperature,
            'stream': True,
            **kwargs
        }

        start_time = time.time()
        full_text = ""

        try:
            timeout = self.config.get('timeout', 3000)
            api_url = self.api_url
            response = requests.post(
                api_url,
                headers=headers,
                json=payload,
                timeout=timeout,
                stream=True
            )

            if response.status_code != 200:
                yield {
                    'type': 'error',
                    'error': f'API请求失败: {response.status_code} - {response.text}'
                }
                return

            # 读取SSE流
            buffer = ''
            decoder = codecs.getincrementaldecoder('utf-8')()
            for chunk_bytes in response.iter_content(chunk_size=1024):
                if not chunk_bytes:
                    continue

                buffer += decoder.decode(chunk_bytes)

                # 按行分割
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    line = line.strip()

                    if not line or line == 'data: [DONE]':
                        continue

                    if line.startswith('data: '):
                        try:
                            json_str = line[6:]  # 移除 'data: ' 前缀
                            chunk = json.loads(json_str)

                            # 提取内容
                            if 'choices' in chunk and len(chunk['choices']) > 0:
                                delta = chunk['choices'][0].get('delta', {})
                                content = delta.get('content', '')
                                if content:
                                    full_text += content
                                    yield {
                                        'type': 'token',
                                        'content': content,
                                        'full_text': full_text
                                    }

                                # 检查是否结束
                                finish_reason = chunk['choices'][0].get('finish_reason')
                                if finish_reason:
                                    # 计算延迟
                                    latency_ms = int((time.time() - start_time) * 1000)

                                    yield {
                                        'type': 'done',
                                        'full_text': full_text,
                                        'metadata': {
                                            'latency_ms': latency_ms,
                                            'model': self.model_name,
                                            'finish_reason': finish_reason
                                        }
                                    }

                        except json.JSONDecodeError:
                            continue

            buffer += decoder.decode(b'', final=True)
            if buffer:
                line = buffer.strip()
                if line.startswith('data: ') and line != 'data: [DONE]':
                    try:
                        json_str = line[6:]
                        chunk = json.loads(json_str)
                        if 'choices' in chunk and len(chunk['choices']) > 0:
                            delta = chunk['choices'][0].get('delta', {})
                            content = delta.get('content', '')
                            if content:
                                full_text += content
                                yield {
                                    'type': 'token',
                                    'content': content,
                                    'full_text': full_text
                                }

                            finish_reason = chunk['choices'][0].get('finish_reason')
                            if finish_reason:
                                latency_ms = int((time.time() - start_time) * 1000)
                                yield {
                                    'type': 'done',
                                    'full_text': full_text,
                                    'metadata': {
                                        'latency_ms': latency_ms,
                                        'model': self.model_name,
                                        'finish_reason': finish_reason
                                    }
                                }
                    except json.JSONDecodeError:
                        pass

        except requests.RequestException as e:
            yield {
                'type': 'error',
                'error': f'网络请求错误: {str(e)}'
            }
        except Exception as e:
            yield {
                'type': 'error',
                'error': f'未知错误: {str(e)}'
            }

    def validate_config(self) -> bool:
        """验证配置"""
        if not self.api_url or not self.api_key or not self.model_name:
            return False

        # 简单的连通性测试
        try:
            headers = {'Authorization': f'Bearer {self.api_key}'}
            response = requests.get(
                f'{self.api_url}/models',
                headers=headers,
                timeout=10
            )
            return response.status_code in [200, 401]  # 401表示认证问题,但API可达
        except Exception:
            return False
