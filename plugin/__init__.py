#!/usr/bin/env python3
"""The Calibre plugin wrapper — the only file Calibre imports to find out what this is.

Two things happen here and nothing else:

  1. `actual_plugin` names the Qt layer as a STRING, so `calibredb`/`calibre-debug` can load the
     plugin's metadata without dragging in the GUI libraries.
  2. `load_actual_plugin` imports the bundled `scourgify` core inside `with self:` — the FanFicFare
     mechanism, verified against Calibre 9.11. `Plugin.__enter__` appends the plugin ZIP to
     sys.path, so zipimport resolves `import scourgify` from `scourgify/` at the zip root and every
     `from scourgify.common import ...` in the core keeps working UNCHANGED. Once the package is in
     sys.modules its submodules resolve through its own __path__, so later lazy imports (inside job
     functions, which is where all of them live) do not need the context manager.

     This is why the core needed no import rewrite: absolute imports inside a zip on sys.path are
     the same absolute imports as on a normal sys.path.

`version` is stamped from pyproject.toml by build_plugin.py — the zip and the wheel are cut from
one source tree and one version, so a GUI and a CLI can never claim different cores.
"""
from calibre.customize import InterfaceActionBase

__version__ = (0, 0, 0)         # stamped by build_plugin.py from pyproject.toml


class ScourgifyPlugin(InterfaceActionBase):
    name                    = 'scourgify'
    description             = 'Normalize and tag a FanFicFare-imported library'
    supported_platforms     = ['osx', 'linux', 'windows']
    author                  = 'Andrei Lavrenov'
    version                 = __version__
    minimum_calibre_version = (6, 0, 0)      # qt.core (Qt 6) — Calibre 5 is PyQt5-only
    actual_plugin           = 'calibre_plugins.scourgify.action:ScourgifyAction'

    def load_actual_plugin(self, gui):
        with self:                           # sys.path gains the zip for the duration
            import scourgify                 # noqa: F401 — caches the core under its plain name
        return InterfaceActionBase.load_actual_plugin(self, gui)

    # ---- settings (NLSpec B5). These hang off the BASE, not the action — Calibre asks the plugin
    # object, not the toolbar button. The widget is imported INSIDE config_widget() on purpose:
    # at module level it would drag Qt into every command-line use of this plugin, which is the
    # same reason `actual_plugin` above is a string.
    def is_customizable(self):
        return True

    def config_widget(self):
        from calibre_plugins.scourgify.config import ConfigWidget
        return ConfigWidget(self.actual_plugin_)

    def save_settings(self, config_widget):
        config_widget.save_settings()
