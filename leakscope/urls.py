from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('', include('leakscope_app.workbench_urls')),
    path('admin/', admin.site.urls),
    path('', include('leakscope_app.urls')),
]
