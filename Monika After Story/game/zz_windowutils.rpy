#NOTE: This ONLY works for Windows atm

#Whether Monika can use notifications or not
default persistent._mas_enable_notifications = False

#Whether notification sounds are enabled or not
default persistent._mas_notification_sounds = True

#Whether Monika can see your active window or not
default persistent._mas_windowreacts_windowreacts_enabled = False

#Persistent windowreacts db
default persistent._mas_windowreacts_database = dict()

#A global list of events we DO NOT want to unlock on a new session
default persistent._mas_windowreacts_no_unlock_list = list()

#A dict of locations where notifs are used, and if they're enabled for said location
default persistent._mas_windowreacts_notif_filters = dict()

init -10 python in mas_windowreacts:
    #We need this in case we cannot get access to the libs, so everything can still run
    can_show_notifs = True

    #If we don't have access to the required libs to do windowreact related things
    can_do_windowreacts = True

    #The windowreacts db
    windowreact_db = {}

    #Group list, to populate the menu screen
    #NOTE: We do this so that we don't have to try to get a notification
    #In order for it to show up in the menu and in the dict
    _groups_list = [
        "Topic Alerts",
        "Window Reactions",
    ]

init python in mas_windowutils:
    import os
    import json
    import subprocess

    import store
    from store import mas_utils
    #The initial setup

    # The window object, used on Linux systems, otherwise always None
    MAS_WINDOW = None

    # Active-window getter for Wayland compositors. None means no
    # compositor-specific getter is available (fall back to X11/XWayland).
    __active_window_getter = None
    #Helpers for Wayland active-window detection. Wayland has no
    #compositor-independent API for reading the active window, so we detect the
    #compositor and pick a case-by-case getter. Hyprland and Sway cover the
    #majority of cases; GNOME is handled via the "Focused Window D-Bus" shell
    #extension when present, otherwise it falls through.
    def _haveBin(binary):
        """
        Checks if a binary is available on PATH (Python 2 safe, no shutil.which)
        """
        for path in os.environ.get("PATH", "").split(os.pathsep):
            full = os.path.join(path, binary)
            if os.path.isfile(full) and os.access(full, os.X_OK):
                return True
        return False

    def _findFocusedNode(node):
        """
        Recursively finds the focused window node in a sway/i3 tree.
        """
        if node.get("focused"):
            return node
        for child in node.get("nodes", []):
            result = _findFocusedNode(child)
            if result is not None:
                return result
        for child in node.get("floating_nodes", []):
            result = _findFocusedNode(child)
            if result is not None:
                return result
        return None

    def __getActiveWindowTitle_Hyprland():
        """
        Gets the active window title via the Hyprland IPC (hyprctl).
        """
        try:
            output = subprocess.check_output(
                ["hyprctl", "-j", "activewindow"],
                stderr=subprocess.STDOUT
            )
            data = json.loads(output)
            if data:
                return data.get("title", "")
        except Exception:
            pass
        return None

    def __getActiveWindowTitle_Sway():
        """
        Gets the active window title via the Sway/i3 IPC (swaymsg).
        """
        try:
            output = subprocess.check_output(
                ["swaymsg", "-t", "get_tree"],
                stderr=subprocess.STDOUT
            )
            tree = json.loads(output)
            focused = _findFocusedNode(tree)
            if focused:
                return focused.get("name", "")
        except Exception:
            pass
        return None

    def __getActiveWindowTitle_GNOME():
        """
        Gets the active window title on GNOME Wayland via the "Focused Window
        D-Bus" shell extension.

        The extension exposes a GVariant tuple containing JSON, e.g.:
            ('{"class": "...", "title": "..."}',)
        We parse the JSON and return its "title".

        NOTE: if the extension is not installed/reachable this returns "" so
        callers fall through (e.g. to XWayland) gracefully.
        """
        try:
            #subprocess.DEVNULL is Python 3 only, so use os.devnull on Python 2
            with open(os.devnull, "w") as devnull:
                cmd = [
                    "gdbus", "call", "--session",
                    "--dest", "org.gnome.Shell",
                    "--object-path", "/org/gnome/shell/extensions/FocusedWindow",
                    "--method", "org.gnome.shell.extensions.FocusedWindow.Get",
                ]
                raw = subprocess.check_output(cmd, stderr=devnull).strip()

            #Strip GVariant tuple quotes: ('{...}',) -> {...}
            if (raw.startswith("('") and raw.endswith("',)")
                    or raw.endswith("')")):
                start_idx = raw.find("'") + 1
                end_idx = raw.rfind("'")
                json_str = raw[start_idx:end_idx]
                data = json.loads(json_str)
                return data.get("title", "")
        except Exception:
            pass
        return ""

    def __getActiveWindowTitle_KWin():
        """
        Gets the active window title on KDE Plasma / KWin (Wayland or X11).

        NOTE: requires the `kdotool` utility to be installed (an xdotool-like
        tool that talks to KWin's D-Bus scripting interface). Without it this
        returns "" and callers fall through to XWayland.
        """
        try:
            wid = subprocess.check_output(
                ["kdotool", "getactivewindow"],
                stderr=subprocess.STDOUT
            ).strip()
            if not wid:
                return ""
            title = subprocess.check_output(
                ["kdotool", "getwindowname", wid],
                stderr=subprocess.STDOUT
            ).strip()
            #Decode to unicode so regex matching in the windowreact db works
            try:
                return unicode(title, "utf-8")
            except Exception:
                return title
        except Exception:
            pass
        return ""

    def __detectWaylandCompositor():
        """
        Detects the Wayland compositor and returns the matching active-window
        getter function, or None if none is available.

        OUT:
            callable returning an active window title (or None/""), or None
        """
        desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or "").lower()

        #Hyprland
        if "hyprland" in desktop or os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
            if _haveBin("hyprctl"):
                return __getActiveWindowTitle_Hyprland

        #Sway (and other wlroots compositors with swaymsg)
        if "sway" in desktop or os.environ.get("SWAYSOCK"):
            if _haveBin("swaymsg"):
                return __getActiveWindowTitle_Sway

        #KDE Plasma / KWin via kdotool
        if "kde" in desktop or "plasma" in desktop or "kwin" in desktop:
            if _haveBin("kdotool"):
                return __getActiveWindowTitle_KWin

        #GNOME: only if the focused-window D-Bus extension is reachable
        if "gnome" in desktop and _haveBin("gdbus"):
            return __getActiveWindowTitle_GNOME

        #Nothing compositor-specific matched
        return None

    def __initX11(context):
        """
        Attempts to initialize the X11 display handles. On Wayland this only
        sees XWayland (X11) windows, used as a fallback when no compositor
        getter applies.

        IN:
            context - string describing the session for logging
        """
        global __display, __root
        __display = None
        __root = None
        try:
            if os.environ.get("DISPLAY"):
                import Xlib

                from Xlib.display import Display
                from Xlib.error import BadWindow, XError

                __display = Display()
                __root = __display.screen().root
        except Exception as e:
            mas_utils.mas_log.warning(
                "Xlib unavailable on {}: {}".format(context, e)
            )

    #We can only do this on windows
    if renpy.windows:
        #We need to extend the sys path to see our packages
        import sys
        sys.path.append(renpy.config.gamedir + '\\python-packages\\')

        #We try/catch/except to make sure the game can run if load fails here
        try:
            #Going to import win32gui for use in destroying notifs
            import win32gui
            #Import win32api so we know if we can or cannot use notifs
            import win32api

            #Since importing the required libs was successful, we can move onto importing and initializing a balloontip
            import balloontip

            #And finally, import the internal functions to make getting window handle easier
            from win32gui import GetWindowText, GetForegroundWindow

            #Now we initialize the notification class
            __tip = balloontip.WindowsBalloonTip()

            #Now we set the hwnd of this temporarily
            __tip.hwnd = None

        except Exception as e:
            #If we fail to import, then we're going to have to make sure nothing can run.
            store.mas_windowreacts.can_show_notifs = False
            store.mas_windowreacts.can_do_windowreacts = False

            #Log this
            store.mas_utils.mas_log.warning(
                "win32api/win32gui failed to be imported, disabling notifications: {}".format(e)
            )


    elif renpy.linux:
        #Get session type
        session_type = os.environ.get("XDG_SESSION_TYPE")

        #Wayland: active-window support is compositor-specific
        if session_type == "wayland":
            __active_window_getter = __detectWaylandCompositor()

            #XWayland fallback: X11 apps still expose active windows via Xlib
            __initX11("Wayland")

            if __active_window_getter is None and __display is None:
                store.mas_windowreacts.can_do_windowreacts = False
                store.mas_utils.mas_log.warning("Wayland active-window detection unavailable, disabling window reactions.")
            else:
                store.mas_utils.mas_log.debug(
                    "Wayland window reactions enabled via: {}".format(
                        getattr(__active_window_getter, "__name__", "xwayland")
                    )
                )

        #X11 however is fine
        elif session_type == "x11":
            __initX11("X11")

            if __display is None:
                store.mas_windowreacts.can_do_windowreacts = False

        else:
            store.mas_windowreacts.can_do_windowreacts = False
            store.mas_utils.mas_log.warning("Cannot detect current session type, disabling window reactions.")

    else:
        store.mas_windowreacts.can_do_windowreacts = False


    class MASWindowFoundException(Exception):
        """
        Custom exception class to flag a window found during a window enum

        Has the hwnd as a property
        """
        def __init__(self, hwnd):
            self.hwnd = hwnd

        def __str__(self):
            return self.hwnd

    #Fallback Const Defintion
    DEF_MOUSE_POS_RETURN = (0, 0)

    ##Now, we start defining OS specific functions which we can set to a var for proper cross platform on a single func
    #Firstly, the internal helper functions
    def __getActiveWindowObj_Linux():
        """
        Gets the active window object

        OUT:
            Xlib.display.Window, or None if errors occur (or not possible to get window obj)
        """
        #If not possible to get active window, we'll just return None
        if not store.mas_windowreacts.can_do_windowreacts:
            return None

        NET_ACTIVE_WINDOW = __display.intern_atom("_NET_ACTIVE_WINDOW")

        # Perform nullchecks on property getters, just in case.
        active_winid_prop = __root.get_full_property(NET_ACTIVE_WINDOW, 0)

        if active_winid_prop is None:
            return None

        active_winid = active_winid_prop.value[0]

        try:
            return __display.create_resource_object("window", active_winid)
        except XError as e:
            mas_utils.mas_log.error("Failed to get active window object: {}".format(e))
            return None

    def __getMASWindowLinux():
        """
        Funtion to get the MAS window on Linux systems

        OUT:
            Xlib.display.Window representing the MAS window

        ASSUMES: OS IS LINUX (renpy.linux)
        """
        #If not possible to get MAS window, we'll just return None
        if not store.mas_windowreacts.can_do_windowreacts:
            return None

        NET_CLIENT_LIST_ATOM = __display.intern_atom('_NET_CLIENT_LIST', False)

        try:
            prop = __root.get_full_property(NET_CLIENT_LIST_ATOM, 0)
            # Apparently x-window can return None here, the reason is unknown to me,
            # but we can just sanity check it, per #9421
            if prop is None:
                return

            winid_list = prop.value
            for winid in winid_list:
                win = __display.create_resource_object("window", winid)
                transient_for = win.get_wm_transient_for()
                winname = win.get_wm_name()

                if transient_for is None and winname and store.mas_getWindowTitle() == winname:
                    return win

        except XError as e:
            mas_utils.mas_log.error("Failed to get MAS window object: {}".format(e))
            return None

    def __getMASWindowHWND():
        """
        Gets the hWnd of the MAS window

        NOTE: Windows ONLY

        OUT:
            int - represents the hWnd of the MAS window
        """
        #Verify we can actually do this before doing anything
        if not store.mas_windowreacts.can_do_windowreacts:
            return None

        def checkMASWindow(hwnd, lParam):
            """
            Internal function to identify the MAS window. Raises an exception when found to allow the main func to return
            """
            if store.mas_getWindowTitle() == win32gui.GetWindowText(hwnd):
                raise MASWindowFoundException(hwnd)

        try:
            win32gui.EnumWindows(checkMASWindow, None)

        except MASWindowFoundException as e:
            return e.hwnd

        mas_utils.mas_log.error("Failed to get MAS window hwnd")
        return None

    def __getAbsoluteGeometry(win):
        """
        Returns the (x, y, height, width) of a window relative to the top-left
        of the screen.

        IN:
            win - Xlib.display.Window object representing the window we wish to get absolute geometry of

        OUT:
            tuple, (x, y, width, height) if possible, otherwise None
        """
        #If win is None, then we should just return a None here
        if win is None:
            # This handles some odd issues with setting window on Linux
            win = _setMASWindow()
            if win is None:
                return None

        try:
            geom = win.get_geometry()
            (x, y) = (geom.x, geom.y)

            while True:
                parent = win.query_tree().parent
                pgeom = parent.get_geometry()
                x += pgeom.x
                y += pgeom.y
                if parent.id == __root.id:
                    break
                win = parent

            return (x, y, geom.width, geom.height)

        except Xlib.error.BadDrawable:
            #In the case of a bad drawable, we'll try to re-get the MAS window to get a good one
            _setMASWindow()

        except XError as e:
            mas_utils.mas_log.error("Failed to get window geometry: {}".format(e))

        return None

    def _setMASWindow():
        """
        Sets the MAS_WINDOW global on Linux systems

        OUT:
            the window object
        """
        global MAS_WINDOW

        if renpy.linux:
            MAS_WINDOW = __getMASWindowLinux()

        else:
            MAS_WINDOW = None

        return MAS_WINDOW

    #Next, the active window handle getters
    def _getActiveWindowHandle_Windows():
        """
        Funtion to get the active window on Windows systems

        OUT:
            string representing the active window handle

        ASSUMES: OS IS WINDOWS (renpy.windows)
        """
        return unicode(GetWindowText(GetForegroundWindow()))

    def _getActiveWindowHandle_Linux():
        """
        Funtion to get the active window on Linux systems

        OUT:
            string representing the active window handle

        ASSUMES: OS IS LINUX (renpy.linux)
        """
        #Compositor-specific getter first (Hyprland/Sway/GNOME on Wayland).
        #If it yields a title we're done; otherwise fall through to X11/XWayland.
        if __active_window_getter is not None:
            title = __active_window_getter()
            if title:
                return title

        #If we have no X11 (XWayland) display, there's nothing else to try
        if __display is None:
            return ""

        NET_WM_NAME = __display.intern_atom("_NET_WM_NAME")
        active_winobj = __getActiveWindowObj_Linux()

        if active_winobj is None:
            return ""

        try:
            # Subsequent method calls might raise BadWindow exception if active_winid refers to nonexistent window.
            active_winname_prop = active_winobj.get_full_property(NET_WM_NAME, 0)

            if active_winname_prop is not None:
                active_winname = unicode(active_winname_prop.value, encoding = "utf-8")
                return active_winname.replace("\n", "")

            else:
                return ""

        except BadWindow:
            return ""

        except XError as e:
            mas_utils.mas_log.error("Failed to get active window handle: {}".format(e))

        return ""

    def _getActiveWindowHandle_OSX():
        """
        Gets the active window on macOS

        NOTE: This currently just returns an empty string, this is because we do not have active window detection
        for MacOS
        """
        return ""

    #Notif show internals
    def _tryShowNotification_Windows(title, body):
        """
        Tries to push a notification to the notification center on Windows.
        If it can't it should fail silently to the user.

        IN:
            title - notification title
            body - notification body

        OUT:
            bool. True if the notification was successfully sent, False otherwise
        """
        # The Windows way, notif_success is adjusted if need be
        notif_success = __tip.showWindow(title, body)

        #We need the IDs of the notifs to delete them from the tray
        store.destroy_list.append(__tip.hwnd)
        return notif_success

    def _tryShowNotification_Linux(title, body):
        """
        Tries to push a notification to the notification center on Linux.
        If it can't it should fail silently to the user.

        IN:
            title - notification title
            body - notification body

        OUT:
            bool - True, representing the notification's success
        """
        # Single quotes have to be escaped.
        # Since single quoting in POSIX shell doesn't allow escaping,
        # we have to close the quotation, insert a literal single quote and reopen the quotation.
        body  = body.replace("'", "'\\''")
        title = title.replace("'", "'\\''") # better safe than sorry
        os.system("notify-send '{0}' '{1}' -a 'Monika' -u low".format(title, body))
        return True

    def _tryShowNotification_OSX(title, body):
        """
        Tries to push a notification to the notification center on macOS.
        If it can't it should fail silently to the user.

        IN:
            title - notification title
            body - notification body

        OUT:
            bool - True, representing the notification's success
        """
        os.system('osascript -e \'display notification "{0}" with title "{1}"\''.format(body, title))
        return True

    #Mouse Position related funcs
    def _getAbsoluteMousePos_Windows():
        """
        Returns an (x, y) co-ord tuple for the mouse position

        OUT:
            tuple representing the absolute position of the mouse
        """
        if store.mas_windowreacts.can_do_windowreacts:
            #Try except here because we may not have permissions to do so
            try:
                cur_pos = win32gui.GetCursorPos()
            except Exception:
                cur_pos = DEF_MOUSE_POS_RETURN

        else:
            cur_pos = DEF_MOUSE_POS_RETURN

        return cur_pos

    def _getAbsoluteMousePos_Linux():
        """
        Returns an (x, y) co-ord tuple represening the absolute mouse position
        """
        mouse_data = __root.query_pointer()._data
        return (mouse_data["root_x"], mouse_data["root_y"])

    #Window position related
    def _getMASWindowPos_Windows():
        """
        Gets the window position for MAS as a tuple of (left, top, right, bottom)

        OUT:
            tuple representing window geometry or None if the window's hWnd could not be found
        """
        hwnd = __getMASWindowHWND()

        if hwnd is None:
            return None

        rv = win32gui.GetWindowRect(hwnd)

        # win32gui may return incorrect geometry (-32k seems to be the limit),
        # in this case we return None
        if rv[0] <= -32000 and rv[1] <= -32000:
            return None

        return rv

    def _getMASWindowPos_Linux():
        """
        Returns (x1, y1, x2, y2) relative to the top-left of the screen.

        OUT:
            tuple representing (left, top, right, bottom) of the window bounds, or None if not possible to get
        """
        geom = __getAbsoluteGeometry(MAS_WINDOW)

        if geom is not None:
            return (
                geom[0],
                geom[1],
                geom[0] + geom[2],
                geom[1] + geom[3]
            )
        return None

    def getMousePosRelative():
        """
        Gets the mouse position relative to the MAS window.
        Returned as a set of coordinates (0, 0) being within the MAS window, (1, 0) being to the left, (0, 1) being above, etc.

        OUT:
            Tuple representing the location of the mouse relative to the MAS window in terms of coordinates
        """
        pos_tuple = getMASWindowPos()

        if pos_tuple is None:
            return (0, 0)

        left, top, right, bottom = pos_tuple

        mouse_x, mouse_y = getMousePos()
        # NOTE: This is so we get correct pos in fullscreen
        if mouse_x == 0:
            mouse_x = 1
        if mouse_y == 0:
            mouse_y = 1

        half_mas_window_width = (right - left)/2
        half_mas_window_height = (bottom - top)/2

        # Sanity check since we'll divide by these,
        # Can be zeros in some rare cases: #9088
        if half_mas_window_width == 0 or half_mas_window_height == 0:
            return (0, 0)

        mid_mas_window_x = left + half_mas_window_width
        mid_mas_window_y = top + half_mas_window_height

        mas_window_to_cursor_x_comp = mouse_x - mid_mas_window_x
        mas_window_to_cursor_y_comp = mouse_y - mid_mas_window_y

        #Divide to handle the middle case
        mas_window_to_cursor_x_comp = int(float(mas_window_to_cursor_x_comp)/half_mas_window_width)
        mas_window_to_cursor_y_comp = -int(float(mas_window_to_cursor_y_comp)/half_mas_window_height)

        #Now return the unit vector direction
        return (
            mas_window_to_cursor_x_comp/abs(mas_window_to_cursor_x_comp) if mas_window_to_cursor_x_comp else 0,
            mas_window_to_cursor_y_comp/abs(mas_window_to_cursor_y_comp) if mas_window_to_cursor_y_comp else 0
        )

    def isCursorInMASWindow():
        """
        Checks if the cursor is within the MAS window

        OUT:
            True if cursor is within the mas window (within x/y), False otherwise
            Also returns True if we cannot get window position
        """
        return getMousePosRelative() == (0, 0)

    def isCursorLeftOfMASWindow():
        """
        Checks if the cursor is to the left of the MAS window (must be explicitly to the left of the left window bound)

        OUT:
            True if cursor is to the left of the window, False otherwise
            Also returns False if we cannot get window position
        """
        return getMousePosRelative()[0] == -1

    def isCursorRightOfMASWindow():
        """
        Checks if the cursor is to the right of the MAS window (must be explicitly to the right of the right window bound)

        OUT:
            True if cursor is to the right of the window, False otherwise
            Also returns False if we cannot get window position
        """
        return getMousePosRelative()[0] == 1

    def isCursorAboveMASWindow():
        """
        Checks if the cursor is above the MAS window (must be explicitly above the window bound)

        OUT:
            True if cursor is above the window, False otherwise
            False as well if we're unable to get a window position
        """
        return getMousePosRelative()[1] == 1

    def isCursorBelowMASWindow():
        """
        Checks if the cursor is above the MAS window (must be explicitly above the window bound)

        OUT:
            True if cursor is above the window, False otherwise
            False as well if we're unable to get a window position
        """
        return getMousePosRelative()[1] == -1

    #Fallback functions because Mac
    def return_true():
        """
        Literally returns True
        """
        return True

    def return_false():
        """
        Literally returns False
        """
        return False

    #Finally, we set vars accordingly to use the appropriate functions without needing to run constant runtime checks
    if renpy.windows:
        _window_get = _getActiveWindowHandle_Windows
        _tryShowNotif = _tryShowNotification_Windows
        getMASWindowPos = _getMASWindowPos_Windows
        getMousePos = _getAbsoluteMousePos_Windows

    else:
        if renpy.linux:
            _window_get = _getActiveWindowHandle_Linux
            _tryShowNotif = _tryShowNotification_Linux
            getMASWindowPos = _getMASWindowPos_Linux
            getMousePos = _getAbsoluteMousePos_Linux

        else:
            _window_get = _getActiveWindowHandle_OSX
            _tryShowNotif = _tryShowNotification_OSX

            #Because we have no method of testing on Mac, we'll use the dummy function for these
            getMASWindowPos = store.dummy
            getMousePos = store.dummy

            #Now make sure we don't use these functions so long as we can't validate Mac
            isCursorAboveMASWindow = return_false
            isCursorBelowMASWindow = return_false
            isCursorLeftOfMASWindow = return_false
            isCursorRightOfMASWindow = return_false
            isCursorInMASWindow = return_true

init python:
    #List of notif quips (used for topic alerts)
    #Windows/Linux
    mas_win_notif_quips = [
        "[player], I want to talk to you about something.",
        "Are you there, [player]?",
        "Can you come here for a second?",
        "[player], do you have a second?",
        "I have something to tell you, [player]!",
        "Do you have a minute, [player]?",
        "I've got something to talk about, [player]!",
    ]

    #OSX, since no active window detection
    mas_other_notif_quips = [
        "I've got something to talk about, [player]!",
        "I have something to tell you, [player]!",
        "Hey [player], I want to tell you something.",
        "Do you have a minute, [player]?",
    ]

    #List of hwnd IDs to destroy
    destroy_list = list()

    #START: Utility methods
    def mas_canCheckActiveWindow():
        """
        Checks if we can check the active window (simplifies conditionals)
        """
        return (
            store.mas_windowreacts.can_do_windowreacts
            and (persistent._mas_windowreacts_windowreacts_enabled or persistent._mas_enable_notifications)
        )

    def mas_getActiveWindowHandle():
        """
        Gets the active window name

        OUT:
            The active window handle if found. If it is not possible to get, we return an empty string

        NOTE: THIS SHOULD NEVER RETURN NONE
        """
        if mas_windowreacts.can_do_windowreacts and mas_canCheckActiveWindow():
            return store.mas_windowutils._window_get()
        return ""

    def mas_display_notif(title, body, group=None, skip_checks=False):
        """
        Notification creation method

        IN:
            title - Notification heading text
            body - A list of items which would go in the notif body (one is picked at random)
            group - Notification group (for checking if we have this enabled)
                (Default: None)
            skip_checks - Whether or not we skips checks
                (Default: False)
        OUT:
            bool indicating status (notif shown or not (by check))

        NOTE:
            We only show notifications if:
                1. We are able to show notifs
                2. MAS isn't the active window
                3. User allows them
                4. And if the notification group is enabled
                OR if we skip checks. BUT this should only be used for introductory or testing purposes.
        """

        #First we want to create this location in the dict, but don't add an extra location if we're skipping checks
        if persistent._mas_windowreacts_notif_filters.get(group) is None and not skip_checks:
            persistent._mas_windowreacts_notif_filters[group] = False

        if (
            skip_checks
            or (
                mas_windowreacts.can_show_notifs
                and ((renpy.windows and not mas_isFocused()) or not renpy.windows)
                and mas_notifsEnabledForGroup(group)
            )
        ):
            #Now we make the notif
            notif_success = mas_windowutils._tryShowNotif(
                renpy.substitute(title),
                renpy.substitute(renpy.random.choice(body))
            )

            #Play the notif sound if we have that enabled and notif was successful
            if persistent._mas_notification_sounds and notif_success:
                renpy.sound.play("mod_assets/sounds/effects/notif.wav")

            #Now we return true if notif was successful, false otherwise
            return notif_success
        return False

    #TODO: Remove this at some point | Alias for depreciation
    display_notif = mas_display_notif

    def mas_isFocused():
        """
        Checks if MAS is the focused window
        """
        #TODO: Mac vers (if possible)
        return store.mas_windowreacts.can_do_windowreacts and mas_getActiveWindowHandle() == store.mas_getWindowTitle()

    def mas_isInActiveWindow(regexp, active_window_handle=None):
        """
        Checks if ALL keywords are in the active window name
        IN:
            regexp:
                Regex pattern to identify the window

            active_window_handle:
                String representing the handle of the active window
                If None, it's fetched
                (Default: None)
        """

        #Don't do work if we don't have to
        if not store.mas_windowreacts.can_do_windowreacts:
            return False

        #Otherwise, let's get the active window
        if active_window_handle is None:
            active_window_handle = mas_getActiveWindowHandle()

        #Case-insensitive so site keywords match regardless of capitalization
        return bool(re.findall(regexp, active_window_handle, re.IGNORECASE))

    def mas_clearNotifs():
        """
        Clears all tray icons (also action center on win10)
        """
        if renpy.windows and store.mas_windowreacts.can_show_notifs:
            for index in range(len(destroy_list)-1,-1,-1):
                store.mas_windowutils.win32gui.DestroyWindow(destroy_list[index])
                destroy_list.pop(index)

    def mas_checkForWindowReacts():
        """
        Runs through events in the windowreact_db to see if we have a reaction, and if so, queue it
        """
        #Do not check anything if we're not supposed to
        if not persistent._mas_windowreacts_windowreacts_enabled or not store.mas_windowreacts.can_do_windowreacts:
            return

        active_window_handle = mas_getActiveWindowHandle()
        for ev_label, ev in mas_windowreacts.windowreact_db.iteritems():
            if (
                Event._filterEvent(ev, unlocked=True, aff=store.mas_curr_affection)
                and ev.checkConditional()
                and mas_isInActiveWindow(ev.category[0], active_window_handle)
                and ((not store.mas_globals.in_idle_mode) or (store.mas_globals.in_idle_mode and ev.show_in_idle))
                and mas_notifsEnabledForGroup(ev.rules.get("notif-group"))
            ):
                MASEventList.queue(ev_label)
                ev.unlocked = False

                #Add the blacklist
                if "no_unlock" in ev.rules:
                    mas_addBlacklistReact(ev_label)

    def mas_resetWindowReacts(excluded=persistent._mas_windowreacts_no_unlock_list):
        """
        Runs through events in the windowreact_db to unlock them
        IN:
            List of ev_labels to exclude from being unlocked
        """
        for ev_label, ev in mas_windowreacts.windowreact_db.iteritems():
            if ev_label not in excluded:
                ev.unlocked=True

    def mas_updateFilterDict():
        """
        Updates the filter dict with the groups in the groups list for the settings menu
        """
        for group in store.mas_windowreacts._groups_list:
            if persistent._mas_windowreacts_notif_filters.get(group) is None:
                persistent._mas_windowreacts_notif_filters[group] = False

    def mas_addBlacklistReact(ev_label):
        """
        Adds the given ev_label to the no unlock list
        IN:
            ev_label: eventlabel to add to the no unlock list
        """
        if renpy.has_label(ev_label) and ev_label not in persistent._mas_windowreacts_no_unlock_list:
            persistent._mas_windowreacts_no_unlock_list.append(ev_label)

    def mas_removeBlacklistReact(ev_label):
        """
        Removes the given ev_label to the no unlock list if exists
        IN:
            ev_label: eventlabel to remove from the no unlock list
        """
        if renpy.has_label(ev_label) and ev_label in persistent._mas_windowreacts_no_unlock_list:
            persistent._mas_windowreacts_no_unlock_list.remove(ev_label)

    def mas_notifsEnabledForGroup(group):
        """
        Checks if notifications are enabled, and if enabled for the specified group
        IN:
            group: notification group to check
        """
        return persistent._mas_enable_notifications and persistent._mas_windowreacts_notif_filters.get(group,False)

    def mas_unlockFailedWRS(ev_label=None):
        """
        Unlocks a wrs again provided that it showed, but failed to show (failed checks in the notif label)
        NOTE: This should only be used for wrs that are only a notification
        IN:
            ev_label: eventlabel of the wrs
        """
        if (
            ev_label
            and renpy.has_label(ev_label)
            and ev_label not in persistent._mas_windowreacts_no_unlock_list
        ):
            mas_unlockEVL(ev_label,"WRS")

    def mas_prepForReload():
        """
        Handles clearing wrs notifs and unregistering the wndclass to allow 'reload' to work properly

        ASSUMES: renpy.windows
        """
        store.mas_clearNotifs()
        store.mas_windowutils.win32gui.UnregisterClass(__tip.classAtom, __tip.hinst)
