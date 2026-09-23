"""超写实人物短剧 —— 3 镜头小样。

用真实模板（超写实人物短剧（内置））+ 真实模型跑通：
定妆照 → 图片资产 → 3 个分镜图（带角色参考图）→ 3 段视频（含台词）

花费：4 张图 + 3 条 4 秒 768P 视频。
"""

import os
import django
import json
import time

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")
django.setup()

from django.conf import settings  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from pathlib import Path  # noqa: E402

from apps.content.models import GeneratedImage, GeneratedVideo, Storyboard  # noqa: E402
from apps.content.processors.image2video_stage import Image2VideoStageProcessor  # noqa: E402
from apps.content.processors.text2image_stage import Text2ImageStageProcessor  # noqa: E402
from apps.models.models import ModelProvider  # noqa: E402
from apps.projects.models import Project, ProjectAssetBinding, Series  # noqa: E402
from apps.prompts.client_param_resolver import resolve_stage_client_params  # noqa: E402
from apps.prompts.models import GlobalVariable, PromptTemplate, PromptTemplateSet  # noqa: E402
from core.ai_client.factory import create_ai_client  # noqa: E402
from core.ai_client.image_service import ImageGenerationService  # noqa: E402
from core.ai_client.schemas import Text2ImageRequest  # noqa: E402

PROJECT_NAME = '超写实短剧小样（AI 生成）'
ASSET_KEY = 'char_linwan'

APPEARANCE = (
    '林晚：28岁中国女性，鹅蛋脸，黑色齐肩短发（发尾微内扣），冷白皮肤，双眼皮大眼睛，'
    '鼻梁挺直，身高约165cm；身穿深灰色系带长风衣，内搭白色高领毛衣，黑色直筒长裤，'
    '黑色短靴，不佩戴首饰'
)

SCENES = [
    {
        'scene_number': 1,
        'shot_type': '中近景',
        'narration': '你终于来了。',
        'visual_prompt': (
            '场景描述：深夜的便利店屋檐下，雨幕在霓虹灯下形成细密的斜线，'
            '地面积水映着红蓝招牌的光，屋檐边缘不断有成串水珠滴落。\n'
            f'主体刻画：{APPEARANCE}。她收起湿透的伞，抬头直视镜头，嘴唇微张正在说话，'
            '睫毛上挂着细小水珠，肩膀因寒气微微收紧。\n'
            '光线与质感：画面右上方是便利店白色冷光源，在她左脸形成硬边轮廓光；'
            '霓虹的暖红从背后补光，风衣被雨水打湿后呈现深浅不一的暗色斑块。\n'
            '视角与构图：中近景，人物位于画面右侧三分线，左侧留出雨夜街道的纵深。\n'
            '实拍风格：电影级实拍剧照，35mm 胶片质感，自然光，浅景深，真实皮肤纹理'
        ),
    },
    {
        'scene_number': 2,
        'shot_type': '过肩镜头',
        'narration': '无台词',
        'visual_prompt': (
            '场景描述：同一处便利店屋檐下，镜头从她右肩后方望去，'
            '前方是被雨水打亮的空荡人行道，远处一辆车的尾灯在雨幕中拖出红色光带。\n'
            f'主体刻画：{APPEARANCE}。她突然转头看向画面右侧，瞳孔收缩，'
            '身体重心后移半步，握伞的手指关节泛白。\n'
            '光线与质感：冷光从画面左侧斜射，雨丝被照亮成银白色细线；'
            '她的侧脸处于半明半暗中，风衣肩部有湿润反光。\n'
            '视角与构图：过肩镜头，人物占据画面左侧三分之一，视线方向留出大片负空间。\n'
            '实拍风格：电影级实拍剧照，35mm 胶片质感，自然光，浅景深，真实皮肤纹理'
        ),
    },
    {
        'scene_number': 3,
        'shot_type': '大特写',
        'narration': '这次，我不会再等你了。',
        'visual_prompt': (
            '场景描述：深夜街头，背景完全虚化成霓虹色斑，雨水在空气中可见细小水雾，'
            '一缕湿发贴在颧骨旁，发梢仍在下滴水。\n'
            f'主体刻画：{APPEARANCE}。大特写只保留面部与颈部，她微微侧脸，'
            '下唇被轻咬出一道浅痕，眼神从慌乱转为冷硬，眼角有未落的泪。\n'
            '光线与质感：左侧霓虹暖光与右侧便利店冷光在脸上交汇，'
            '鼻梁形成明暗分界线，皮肤毛孔与细汗可见，无磨皮感。\n'
            '视角与构图：大特写，面部占画面约七成，眼睛位于上三分线。\n'
            '实拍风格：电影级实拍剧照，35mm 胶片质感，自然光，浅景深，真实皮肤纹理'
        ),
    },
]


def localize_image(url, timeout=120):
    """把远程图片下载进 storage/image，返回本地访问地址。"""
    import uuid as _uuid

    import requests

    from core.utils.file_storage import image_storage

    if not url or url.startswith('/api/v1/content/storage/image/'):
        return url

    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    content_type = response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
    extension = {
        'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp',
    }.get(content_type, '.jpg')
    filename = 'image_%s%s' % (_uuid.uuid4().hex, extension)
    full_path, relative_path = image_storage.get_unique_filepath(filename=filename, create_dirs=True)
    Path(full_path).write_bytes(response.content)
    return '/api/v1/content/storage/image/%s' % relative_path


def main():
    User = get_user_model()
    user = User.objects.filter(is_superuser=True).first() or User.objects.first()

    drama_set = PromptTemplateSet.objects.get(name='超写实人物短剧（内置）')
    image_provider = ModelProvider.objects.get(name='火山引擎 / doubao-seedream-5-0-260128')
    video_provider = ModelProvider.objects.get(name='MiniMax H3')

    # 清理上一次运行
    Project.objects.filter(name=PROJECT_NAME, user=user).delete()
    GlobalVariable.objects.filter(key=ASSET_KEY, created_by=user).delete()

    series, _ = Series.objects.get_or_create(
        name='超写实短剧小样系列',
        user=user,
        defaults={'description': 'AI 生成的超写实短剧小样（脚本自动创建）'},
    )

    project = Project.objects.create(
        user=user,
        series=series,
        episode_number=1,
        sort_order=1,
        episode_title='第1集',
        name=PROJECT_NAME,
        original_topic='雨夜重逢：林晚在便利店屋檐下等一个人，等来的却是让她心冷的答案。',
        prompt_template_set=drama_set,
    )
    print('project=%s (%s) user=%s' % (project.name, project.id, user.username))

    image_processor = Text2ImageStageProcessor()
    video_processor = Image2VideoStageProcessor()

    image_template = PromptTemplate.objects.get(template_set=drama_set, stage_type='image_generation')
    video_template = PromptTemplate.objects.get(template_set=drama_set, stage_type='video_generation')
    image_params = resolve_stage_client_params('image_generation', template=image_template, provider=image_provider)
    video_params = resolve_stage_client_params('video_generation', template=video_template, provider=video_provider)
    print('image params =', json.dumps(image_params, ensure_ascii=False))
    print('video params =', json.dumps(video_params, ensure_ascii=False))

    image_client = create_ai_client(image_provider)

    # ===== 1) 定妆照（此时项目还没有图片资产，模板不会带参考图）=====
    print('\n[1/3] 生成角色定妆照 ...')
    portrait_storyboard = {
        'scene_number': 0,
        'narration': '无台词',
        'shot_type': '中近景',
        'visual_prompt': (
            '场景描述：纯浅灰色背景的人物定妆棚拍，背景干净无杂物。\n'
            f'主体刻画：{APPEARANCE}。正面半身站姿，双手自然垂放，表情平静中性，直视镜头。\n'
            '光线与质感：左右两侧柔光箱均匀布光，面部无强烈阴影，皮肤纹理自然。\n'
            '视角与构图：中近景半身，人物居中，头顶留出少量空间。\n'
            '实拍风格：电影级实拍剧照，35mm 胶片质感，浅景深，真实皮肤纹理'
        ),
    }
    payload = image_processor._build_generation_prompt_payload(project, portrait_storyboard)
    response = ImageGenerationService.generate(
        provider=image_provider,
        client=image_client,
        request=Text2ImageRequest(
            prompt=payload['prompt'],
            negative_prompt=image_params.get('negative_prompt', ''),
            reference_images=payload['image'],
            aspect_ratio=image_params.get('ratio', '16:9'),
            width=image_params.get('width', 1024),
            height=image_params.get('height', 1024),
            extra={'resolution': image_params.get('resolution', '1k')},
        ),
    )
    if not getattr(response, 'data', None):
        raise SystemExit('定妆照生成失败: %s' % getattr(response, 'error', response))
    portrait_url = localize_image(response.data[0].get('url'))
    print('  定妆照:', portrait_url)

    asset = GlobalVariable.objects.create(
        key=ASSET_KEY,
        value=portrait_url,
        variable_type='image',
        scope='user',
        group='角色',
        description=APPEARANCE,
        created_by=user,
    )
    ProjectAssetBinding.objects.create(project=project, asset=asset)
    print('  已建图片资产 %s 并绑定到项目' % ASSET_KEY)

    # ===== 2) 三个分镜图（带角色参考图）=====
    storyboards = []
    for scene in SCENES:
        sb = Storyboard.objects.create(
            project=project,
            sequence_number=scene['scene_number'],
            scene_description=scene['shot_type'],
            narration_text=scene['narration'],
            image_prompt=scene['visual_prompt'],
            duration_seconds=4.0,
            model_provider=image_provider,
            generation_metadata={'shot_type': scene['shot_type']},
        )
        storyboards.append(sb)

    generated_images = []
    for index, (sb, scene) in enumerate(zip(storyboards, SCENES), 1):
        print('\n[2/3] 生成分镜图 %d/%d ...' % (index, len(SCENES)))
        sb_dict = {
            'scene_number': sb.sequence_number,
            'narration': sb.narration_text,
            'visual_prompt': sb.image_prompt,
            'shot_type': sb.scene_description,
        }
        payload = image_processor._build_generation_prompt_payload(project, sb_dict)
        print('  prompt 片段:', payload['prompt'][:110].replace('\n', ' / '))
        print('  参考图数量:', len(payload['image']))
        response = ImageGenerationService.generate(
            provider=image_provider,
            client=image_client,
            request=Text2ImageRequest(
                prompt=payload['prompt'],
                negative_prompt=image_params.get('negative_prompt', ''),
                reference_images=payload['image'],
                aspect_ratio=image_params.get('ratio', '16:9'),
                width=image_params.get('width', 1024),
                height=image_params.get('height', 1024),
                extra={'resolution': image_params.get('resolution', '1k')},
            ),
        )
        if not getattr(response, 'data', None):
            print('  ! 失败:', getattr(response, 'error', response))
            continue
        image_url = localize_image(response.data[0].get('url'))
        print('  分镜图:', image_url)
        generated_images.append(
            GeneratedImage.objects.create(
                storyboard=sb,
                image_url=image_url,
                generation_params={'prompt': payload['prompt'], 'reference_count': len(payload['image'])},
                model_provider=image_provider,
                status='completed',
            )
        )

    # ===== 3) 三段视频（含台词）=====
    video_client = create_ai_client(video_provider)
    for index, (sb, gen_image) in enumerate(zip(storyboards, generated_images), 1):
        print('\n[3/3] 生成视频 %d/%d ...' % (index, len(generated_images)))
        sb_dict = {
            'scene_number': sb.sequence_number,
            'narration': sb.narration_text,
            'visual_prompt': sb.image_prompt,
            'shot_type': sb.scene_description,
            'urls': [{'url': gen_image.image_url}],
        }
        video_prompt = video_processor._build_prompt(project, sb_dict)
        print('  video prompt:', video_prompt[:150].replace('\n', ' / '))
        started = time.time()
        result = video_client._generate_video(
            prompt=video_prompt,
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
            print('  ! 视频失败(%ss): %s' % (elapsed, result.get('error')))
            continue
        data = result['data'][0]
        GeneratedVideo.objects.create(
            storyboard=sb,
            image=gen_image,
            video_url=data.get('url', ''),
            thumbnail_url='',
            generation_params={'prompt': video_prompt, 'original_data': data},
            model_provider=video_provider,
            status='completed',
            duration=data.get('duration') or video_params.get('duration', 4),
            fps=24,
        )
        print('  ✓ 成片(%ss): %s' % (elapsed, data.get('url')))
        print('    原始链接:', (data.get('original_url') or '')[:90])

    print('\n===== 完成 =====')
    print('项目: %s' % project.name)
    print('分镜图: %d 张，视频: %d 段' % (len(generated_images), GeneratedVideo.objects.filter(storyboard__project=project).count()))
    for video in GeneratedVideo.objects.filter(storyboard__project=project).order_by('storyboard__sequence_number'):
        print('  第%s镜: %s' % (video.storyboard.sequence_number, video.video_url))


if __name__ == '__main__':
    main()
