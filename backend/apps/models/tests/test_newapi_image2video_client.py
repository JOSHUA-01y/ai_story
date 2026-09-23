"""New API 网关图生视频执行器测试。"""

from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from core.ai_client.newapi_image2video_client import NewApiImage2VideoClient

POST_RETRY_PATH = 'core.ai_client.newapi_image2video_client.post_with_retry'
GET_RETRY_PATH = 'core.ai_client.newapi_image2video_client.get_with_retry'
LOCALIZE_PATH = (
    'core.ai_client.newapi_image2video_client.NewApiImage2VideoClient._localize_video_data'
)


def _response(payload, status_code=200):
    """构造一个假的 requests.Response。"""
    response = Mock()
    response.status_code = status_code
    response.json.return_value = payload
    response.text = str(payload)
    return response


class NewApiUrlTestCase(SimpleTestCase):
    def test_endpoint_normalizes_base_url_variants(self):
        variants = [
            'https://newapi.example.com',
            'https://newapi.example.com/',
            'https://newapi.example.com/v1',
            'https://newapi.example.com/v1/videos',
            'https://newapi.example.com/v1/video/generations',
            'https://newapi.example.com/v1/videos/generations',
        ]

        for api_url in variants:
            with self.subTest(api_url=api_url):
                client = NewApiImage2VideoClient(api_url=api_url, api_key='sk-test')
                self.assertEqual(client._build_create_video_url(), 'https://newapi.example.com/v1/videos')
                self.assertEqual(
                    client._build_task_status_url('abc'),
                    'https://newapi.example.com/v1/videos/abc',
                )

    def test_endpoint_keeps_path_prefix(self):
        client = NewApiImage2VideoClient(api_url='https://host.example.com/gateway/v1')
        self.assertEqual(client._build_create_video_url(), 'https://host.example.com/gateway/v1/videos')

    def test_video_generations_protocol_endpoints(self):
        client = NewApiImage2VideoClient(
            api_url='https://newapi.example.com',
            api_key='sk-test',
            protocol='kling',
        )
        self.assertEqual(client.protocol, 'video-generations')
        self.assertEqual(
            client._build_create_video_url(),
            'https://newapi.example.com/v1/video/generations',
        )
        self.assertEqual(
            client._build_task_status_url('task-1'),
            'https://newapi.example.com/v1/video/generations/task-1',
        )


class NewApiV1VideosTestCase(SimpleTestCase):
    @patch(LOCALIZE_PATH, side_effect=lambda data, timeout: data)
    @patch(GET_RETRY_PATH)
    @patch(POST_RETRY_PATH)
    def test_seedance_payload_and_polling(self, mock_post, mock_get, mock_localize):
        mock_post.return_value = _response({'id': 'task-123', 'status': 'processing'})
        mock_get.return_value = _response(
            {'id': 'task-123', 'status': 'succeeded', 'url': 'https://cdn.example.com/out.mp4'}
        )

        client = NewApiImage2VideoClient(
            api_url='https://newapi.example.com',
            api_key='sk-test',
            model_name='doubao-seedance-2-0-260128',
            timeout=30,
        )

        result = client._generate_video(
            prompt='让这张图片动起来',
            image_base64='ZmFrZV9pbWFnZQ==',
            duration_seconds=5,
            aspect_ratio='16:9',
            resolution='1080p',
            poll_interval=0,
            max_wait_time=30,
        )

        self.assertTrue(result['success'])
        self.assertEqual(result['data'], [{'url': 'https://cdn.example.com/out.mp4'}])
        self.assertEqual(result['metadata']['task_id'], 'task-123')
        self.assertEqual(result['metadata']['protocol'], 'v1-videos')

        # 提交地址与请求体
        self.assertEqual(mock_post.call_args.args[0], 'https://newapi.example.com/v1/videos')
        payload = mock_post.call_args.kwargs['json']
        self.assertEqual(payload['model'], 'doubao-seedance-2-0-260128')
        self.assertEqual(payload['seconds'], '5')
        self.assertEqual(payload['metadata']['ratio'], '16:9')
        self.assertEqual(payload['metadata']['resolution'], '1080p')
        content = payload['metadata']['content']
        self.assertEqual(content[0]['role'], 'first_frame')
        self.assertEqual(
            content[0]['image_url']['url'],
            'data:image/jpeg;base64,ZmFrZV9pbWFnZQ==',
        )

        # 轮询地址
        self.assertEqual(mock_get.call_args.args[0], 'https://newapi.example.com/v1/videos/task-123')
        self.assertEqual(
            mock_get.call_args.kwargs['headers']['Authorization'],
            'Bearer sk-test',
        )

    @patch(LOCALIZE_PATH, side_effect=lambda data, timeout: data)
    @patch(GET_RETRY_PATH)
    @patch(POST_RETRY_PATH)
    def test_wan_style_puts_image_into_metadata_img_url(self, mock_post, mock_get, mock_localize):
        mock_post.return_value = _response({'id': 'task-wan', 'status': 'processing'})
        mock_get.return_value = _response(
            {'id': 'task-wan', 'status': 'succeeded', 'url': 'https://cdn.example.com/wan.mp4'}
        )

        client = NewApiImage2VideoClient(
            api_url='https://newapi.example.com',
            api_key='sk-test',
            model_name='wan2.5-i2v-preview',
        )
        result = client._generate_video(
            prompt='让这张图片动起来',
            image_uri='https://cdn.example.com/in.png',
            poll_interval=0,
        )

        self.assertTrue(result['success'])
        payload = mock_post.call_args.kwargs['json']
        self.assertEqual(payload['metadata']['img_url'], 'https://cdn.example.com/in.png')
        self.assertNotIn('images', payload)
        self.assertEqual(payload['size'], '1280x720')

    @patch(LOCALIZE_PATH, side_effect=lambda data, timeout: data)
    @patch(GET_RETRY_PATH)
    @patch(POST_RETRY_PATH)
    def test_happyhorse_style_uses_top_level_images(self, mock_post, mock_get, mock_localize):
        mock_post.return_value = _response({'id': 'task-hh', 'status': 'processing'})
        mock_get.return_value = _response(
            {'id': 'task-hh', 'status': 'succeeded', 'url': 'https://cdn.example.com/hh.mp4'}
        )

        client = NewApiImage2VideoClient(
            api_url='https://newapi.example.com',
            api_key='sk-test',
            model_name='happyhorse-1.1-i2v',
        )
        client._generate_video(
            prompt='缓慢转身',
            image_uri='https://cdn.example.com/in.png',
            duration_seconds=5,
            poll_interval=0,
        )

        payload = mock_post.call_args.kwargs['json']
        self.assertEqual(payload['images'], ['https://cdn.example.com/in.png'])
        self.assertNotIn('metadata', payload)

    @patch(LOCALIZE_PATH, side_effect=lambda data, timeout: data)
    @patch(GET_RETRY_PATH)
    @patch(POST_RETRY_PATH)
    def test_falls_back_to_content_endpoint_when_no_direct_url(self, mock_post, mock_get, mock_localize):
        mock_post.return_value = _response({'id': 'task-content', 'status': 'processing'})
        mock_get.return_value = _response({'id': 'task-content', 'status': 'succeeded'})

        client = NewApiImage2VideoClient(
            api_url='https://newapi.example.com',
            api_key='sk-test',
            model_name='sora-2',
        )
        result = client._generate_video(prompt='太空漫步', poll_interval=0)

        self.assertTrue(result['success'])
        item = result['data'][0]
        self.assertEqual(
            item['content_url'],
            'https://newapi.example.com/v1/videos/task-content/content',
        )
        self.assertEqual(item['download_headers']['Authorization'], 'Bearer sk-test')

    @patch(LOCALIZE_PATH, side_effect=lambda data, timeout: data)
    @patch(POST_RETRY_PATH)
    def test_multipart_request_sends_input_reference_file(self, mock_post, mock_localize):
        mock_post.return_value = _response(
            {'id': 'task-mp', 'status': 'succeeded', 'url': 'https://cdn.example.com/mp.mp4'}
        )

        client = NewApiImage2VideoClient(
            api_url='https://newapi.example.com',
            api_key='sk-test',
            model_name='sora-2',
            request_format='multipart',
        )
        result = client._generate_video(prompt='猫咪睁眼', image_base64='ZmFrZQ==', poll_interval=0)

        self.assertTrue(result['success'])
        files = mock_post.call_args.kwargs['files']
        self.assertIn('input_reference', files)
        self.assertEqual(files['input_reference'][1], b'fake')
        # multipart 不能带 Content-Type，否则 boundary 丢失
        self.assertNotIn('Content-Type', mock_post.call_args.kwargs['headers'])
        self.assertEqual(mock_post.call_args.kwargs['data']['seconds'], '5')

    @patch(LOCALIZE_PATH, side_effect=lambda data, timeout: data)
    @patch(GET_RETRY_PATH)
    @patch(POST_RETRY_PATH)
    def test_failed_task_returns_error(self, mock_post, mock_get, mock_localize):
        mock_post.return_value = _response({'id': 'task-fail', 'status': 'processing'})
        mock_get.return_value = _response(
            {'id': 'task-fail', 'status': 'failed', 'error': {'message': '内容审核不通过'}}
        )

        client = NewApiImage2VideoClient(
            api_url='https://newapi.example.com',
            api_key='sk-test',
            model_name='kling-v1',
        )
        result = client._generate_video(prompt='测试', poll_interval=0)

        self.assertFalse(result['success'])
        self.assertIn('内容审核不通过', result['error'])

    @patch(POST_RETRY_PATH)
    def test_http_error_exposes_gateway_message(self, mock_post):
        mock_post.return_value = _response(
            {'error': {'message': 'model not found', 'type': 'invalid_request_error'}},
            status_code=404,
        )

        client = NewApiImage2VideoClient(
            api_url='https://newapi.example.com',
            api_key='sk-test',
            model_name='not-exist',
        )
        result = client._generate_video(prompt='测试', poll_interval=0)

        self.assertFalse(result['success'])
        self.assertIn('404', result['error'])
        self.assertIn('model not found', result['error'])


class NewApiVideoGenerationsTestCase(SimpleTestCase):
    @patch(LOCALIZE_PATH, side_effect=lambda data, timeout: data)
    @patch(GET_RETRY_PATH)
    @patch(POST_RETRY_PATH)
    def test_kling_protocol_payload_and_polling(self, mock_post, mock_get, mock_localize):
        mock_post.return_value = _response({'task_id': 'kling-task', 'status': 'processing'})
        mock_get.return_value = _response(
            {'task_id': 'kling-task', 'status': 'succeeded', 'url': 'https://cdn.example.com/kling.mp4'}
        )

        client = NewApiImage2VideoClient(
            api_url='https://newapi.example.com',
            api_key='sk-test',
            model_name='kling-v1',
            protocol='video-generations',
        )
        result = client._generate_video(
            prompt='宇航员在月球行走',
            image_uri='https://cdn.example.com/in.png',
            duration_seconds=5,
            aspect_ratio='16:9',
            negative_prompt='模糊',
            poll_interval=0,
        )

        self.assertTrue(result['success'])
        self.assertEqual(result['data'], [{'url': 'https://cdn.example.com/kling.mp4'}])

        self.assertEqual(
            mock_post.call_args.args[0],
            'https://newapi.example.com/v1/video/generations',
        )
        payload = mock_post.call_args.kwargs['json']
        self.assertEqual(payload['image'], 'https://cdn.example.com/in.png')
        self.assertEqual(payload['duration'], 5)
        self.assertEqual(payload['metadata']['negative_prompt'], '模糊')
        self.assertEqual(payload['metadata']['image_urls'], ['https://cdn.example.com/in.png'])

        self.assertEqual(
            mock_get.call_args.args[0],
            'https://newapi.example.com/v1/video/generations/kling-task',
        )

    @patch(LOCALIZE_PATH, side_effect=lambda data, timeout: data)
    @patch(GET_RETRY_PATH)
    @patch(POST_RETRY_PATH)
    def test_local_image_is_inlined_as_data_url(self, mock_post, mock_get, mock_localize):
        mock_post.return_value = _response({'task_id': 'k2', 'status': 'processing'})
        mock_get.return_value = _response(
            {'task_id': 'k2', 'status': 'succeeded', 'url': 'https://cdn.example.com/k2.mp4'}
        )

        client = NewApiImage2VideoClient(
            api_url='https://newapi.example.com',
            api_key='sk-test',
            model_name='kling-v1',
            protocol='video-generations',
        )
        client._generate_video(
            prompt='测试',
            image_base64='bG9jYWw=',
            duration_seconds=5,
            poll_interval=0,
        )

        payload = mock_post.call_args.kwargs['json']
        self.assertEqual(payload['image'], 'data:image/jpeg;base64,bG9jYWw=')

    @patch(LOCALIZE_PATH, side_effect=lambda data, timeout: data)
    @patch(GET_RETRY_PATH)
    @patch(POST_RETRY_PATH)
    def test_sync_result_without_task_id_is_returned_directly(self, mock_post, mock_get, mock_localize):
        mock_post.return_value = _response(
            {'data': [{'url': 'https://cdn.example.com/sync.mp4'}]}
        )

        client = NewApiImage2VideoClient(
            api_url='https://newapi.example.com',
            api_key='sk-test',
            model_name='kling-v1',
            protocol='video-generations',
        )
        result = client._generate_video(prompt='测试', poll_interval=0)

        self.assertTrue(result['success'])
        self.assertEqual(result['data'], [{'url': 'https://cdn.example.com/sync.mp4'}])
        mock_get.assert_not_called()
