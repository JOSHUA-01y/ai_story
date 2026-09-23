电影级实拍剧照，横构图 16:9。
{% for asset in asset_bindings %}{% if asset.variable_type == 'image' %}【形象参考 {{ asset.prompt_text }}】五官、脸型、发型发色、肤色、体型、服装与配饰必须与 {{ asset.prompt_text }} 中的角色完全一致；不要换脸、不要美化、不要改变年龄感、不要更换服装。
{% endif %}{% endfor %}{{ visual_prompt }}

实拍摄影，35mm 胶片质感，自然光，浅景深，皮肤纹理与毛孔可见，发丝与衣物褶皱真实，高动态范围，电影调色；画面内不得出现文字、字幕、水印、logo。
