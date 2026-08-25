"""
Path utilities for managing resource files (DLL, images)
Works with both development and PyInstaller bundled modes.
Config files are stored in %APPDATA%\Spectr_alLED\
"""

import sys
import os


def get_project_root():
    """Get the project root directory (parent of script folder)"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(script_dir)


def resolve_resource_path(relative_path):
    """
    Resolve path to resource file (works with PyInstaller and external folders).
    
    Args:
        relative_path: Path to the resource, e.g., "img/main.png" or just "main.png"
        
    Returns:
        Absolute path to the resource file
    """
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller bundled mode
        # For images in 'img' folder
        if relative_path.endswith(('.png', '.jpg', '.jpeg', '.ico', '.bmp')):
            return os.path.join(sys._MEIPASS, "img", os.path.basename(relative_path))
        return os.path.join(sys._MEIPASS, relative_path)
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = get_project_root()
    
    # For image files, check img folder first
    if relative_path.endswith(('.png', '.jpg', '.jpeg', '.ico', '.bmp')):
        img_path = os.path.join(project_root, "img", os.path.basename(relative_path))
        if os.path.exists(img_path):
            return img_path
    
    # Check in project root first (for files like background.png)
    resource_path = os.path.join(project_root, relative_path)
    if os.path.exists(resource_path):
        return resource_path
    
    # Fallback to script directory
    return os.path.join(script_dir, relative_path)


def resolve_dll_path():
    """
    Resolve path to DLL file.
    
    Returns:
        Absolute path to capture_bridge.dll
    """
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller bundled mode
        return os.path.join(sys._MEIPASS, "dll", "capture_bridge.dll")
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = get_project_root()
    dll_path = os.path.join(project_root, "dll", "capture_bridge.dll")
    
    if os.path.exists(dll_path):
        return dll_path
    
    # Fallback to same directory as script
    return os.path.join(script_dir, "capture_bridge.dll")


def get_config_dir():
    """
    Get the configuration directory path.
    Configs are stored in %APPDATA%\Spectr_alLED\ on Windows.
    Creates the directory if it does not exist.
    
    Returns:
        Absolute path to the config directory
    """
    app_data = os.environ.get('APPDATA', os.path.expanduser('~/.config'))
    config_dir = os.path.join(app_data, 'Spectr_alLED')
    os.makedirs(config_dir, exist_ok=True)
    return config_dir


def resolve_config_path(filename="app_config.json"):
    """
    Resolve path to config file.
    Config files are stored in %APPDATA%\Spectr_alLED\ on Windows.
    
    Args:
        filename: Name of the config file
        
    Returns:
        Absolute path to config file
    """
    config_dir = get_config_dir()
    config_path = os.path.join(config_dir, filename)
    
    # If config exists in AppData, return it
    if os.path.exists(config_path):
        return config_path
    
    # Fallback: check project root for migration (old config location)
    if not hasattr(sys, '_MEIPASS'):
        project_root = get_project_root()
        legacy_path = os.path.join(project_root, filename)
        if os.path.exists(legacy_path):
            return legacy_path
    
    # Return path in config dir (file may not exist yet)
    return config_path


def get_dll_path():
    """Get full path to DLL file"""
    return resolve_dll_path()


# Backward compatibility aliases
