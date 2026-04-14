from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from events.views import admin_dashboard, admin_compress_images

urlpatterns = [
    path('admin/dashboard/', admin_dashboard, name='admin_dashboard'),
    path('admin/compress-images/', admin_compress_images, name='admin_compress_images'),
    path('admin/', admin.site.urls),
    path('board/', include('board.urls')),
    path('', include('events.urls')),
] + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT) \
  + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)