"""
Preview Module for Stream 2
Обработчик превью для Stream 2
"""

import cv2
import time
import numpy as np

try:
    from window_utils import apply_dark_mode_to_window_by_title
except ImportError:
    def apply_dark_mode_to_window_by_title(title):
        pass


def _window_visible(window):
    """True, если OpenCV-окно существует и видимо (не закрыто крестиком)."""
    try:
        return cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) > 0
    except Exception:
        return False


def _notify_close(on_window_closed, stream):
    """Уведомить приложение, что окно превью закрыто крестиком."""
    try:
        if callable(on_window_closed):
            on_window_closed(stream)
    except Exception:
        pass


def run_preview2_loop(app, on_window_closed=None):
    """
    Stream 2 preview stream - runs in a separate thread

    Args:
        app: Экземпляр приложения GPUCaptureApp (для доступа к очереди и состоянию)
        on_window_closed: необязательный колбэк on_window_closed(stream),
            вызывается, когда пользователь закрыл окно крестиком (превью
            останавливается автоматически, как будто нажали кнопку)
    """
    window_created = False
    
    while getattr(app, 'running', False):
        second_stream_enabled = False
        if hasattr(app, 'second_stream_enabled'):
            if hasattr(app.second_stream_enabled, 'get'):
                # Tk cross-thread .get() raises RuntimeError while the main
                # thread is busy — never let that kill this thread
                try:
                    second_stream_enabled = bool(app.second_stream_enabled.get())
                except Exception:
                    second_stream_enabled = bool(getattr(app, 'stream2_enabled', False))
            else:
                second_stream_enabled = bool(app.second_stream_enabled)
        
        if not getattr(app, 'preview2_enabled', False) or not second_stream_enabled:
            if window_created:
                try:
                    cv2.destroyWindow("Preview2")
                except Exception:
                    pass
                window_created = False
            time.sleep(0.05)
            continue

        try:
            new_frame = app.preview2_queue.get(timeout=1.0)
        except Exception:
            continue

        preview_start = time.perf_counter()

        # Окно закрыто крестиком в заголовке (флаг превью включен, а окна нет)
        # -> останавливаем превью, вместо бесконечного пересоздания окна
        if window_created and not _window_visible("Preview2"):
            app.preview2_enabled = False
            window_created = False
            _notify_close(on_window_closed, 2)
            time.sleep(0.05)
            continue

        if not window_created:
            try:
                cv2.destroyWindow("Preview2")
            except Exception:
                pass
            cv2.namedWindow("Preview2", cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO)
            cv2.setWindowProperty("Preview2", cv2.WND_PROP_TOPMOST, 1)
            cv2.resizeWindow("Preview2", 720, 400)
            apply_dark_mode_to_window_by_title("Preview2")
            window_created = True

        img = new_frame
        
        # If float - convert to uint8
        if img.dtype != np.uint8:
            img = np.clip(img, 0.0, 1.0)
            img = (img * 255.0).astype(np.uint8)
        
        preview = cv2.resize(
            img,
            (720, 400),
            interpolation=cv2.INTER_AREA
        )
        
        # Ensure window stays on top (only if window still exists)
        try:
            cv2.setWindowProperty("Preview2", cv2.WND_PROP_TOPMOST, 1)
        except Exception:
            # Окно закрыто крестиком -> останавливаем превью (не пересоздаем)
            app.preview2_enabled = False
            window_created = False
            _notify_close(on_window_closed, 2)
            time.sleep(0.05)
            continue

        try:
            cv2.imshow("Preview2", preview)
        except Exception:
            # Окно закрыто крестиком -> останавливаем превью (не пересоздаем)
            app.preview2_enabled = False
            window_created = False
            _notify_close(on_window_closed, 2)
            time.sleep(0.05)
            continue
        cv2.waitKey(1)
        
        app.preview2_count += 1
        
        app.preview2_delay_ms = (time.perf_counter() - preview_start) * 1000