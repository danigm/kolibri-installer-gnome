subdir('kolibri_gnome')

configure_file(
    input: 'kolibri-gnome-env.sh.in',
    output: 'kolibri-gnome-env.sh',
    configuration: kolibri_gnome_config,
    install: true,
    install_dir: kolibri_gnome_libexecdir,
    install_mode: 'rw-r--r--'
)

configure_file(
    input: 'kolibri-gnome.in',
    output: 'kolibri-gnome',
    configuration: kolibri_gnome_config,
    install: true,
    install_dir: bindir,
    install_mode: 'rwxr-xr-x'
)

configure_file(
    input: 'kolibri-search-provider.in',
    output: 'kolibri-search-provider',
    configuration: kolibri_gnome_config,
    install: true,
    install_dir: kolibri_gnome_libexecdir,
    install_mode: 'rwxr-xr-x'
)
