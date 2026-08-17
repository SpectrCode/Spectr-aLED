"""
Preview Module for Stream 1
Обработчик превью для Stream 1
"""

import cv2
import time
import numpy as np

try:
    from window_utils import apply_dark_mode_to_window_by_title
except ImportError:
    def apply_dark_mode_to_window_by_title(title):
        pass


def run_preview_loop(app):
    """
    Stream 1 preview stream - runs in a separate thread
    
    Args:
        app: Экземпляр приложения GPUCaptureApp (для доступа к очереди и состоянию)
    """
    window_created = False
    
    while getattr(app, 'running', False):
        if not getattr(app, 'preview_enabled', False) or not getattr(app, 'stream1_enabled', True):
            if window_created:
                cv2.destroyWindow("Preview")
                window_created = False
            time.sleep(0.05)
            continue
        
        try:
            new_frame = app.preview_queue.get(timeout=1.0)
        except Exception:
            continue
        
        preview_start = time.perf_counter()
        
        img = new_frame
        
        if not window_created:
            # Создаем окно с задержкой для корректной инициализации
            cv2.namedWindow("Preview", cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO)
            time.sleep(0.1)  # Небольшая задержка для инициализации окна
            try:
                cv2.setWindowProperty("Preview", cv2.WND_PROP_TOPMOST, 1)
                cv2.resizeWindow("Preview", 720, 400)
            except cv2.error:
                pass  # Окно еще не готово, пропускаем
            apply_dark_mode_to_window_by_title("Preview")
            window_created = True
        
        # If float - convert to uint8
        if img.dtype != np.uint8:
            img = np.clip(img, 0.0, 1.0)
            img = (img * 255.0).astype(np.uint8)
        
        preview = cv2.resize(
            img,
            (720, 400),
            interpolation=cv2.INTER_AREA
        )
        
        # Пытаемся удерживать окно поверх других (игнорируем ошибки если окно закрыто)
        try:
            cv2.setWindowProperty("Preview", cv2.WND_PROP_TOPMOST, 1)
        except cv2.error:
            pass
        try:
            cv2.imshow("Preview", preview)
        except cv2.error:
            # Окно закрыто, пересоздаем его
            window_created = False
        cv2.waitKey(1)
        
        app.preview_count += 1
        
        app.preview_delay_ms = (time.perf_counter() - preview_start) * 1000