import logging

logger = logging.getLogger(__name__)

import gi
import subprocess

from urllib.parse import urlsplit
from urllib.parse import urlunparse
from gi.repository import Gio

from .. import config


class Launcher(Gio.Application):
    def __init__(self):
        application_id = config.LAUNCHER_APPLICATION_ID

        super().__init__(application_id=application_id,
                         flags=Gio.ApplicationFlags.IS_SERVICE |
                         Gio.ApplicationFlags.HANDLES_COMMAND_LINE |
                         Gio.ApplicationFlags.HANDLES_OPEN)

    def do_open(self, files, n_files, hint):
        file_uris = [f.get_uri() for f in files]

        for uri in file_uris:
            self.handle_uri(uri)

    def handle_uri(self, uri):
        valid_url_schemes = ("kolibri-channel", 'x-kolibri-dispatch')

        url_tuple = urlsplit(uri)

        if url_tuple.scheme == 'kolibri-channel':
            channel_id = url_tuple.path.strip('/')
            node_path = None
            node_query = None
        elif url_tuple.scheme == 'x-kolibri-dispatch':
            channel_id = url_tuple.netloc
            node_path = url_tuple.path
            node_query = url_tuple.query
        else:
            logger.info(f"Invalid URL scheme: {uri}")
            return

        kolibri_gnome_args = []

        # Don't include search context for channel-specific URIs, because
        # it causes Kolibri to add a Close button which leads outside the
        # channel.
        # TODO: Implement channel-specific search endpoints in Kolibri and
        #       remove this special case.
        if channel_id:
            node_query = None

        if channel_id and channel_id != "_":
            kolibri_gnome_args.extend(["--channel-id", channel_id])

        if node_path or node_query:
            kolibri_node_url = urlunparse(("kolibri", node_path, '', None, node_query, None))
            kolibri_gnome_args.append(kolibri_node_url)

        subprocess.Popen(["kolibri-gnome", *kolibri_gnome_args])

