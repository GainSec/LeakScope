from django.contrib.staticfiles.management.commands.collectstatic import Command as CollectStaticCommand

from leakscope_app.ui_bundles import build_bundles


class Command(CollectStaticCommand):
    def handle(self, *args, **options):
        build_bundles()
        return super().handle(*args, **options)
