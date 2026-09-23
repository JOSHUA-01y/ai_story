"""
主URL配置
遵循REST API最佳实践
"""

from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import RedirectView


def superuser_admin_permission(request):
    return request.user.is_active and request.user.is_superuser


admin.site.has_permission = superuser_admin_permission

urlpatterns = [
    path('admin/', admin.site.urls),
    path('mcp/', include('apps.mcp.urls')),
    path('api/v1/projects/', include('apps.projects.urls')),
    path('api/v1/prompts/', include('apps.prompts.urls')),
    path('api/v1/models/', include('apps.models.urls')),
    path('api/v1/content/', include('apps.content.urls')),
    path('api/v1/users/', include('apps.users.urls')),
    path('api/v1/agent/', include('apps.agent.urls')),
    path('api/v1/ai/', include('apps.ai_proxy.urls')),
    path('api/v1/scripts/', include('apps.scripts.urls')),
    path('api/v1/workflows/', include('apps.workflows.urls')),
    path('api/mock/', include('apps.mock_api.urls')),

    # legacy /storage/... 兼容路由。
    # 说明: 上面的 static(STORAGE_URL, ...) 只在 DEBUG=True 时生效，生产配置下
    # (config.settings.production, DEBUG=False) /storage/** 会直接 404；而 MCP 的
    # apps/mcp/toolsets/artifacts.py::_public_storage_url() 恰好产出 /storage/... 形状的
    # 地址，前端会直连后端端口去取，因而全部 404。这里统一重定向到 content app 的
    # storage API（AllowAny，无需 token），保证两种访问前缀都能取到文件。
    re_path(
        r'^storage/(?P<path>.*)$',
        RedirectView.as_view(
            url='/api/v1/content/storage/%(path)s',
            permanent=False,
        ),
        name='legacy-storage-redirect',
    ),
]

# 开发环境下提供媒体文件和 storage 文件访问
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STORAGE_URL, document_root=settings.STORAGE_ROOT)
