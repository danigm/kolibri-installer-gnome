import collections
from concurrent.futures import ProcessPoolExecutor

from ..globals import init_kolibri
from ..globals import init_logging


NODE_ICON_LOOKUP = {
    "video": "video-x-generic",
    "exercise": "edit-paste",
    "document": "x-office-document",
    "topic": "folder",
    "audio": "audio-x-generic",
    "html5": "text-html",
    "slideshow": "image-x-generic",
}

DEFAULT_NODE_ICON = "application-x-executable"


class SearchHandler(object):
    class SearchHandlerFailed(Exception):
        pass

    def get_item_ids_for_search(self, search):
        """
        Returns a list of item IDs matching a search query.
        """

        raise NotImplementedError()

    def get_metadata_for_item_ids(self, item_ids):
        """
        Returns a list of search metadata objects for the given item IDs.
        """

        raise NotImplementedError()

    @staticmethod
    def _node_data_to_item_id(node_data):
        """
        Converts a Kolibri node ID to an item ID for a search result. This
        encodes the node type and node ID and matches the format Kolibri uses
        for URLs.
        """

        node_id = node_data.get("id")
        channel_id = node_data.get("channel_id")

        if node_data.get("kind") == "topic":
            return "t/{node_id}?{context}".format(node_id=node_id, context=channel_id)
        else:
            return "c/{node_id}?{context}".format(node_id=node_id, context=channel_id)

    @staticmethod
    def _item_id_to_node_id(item_id):
        """
        Converts an item ID from a search result back to a Kolibri node ID.
        Raises ValueError if item_id is an invalid format.
        """

        _kind_code, _sep, node_id_and_context = item_id.partition("/")
        node_id, _sep, _context = node_id_and_context.partition("?")
        return node_id

    @staticmethod
    def _node_data_to_search_metadata(item_id, node_data):
        """
        Given a node data object, returns search metadata as described in the
        GNOME Shell SearchProvider interface:
        <https://developer.gnome.org/SearchProvider/#The_SearchProvider_interface>
        """

        if not isinstance(node_data, collections.Mapping):
            return None

        node_icon = NODE_ICON_LOOKUP.get(node_data.get("kind"), DEFAULT_NODE_ICON)

        return {
            "id": item_id,
            "name": node_data.get("title"),
            "description": node_data.get("description"),
            "gicon": node_icon,
        }


class LocalSearchHandler(SearchHandler):
    """
    Search handler that uses the locally available Kolibri database files. This
    works by setting up Django and calling into Kolibri's Python code directly.
    We use multiprocessing.Pool as a convenient way to pass results between
    processes. The actual work is IO-bound so we won't bother creating more than
    one process, but it is useful running the search handler in a separate
    process to avoid globals leaking into the main thread.
    """

    def __init__(self):
        self.__executor = None

    def init(self):
        self.__executor = ProcessPoolExecutor(
            max_workers=1, initializer=self.__process_initializer
        )

    def stop(self):
        self.__executor.shutdown()

    def __process_initializer(self):
        from setproctitle import setproctitle

        setproctitle("kolibri-daemon-search")

        init_logging("kolibri-daemon-search.txt")

        init_kolibri()

    def get_item_ids_for_search(self, search):
        args = (search,)
        future = self.__executor.submit(
            LocalSearchHandler._get_item_ids_for_search, args
        )
        return future.result()

    def get_metadata_for_item_ids(self, item_ids):
        return list(
            filter(
                lambda metadata: metadata is not None,
                self.__executor.map(
                    LocalSearchHandler._get_metadata_for_item_id, item_ids
                ),
            )
        )

    @staticmethod
    def _get_item_ids_for_search(search):
        from kolibri.core.content.api import ContentNodeSearchViewset
        from kolibri.dist.rest_framework.test import APIRequestFactory

        request = APIRequestFactory().get("", {"search": search, "max_results": 10})
        search_view = ContentNodeSearchViewset.as_view({"get": "list"})
        response = search_view(request)
        search_results = response.data.get("results", [])

        return list(map(SearchHandler._node_data_to_item_id, search_results))

    @staticmethod
    def _get_metadata_for_item_id(item_id):
        from kolibri.core.content.api import ContentNodeViewset
        from kolibri.dist.rest_framework.test import APIRequestFactory

        node_id = SearchHandler._item_id_to_node_id(item_id)

        request = APIRequestFactory().get("", {})
        node_view = ContentNodeViewset.as_view({"get": "retrieve"})
        response = node_view(request, pk=node_id)
        node_data = response.data

        return SearchHandler._node_data_to_search_metadata(item_id, node_data)
