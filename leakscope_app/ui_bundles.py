"""Deterministic workspace assets, assembled before collectstatic collects/compresses them.

Keep source modules separate for review and tests. Script separators prevent
adjacent IIFEs being interpreted as function calls; CSS order is the cascade.
"""
from pathlib import Path

BUNDLES = {'css/workspace_bundle.css': ('css/leakscope.css',
                              'css/relationships.css',
                              'css/expansion.css',
                              'css/ics.css',
                              'css/browser_sessions.css',
                              'css/workspace.css',
                              'css/table_browser.css',
                              'css/service_catalog.css',
                              'css/run_logs.css',
                              'css/relationship_map.css',
                              'css/inspector.css',
                              'css/network_context.css',
                              'css/organization_overview.css',
                              'css/workspace_workflows.css',
                              'css/atlas.css',
                              'css/scope.css',
                              'css/evidence.css',
                              'css/experience.css',
                              'css/domain_coverage.css',
                              'css/list_toolbar.css',
                              'css/build.css',
                              'css/public_signals.css'),
 'js/leakscope/workspace_bundle.js': ('js/leakscope/utils.js',
                                      'js/leakscope/tables.js',
                                      'js/leakscope/table_export.js',
                                      'js/leakscope/data_viewer.js',
                                      'js/leakscope/visualizations.js',
                                      'js/leakscope/scope.js',
                                      'js/leakscope/evidence.js',
                                      'js/leakscope/workspace.js',
                                      'js/leakscope/list_toolbar.js',
                                      'js/leakscope/run_logs.js',
                                      'js/leakscope/intelligence.js',
                                      'js/leakscope/presets.js',
                                      'js/leakscope/browser_sessions.js',
                                      'js/leakscope/network_context.js',
                                      'js/leakscope/inspector.js',
                                      'js/leakscope/organization_overview.js',
                                      'js/leakscope/signatures.js',
                                      'js/leakscope/operations.js',
                                      'js/leakscope/cloud.js',
                                      'js/leakscope/relationship_map.js',
                                      'js/leakscope/relationships.js',
                                      'js/leakscope/expansion.js',
                                      'js/leakscope/ics.js',
                                      'js/leakscope/public_signals.js',
                                      'js/leakscope/workspace_workflows.js',
                                      'js/leakscope/atlas.js',
                                      'js/leakscope/domain_coverage.js',
                                      'js/leakscope/preview.js',
                                      'js/leakscope/build.js',
                                      'js/leakscope/app.js')}


def build_bundles(root=None, bundles=None):
    root = Path(root) if root else Path(__file__).resolve().parent / 'static'
    for target, sources in (BUNDLES if bundles is None else bundles).items():
        separator = '\n;\n' if target.endswith('.js') else '\n'
        content = separator.join((root / source).read_text(encoding='utf-8') for source in sources) + separator
        path = root / target
        if not path.exists() or path.read_text(encoding='utf-8') != content:
            path.write_text(content, encoding='utf-8')
