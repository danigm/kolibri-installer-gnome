import logging

logger = logging.getLogger(__name__)

import re
import requests
import subprocess

from gettext import gettext as _
from pathlib import Path
from urllib.parse import parse_qs
from urllib.parse import urlencode
from urllib.parse import urlsplit

import pew
import pew.ui

from pew.pygobject_gtk.menus import PEWMenuItem
from pew.ui import PEWShortcut

import gi

from gi.repository import GLib
from gi.repository import Gtk

from .. import config

from ..globals import KOLIBRI_APP_DEVELOPER_EXTRAS
from ..globals import KOLIBRI_HOME_PATH
from ..globals import XDG_CURRENT_DESKTOP
from ..kolibri_daemon_proxy import KolibriDaemonProxy

from .utils import get_localized_file


INACTIVITY_TIMEOUT_MS = 10 * 1000  # 10 seconds in milliseconds


class InvalidBaseURLError(ValueError):
    pass


class MenuEventHandler:
    def on_documentation(self):
        subprocess.call(["xdg-open", "https://kolibri.readthedocs.io/en/latest/"])

    def on_forums(self):
        subprocess.call(["xdg-open", "https://community.learningequality.org/"])

    def on_new_window(self):
        self.open_window()

    def on_close_window(self):
        self.close()

    def on_open_in_browser(self):
        self.open_in_browser()

    def on_open_kolibri_home(self):
        self.open_kolibri_home()

    def on_navigate_home(self):
        self.load_url(self.initial_url)

    def on_navigate_back(self):
        self.go_back()

    def on_navigate_forward(self):
        self.go_forward()

    def on_reload(self):
        self.reload()

    def on_actual_size(self):
        self.set_zoom_level(self.default_zoom)

    def on_zoom_in(self):
        self.set_zoom_level(self.get_zoom_level() + 1)

    def on_zoom_out(self):
        self.set_zoom_level(self.get_zoom_level() - 1)

    def open_in_browser(self):
        raise NotImplementedError()

    def open_window(self):
        raise NotImplementedError()

    def open_kolibri_home(self):
        raise NotImplementedError()


class KolibriView(pew.ui.WebUIView, MenuEventHandler):
    """
    PyEverywhere UIView subclass for Kolibri. This joins the provided URL with
    Kolibri's base URL, so `load_url` can be given a relative path. In addition,
    it will pin the URL to the application's `loader_url` depending on its
    status.
    """

    def __init__(self, name, url=None, **kwargs):
        self.__initial_url = url
        self.__was_kolibri_started = False
        super().__init__(name, url, **kwargs)

    @property
    def initial_url(self):
        return self.__initial_url

    def shutdown(self):
        self.delegate.remove_window(self)

    def kolibri_change_notify(self):
        if self.__target_url:
            self.load_url(self.__target_url)
        elif self.delegate.is_internal_url(self.get_url()):
            self.load_url(self.get_url())
        else:
            # Convert current URL to a new kolibri-app URL for deferred loading
            kolibri_app_url = (
                urlsplit(self.get_url())
                ._replace(scheme="x-kolibri-app", netloc="")
                .geturl()
            )
            self.load_url(kolibri_app_url)

        is_kolibri_started = self.delegate.is_started()
        if is_kolibri_started and not self.__was_kolibri_started:
            self.on_kolibri_started()
        self.__was_kolibri_started = is_kolibri_started

    def on_kolibri_started(self):
        pass

    def load_url(self, url):
        if self.delegate.is_error():
            self.__target_url = url
            self.__load_url_error()
        elif not self.delegate.is_started():
            self.__target_url = url
            self.__load_url_loading()
        else:
            full_url = self.delegate.get_full_url(url)
            if self.get_url() != full_url:
                self.__target_url = None
                super().load_url(full_url)
                self.present_window()

    def __load_url_loading(self):
        loading_url = self.delegate.loader_url + "#loading"
        if self.current_url != loading_url:
            super().load_url(loading_url)

    def __load_url_error(self):
        error_url = self.delegate.loader_url + "#error"
        if self.current_url != error_url:
            super().load_url(error_url)

    def get_current_or_target_url(self):
        if self.__target_url is None:
            return self.get_url()
        else:
            return self.__target_url

    def open_window(self):
        self.delegate.open_window(None)

    def open_in_browser(self):
        url = self.get_current_or_target_url()
        self.delegate.open_in_browser(url)

    def open_kolibri_home(self):
        self.delegate.open_kolibri_home()


class KolibriWindowDelegate(object):
    # Quick hack to make certain application methods called by pew.ui.WebView
    # available per window, instead. This allows us to have `should_load_url`
    # behave differently for standalone windows.

    def __init__(self, window, delegate):
        self.__window = window
        self.__delegate = delegate

    def __getattr__(self, name):
        return getattr(self.__delegate, name)

    def is_internal_url(self, url):
        return self.__delegate.is_internal_url(url, window=self.__window)

    def should_load_url(self, url):
        return self.__delegate.should_load_url(url, window=self.__window)


class KolibriWindow(KolibriView):
    def __init__(self, *args, delegate=None, **kwargs):
        if delegate:
            delegate = KolibriWindowDelegate(self, delegate)

        self._open_in_browser_menu_item = PEWMenuItem(
            _("Open in Browser"), handler=self.on_open_in_browser
        )
        self._open_in_browser_menu_item.gio_action.set_enabled(False)

        self._back_menu_item = PEWMenuItem(
            _("Back"),
            handler=self.on_navigate_back,
            shortcut=PEWShortcut("[", modifiers=["CTRL"]),
        )
        self._back_menu_item.gio_action.set_enabled(False)

        self._forward_menu_item = PEWMenuItem(
            _("Forward"),
            handler=self.on_navigate_forward,
            shortcut=PEWShortcut("]", modifiers=["CTRL"]),
        )
        self._forward_menu_item.gio_action.set_enabled(False)

        self._home_menu_item = PEWMenuItem(
            _("Home"),
            handler=self.on_navigate_home,
            shortcut=PEWShortcut("Home", modifiers=["ALT"]),
        )
        self._home_menu_item.gio_action.set_enabled(True)

        menu_bar = self.build_menu_bar()

        super().__init__(*args, delegate=delegate, **kwargs)

        self.set_menubar(menu_bar)

    def build_menu_bar(self):
        # create menu bar, we do this per-window for cross-platform purposes
        menu_bar = pew.ui.PEWMenuBar()

        file_menu = pew.ui.PEWMenu(_("File"))
        file_menu.add(
            _("New Window"),
            handler=self.on_new_window,
            shortcut=PEWShortcut("N", modifiers=["CTRL"]),
        )
        file_menu.add(
            _("Close Window"),
            handler=self.on_close_window,
            shortcut=PEWShortcut("W", modifiers=["CTRL"]),
        )
        file_menu.add_separator()
        file_menu.add(_("Open Kolibri Home Folder"), handler=self.on_open_kolibri_home)

        menu_bar.add_menu(file_menu)

        view_menu = pew.ui.PEWMenu(_("View"))
        view_menu.add(_("Reload"), handler=self.on_reload)
        view_menu.add(
            _("Actual Size"),
            handler=self.on_actual_size,
            shortcut=PEWShortcut("0", modifiers=["CTRL"]),
        )
        view_menu.add(
            _("Zoom In"),
            handler=self.on_zoom_in,
            shortcut=PEWShortcut("+", modifiers=["CTRL"]),
        )
        view_menu.add(
            _("Zoom Out"),
            handler=self.on_zoom_out,
            shortcut=PEWShortcut("-", modifiers=["CTRL"]),
        )
        view_menu.add_separator()
        view_menu.add_item(self._open_in_browser_menu_item)
        menu_bar.add_menu(view_menu)

        history_menu = pew.ui.PEWMenu(_("History"))
        history_menu.add_item(self._back_menu_item)
        history_menu.add_item(self._forward_menu_item)
        history_menu.add_item(self._home_menu_item)
        menu_bar.add_menu(history_menu)

        help_menu = pew.ui.PEWMenu(_("Help"))
        help_menu.add(
            _("Documentation"),
            handler=self.on_documentation,
            shortcut=PEWShortcut("F1"),
        )
        help_menu.add(_("Community Forums"), handler=self.on_forums)
        menu_bar.add_menu(help_menu)

        return menu_bar

    def show(self):
        if hasattr(self, "gtk_window"):
            self._tweak_gtk_ui()

        # Maximize windows on Endless OS
        if hasattr(self, "gtk_window") and XDG_CURRENT_DESKTOP == "endless:GNOME":
            self.gtk_window.maximize()

        super().show()

    def _tweak_gtk_ui(self):
        # TODO: Implement this in pyeverywhere

        # Navigation buttons for the header bar

        navigation_box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
        navigation_box.get_style_context().add_class("linked")
        self._NativeWebView__gtk_header_bar.pack_start(navigation_box)

        back_button = Gtk.Button.new_from_icon_name(
            "go-previous-symbolic", Gtk.IconSize.BUTTON
        )
        back_button.set_action_name("win." + self._back_menu_item.gio_action.get_name())
        navigation_box.add(back_button)

        forward_button = Gtk.Button.new_from_icon_name(
            "go-next-symbolic", Gtk.IconSize.BUTTON
        )
        forward_button.set_action_name(
            "win." + self._forward_menu_item.gio_action.get_name()
        )
        navigation_box.add(forward_button)

        # Additional functionality for the webview

        if KOLIBRI_APP_DEVELOPER_EXTRAS:
            self.gtk_webview.get_settings().set_enable_developer_extras(True)

        self.gtk_webview.connect("create", self.__gtk_webview_on_create)
        self.gtk_webview.connect("notify::uri", self.__gtk_webview_on_notify_uri)
        self.gtk_webview.get_back_forward_list().connect(
            "changed", self.__gtk_webview_back_forward_list_on_changed
        )

        # Set WM_CLASS for improved window management
        # FIXME: GTK+ strongly discourages doing this:
        #        <https://docs.gtk.org/gtk3/method.Window.set_wmclass.html>
        #        However, our WM_CLASS becomes `"main.py", "Main.py"`, which
        #        causes GNOME Shell to treat unique instances of this
        #        application (with different application IDs) as the same.

        self.gtk_window.set_wmclass("Kolibri", self.delegate.application_id)

    def __gtk_webview_on_create(self, webview, navigation_action):
        # TODO: Implement this behaviour in pyeverywhere, and pass the related
        #       webview to the new window so it can use
        #       `WebKit2.WebView.new_with_related_view`
        target_uri = navigation_action.get_request().get_uri()
        window = self.delegate.open_window(target_uri)
        if window:
            return window.gtk_webview
        else:
            return None

    def __gtk_webview_on_notify_uri(self, webview, pspec):
        # PEWApp.should_load_url is not called when the URL fragment changes.
        # So, when the uri property changes, we need to check if the URL
        # fragment refers to content which belongs inside the standalone window.
        # Changes to other parts of the URL will go through the usual
        # should_load_url code.

        url = webview.get_uri()

        if not url:
            return

        if url == self.delegate.loader_url and self.delegate.is_started():
            self.load_url(self.initial_url)
        else:
            self.on_url_changed(url)

    def on_url_changed(self, url):
        if urlsplit(url).scheme in ("http", "https"):
            self._open_in_browser_menu_item.gio_action.set_enabled(True)
        else:
            self._open_in_browser_menu_item.gio_action.set_enabled(False)

    def accepts_url(self, url):
        return True
    def __gtk_webview_back_forward_list_on_changed(
        self, back_forward_list, item_added, items_removed
    ):
        can_go_back = back_forward_list.get_back_item() is not None
        can_go_forward = back_forward_list.get_forward_item() is not None

        self._back_menu_item.gio_action.set_enabled(can_go_back)
        self._forward_menu_item.gio_action.set_enabled(can_go_forward)


class KolibriGenericWindow(KolibriWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(_("Kolibri"), *args, **kwargs)


class KolibriChannelWindow(KolibriWindow):
    def __init__(self, channel_id, *args, **kwargs):
        self.__channel_id = channel_id
        self.__last_good_url = None
        super().__init__(_("Kolibri"), *args, **kwargs)

    @property
    def standalone_channel_id(self):
        return self.__channel_id

    def _tweak_gtk_ui(self):
        super()._tweak_gtk_ui()

        home_button = Gtk.Button.new_from_icon_name(
            "go-home-symbolic", Gtk.IconSize.BUTTON
        )
        home_button.set_action_name("win." + self._home_menu_item.gio_action.get_name())
        self._NativeWebView__gtk_header_bar.pack_start(home_button)

    def on_url_changed(self, url):
        super().on_url_changed(url)
        if not self.delegate.is_internal_url(url):
            self.load_url(self.__last_good_url)
            self.delegate.open_generic_window(url)
        else:
            self.__last_good_url = url

    def accepts_url(self, url):
        url_tuple = urlsplit(url)

        if url == self.initial_url:
            return True
        elif re.match(r"^\/(?P<lang>\w+\/)?learn\/?", url_tuple.path):
            return self.is_learn_fragment_in_channel(url_tuple.fragment.lstrip("/"))
        elif re.match(r"^\/(?P<lang>\w+\/)?user\/?", url_tuple.path):
            return True
        elif re.match(r"^\/zipcontent\/?", url_tuple.path):
            return True
        elif re.match(r"^\/static\/?", url_tuple.path):
            return True
        elif re.match(r"^\/downloadcontent\/?", url_tuple.path):
            return True
        elif re.match(r"^\/content\/storage\/?", url_tuple.path):
            return True
        else:
            return False

    def contentnode_id_for_learn_fragment(self, fragment):
        patterns = (
            r"^topics\/c\/(?P<node_id>\w+)",
            r"^topics\/t\/(?P<node_id>\w+)",
            r"^topics\/(?P<node_id>\w+)",
        )

        for pattern in patterns:
            match = re.match(pattern, fragment)
            if match:
                return match.group("node_id")

        return None

    def is_learn_fragment_in_channel(self, fragment):
        if re.match(r"^content-unavailable", fragment):
            return True

        contentnode_id = self.contentnode_id_for_learn_fragment(fragment)

        if contentnode_id is None:
            return False

        response = self.delegate.kolibri_api_get(
            "/api/content/contentnode/{contentnode_id}".format(
                contentnode_id=contentnode_id
            )
        )

        if response is None:
            return False

        contentnode_channel = response.get("channel_id")

        return contentnode_channel == self.standalone_channel_id

    def on_kolibri_started(self):
        super().on_kolibri_started()

        # TODO: Add KolibriView.set_name in pyeverywhere

        response = self.delegate.kolibri_api_get(
            "/api/content/channel/{channel_id}".format(
                channel_id=self.standalone_channel_id
            )
        )

        if response is None:
            return

        channel_name = response.get("name")

        if channel_name:
            self._NativeWebView__gtk_header_bar.set_title(channel_name)


class Application(pew.ui.PEWApp):
    handles_open_file_uris = True

    def __init__(self, application_id=None):
        self.__application_id = application_id

        self.__did_init = False
        self.__starting_kolibri = False

        loader_path = get_localized_file(
            Path(config.DATA_DIR, "assets", "_load-{}.html").as_posix(),
            Path(config.DATA_DIR, "assets", "_load.html"),
        )
        self.__loader_url = loader_path.as_uri()

        self.__kolibri_daemon = KolibriDaemonProxy.create_default()
        self.__kolibri_daemon_has_error = None
        self.__kolibri_daemon_owner = None

        self.__windows = []

        super().__init__()

        gtk_application = getattr(self, "gtk_application", None)
        if gtk_application:
            gtk_application.set_inactivity_timeout(INACTIVITY_TIMEOUT_MS)

    @property
    def application_id(self):
        return self.__application_id

    @property
    def loader_url(self):
        return self.__loader_url

    @property
    def default_url(self):
        return "x-kolibri-app:/"

    def init_ui(self):
        if len(self.__windows) > 0:
            return

        if not self.__did_init:
            self.__kolibri_daemon.init_async(
                GLib.PRIORITY_DEFAULT, None, self.__kolibri_daemon_on_init
            )

            self.__did_init = True

        self.open_window()

    def shutdown(self):
        if self.__kolibri_daemon.get_name_owner():
            try:
                self.__kolibri_daemon.release()
            except GLib.Error as error:
                logger.warning(
                    "Error calling KolibriDaemonProxy.release: {error}".format(
                        error=error
                    )
                )
        super().shutdown()

    def __kolibri_daemon_on_init(self, source, result):
        try:
            self.__kolibri_daemon.init_finish(result)
        except GLib.Error as error:
            logger.warning(
                "Error initializing KolibriDaemonProxy: {error}".format(error=error)
            )
            self.__kolibri_daemon_has_error = True
            self.__notify_all_windows()
        else:
            self.__kolibri_daemon_has_error = False
            self.__kolibri_daemon.connect("notify", self.__kolibri_daemon_on_notify)
            self.__kolibri_daemon_on_notify(self.__kolibri_daemon, None)

    def __kolibri_daemon_on_notify(self, kolibri_daemon, param_spec):
        if self.__kolibri_daemon_has_error:
            return

        kolibri_daemon_owner = kolibri_daemon.get_name_owner()
        kolibri_daemon_owner_changed = bool(
            self.__kolibri_daemon_owner != kolibri_daemon_owner
        )
        self.__kolibri_daemon_owner = kolibri_daemon_owner

        if kolibri_daemon_owner_changed:
            self.__starting_kolibri = False
            self.__kolibri_daemon.hold(
                result_handler=self.__kolibri_daemon_null_result_handler
            )

        if self.__starting_kolibri and kolibri_daemon.is_started():
            self.__starting_kolibri = False
        elif self.__starting_kolibri:
            pass
        elif not kolibri_daemon.is_error() or kolibri_daemon_owner_changed:
            self.__starting_kolibri = True
            self.__kolibri_daemon.start(
                result_handler=self.__kolibri_daemon_null_result_handler
            )

        self.__notify_all_windows()

    def __kolibri_daemon_null_result_handler(self, proxy, result, user_data):
        if isinstance(result, Exception):
            self.__kolibri_daemon_has_error = True
        else:
            self.__kolibri_daemon_has_error = False
        self.__notify_all_windows()

    def __notify_all_windows(self):
        for window in self.__windows:
            window.kolibri_change_notify()

    def is_started(self):
        return self.__kolibri_daemon.is_started()

    def is_error(self):
        return self.__kolibri_daemon_has_error or self.__kolibri_daemon.is_error()

    def __kolibri_daemon_hold(self):
        return GLib.SOURCE_REMOVE

    def __kolibri_daemon_start(self):
        return GLib.SOURCE_REMOVE

    def open_window(self, target_url=None):
        target_url = target_url or self.default_url

        if not self.should_load_url(target_url):
            if not target_url.startswith("about:"):
                self.open_in_browser(target_url)
                return None

        window = self._create_window(target_url)
        self.add_window(window)
        window.kolibri_change_notify()
        window.show()

        return window

    def _create_window(self, target_url):
        raise NotImplementedError()

    def load_url_in_window(self, target_url):
        last_window = next(
            (
                window
                for window in reversed(self.__windows)
                if isinstance(window, KolibriGenericWindow)
            ),
            None,
        )

        if last_window:
            last_window.load_url(target_url)
        else:
            self.open_window(target_url)

    def is_internal_url(self, url, window=None):
        url_tuple = urlsplit(url)

        if url_tuple.scheme in ("kolibri", "x-kolibri-app"):
            return True
        elif self.__kolibri_daemon.is_kolibri_app_url(url):
            if url_tuple.path.startswith("/app/"):
                return True
            elif window:
                return window.accepts_url(url)
            else:
                return True
        elif url == self.loader_url:
            return not self.is_started()
        else:
            return False

    def should_load_url(self, url, window=None):
        url_tuple = urlsplit(url)

        if self.is_internal_url(url, window=window):
            return True
        elif url_tuple.scheme == "about" or url == self.loader_url:
            return True
        else:
            return False

    def add_window(self, window):
        self.__windows.append(window)

    def remove_window(self, window):
        self.__windows.remove(window)

    def handle_open_file_uris(self, uris):
        for uri in uris:
            self.__open_window_for_kolibri_scheme_uri(uri)

    def __open_window_for_kolibri_scheme_uri(self, kolibri_scheme_uri):
        url_tuple = urlsplit(kolibri_scheme_uri)
        url_query = parse_qs(url_tuple.query, keep_blank_values=True)

        if url_tuple.scheme != "kolibri":
            logger.info("Invalid URI scheme: %s", kolibri_scheme_uri)
            return

        self.load_url_in_window(kolibri_scheme_uri)

    def get_full_url(self, url):
        try:
            return self.parse_kolibri_url(url)
        except ValueError:
            pass

        try:
            return self.parse_x_kolibri_app_url(url)
        except ValueError:
            pass

        return url

    def parse_kolibri_url(self, url):
        """
        Parse a URL according to the public Kolibri URL format. This format uses
        a single-character identifier for a node type - "t" for topic or "c"
        for content, followed by its unique identifier. It is constrained to
        opening content nodes or search pages.

        Examples:

        - kolibri:t/TOPIC_NODE_ID?searchTerm=addition
        - kolibri:c/CONTENT_NODE_ID
        - kolibri:?searchTerm=addition
        """

        url_tuple = urlsplit(url)
        url_query = parse_qs(url_tuple.query, keep_blank_values=True)

        if url_tuple.scheme != "kolibri":
            raise ValueError()

        if url_tuple.path and url_tuple.path != "/":
            item_path = "/learn"
            item_fragment = "/topics/" + url_tuple.path.lstrip("/")
        elif url_tuple.query:
            item_path = "/learn"
            item_fragment = "/search"
        else:
            item_path = "/"
            item_fragment = ""

        if "searchTerm" in url_query:
            item_fragment += "?searchTerm={search}".format(
                search=url_query["searchTerm"]
            )

        target_url = "{path}#{fragment}".format(path=item_path, fragment=item_fragment)
        return self.__kolibri_daemon.get_kolibri_initialize_url(target_url)

    def parse_x_kolibri_app_url(self, url):
        """
        Parse a URL according to the internal Kolibri app URL format. This
        format is the same as Kolibri's URLs, but without the hostname or port
        number.

        - x-kolibri-app:/device
        """

        url_tuple = urlsplit(url)

        if url_tuple.scheme != "x-kolibri-app":
            raise ValueError()

        target_url = url_tuple._replace(scheme="", netloc="").geturl()
        return self.__kolibri_daemon.get_kolibri_initialize_url(target_url)

    def kolibri_api_get(self, path, *args, **kwargs):
        url = self.__kolibri_daemon.get_kolibri_url(path)
        if url:
            request = requests.get(url, *args, **kwargs)
        else:
            logger.debug("Skipping Kolibri API request: Kolibri is not ready")
            return None

        try:
            return request.json()
        except ValueError as error:
            logger.info(
                "Error reading Kolibri API response: {error}".format(error=error)
            )
            return None

    def open_in_browser(self, url):
        subprocess.call(["xdg-open", url])

    def open_kolibri_home(self):
        # TODO: It would be better to open self.__kolibri_daemon.kolibri_home,
        #       but the Flatpak's OpenURI portal only allows us to open files
        #       that exist in our sandbox.
        subprocess.call(["xdg-open", KOLIBRI_HOME_PATH.as_uri()])

    def quit(self):
        for window in self.__windows:
            window.close()


class GenericApplication(Application):
    def _create_window(self, target_url=None):
        return KolibriGenericWindow(target_url, delegate=self)


class ChannelApplication(Application):
    def __init__(self, channel_id, *args, **kwargs):
        self.__channel_id = channel_id
        super().__init__(*args, **kwargs)

    @property
    def channel_id(self):
        return self.__channel_id

    @property
    def default_url(self):
        return "x-kolibri-app:/learn#topics/{channel_id}".format(
            channel_id=self.channel_id
        )

    def _create_window(self, target_url=None):
        return KolibriChannelWindow(self.channel_id, target_url, delegate=self)

    def open_generic_window(self, target_url):
        # TODO: Translate to a `kolibri:` URL and open that if possible
        self.open_in_browser(target_url)
