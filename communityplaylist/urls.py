from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import RedirectView, TemplateView
from django.contrib.sitemaps.views import sitemap
from events.views import admin_dashboard, admin_compress_images
from events.sitemaps import EventSitemap, ArtistSitemap, VenueSitemap, PromoterSitemap, StaticSitemap

SITEMAPS = {
    'events':    EventSitemap,
    'artists':   ArtistSitemap,
    'venues':    VenueSitemap,
    'promoters': PromoterSitemap,
    'static':    StaticSitemap,
}

urlpatterns = [
    path('favicon.ico', RedirectView.as_view(
        url='https://hihi.communityplaylist.com/files/timeline_files/store_file6809b5ed4135d-community_playlist_site_logo_2025.png',
        permanent=True,
    )),
    path('admin/dashboard/', admin_dashboard, name='admin_dashboard'),
    path('admin/compress-images/', admin_compress_images, name='admin_compress_images'),
    path('admin/', admin.site.urls),
    path('api/worker/', include('events.worker_urls')),
    path('board/', include('board.urls')),
    path('', include('events.urls')),
    path('sitemap.xml', sitemap, {'sitemaps': SITEMAPS}, name='django.contrib.sitemaps.views.sitemap'),
    path('robots.txt', TemplateView.as_view(template_name='robots.txt', content_type='text/plain')),
] + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT) \
  + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)