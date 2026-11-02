"""SetBuilder: starts the local API and opens the app window."""
import multiprocessing
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Api:
    """Native dialogs exposed to the page as window.pywebview.api.*"""

    def __init__(self):
        self.window = None

    def _dialog(self, kind, multiple=False, types=()):
        import webview
        fd = getattr(webview, "FileDialog", None)
        k = {"folder": fd.FOLDER if fd else webview.FOLDER_DIALOG,
             "open": fd.OPEN if fd else webview.OPEN_DIALOG}[kind]
        res = self.window.create_file_dialog(k, allow_multiple=multiple, file_types=types)
        return list(res or [])

    def pick_folders(self):
        return self._dialog("folder", multiple=True)

    def pick_folder(self):
        r = self._dialog("folder")
        return r[0] if r else None

    def pick_files(self):
        return self._dialog("open", multiple=True,
                            types=("Audio (*.mp3;*.m4a;*.flac;*.wav;*.aif;*.aiff;*.aac;*.ogg)",))

    def pick_image(self):
        r = self._dialog("open", types=("Images (*.jpg;*.jpeg;*.png)",))
        return r[0] if r else None


def serve(port):
    import uvicorn
    from setbuilder.server import app
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def main():
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else free_port()
    threading.Thread(target=serve, args=(port,), daemon=True).start()
    for _ in range(100):  # wait for the server
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    if "--headless" in sys.argv:
        print(f"http://127.0.0.1:{port}", flush=True)
        threading.Event().wait()
    try:  # show "SetBuilder" and its icon in the Dock and menu bar instead of "Python"
        from AppKit import NSApplication, NSImage
        from Foundation import NSBundle
        NSBundle.mainBundle().infoDictionary()["CFBundleName"] = "SetBuilder"
        icon = Path(__file__).resolve().parent.parent / "AppIcon.icns"
        NSApplication.sharedApplication().setApplicationIconImage_(NSImage.alloc().initWithContentsOfFile_(str(icon)))
    except Exception:
        pass
    import webview
    api = Api()
    api.window = webview.create_window("SetBuilder", f"http://127.0.0.1:{port}/", js_api=api,
                                       width=1440, height=920, min_size=(1000, 680), background_color="#0e0f13")
    webview.start()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
