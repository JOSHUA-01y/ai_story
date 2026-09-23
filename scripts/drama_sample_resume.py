"""续跑：补上运镜、挂回已生成的第 1 段视频，再生成第 2/3 段。

第 1 段视频（21:22 已落盘）不重复生成，避免重复计费。
"""

import os
import django
import time

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")
django.setup()

from jinja2 import Template  # noqa: E402

from apps.content.models import CameraMovement, GeneratedImage, GeneratedVideo, Storyboard  # noqa: E402
from apps.content.processors.image2video_stage import Image2VideoStageProcessor  # noqa: E402
from apps.models.models import ModelProvider  # noqa: E402
from apps.projects.asset_context import build_project_asset_context  # noqa: E402
from apps.projects.models import Project  # noqa: E402
from apps.prompts.client_param_resolver import resolve_stage_client_params  # noqa: E402
from apps.prompts.models import PromptTemplate, PromptTemplateSet  # noqa: E402
from core.ai_client.factory import create_ai_client  # noqa: E402

PROJECT_NAME = '超写实短剧小样（AI 生成）'
REUSE_VIDEO_SHOT_1 = '/api/v1/content/storage/video/2026-09-20/video_78a2e00271664b69a1095ee90fc655e4.mp4'


def render_camera_system_prompt(project, template):
    asset_context = build_project_asset_context(project)
    return Template(template.template_content).render(
        **asset_context,
        project={
            'name': project.name,
            'description': project.description,
            'original_topic': project.original_topic,
        },
    )


def generate_camera_description(client, system_prompt, storyboard, params):
    """调用 LLM 生成这一镜的运镜描述（与 camera_movement 阶段同参数）。"""
    full_text = ''
    for chunk in client.generate_stream(
        prompt='剧本:%s\n 画面: %s' % (storyboard.narration_text, storyboard.image_prompt),
        system_prompt=system_prompt,
        max_tokens=params.get('max_tokens', 2048),
        temperature=params.get('temperature', 0.7),
        top_p=params.get('top_p', 1.0),
    ):
        if chunk.get('type') == 'token':
            full_text = chunk.get('full_text', full_text)
        elif chunk.get('type') == 'done':
            full_text = chunk.get('full_text', full_text)
            break
    return (full_text or '').strip()


def main():
    drama_set = PromptTemplateSet.objects.get(name='超写实人物短剧（内置）')
    project = Project.objects.get(name=PROJECT_NAME)
    storyboards = list(Storyboard.objects.filter(project=project).order_by('sequence_number'))
    print('project=%s storyboards=%d' % (project.name, len(storyboards)))

    llm_provider = ModelProvider.objects.get(name='火山引擎 / doubao-seed-2-1-pro-260915')
    video_provider = ModelProvider.objects.get(name='MiniMax H3')
    cm_template = PromptTemplate.objects.get(template_set=drama_set, stage_type='camera_movement')
    video_template = PromptTemplate.objects.get(template_set=drama_set, stage_type='video_generation')

    cm_params = resolve_stage_client_params('camera_movement', template=cm_template, provider=llm_provider)
    video_params = resolve_stage_client_params('video_generation', template=video_template, provider=video_provider)
    cm_system_prompt = render_camera_system_prompt(project, cm_template)
    llm_client = create_ai_client(llm_provider)

    # ===== 1) 运镜（LLM，成本可忽略）=====
    for sb in storyboards:
        if CameraMovement.objects.filter(storyboard=sb).exists():
            print('第%s镜 运镜已存在，跳过' % sb.sequence_number)
            continue
        text = generate_camera_description(llm_client, cm_system_prompt, sb, cm_params)
        CameraMovement.objects.update_or_create(
            storyboard=sb,
            defaults={
                'movement_type': '',
                'movement_params': {'description': text},
                'model_provider': llm_provider,
            },
        )
        print('第%s镜 运镜: %s' % (sb.sequence_number, text))

    # ===== 2) 第 1 段视频直接挂回已生成的文件 =====
    first_sb = storyboards[0]
    first_image = GeneratedImage.objects.filter(storyboard=first_sb, status='completed').first()
    if first_image and not GeneratedVideo.objects.filter(storyboard=first_sb).exists():
        GeneratedVideo.objects.create(
            storyboard=first_sb,
            image=first_image,
            camera_movement=CameraMovement.objects.filter(storyboard=first_sb).first(),
            video_url=REUSE_VIDEO_SHOT_1,
            generation_params={'reused': True, 'note': '第一次运行时已生成并落盘，未重复计费'},
            model_provider=video_provider,
            status='completed',
            duration=video_params.get('duration', 4),
            fps=24,
        )
        print('第1镜 视频已挂回（未重复生成、未重复计费）: %s' % REUSE_VIDEO_SHOT_1)

    # ===== 3) 第 2/3 段视频 =====
    processor = Image2VideoStageProcessor()
    client = create_ai_client(video_provider)
    pending = [sb for sb in storyboards if not GeneratedVideo.objects.filter(storyboard=sb).exists()]
    print('\n待生成视频: %d 段（预计花费 %.1f 元）' % (len(pending), len(pending) * 2.0))

    for sb in pending:
        gen_image = GeneratedImage.objects.filter(storyboard=sb, status='completed').first()
        if not gen_image:
            print('第%s镜 缺少分镜图，跳过' % sb.sequence_number)
            continue

        sb_dict = {
            'scene_number': sb.sequence_number,
            'narration': sb.narration_text,
            'visual_prompt': sb.image_prompt,
            'shot_type': sb.scene_description,
            'urls': [{'url': gen_image.image_url}],
        }
        prompt = processor._build_prompt(project, sb_dict)
        print('\n第%s镜 视频生成中 ...' % sb.sequence_number)
        print('  运镜描述已自动拼接；prompt 前 120 字:', prompt[:120].replace('\n', ' / '))
        started = time.time()
        result = client._generate_video(
            prompt=prompt,
            model=video_provider.model_name,
            image_uri=gen_image.image_url,
            duration_seconds=video_params.get('duration', 4),
            aspect_ratio=video_params.get('aspect_ratio', '16:9'),
            resolution=video_params.get('resolution', '768P'),
            poll_interval=video_params.get('poll_interval', 5),
            max_wait_time=video_params.get('max_wait_time', 1800),
        )
        elapsed = int(time.time() - started)
        if not result.get('success'):
            print('  ! 失败(%ss): %s' % (elapsed, result.get('error')))
            continue
        data = result['data'][0]
        GeneratedVideo.objects.create(
            storyboard=sb,
            image=gen_image,
            camera_movement=CameraMovement.objects.filter(storyboard=sb).first(),
            video_url=data.get('url', ''),
            generation_params={'prompt': prompt, 'original_data': data},
            model_provider=video_provider,
            status='completed',
            duration=data.get('duration') or video_params.get('duration', 4),
            fps=24,
        )
        print('  ✓ 完成(%ss): %s' % (elapsed, data.get('url')))

    print('\n===== 小样完成 =====')
    for video in GeneratedVideo.objects.filter(storyboard__project=project).order_by('storyboard__sequence_number'):
        sb = video.storyboard
        print('第%s镜 [%s] 台词=%s' % (sb.sequence_number, sb.scene_description, sb.narration_text))
        print('   视频: %s' % video.video_url)
        image = GeneratedImage.objects.filter(storyboard=sb, status='completed').first()
        if image:
            print('   首帧: %s' % image.image_url)


if __name__ == '__main__':
    main()
