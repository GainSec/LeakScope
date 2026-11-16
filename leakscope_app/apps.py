from django.apps import AppConfig


class LeaklookerAppConfig(AppConfig):
    name = 'leakscope_app'

    def ready(self):
        from . import signals  # noqa: F401
