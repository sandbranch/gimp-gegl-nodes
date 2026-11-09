project('gimp-gegl-nodes', 'c',
  version: '0.1',
  license: 'LGPL-3.0-or-later AND GPL-3.0-or-later',
  meson_version: '>= 0.60.0',
  default_options: ['warning_level=1', 'buildtype=debugoptimized'],
)

# The GEGL operation nodes:graph (LGPL-3.0-or-later). The GIMP plug-in in
# plug-ins/gegl-nodes is Python and is installed by dev/install.sh.
gegl_dep = dependency('gegl-0.4', version: '>= 0.4.62')

# GEGL loads user operations from $XDG_DATA_HOME/gegl-0.4/plug-ins
# (~/.local/share/gegl-0.4/plug-ins).
moduledir = get_option('moduledir')
if moduledir == ''
  moduledir = gegl_dep.get_variable(pkgconfig: 'pluginsdir')
endif

shared_module('node-graph',
  'operations/node-graph.c',
  name_prefix: '',
  include_directories: 'operations',
  dependencies: [gegl_dep],
  c_args: ['-DGETTEXT_PACKAGE="gimp-gegl-nodes"'],
  install: true,
  install_dir: moduledir,
)
