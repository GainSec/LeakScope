"""Every module in this folder is imported here, so adding a file adds its blocks."""
import importlib
import pkgutil

for _module in pkgutil.iter_modules(__path__):
    importlib.import_module(__name__ + '.' + _module.name)
