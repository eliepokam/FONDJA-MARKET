"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from users.views import CreateStaffAccountView, UserListView, ToggleUserStatusView, DeleteUserView
from core.views import AdminStatsView, ExportDataView
from core.views import JournalActiviteListView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/', include('users.urls')),
    path('api/', include('shipments.urls')),
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema')),
    path('api/', include('tracking.urls')),
    path('api/', include('notifications.urls')),
    path('api/', include('payments.urls')),
    path('api/', include('complaints.urls')),
    path('api/users/', UserListView.as_view()),
    path('api/admin/users/<int:user_id>/statut/', ToggleUserStatusView.as_view()),
    path('api/admin/users/<int:user_id>/', DeleteUserView.as_view()),
    path('api/admin/staff-accounts/', CreateStaffAccountView.as_view()),
    path('api/admin/stats/', AdminStatsView.as_view()),
    path('api/admin/export/', ExportDataView.as_view()),
    path('api/admin/journal/', JournalActiviteListView.as_view()),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
