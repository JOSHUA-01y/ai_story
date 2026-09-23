{{ visual_prompt }}
{% for asset in asset_bindings %}{% if asset.variable_type == 'image' and asset.description %}【角色设定】{{ asset.description }}
{% endif %}{% endfor %}{% if narration and narration.strip() not in ['无台词', '（无台词）', '(无台词)'] %}【台词】{{ narration }}
要求：台词以自然口语说出，语气符合当下情绪，口型与台词严格对齐。
{% else %}【台词】本镜头没有对白，不要生成任何人声，只保留与场景匹配的环境音。
{% endif %}{% if shot_type %}【镜头】{{ shot_type }}
{% endif %}实拍电影质感，自然光，浅景深，人物外观、服装与首帧画面完全一致；
动作连贯自然，人物运动幅度适中，画面稳定不闪烁；无文字、无字幕、无水印、无 logo。
