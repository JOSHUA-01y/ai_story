from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from core.ai_client.executors.chat_completions_image_executor import ChatCompletionsImageExecutor
from core.ai_client.executors.openai_images_generation_executor import (
    OpenAIImagesGenerationExecutor,
)
from core.ai_client.schemas import Text2ImageRequest
from core.ai_client.text2image_client import Text2ImageClient


class OpenAIImagesGenerationExecutorWatermarkTestCase(SimpleTestCase):
    @patch('core.ai_client.executors.openai_images_generation_executor.post_with_retry')
    @patch(
        'core.ai_client.executors.openai_images_generation_executor.localize_image_item',
        side_effect=lambda item, width, height, timeout: {'url': item['url']},
    )
    def test_watermark_from_extra_config_is_forwarded(self, mock_localize, mock_post):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {'data': [{'url': 'https://example.com/a.png'}]}
        mock_post.return_value = response

        executor = OpenAIImagesGenerationExecutor(
            api_url='https://ark.cn-beijing.volces.com/api/v3/images/generations',
            api_key='test-key',
            model_name='doubao-seedream-5-0-260128',
            watermark=False,
        )

        executor.generate(prompt='写实剧照', width=1024, height=1024)

        self.assertFalse(mock_post.call_args.kwargs['json']['watermark'])

    @patch('core.ai_client.executors.openai_images_generation_executor.post_with_retry')
    @patch(
        'core.ai_client.executors.openai_images_generation_executor.'
        'localize_image_item',
        side_effect=lambda item, width, height, timeout: {'url': item['url']},
    )
    def test_watermark_absent_by_default(self, mock_localize, mock_post):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {'data': [{'url': 'https://example.com/a.png'}]}
        mock_post.return_value = response

        executor = OpenAIImagesGenerationExecutor(
            api_url='https://ark.cn-beijing.volces.com/api/v3/images/generations',
            api_key='test-key',
            model_name='doubao-seedream-5-0-260128',
        )

        executor.generate(prompt='写实剧照', width=1024, height=1024)

        self.assertNotIn('watermark', mock_post.call_args.kwargs['json'])

    @patch('core.ai_client.executors.openai_images_generation_executor.post_with_retry')
    @patch(
        'core.ai_client.executors.openai_images_generation_executor.'
        'localize_image_item',
        side_effect=lambda item, width, height, timeout: {'url': item['url']},
    )
    def test_request_extra_beats_extra_config(self, mock_localize, mock_post):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {'data': [{'url': 'https://example.com/a.png'}]}
        mock_post.return_value = response

        executor = OpenAIImagesGenerationExecutor(
            api_url='https://ark.cn-beijing.volces.com/api/v3/images/generations',
            api_key='test-key',
            model_name='doubao-seedream-5-0-260128',
            watermark=False,
        )

        executor._run_request(
            Text2ImageRequest(prompt='写实剧照', extra={'watermark': True}),
        )

        self.assertTrue(mock_post.call_args.kwargs['json']['watermark'])


class Text2ImageClientTestCase(SimpleTestCase):
    @patch('core.ai_client.text2image_client.requests.post')
    def test_images_generations_endpoint_includes_image_array(self, mock_post):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'created': 123,
            'usage': {},
            'data': [
                {'url': 'https://example.com/generated.png'}
            ],
        }
        mock_post.return_value = mock_response

        client = Text2ImageClient(
            api_url='https://ark.cn-beijing.volces.com/api/v3/images/generations',
            api_key='test-key',
            model_name='doubao-seedream-5-0-250428',
        )

        with patch.object(client, '_localize_image_item', side_effect=lambda item, width, height, timeout: {
            'url': item['url'],
            'width': width,
            'height': height,
        }):
            result = client.generate(
                prompt='请参考图1生成新图',
                image=['data:image/png;base64,ZmFrZV9pbWFnZQ=='],
                width=1024,
                height=1024,
            )

        self.assertTrue(result.success)
        payload = mock_post.call_args.kwargs['json']
        self.assertEqual(payload['prompt'], '请参考图1生成新图')
        self.assertEqual(payload['image'], ['data:image/png;base64,ZmFrZV9pbWFnZQ=='])
        self.assertEqual(result.metadata['input_image_count'], 1)

    @patch('core.ai_client.image_result_utils.image_storage.save_file')
    @patch('core.ai_client.executors.chat_completions_image_executor.requests.post')
    def test_chat_completions_executor_parses_markdown_data_url_image(self, mock_post, mock_save_file):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'choices': [
                {
                    'message': {
                        'content': '![image_1](data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==)'
                    }
                }
            ],
            'usage': {},
        }
        mock_post.return_value = mock_response
        mock_save_file.return_value = (Mock(), '2026-06-05/generated.jpg')

        client = ChatCompletionsImageExecutor(
            api_url='https://example.com/v1/chat/completions',
            api_key='test-key',
            model_name='gpt-image-2',
        )

        result = client.generate(prompt='生成一张图', width=1024, height=1024)

        self.assertTrue(result.success)
        self.assertEqual(result.text, '/api/v1/content/storage/image/2026-06-05/generated.jpg')
        self.assertEqual(result.data[0]['url'], '/api/v1/content/storage/image/2026-06-05/generated.jpg')
        self.assertEqual(result.data[0]['width'], 1024)
        self.assertEqual(result.data[0]['height'], 1024)
        self.assertEqual(mock_save_file.call_args.kwargs['filename'].split('.')[-1], 'jpg')
        self.assertEqual(mock_save_file.call_args.kwargs['content'][:3], b'\xff\xd8\xff')
