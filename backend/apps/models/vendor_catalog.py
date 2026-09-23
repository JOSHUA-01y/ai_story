"""内置模型厂商目录与模型发现工具。"""

from typing import Any, Dict


VENDOR_CATALOG: Dict[str, Dict[str, Any]] = {
    'deepseek': {
        'label': 'DeepSeek',
        'capabilities': {
            'llm': {
                'provider_type': 'llm',
                'api_url': 'https://api.deepseek.com/chat/completions',
                'models_endpoint': 'https://api.deepseek.com/models',
                'executor_class': 'core.ai_client.openai_client.OpenAIClient',
                'model_filter': ['deepseek'],
                'recommended_patterns': ['deepseek-chat', 'deepseek-reasoner'],
                'configurable_api_url': True,
            },
        },
    },
    'volcengine': {
        'label': '火山引擎',
        'capabilities': {
            'llm': {
                'provider_type': 'llm',
                'api_url': 'https://ark.cn-beijing.volces.com/api/v3/chat/completions',
                'models_endpoint': 'https://ark.cn-beijing.volces.com/api/v3/models',
                'executor_class': 'core.ai_client.openai_client.OpenAIClient',
                'recommended_patterns': ['doubao', 'deepseek', 'seed'],
                'configurable_api_url': True,
            },
            'text2image': {
                'provider_type': 'text2image',
                'api_url': 'https://ark.cn-beijing.volces.com/api/v3/images/generations',
                'models_endpoint': 'https://ark.cn-beijing.volces.com/api/v3/models',
                'executor_class': 'core.ai_client.executors.openai_images_generation_executor.OpenAIImagesGenerationExecutor',
                'recommended_patterns': ['seedream', 'doubao'],
                'configurable_api_url': True,
            },
            'image_edit': {
                'provider_type': 'image_edit',
                'api_url': 'https://ark.cn-beijing.volces.com/api/v3/images/edits',
                'models_endpoint': 'https://ark.cn-beijing.volces.com/api/v3/models',
                'executor_class': 'core.ai_client.executors.openai_images_edit_executor.OpenAIImagesEditExecutor',
                'model_filter': ['edit'],
                'recommended_patterns': ['edit'],
                'configurable_api_url': True,
            },
            'image2video': {
                'provider_type': 'image2video',
                'api_url': 'https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks',
                'models_endpoint': 'https://ark.cn-beijing.volces.com/api/v3/models',
                'executor_class': 'core.ai_client.volcengine_image2video_client.VolcengineImage2VideoClient',
                'model_filter': ['video', 'seedance'],
                'recommended_patterns': ['seedance', 'video'],
                'configurable_api_url': True,
            },
        },
    },
    'dashscope': {
        'label': '阿里云百炼',
        'capabilities': {
            'llm': {
                'provider_type': 'llm',
                'api_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
                'models_endpoint': 'https://dashscope.aliyuncs.com/compatible-mode/v1/models',
                'executor_class': 'core.ai_client.openai_client.OpenAIClient',
                'model_filter': ['qwen'],
                'recommended_patterns': ['qwen-plus', 'qwen-max', 'qwen-turbo'],
                'configurable_api_url': True,
            },
            'text2image': {
                'provider_type': 'text2image',
                'api_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1/images/generations',
                'models_endpoint': 'https://dashscope.aliyuncs.com/compatible-mode/v1/models',
                'executor_class': 'core.ai_client.executors.openai_images_generation_executor.OpenAIImagesGenerationExecutor',
                'recommended_patterns': ['wanx', 'image'],
                'configurable_api_url': True,
            },
        },
    },
    'modelscope': {
        'label': 'ModelScope',
        'capabilities': {
            'llm': {
                'provider_type': 'llm',
                'api_url': 'https://api-inference.modelscope.cn/v1/chat/completions',
                'models_endpoint': 'https://api-inference.modelscope.cn/v1/models',
                'executor_class': 'core.ai_client.openai_client.OpenAIClient',
                'recommended_patterns': ['qwen', 'deepseek', 'glm', 'llama', 'kimi'],
                'configurable_api_url': True,
            },
        },
    },
    'newapi': {
        'label': 'New API 网关',
        'capabilities': {
            'llm': {
                'provider_type': 'llm',
                'api_url': 'https://your-newapi-server-address/v1/chat/completions',
                'models_endpoint': 'https://your-newapi-server-address/v1/models',
                'executor_class': 'core.ai_client.openai_client.OpenAIClient',
                'recommended_patterns': [],
                'configurable_api_url': True,
            },
            'text2image': {
                'provider_type': 'text2image',
                'api_url': 'https://your-newapi-server-address/v1/images/generations',
                'models_endpoint': 'https://your-newapi-server-address/v1/models',
                'executor_class': 'core.ai_client.executors.openai_images_generation_executor.OpenAIImagesGenerationExecutor',
                'recommended_patterns': ['flux', 'sdxl', 'wanx', 'gpt-image'],
                'configurable_api_url': True,
            },
            'image2video': {
                'provider_type': 'image2video',
                # New API 的统一任务接口（OpenAI Video Format）：POST /v1/videos
                # + GET /v1/videos/{id} + GET /v1/videos/{id}/content。
                # 若你的网关只暴露可灵/即梦原生的 /v1/video/generations，
                # 把 api_url 改成它即可，同时在 extra_config 里设
                # {"protocol": "video-generations"}。
                'api_url': 'https://your-newapi-server-address/v1/videos',
                'models_endpoint': 'https://your-newapi-server-address/v1/models',
                'executor_class': 'core.ai_client.newapi_image2video_client.NewApiImage2VideoClient',
                'recommended_patterns': ['veo', 'kling', 'wan', 'seedance', 'sora', 'vidu', 'jimeng', 'happyhorse'],
                'configurable_api_url': True,
                'default_extra_config': {
                    'protocol': 'v1-videos',
                    'image_field_style': 'auto',
                },
            },
        },
    },
    'openai': {
        'label': 'OpenAI',
        'capabilities': {
            'llm': {
                'provider_type': 'llm',
                'api_url': 'https://api.openai.com/v1/chat/completions',
                'models_endpoint': 'https://api.openai.com/v1/models',
                'executor_class': 'core.ai_client.openai_client.OpenAIClient',
                'model_filter': ['gpt', 'o1', 'o3', 'o4'],
                'recommended_patterns': ['gpt-4.1', 'gpt-4o', 'o3', 'o1'],
                'configurable_api_url': True,
            },
            'text2image': {
                'provider_type': 'text2image',
                'api_url': 'https://api.openai.com/v1/images/generations',
                'models_endpoint': 'https://api.openai.com/v1/models',
                'executor_class': 'core.ai_client.executors.openai_images_generation_executor.OpenAIImagesGenerationExecutor',
                'model_filter': ['gpt-image', 'dall-e'],
                'recommended_patterns': ['gpt-image-1', 'dall-e-3'],
                'configurable_api_url': True,
            },
            'image_edit': {
                'provider_type': 'image_edit',
                'api_url': 'https://api.openai.com/v1/images/edits',
                'models_endpoint': 'https://api.openai.com/v1/models',
                'executor_class': 'core.ai_client.executors.openai_images_edit_executor.OpenAIImagesEditExecutor',
                'model_filter': ['gpt-image', 'dall-e'],
                'recommended_patterns': ['gpt-image-1'],
                'configurable_api_url': True,
            },
        },
    },
    'gemini': {
        'label': 'Gemini',
        'capabilities': {
            'llm': {
                'provider_type': 'llm',
                'api_url': 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions',
                'models_endpoint': 'https://generativelanguage.googleapis.com/v1beta/openai/models',
                'executor_class': 'core.ai_client.openai_client.OpenAIClient',
                'model_filter': ['gemini'],
                'recommended_patterns': ['gemini-2.5-pro', 'gemini-2.5-flash', 'gemini-1.5-pro'],
                'configurable_api_url': True,
            },
            'text2image': {
                'provider_type': 'text2image',
                'api_url': 'https://generativelanguage.googleapis.com/v1beta/openai/images/generations',
                'models_endpoint': 'https://generativelanguage.googleapis.com/v1beta/openai/models',
                'executor_class': 'core.ai_client.executors.openai_images_generation_executor.OpenAIImagesGenerationExecutor',
                'model_filter': ['imagen', 'gemini'],
                'recommended_patterns': ['imagen-3'],
                'configurable_api_url': True,
            },
            'image2video': {
                'provider_type': 'image2video',
                'api_url': 'https://generativelanguage.googleapis.com/v1beta/openai/videos/generations',
                'models_endpoint': 'https://generativelanguage.googleapis.com/v1beta/openai/models',
                'executor_class': 'core.ai_client.image2video_client.VideoGeneratorClient',
                'model_filter': ['veo', 'gemini'],
                'recommended_patterns': ['veo-3', 'veo-2'],
                'configurable_api_url': True,
            },
        },
    },
    'grok': {
        'label': 'Grok',
        'capabilities': {
            'llm': {
                'provider_type': 'llm',
                'api_url': 'https://api.x.ai/v1/chat/completions',
                'models_endpoint': 'https://api.x.ai/v1/models',
                'executor_class': 'core.ai_client.openai_client.OpenAIClient',
                'model_filter': ['grok'],
                'recommended_patterns': ['grok-2', 'grok-beta'],
                'configurable_api_url': True,
            },
        },
    },
    'minimax': {
        'label': 'MiniMax',
        'capabilities': {
            'llm': {
                'provider_type': 'llm',
                # 国内站 api.minimaxi.com；国际站 api.minimax.io。
                # 走 OpenAI 兼容的 /v1/chat/completions（旧版 chatcompletion_v2
                # 的响应结构与 OpenAI 不一致，OpenAIClient 解析不了）。
                'api_url': 'https://api.minimaxi.com/v1/chat/completions',
                'models_endpoint': 'https://api.minimaxi.com/v1/models',
                'executor_class': 'core.ai_client.openai_client.OpenAIClient',
                'model_filter': ['minimax', 'abab'],
                'recommended_patterns': ['MiniMax-M3', 'MiniMax-M2.5'],
                'configurable_api_url': True,
            },
            'image2video': {
                'provider_type': 'image2video',
                # MiniMax H3 走 v2 任务接口：POST /v2/video_generation
                # + GET /v2/query/video_generation/{task_id}。
                # 旧版 Hailuo 2.3 把 api_url 换成
                # https://api.minimaxi.com/v1/video_generation，
                # 并在 extra_config 里设 {"api_version": "v1"}。
                'api_url': 'https://api.minimaxi.com/v2/video_generation',
                'models_endpoint': 'https://api.minimaxi.com/v1/models',
                'executor_class': 'core.ai_client.minimax_image2video_client.MinimaxImage2VideoClient',
                'model_filter': ['h3', 'hailuo', 'i2v'],
                'recommended_patterns': ['MiniMax-H3', 'MiniMax-H3-Max', 'MiniMax-Hailuo-2.3'],
                'configurable_api_url': True,
                'default_extra_config': {
                    'api_version': 'v2',
                    # H3 出片较慢，避免通用默认值 600 秒误杀
                    'max_wait_time': 1800,
                },
            },
        },
    },
    'siliconflow': {
        'label': '硅基流动',
        'capabilities': {
            'llm': {
                'provider_type': 'llm',
                'api_url': 'https://api.siliconflow.cn/v1/chat/completions',
                'models_endpoint': 'https://api.siliconflow.cn/v1/models',
                'executor_class': 'core.ai_client.openai_client.OpenAIClient',
                'recommended_patterns': ['deepseek', 'qwen', 'glm', 'kimi'],
                'configurable_api_url': True,
            },
        },
    },
    'openrouter': {
        'label': 'OpenRouter',
        'capabilities': {
            'llm': {
                'provider_type': 'llm',
                'api_url': 'https://openrouter.ai/api/v1/chat/completions',
                'models_endpoint': 'https://openrouter.ai/api/v1/models',
                'executor_class': 'core.ai_client.openai_client.OpenAIClient',
                'recommended_patterns': ['grok', 'deepseek', 'qwen', 'claude', 'gemini'],
                'configurable_api_url': True,
            },
        },
    },
    'zhipu': {
        'label': '智谱 AI',
        'capabilities': {
            'llm': {
                'provider_type': 'llm',
                'api_url': 'https://open.bigmodel.cn/api/paas/v4/chat/completions',
                'models_endpoint': 'https://open.bigmodel.cn/api/paas/v4/models',
                'executor_class': 'core.ai_client.openai_client.OpenAIClient',
                'model_filter': ['glm'],
                'recommended_patterns': ['glm-4', 'glm-4.5'],
                'configurable_api_url': True,
            },
        },
    },
    'moonshot': {
        'label': 'Moonshot',
        'capabilities': {
            'llm': {
                'provider_type': 'llm',
                'api_url': 'https://api.moonshot.cn/v1/chat/completions',
                'models_endpoint': 'https://api.moonshot.cn/v1/models',
                'executor_class': 'core.ai_client.openai_client.OpenAIClient',
                'model_filter': ['moonshot', 'kimi'],
                'recommended_patterns': ['moonshot-v1', 'kimi'],
                'configurable_api_url': True,
            },
        },
    },
}
