"""内置「超写实人物短剧」提示词模板集。

用真人实拍方向重写了五个阶段的模板：

- ``rewrite``           剧本精修（虚构角色、口语台词、每场 4~6 秒）
- ``storyboard``        分镜（强制逐字复用同一段人物外形描述 + 严格 JSON）
- ``image_generation``  分镜图（自动把项目绑定的图片资产作为「图N」形象参考）
- ``camera_movement``   运镜（逐个镜头输出一句话运镜描述）
- ``video_generation``  图生视频（台词 / 无台词分支 + 口型对齐 + 环境音）

与 ``apps/projects/signals.py`` 的默认模板集相互独立：本命令只创建/更新名为
「超写实人物短剧（内置）」的模板集，不动默认集，也不会把它设为 is_default。
用法::

    python manage.py seed_realistic_drama_prompts           # 只补空，不改已有内容
    python manage.py seed_realistic_drama_prompts --force    # 用文件内容覆盖模板
"""

from pathlib import Path
from typing import Any, Dict, Optional

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from jinja2 import Environment, meta

from apps.projects.signals import DEFAULT_PROMPT_DIR

#: 模板 markdown 所在目录
DRAMA_TEMPLATE_DIR: Path = DEFAULT_PROMPT_DIR / 'realistic_drama'

#: 模板集名称
DRAMA_TEMPLATE_SET_NAME = '超写实人物短剧（内置）'

DRAMA_TEMPLATE_SET_DESCRIPTION = (
    '真人实拍向的短剧模板集：写实分镜 + 图片资产锁定人物一致性 + 台词/口型对齐。'
    '把角色定妆照建成「图片类型」变量并绑定到项目，模板会自动把它作为形象参考图'
    '喂给文生图模型，从而保证所有镜头是同一个人。在项目里把「提示词集」选成本套即可使用。'
)

#: 阶段 → 模型类型 / 默认执行参数
STAGE_CONFIG: Dict[str, Dict[str, Any]] = {
    'rewrite': {
        'provider_type': 'llm',
        'client_params': {'temperature': 0.7, 'max_tokens': 8192},
    },
    'storyboard': {
        'provider_type': 'llm',
        'client_params': {'temperature': 0.5, 'max_tokens': 40960},
    },
    'image_generation': {
        'provider_type': 'text2image',
        'client_params': {
            'ratio': '16:9',
            # 注意：火山方舟 doubao-seedream-5.0 要求单图 ≥3,686,400 像素
            # （16:9 的 2k=1920x1080 只有 207 万像素会被 400 拒掉），
            # 因此这里默认 4k（3840x2160）。换成别的图像模型可按需下调。
            'resolution': '4k',
            'negative_prompt': (
                '3D渲染, CG, 卡通, 插画, 二次元, 皮克斯, 塑料感, 过度磨皮, 假脸, '
                '变形的手, 多余手指, 文字, 字幕, 水印, logo'
            ),
        },
    },
    'camera_movement': {
        'provider_type': 'llm',
        'client_params': {'temperature': 0.7, 'max_tokens': 2048},
    },
    'video_generation': {
        'provider_type': 'image2video',
        'client_params': {
            'duration': 4,
            'resolution': '768P',
            'aspect_ratio': '16:9',
            'poll_interval': 5,
            'max_wait_time': 1800,
        },
    },
}


def _extract_template_variables(template_content: str) -> Dict[str, str]:
    """从 Jinja 模板中提取变量定义（与 signals.py 的默认集保持一致的推断规则）。"""
    if not template_content:
        return {}

    parsed_content = Environment().parse(template_content)
    variable_names = sorted(meta.find_undeclared_variables(parsed_content))
    return {name: _infer_variable_type(name) for name in variable_names}


def _infer_variable_type(variable_name: str) -> str:
    """根据变量名推断变量类型。"""
    if variable_name.startswith(('is_', 'has_')):
        return 'bool'
    if variable_name.endswith(('_count', '_index')):
        return 'int'
    if variable_name.endswith(('_duration', '_ratio')):
        return 'float'
    if variable_name.endswith(('_list', '_items')):
        return 'list'
    if variable_name.endswith(('_map', '_dict', '_config')):
        return 'dict'
    return 'string'


class Command(BaseCommand):
    help = '创建/更新内置的「超写实人物短剧」提示词模板集'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='用模板文件内容覆盖已存在的模板（默认只补空，不覆盖手工修改）',
        )
        parser.add_argument(
            '--set-name',
            default=DRAMA_TEMPLATE_SET_NAME,
            help='模板集名称，默认「%s」' % DRAMA_TEMPLATE_SET_NAME,
        )

    def handle(self, *args, **options):
        if not DRAMA_TEMPLATE_DIR.exists():
            raise CommandError(f'模板目录不存在: {DRAMA_TEMPLATE_DIR}')

        force: bool = options['force']
        set_name: str = options['set_name']

        User = apps.get_model('auth', 'User')
        ModelProvider = apps.get_model('models', 'ModelProvider')
        PromptTemplateSet = apps.get_model('prompts', 'PromptTemplateSet')
        PromptTemplate = apps.get_model('prompts', 'PromptTemplate')

        system_user, _ = User.objects.get_or_create(
            username='system',
            defaults={
                'email': 'system@example.com',
                'is_staff': True,
                'is_superuser': False,
            },
        )

        with transaction.atomic():
            template_set = PromptTemplateSet.objects.filter(name=set_name).first()
            if template_set is None:
                template_set = PromptTemplateSet.objects.create(
                    name=set_name,
                    description=DRAMA_TEMPLATE_SET_DESCRIPTION,
                    is_active=True,
                    is_default=False,
                    created_by=system_user,
                )
                self.stdout.write(self.style.SUCCESS(f'✓ 已创建模板集: {template_set.name}'))
            else:
                self.stdout.write(f'· 复用已有模板集: {template_set.name}')

            created_count = 0
            updated_count = 0
            skipped_count = 0

            for stage_type, config in STAGE_CONFIG.items():
                template_path = DRAMA_TEMPLATE_DIR / f'{stage_type}.md'
                if not template_path.exists():
                    self.stderr.write(f'! 跳过 {stage_type}: 缺少 {template_path.name}')
                    continue

                content = template_path.read_text(encoding='utf-8').strip()
                variables = _extract_template_variables(content)

                provider = (
                    ModelProvider.objects.filter(
                        provider_type=config['provider_type'],
                        is_active=True,
                    )
                    .order_by('-priority', '-created_at')
                    .first()
                )

                template = PromptTemplate.objects.filter(
                    template_set=template_set,
                    stage_type=stage_type,
                ).first()

                if template is None:
                    PromptTemplate.objects.create(
                        template_set=template_set,
                        stage_type=stage_type,
                        model_provider=provider,
                        template_content=content,
                        variables=variables,
                        client_params=dict(config.get('client_params') or {}),
                        version=1,
                        is_active=True,
                    )
                    created_count += 1
                    self.stdout.write(
                        f'  + {stage_type}: 已创建（provider='
                        f'{provider.name if provider else "未绑定"}）'
                    )
                    continue

                changed_fields = []
                if force and template.template_content != content:
                    template.template_content = content
                    template.variables = variables
                    changed_fields += ['template_content', 'variables']
                elif not template.template_content:
                    template.template_content = content
                    template.variables = variables
                    changed_fields += ['template_content', 'variables']

                if provider and template.model_provider_id != provider.id:
                    template.model_provider = provider
                    changed_fields.append('model_provider')
                if force and template.client_params != dict(config.get('client_params') or {}):
                    template.client_params = dict(config.get('client_params') or {})
                    changed_fields.append('client_params')
                elif not template.client_params:
                    template.client_params = dict(config.get('client_params') or {})
                    changed_fields.append('client_params')
                if not template.is_active:
                    template.is_active = True
                    changed_fields.append('is_active')

                if changed_fields:
                    template.save(update_fields=changed_fields)
                    updated_count += 1
                    self.stdout.write(f'  ~ {stage_type}: 已更新 {", ".join(changed_fields)}')
                else:
                    skipped_count += 1
                    self.stdout.write(f'  = {stage_type}: 无变化')

        self.stdout.write('')
        self.stdout.write(
            self.style.SUCCESS(
                f'完成：新增 {created_count} 个，更新 {updated_count} 个，跳过 {skipped_count} 个'
            )
        )
        self.stdout.write(
            '使用方式：在项目的「提示词集」里选择「%s」；'
            '并把角色定妆照建成图片类型变量、绑定到该项目。' % set_name
        )
