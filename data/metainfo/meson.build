metainfo_dir = join_paths(datadir, 'metainfo')

metainfo_file = i18n.merge_file(
    input: 'org.learningequality.Kolibri.metainfo.xml.in',
    output: 'org.learningequality.Kolibri.metainfo.xml',
    type: 'xml',
    po_dir: po_dir,
    configuration: kolibri_gnome_config,
    install: true,
    install_dir: metainfo_dir
)

appstream_util = find_program('appstream-util', required: false)

if appstream_util.found()
    test(
        'Validate metainfo file',
        appstream_util,
        args: ['validate', metainfo_file]
    )
endif
