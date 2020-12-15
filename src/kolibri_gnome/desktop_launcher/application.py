import logging

logger = logging.getLogger(__name__)

import json
import os
import subprocess
import threading
import time

from gettext import gettext as _
from urllib.parse import urlsplit

import pew
import pew.ui

from pew.ui import PEWShortcut

import gi

gi.require_version("WebKit2", "4.0")
from gi.repository import WebKit2
from gi.repository import Gio

from .. import config

from ..globals import KOLIBRI_APP_DEVELOPER_EXTRAS, KOLIBRI_HOME, XDG_CURRENT_DESKTOP
from .utils import get_localized_file


class RedirectLoading(Exception):
    pass


class RedirectError(Exception):
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
        subprocess.call(["xdg-open", self.get_current_or_target_url()])

    def on_open_kolibri_home(self):
        subprocess.call(["xdg-open", KOLIBRI_HOME])

    def on_back(self):
        self.go_back()

    def on_forward(self):
        self.go_forward()

    def on_reload(self):
        self.reload()

    def on_actual_size(self):
        self.set_zoom_level(self.default_zoom)

    def on_zoom_in(self):
        self.set_zoom_level(self.get_zoom_level() + 1)

    def on_zoom_out(self):
        self.set_zoom_level(self.get_zoom_level() - 1)

    def get_url(self):
        raise NotImplementedError()

    def open_window(self):
        raise NotImplementedError()


class KolibriView(pew.ui.WebUIView, MenuEventHandler):
    def __init__(
        self, name, url, loader_url=None, await_kolibri_fn=lambda: None, **kwargs
    ):
        self.__loader_url = loader_url
        self.__await_kolibri_fn = await_kolibri_fn
        self.__target_url = None
        self.__load_url_lock = threading.Lock()
        self.__redirect_thread = None

        super().__init__(name, url, **kwargs)

    @property
    def target_url(self):
        return self.__target_url

    def shutdown(self):
        self.delegate.remove_window(self)

    def load_url(self, url):
        with self.__load_url_lock:
            self.__target_url = url
            try:
                redirect_url = self.delegate.get_redirect_url(url)
            except RedirectLoading:
                self.__load_url_loading()
            except RedirectError:
                self.__load_url_error()
            else:
                super().load_url(redirect_url)
        self.present_window()

    def get_current_or_target_url(self):
        if self.current_url == self.__loader_url:
            return self.__target_url
        else:
            return self.get_url()

    def is_showing_loading_screen(self):
        return self.current_url == self.__loader_url

    def __load_url_loading(self):
        if self.current_url != self.__loader_url:
            super().load_url(self.__loader_url)

        if not self.__redirect_thread:
            self.__redirect_thread = pew.ui.PEWThread(
                target=self.__do_redirect_on_load, args=()
            )
            self.__redirect_thread.daemon = True
            self.__redirect_thread.start()

    def __load_url_error(self):
        if self.current_url == self.__loader_url:
            pew.ui.run_on_main_thread(self.evaluate_javascript, "show_error()")
        else:
            super().load_url(self.__loader_url)
            pew.ui.run_on_main_thread(
                self.evaluate_javascript, "window.onload = function() { show_error() }"
            )

    def __do_redirect_on_load(self):
        self.__await_kolibri_fn()
        self.load_url(self.__target_url)

    def open_window(self):
        target_url = self.get_url()
        if target_url == self.__loader_url:
            self.delegate.open_window(None)
        else:
            self.delegate.open_window(target_url)


class KolibriWindow(KolibriView):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

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
        view_menu.add(_("Open in Browser"), handler=self.on_open_in_browser)
        menu_bar.add_menu(view_menu)

        history_menu = pew.ui.PEWMenu(_("History"))
        history_menu.add(
            _("Back"),
            handler=self.on_back,
            shortcut=PEWShortcut("[", modifiers=["CTRL"]),
        )
        history_menu.add(
            _("Forward"),
            handler=self.on_forward,
            shortcut=PEWShortcut("]", modifiers=["CTRL"]),
        )
        menu_bar.add_menu(history_menu)

        help_menu = pew.ui.PEWMenu(_("Help"))
        help_menu.add(
            _("Documentation"),
            handler=self.on_documentation,
            shortcut=PEWShortcut("F1"),
        )
        help_menu.add(_("Community Forums"), handler=self.on_forums)
        menu_bar.add_menu(help_menu)

        self.set_menubar(menu_bar)

    def show(self):
        # TODO: Implement this in pyeverywhere
        if KOLIBRI_APP_DEVELOPER_EXTRAS:
            self.gtk_webview.get_settings().set_enable_developer_extras(True)
        self.gtk_webview.connect("decide-policy", self.__gtk_webview_on_decide_policy)
        self.gtk_webview.connect("create", self.__gtk_webview_on_create)

        # Maximize windows on Endless OS
        if hasattr(self, "gtk_window") and XDG_CURRENT_DESKTOP == "endless:GNOME":
            self.gtk_window.maximize()

        super().show()

    def __gtk_webview_on_decide_policy(self, webview, decision, decision_type):
        if decision_type == WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION:
            # Force internal _blank links to open in the same window
            target_uri = decision.get_request().get_uri()
            frame_name = decision.get_frame_name()
            if frame_name == "_blank" and self.delegate.is_kolibri_app_url(target_uri):
                decision.ignore()
                pew.ui.run_on_main_thread(self.load_url, target_uri)
                return True
        return False

    def __gtk_webview_on_create(self, webview, navigation_action):
        # TODO: Implement this behaviour in pyeverywhere, and pass the related
        #       webview to the new window so it can use
        #       `WebKit2.WebView.new_with_related_view`
        target_uri = navigation_action.get_request().get_uri()
        if self.delegate.is_kolibri_app_url(target_uri):
            window = self.delegate.open_window(target_uri)
            return window.gtk_webview
        else:
            subprocess.call(["xdg-open", target_uri])
            return None


class KolibriDaemonProxy(object):
    def __init__(self, application):
        self.__application = application
        self.__proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.NONE,
            None,
            config.DAEMON_APPLICATION_ID,
            "/org/learningequality/Kolibri/Devel/Daemon",
            "org.learningequality.Kolibri.Daemon",
            None
        )
        self.__is_ready_event = threading.Event()
        self.__is_ready_value = None
        self.__proxy.connect("g_properties_changed", self.__on_proxy_g_properties_changed)
        self.__update_is_ready_event()

    def __on_proxy_g_properties_changed(self, proxy, changed_properties, invalidated_properties):
        self.__update_is_ready_event()

    @property
    def app_key(self):
        variant = self.__proxy.get_cached_property("AppKey")
        return variant.get_string()

    @property
    def base_url(self):
        variant = self.__proxy.get_cached_property("BaseURL")
        return variant.get_string()

    @property
    def status(self):
        variant = self.__proxy.get_cached_property("Status")
        return variant.get_string()

    def hold(self):
        self.__proxy.call_sync("Hold", None, Gio.DBusCallFlags.NONE, -1, None)

    def release(self):
        self.__proxy.call_sync("Release", None, Gio.DBusCallFlags.NONE, -1, None)

    def is_loading(self):
        if not self.app_key or not self.base_url:
            return True
        else:
            return self.status in ["NONE", "STARTING"]

    def is_started(self):
        if self.app_key and self.base_url:
            return self.status in ["STARTED"]
        else:
            return False

    def is_error(self):
        return self.status in ["ERROR"]

    def __update_is_ready_event(self):
        if self.is_started() or self.is_error():
            self.__is_ready_event.set()
        else:
            self.__is_ready_event.clear()

    def await_is_ready(self):
        self.__is_ready_event.wait()
        return self.is_started()

    def is_kolibri_app_url(self, url):
        if callable(url):
            return True

        if not url:
            return False
        elif not url.startswith(self.base_url):
            return False
        elif url.startswith(self.base_url + "static/"):
            return False
        elif url.startswith(self.base_url + "downloadcontent/"):
            return False
        elif url.startswith(self.base_url + "content/storage/"):
            return False
        else:
            return True

    def get_initialize_url(self, next_url):
        if callable(next_url):
            next_url = next_url()
        return self.__get_kolibri_initialize_url(next_url)

    def __get_kolibri_initialize_url(self, next_url):
        path = "app/api/initialize/{key}".format(key=self.app_key)
        if next_url:
            path += "?next={next_url}".format(next_url=next_url)
        return self.base_url + path.lstrip("/")



class Application(pew.ui.PEWApp):
    application_id = config.FRONTEND_APPLICATION_ID

    handles_open_file_uris = True

    def __init__(self, *args, **kwargs):
        loader_path = get_localized_file(
            os.path.join(config.DATA_DIR, "assets", "_load-{}.html"),
            os.path.join(config.DATA_DIR, "assets", "_load.html"),
        )
        self.__loader_url = "file://{path}".format(path=os.path.abspath(loader_path))

        self.__kolibri_service_manager = KolibriDaemonProxy(self)

        self.__windows = []

        super().__init__(*args, **kwargs)

    def init_ui(self):
        if len(self.__windows) > 0:
            return

        main_window = self.__open_window()

        # Check for saved URL, which exists when the app was put to sleep last time it ran
        saved_state = main_window.get_view_state()
        logger.debug("Persisted View State: %s", saved_state)

        saved_url = saved_state.get("URL")
        if self.__kolibri_service_manager.is_kolibri_app_url(saved_url):
            pew.ui.run_on_main_thread(main_window.load_url, saved_url)

    def shutdown(self):
        self.__kolibri_service_manager.release()
        super().shutdown()

    def should_load_url(self, url):
        if self.is_kolibri_app_url(url):
            return True
        elif self.__is_loader_url(url):
            return not self.__kolibri_service_manager.is_started()
        elif not url.startswith("about:"):
            subprocess.call(["xdg-open", url])
            return False
        return True

    def is_kolibri_app_url(self, url):
        return self.__kolibri_service_manager.is_kolibri_app_url(url)

    def get_redirect_url(self, url):
        if self.__kolibri_service_manager.is_error():
            raise RedirectError()
        elif self.__kolibri_service_manager.is_loading():
            raise RedirectLoading()
        elif self.__kolibri_service_manager.is_kolibri_app_url(url):
            return self.__kolibri_service_manager.get_initialize_url(url)
        else:
            return url

    def open_window(self, target_url=None):
        return self.__open_window(target_url)

    def __open_window(self, target_url=None):
        self.__kolibri_service_manager.hold()

        target_url = target_url or self.__get_base_url
        window = KolibriWindow(
            _("Kolibri"),
            target_url,
            delegate=self,
            loader_url=self.__loader_url,
            await_kolibri_fn=self.__kolibri_service_manager.await_is_ready,
        )
        self.add_window(window)
        window.show()
        return window

    def __get_base_url(self):
        return self.__kolibri_service_manager.base_url

    def add_window(self, window):
        self.__windows.append(window)

    def remove_window(self, window):
        self.__windows.remove(window)

    def handle_open_file_uris(self, uris):
        for uri in uris:
            self.__open_window_for_kolibri_scheme_uri(uri)

    def __open_window_for_kolibri_scheme_uri(self, kolibri_scheme_uri):
        parse = urlsplit(kolibri_scheme_uri)

        if parse.scheme != "kolibri":
            logger.info("Invalid URI scheme: %s", kolibri_scheme_uri)
            return

        if parse.path and parse.path != "/":
            item_path = "/learn"
            if parse.path.startswith("/"):
                # Sometimes the path has a / prefix. We need to avoid double
                # slashes for Kolibri's JavaScript router.
                item_fragment = "/topics" + parse.path
            else:
                item_fragment = "/topics/" + parse.path
        elif parse.query:
            item_path = "/learn"
            item_fragment = "/search"
        else:
            item_path = "/"
            item_fragment = ""

        if parse.query:
            item_fragment += "?{}".format(parse.query)

        # FIXME: This is broken. Fix before merging.
        target_url = self.__kolibri_service_manager.get_base_url(
            path=item_path, fragment=item_fragment
        )

        blank_window = self.__find_blank_window()

        if blank_window:
            blank_window.load_url(target_url)
        else:
            self.__open_window(target_url)

    def __find_blank_window(self):
        # If a window hasn't navigated away from the landing page, we will
        # treat it as a "blank" window which can be reused to show content
        # from handle_open_file_uris.
        for window in reversed(self.__windows):
            if window.target_url == self.__kolibri_service_manager.base_url:
                return window
        return None

    def __is_loader_url(self, url):
        return url and not callable(url) and url.startswith(self.__loader_url)
