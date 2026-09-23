"""MiniMax（海螺）图生视频执行器单元测试。"""

from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from apps.models.models import ModelProvider
from apps.models.vendor_catalog import VENDOR_CATALOG
from core.ai_client.minimax_image2video_client import (
    PROTOCOL_V1,
    PROTOCOL_V2,
    MinimaxImage2VideoClient,
)

LOCALIZE_PATH = (
    'core.ai_client.minimax_image2video_client.'
    'MinimaxImage2VideoClient._localize_video_data'
)
POST_PATH = 'core.ai_client.minimax_image2video_client.post_with_retry'
GET_PATH = 'core.ai_client.minimax_image2video_client.get_with_retry'


def _response(payload, status_code=200):
    """构造一个最小可用的 requests 响应替身。"""
    response = Mock()
    response.status_code = status_code
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    return response


class MinimaxV2ProtocolTestCase(SimpleTestCase):
    @patch(LOCALIZE_PATH, side_effect=lambda data, timeout: data)
    @patch(GET_PATH)
    @patch(POST_PATH)
    def test_generate_video_uses_h3_v2_task_api(self, mock_post, mock_get, mock_localize):
        mock_post.return_value = _response(
            {'task_id': '424010985738629', 'base_resp': {'status_code': 0, 'status_msg': 'success'}}
        )
        mock_get.return_value = _response(
            {
                'task': {
                    'id': '424010985738629',
                    'model': 'MiniMax-H3',
                    'status': 'succeeded',
                    'content': {'url': 'https://cdn.example.com/h3-out.mp4'},
                    'resolution': '2K',
                    'duration': 5,
                    'usage': {'output_seconds': 5, 'total_seconds': 5},
                }
            }
        )

        client = MinimaxImage2VideoClient(
            api_url='https://api.minimaxi.com/v2/video_generation',
            api_token='secret',
            model='MiniMax-H3',
        )

        result = client._generate_video(
            prompt='海边打球的男孩',
            image_base64='ZmFrZV9pbWFnZQ==',
            duration_seconds=5,
            aspect_ratio='16:9',
            resolution='1080p',
            poll_interval=0,
            max_wait_time=10,
        )

        self.assertTrue(result['success'])
        self.assertEqual(result['data'][0]['url'], 'https://cdn.example.com/h3-out.mp4')
        self.assertEqual(result['metadata']['task_id'], '424010985738629')
        self.assertEqual(result['metadata']['usage']['output_seconds'], 5)

        self.assertEqual(
            mock_post.call_args.args[0],
            'https://api.minimaxi.com/v2/video_generation',
        )
        payload = mock_post.call_args.kwargs['json']
        self.assertEqual(payload['model'], 'MiniMax-H3')
        self.assertEqual(payload['resolution'], '2K')
        self.assertEqual(payload['duration'], 5)
        self.assertEqual(payload['ratio'], 'adaptive')
        self.assertEqual(payload['content'][0]['type'], 'text')
        self.assertEqual(payload['content'][1]['role'], 'first_frame')
        self.assertEqual(
            payload['content'][1]['image_url']['url'],
            'data:image/jpeg;base64,ZmFrZV9pbWFnZQ==',
        )

        self.assertEqual(
            mock_get.call_args.args[0],
            'https://api.minimaxi.com/v2/query/video_generation/424010985738629',
        )

    def test_build_v2_content_assigns_frame_and_reference_roles(self):
        content = MinimaxImage2VideoClient._build_v2_content('p', ['a'], 'image/jpeg')
        self.assertEqual([item.get('role') for item in content if item['type'] == 'image_url'], ['first_frame'])

        content = MinimaxImage2VideoClient._build_v2_content('p', ['a', 'b'], 'image/jpeg')
        self.assertEqual(
            [item.get('role') for item in content if item['type'] == 'image_url'],
            ['first_frame', 'last_frame'],
        )

        content = MinimaxImage2VideoClient._build_v2_content('p', ['a', 'b', 'c'], 'image/jpeg')
        self.assertEqual(
            [item.get('role') for item in content if item['type'] == 'image_url'],
            ['reference_image', 'reference_image', 'reference_image'],
        )

    def test_text_to_video_uses_concrete_ratio(self):
        client = MinimaxImage2VideoClient(
            api_url='https://api.minimaxi.com/v2/video_generation',
            api_token='secret',
            model='MiniMax-H3',
        )

        payload = client._build_payload(
            prompt='一只猫在跳舞',
            aspect_ratio='adaptive',
            duration_seconds=5,
        )

        self.assertEqual(payload['ratio'], '16:9')
        self.assertEqual(len(payload['content']), 1)
        self.assertEqual(payload['content'][0]['type'], 'text')

    def test_h3_resolution_and_duration_are_clamped(self):
        client = MinimaxImage2VideoClient(api_token='secret', model='MiniMax-H3')

        self.assertEqual(client._resolve_resolution('720p'), '768P')
        self.assertEqual(client._resolve_resolution('1080p'), '2K')
        self.assertEqual(client._resolve_resolution(''), '768P')
        self.assertEqual(client._resolve_duration(3, '768P'), 4)
        self.assertEqual(client._resolve_duration(20, '768P'), 15)

        h3_max = MinimaxImage2VideoClient(api_token='secret', model='MiniMax-H3-Max')
        self.assertEqual(h3_max._resolve_resolution('2K'), '768P')
        self.assertEqual(h3_max._resolve_duration(4, '768P'), 5)

    def test_base_resp_error_is_mapped_to_readable_message(self):
        client = MinimaxImage2VideoClient(api_token='secret', model='MiniMax-H3')

        with self.assertRaises(RuntimeError) as ctx:
            client._check_base_resp({'base_resp': {'status_code': 1008, 'status_msg': 'insufficient balance'}})

        self.assertIn('余额不足', str(ctx.exception))

    @patch(LOCALIZE_PATH, side_effect=lambda data, timeout: data)
    @patch(GET_PATH)
    @patch(POST_PATH)
    def test_balance_error_returns_failed_result(self, mock_post, mock_get, mock_localize):
        mock_post.return_value = _response(
            {'base_resp': {'status_code': 1008, 'status_msg': 'insufficient balance'}}
        )

        client = MinimaxImage2VideoClient(api_token='secret', model='MiniMax-H3')
        result = client._generate_video(prompt='测试', image_base64='ZmFrZQ==', poll_interval=0)

        self.assertFalse(result['success'])
        self.assertIn('余额不足', result['error'])
        mock_get.assert_not_called()

    @patch(LOCALIZE_PATH, side_effect=lambda data, timeout: data)
    @patch(GET_PATH)
    @patch(POST_PATH)
    def test_http_error_body_is_surfaced(self, mock_post, mock_get, mock_localize):
        mock_post.return_value = _response(
            {
                'type': 'error',
                'error': {
                    'type': 'insufficient_balance_error',
                    'message': 'insufficient balance (1008)',
                    'http_code': '402',
                },
            },
            status_code=402,
        )

        client = MinimaxImage2VideoClient(api_token='secret', model='MiniMax-H3')
        result = client._generate_video(prompt='测试', image_base64='ZmFrZQ==', poll_interval=0)

        self.assertFalse(result['success'])
        self.assertIn('余额不足', result['error'])
        self.assertIn('insufficient balance (1008)', result['error'])

    @patch(LOCALIZE_PATH, side_effect=lambda data, timeout: data)
    @patch(GET_PATH)
    @patch(POST_PATH)
    def test_failed_task_surfaces_sensitive_content_reason(self, mock_post, mock_get, mock_localize):
        mock_post.return_value = _response({'task_id': 'task-1'})
        mock_get.return_value = _response(
            {
                'task': {
                    'id': 'task-1',
                    'status': 'failed',
                    'error': {'code': '1026', 'message': 'video description contains sensitive content'},
                }
            }
        )

        client = MinimaxImage2VideoClient(api_token='secret', model='MiniMax-H3')
        result = client._generate_video(prompt='测试', image_base64='ZmFrZQ==', poll_interval=0)

        self.assertFalse(result['success'])
        self.assertIn('内容审核', result['error'])
        self.assertIn('sensitive content', result['error'])

    def test_identity_policy_rejection_gets_readable_hint(self):
        client = MinimaxImage2VideoClient(api_token='secret', model='MiniMax-H3')

        message = client._extract_error_message(
            {
                'task': {
                    'id': 'task-1',
                    'status': 'failed',
                    'error': {
                        'code': '2304',
                        'message': 'prompt rejected by identity policy',
                    },
                }
            }
        )

        self.assertIn('身份/肖像策略', message)
        self.assertIn('prompt rejected by identity policy', message)

    def test_h3_max_wait_time_floor_is_raised(self):
        client = MinimaxImage2VideoClient(api_token='secret', model='MiniMax-H3')
        self.assertEqual(client._resolve_max_wait_time(600), 900)
        self.assertEqual(client._resolve_max_wait_time(1800), 1800)

        legacy = MinimaxImage2VideoClient(api_token='secret', model='MiniMax-Hailuo-2.3')
        self.assertEqual(legacy._resolve_max_wait_time(600), 600)


class MinimaxV1ProtocolTestCase(SimpleTestCase):
    @patch(LOCALIZE_PATH, side_effect=lambda data, timeout: data)
    @patch(GET_PATH)
    @patch(POST_PATH)
    def test_generate_video_uses_legacy_hailuo_api(self, mock_post, mock_get, mock_localize):
        mock_post.return_value = _response(
            {'task_id': '106916112212032', 'base_resp': {'status_code': 0, 'status_msg': 'success'}}
        )
        mock_get.side_effect = [
            _response(
                {
                    'task_id': '106916112212032',
                    'status': 'Success',
                    'file_id': '205258526306433',
                    'base_resp': {'status_code': 0, 'status_msg': 'success'},
                }
            ),
            _response(
                {
                    'file': {'file_id': '205258526306433', 'download_url': 'https://cdn.example.com/legacy.mp4'},
                    'base_resp': {'status_code': 0, 'status_msg': 'success'},
                }
            ),
        ]

        client = MinimaxImage2VideoClient(
            api_url='https://api.minimaxi.com/v1/video_generation',
            api_token='secret',
            model='MiniMax-Hailuo-2.3',
        )

        result = client._generate_video(
            prompt='小狗在草地上奔跑',
            image_base64='ZmFrZV9pbWFnZQ==',
            duration_seconds=10,
            resolution='768p',
            poll_interval=0,
        )

        self.assertTrue(result['success'])
        self.assertEqual(result['data'][0]['url'], 'https://cdn.example.com/legacy.mp4')

        self.assertEqual(client._protocol(), PROTOCOL_V1)
        self.assertEqual(
            mock_post.call_args.args[0],
            'https://api.minimaxi.com/v1/video_generation',
        )
        payload = mock_post.call_args.kwargs['json']
        self.assertEqual(payload['duration'], 10)
        self.assertEqual(payload['resolution'], '768P')
        self.assertTrue(payload['prompt_optimizer'])
        self.assertEqual(payload['first_frame_image'], 'data:image/jpeg;base64,ZmFrZV9pbWFnZQ==')

        self.assertEqual(
            mock_get.call_args_list[0].args[0],
            'https://api.minimaxi.com/v1/query/video_generation?task_id=106916112212032',
        )
        self.assertEqual(
            mock_get.call_args_list[1].args[0],
            'https://api.minimaxi.com/v1/files/retrieve',
        )
        self.assertEqual(
            mock_get.call_args_list[1].kwargs['params'],
            {'file_id': '205258526306433'},
        )

    def test_legacy_duration_and_resolution_are_snapped(self):
        client = MinimaxImage2VideoClient(api_token='secret', model='MiniMax-Hailuo-2.3')

        self.assertEqual(client._resolve_duration(5, '768P'), 6)
        self.assertEqual(client._resolve_duration(8, '768P'), 10)
        self.assertEqual(client._resolve_duration(10, '1080P'), 6)
        self.assertEqual(client._resolve_resolution('2k'), '1080P')
        self.assertEqual(client._resolve_resolution('1080p'), '1080P')

    def test_hailuo_camera_description_is_mapped_to_official_commands(self):
        client = MinimaxImage2VideoClient(api_token='secret', model='MiniMax-Hailuo-2.3')

        prompt = client._build_prompt('人物特写', '镜头缓慢推进，最后静止', 'MiniMax-Hailuo-2.3')

        self.assertIn('[Push in]', prompt)
        self.assertIn('[Static shot]', prompt)
        self.assertIn('人物特写', prompt)

    def test_h3_keeps_natural_language_camera_description(self):
        client = MinimaxImage2VideoClient(api_token='secret', model='MiniMax-H3')

        prompt = client._build_prompt('人物特写', '镜头缓慢推进，最后静止', 'MiniMax-H3')

        self.assertIn('镜头缓慢推进，最后静止', prompt)
        self.assertNotIn('[Push in]', prompt)

    def test_empty_prompt_falls_back_for_v2_required_text(self):
        client = MinimaxImage2VideoClient(api_token='secret', model='MiniMax-H3')

        payload = client._build_payload(prompt='', image_base64='ZmFrZQ==', duration_seconds=5)

        self.assertTrue(payload['content'][0]['text'])
        self.assertEqual(client._protocol(), PROTOCOL_V2)


class MinimaxProtocolResolutionTestCase(SimpleTestCase):
    def test_protocol_is_resolved_from_model_and_url(self):
        from_model = MinimaxImage2VideoClient(api_token='secret', model='MiniMax-H3-Max')
        self.assertEqual(from_model._protocol(), PROTOCOL_V2)

        from_legacy_model = MinimaxImage2VideoClient(api_token='secret', model='MiniMax-Hailuo-02')
        self.assertEqual(from_legacy_model._protocol(), PROTOCOL_V1)

        from_url = MinimaxImage2VideoClient(
            api_url='https://api.minimaxi.com/v1/video_generation',
            api_token='secret',
            model='MiniMax-H3',
        )
        self.assertEqual(from_url._protocol(), PROTOCOL_V1)

        explicit = MinimaxImage2VideoClient(
            api_url='https://api.minimaxi.com',
            api_token='secret',
            model='MiniMax-H3',
            api_version='v1',
        )
        self.assertEqual(explicit._protocol(), PROTOCOL_V1)

    def test_api_base_strips_known_suffixes(self):
        v2 = MinimaxImage2VideoClient(
            api_url='https://api.minimaxi.com/v2/video_generation',
            api_token='secret',
            model='MiniMax-H3',
        )
        self.assertEqual(v2._api_base(), 'https://api.minimaxi.com')

        v1 = MinimaxImage2VideoClient(
            api_url='https://api.minimaxi.com/v1/query/video_generation',
            api_token='secret',
            model='MiniMax-Hailuo-2.3',
        )
        self.assertEqual(v1._api_base(), 'https://api.minimaxi.com')
        self.assertEqual(v1._build_create_video_url(), 'https://api.minimaxi.com/v1/video_generation')

    def test_default_api_base_is_domestic_site(self):
        client = MinimaxImage2VideoClient(api_token='secret', model='MiniMax-H3')
        self.assertEqual(client._build_create_video_url(), 'https://api.minimaxi.com/v2/video_generation')


class MinimaxRegistrationTestCase(SimpleTestCase):
    def test_executor_is_registered_for_image2video(self):
        executor_values = [value for value, _ in ModelProvider.IMAGE2VIDEO_EXECUTORS]
        self.assertIn(
            'core.ai_client.minimax_image2video_client.MinimaxImage2VideoClient',
            executor_values,
        )

    def test_default_executor_is_video_generator_client(self):
        provider = ModelProvider(
            name='MiniMax H3',
            provider_type='image2video',
            api_url='https://api.minimaxi.com/v2/video_generation',
            api_key='secret',
            model_name='MiniMax-H3',
        )
        self.assertEqual(
            provider.get_default_executor(),
            'core.ai_client.image2video_client.VideoGeneratorClient',
        )

    def test_vendor_catalog_exposes_minimax_image2video(self):
        capability = VENDOR_CATALOG['minimax']['capabilities']['image2video']

        self.assertEqual(capability['provider_type'], 'image2video')
        self.assertEqual(capability['api_url'], 'https://api.minimaxi.com/v2/video_generation')
        self.assertEqual(
            capability['executor_class'],
            'core.ai_client.minimax_image2video_client.MinimaxImage2VideoClient',
        )
        self.assertEqual(capability['default_extra_config']['api_version'], 'v2')
        self.assertIn('MiniMax-H3', capability['recommended_patterns'])

    def test_vendor_catalog_minimax_llm_uses_openai_compatible_endpoint(self):
        capability = VENDOR_CATALOG['minimax']['capabilities']['llm']
        self.assertEqual(capability['api_url'], 'https://api.minimaxi.com/v1/chat/completions')
