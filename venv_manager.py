"""
Virtual environment manager for Spectr aLED
Automatically checks and creates venv, installs dependencies and launches the application.
"""

import sys
import os
import subprocess
import shutil
import time
import struct
import ctypes
import winreg

# Get the path to the script directory (where venv will be located)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Required Python version
REQUIRED_PYTHON_MAJOR = 3
REQUIRED_PYTHON_MINOR = 10


def print_info(message):
    """Print info message"""
    print(f"[INFO] {message}")


def print_ok(message):
    """Print success message"""
    print(f"[OK] {message}")


def print_error(message):
    """Print error message"""
    print(f"[ERROR] {message}")


def find_python_310():
    """
    Search for Python 3.10 installation on the system.
    Checks registry (Windows), common install paths, and PATH.
    Returns the path to python.exe if found, None otherwise.
    """
    print_info("Searching for Python 3.10...")

    # Method 1: Check Windows Registry for Python installations
    python_paths = check_registry_for_python()

    # Method 2: Check common installation directories
    common_paths = [
        r"C:\Python310\python.exe",
        r"C:\Python310\Scripts\python.exe",
        r"C:\Users\{user}\AppData\Local\Programs\Python\Python310\python.exe".format(user=os.getenv("USERNAME", "")),
        r"C:\Users\{user}\AppData\Local\Programs\Python\Python310-32\python.exe".format(user=os.getenv("USERNAME", "")),
        r"C:\Program Files\Python310\python.exe",
        r"C:\Program Files (x86)\Python310\python.exe",
    ]

    for path in common_paths:
        if os.path.exists(path) and path not in python_paths:
            python_paths.append(path)

    # Method 3: Check 'python' in PATH
    try:
        result = subprocess.run(["where", "python"], 
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if "python310" in line.lower() or "python-3.10" in line.lower():
                    if line not in python_paths:
                        python_paths.append(line)
    except Exception:
        pass

    # Also check 'py' launcher for Python 3.10
    try:
        result = subprocess.run(["py", "-3.10", "--version"], 
                              capture_output=True, text=True, timeout=5)
        version_output = (result.stdout + result.stderr).strip()
        if "3.10" in version_output:
            print_ok("Python 3.10 found via 'py' launcher!")
            return "py -3.10"
    except Exception:
        pass

    # Verify each found path to check if it's actually Python 3.10
    for path in python_paths:
        if verify_python_version(path, 3, 10):
            print_ok(f"Found Python 3.10 at: {path}")
            return path

    print_error("Python 3.10 not found on this system!")
    return None


def check_registry_for_python():
    """
    Check Windows registry for installed Python versions.
    Returns a list of paths to python.exe candidates.
    """
    python_paths = []

    # Registry paths to check
    registry_paths = [
        r"SOFTWARE\Python\PythonCore\3.10\InstallPath",
        r"SOFTWARE\WOW6432Node\Python\PythonCore\3.10\InstallPath",
        r"SOFTWARE\Python\PythonCore\3.10-32\InstallPath",
        r"SOFTWARE\WOW6432Node\Python\PythonCore\3.10-32\InstallPath",
    ]

    for reg_path in registry_paths:
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path)
            install_path, _ = winreg.QueryValueEx(key, "")
            python_exe = os.path.join(install_path, "python.exe")
            if os.path.exists(python_exe):
                python_paths.append(python_exe)
            winreg.CloseKey(key)
        except (FileNotFoundError, PermissionError, OSError):
            continue

    # Also check Python Display registry entries
    try:
        uninstall_key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, 
                                       r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")
        i = 0
        while True:
            try:
                sub_key_name = winreg.EnumKey(uninstall_key, i)
                if "python" in sub_key_name.lower() and "3.10" in sub_key_name.lower():
                    sub_key = winreg.OpenKey(uninstall_key, sub_key_name)
                    try:
                        install_loc, _ = winreg.QueryValueEx(sub_key, "InstallLocation")
                        python_exe = os.path.join(install_loc, "python.exe")
                        if os.path.exists(python_exe):
                            python_paths.append(python_exe)
                    except FileNotFoundError:
                        pass
                    winreg.CloseKey(sub_key)
                i += 1
            except FileNotFoundError:
                break
        winreg.CloseKey(uninstall_key)
    except (FileNotFoundError, PermissionError, OSError):
        pass

    return python_paths


def verify_python_version(python_path, major, minor):
    """
    Verify that the given Python executable matches the required version.
    Returns True if version matches, False otherwise.
    """
    try:
        # Handle 'py -3.10' launcher syntax
        if python_path == "py -3.10":
            result = subprocess.run(["py", "-3.10", "--version"], 
                                  capture_output=True, text=True, timeout=5)
        else:
            result = subprocess.run([python_path, "--version"], 
                                  capture_output=True, text=True, timeout=5)
        
        version_output = (result.stdout + result.stderr).strip()
        
        # Parse version string like "Python 3.10.5" or "3.10.5"
        parts = version_output.replace("Python ", "").split(".")
        if len(parts) >= 2:
            ver_major = int(parts[0])
            ver_minor = int(parts[1])
            return ver_major == major and ver_minor == minor
        
        # Fallback: check if "3.10" is in the version string
        return f"{major}.{minor}" in version_output
        
    except Exception:
        return False


def check_python():
    """Check if Python 3.10 is installed and available"""
    print_info("Checking for Python 3.10...")
    
    python_path = find_python_310()
    
    if python_path:
        # Get and display the exact version
        try:
            if python_path == "py -3.10":
                result = subprocess.run(["py", "-3.10", "--version"], 
                                      capture_output=True, text=True, timeout=5)
            else:
                result = subprocess.run([python_path, "--version"], 
                                      capture_output=True, text=True, timeout=5)
            version = (result.stdout + result.stderr).strip()
            print_ok(f"Python found: {version}")
        except Exception:
            print_ok("Python 3.10 found!")
        return python_path
    else:
        print_error("Python 3.10 not found on this system!")
        print("\nPlease install Python 3.10:")
        print("https://www.python.org/downloads/release/python-3100/")
        print("\nIMPORTANT: Make sure to check 'Add Python to PATH' during installation!")
        print("You must install specifically Python 3.10 (not 3.9, 3.11, or other versions).")
        return None


def check_venv(python_path=None):
    """Check if virtual environment exists and uses Python 3.10"""
    venv_path = os.path.join(SCRIPT_DIR, "venv")
    venv_python = os.path.join(venv_path, "Scripts", "python.exe")
    
    if os.path.exists(venv_python):
        # Verify the venv uses Python 3.10
        if verify_python_version(venv_python, 3, 10):
            print_ok(f"Virtual environment found with Python 3.10: {venv_path}")
            return True
        else:
            print_info("Existing virtual environment does not use Python 3.10.")
            print_info("It will be recreated with Python 3.10.")
            # Remove the old venv
            try:
                shutil.rmtree(venv_path)
                print_ok("Old virtual environment removed.")
            except Exception as e:
                print_error(f"Could not remove old venv: {e}")
                return False
    else:
        print_info("Virtual environment not found.")
    
    return False


def create_venv(python_path):
    """Create virtual environment with Python 3.10"""
    venv_path = os.path.join(SCRIPT_DIR, "venv")
    venv_python = os.path.join(venv_path, "Scripts", "python.exe")
    
    print_info(f"Creating virtual environment with Python 3.10: {venv_path}")
    
    try:
        # Handle 'py -3.10' launcher syntax
        if python_path == "py -3.10":
            cmd = ["py", "-3.10", "-m", "venv", venv_path]
        else:
            cmd = [python_path, "-m", "venv", venv_path]
        
        subprocess.run(cmd, check=True, timeout=120)
        
        # Verify the created venv uses Python 3.10
        if verify_python_version(venv_python, 3, 10):
            print_ok(f"Virtual environment created with Python 3.10: {venv_path}")
            return venv_python
        else:
            print_error("Created virtual environment does not use Python 3.10!")
            return None
            
    except subprocess.CalledProcessError as e:
        print_error(f"Failed to create virtual environment! {e}")
        return None
    except subprocess.TimeoutExpired:
        print_error("Venv creation timed out (120s).")
        return None
    except Exception as e:
        print_error(f"Error creating venv: {e}")
        return None


def setup_pip_environment():
    """Force pip to use only PyPI and ignore external pip configuration."""
    os.environ["PIP_CONFIG_FILE"] = os.devnull
    os.environ["PIP_INDEX_URL"] = "https://pypi.org/simple"
    os.environ.pop("PIP_EXTRA_INDEX_URL", None)
    os.environ.pop("PIP_NO_INDEX", None)
    os.environ.pop("PIP_FIND_LINKS", None)


def upgrade_pip(python_cmd):
    """Ensure venv has the latest pip from PyPI."""
    print_info("Updating pip in virtual environment...")

    try:
        result = subprocess.run(
            [
                python_cmd,
                "-m",
                "pip",
                "install",
                "--upgrade",
                "pip",
                "--index-url",
                "https://pypi.org/simple",
                "--no-cache-dir",
            ],
            timeout=120,
        )

        if result.returncode != 0:
            print_error("Failed to update pip.")
            return False

        version_result = subprocess.run(
            [python_cmd, "-m", "pip", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        print_ok(f"Pip updated: {version_result.stdout.strip()}")
        return True

    except subprocess.TimeoutExpired:
        print_error("Pip update timed out (120s).")
        return False

    except Exception as e:
        print_error(f"Error updating pip: {e}")
        return False


def install_requirements(python_cmd):
    """Install dependencies from requirements.txt"""
    requirements_file = os.path.join(
        SCRIPT_DIR, "requirements", "requirements.txt"
    )

    if not os.path.exists(requirements_file):
        print_error(f"Requirements file not found: {requirements_file}")
        return False

    print_info(f"Installing dependencies from {requirements_file}...")
    print("-" * 50)

    try:
        result = subprocess.run(
            [
                python_cmd,
                "-m",
                "pip",
                "install",
                "-r",
                requirements_file,
                "--index-url",
                "https://pypi.org/simple",
                "--no-cache-dir",
            ],
            timeout=300,
        )

        if result.returncode == 0:
            print("-" * 50)
            print_ok("Dependencies installed/updated successfully")
            return True
        else:
            print("-" * 50)
            print_error("Some dependencies failed to install. Try running the script again.")


    except subprocess.TimeoutExpired:
        print("-" * 50)
        print_error("Installation timed out (300s). Try running the script again.")


    except Exception as e:
        print("-" * 50)
        print_error(f"Error installing dependencies: {e}")


    return True


def check_dll():
    """Check if DLL file exists"""
    dll_path = os.path.join(SCRIPT_DIR, "dll", "capture_bridge.dll")
    
    if os.path.exists(dll_path):
        print_ok(f"DLL file found: {dll_path}")
        return True
    else:
        print_info(f"DLL file not found: {dll_path}")
        
        build_script = os.path.join(SCRIPT_DIR, "dll", "buildDLL.bat")
        if os.path.exists(build_script):
            print_info("Attempting to build DLL...")
            
            # Change to dll directory and run the build script
            original_dir = os.getcwd()
            try:
                os.chdir(os.path.join(SCRIPT_DIR, "dll"))
                result = subprocess.run(["buildDLL.bat"], 
                                      shell=True, capture_output=True, text=True, timeout=300)
                
                if result.returncode == 0 and os.path.exists(dll_path):
                    print_ok("DLL built successfully!")
                    return True
                else:
                    print_error("Failed to build DLL. Check the buildDLL.bat file")
                    
            except Exception as e:
                print_error(f"Error building DLL: {e}")
            finally:
                os.chdir(original_dir)
        else:
            print_error("buildDLL.bat not found! Place capture_bridge.dll in the dll\\ folder")
        
        return False


def run_main_app(python_cmd):
    """Run main application without console window"""
    script_path = os.path.join(SCRIPT_DIR, "script", "main.py")
    
    if not os.path.exists(script_path):
        print_error(f"main.py not found in {os.path.dirname(script_path)}\\")
        return False
    
    print_info("Launching main application...")
    print("=" * 50)
    
    try:
        # Configure STARTUPINFO to hide console window (Windows only)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        
        # Use Popen to run without waiting for completion
        process = subprocess.Popen([python_cmd, script_path], 
                                  cwd=os.path.dirname(script_path),
                                  startupinfo=startupinfo,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        return process
    except Exception as e:
        print_error(f"Error launching main.py: {e}")
        return None


def main():
    """Main function - requires Python 3.10 to proceed"""
    print("=" * 40)
    print("Spectr aLED - Application Launcher")
    print("=" * 40)
    
    # Check for Python 3.10 - REQUIRED to continue
    python_path = check_python()
    
    if python_path is None:
        # Python 3.10 not found — keep window open, retry on keypress
        print("\n[!] Python 3.10 not found. Install it and press Enter to retry.")
        print("    https://www.python.org/downloads/release/python-3100/")
        print("    IMPORTANT: Check 'Add Python to PATH' during installation.\n")
        while True:
            input("Press Enter to retry...")
            python_path = check_python()
            if python_path is not None:
                break
            print("\n[!] Still not found. Please install Python 3.10 and try again.\n")
    
    # Python 3.10 found - proceed with setup
    # Check if venv exists and uses Python 3.10
    venv_exists = check_venv(python_path)
    
    # If venv does not exist or doesn't use 3.10, create it
    if not venv_exists:
        python_cmd = create_venv(python_path)
        if python_cmd is None:
            input("\nPress Enter to exit...")
            return
    else:
        python_cmd = os.path.join(SCRIPT_DIR, "venv", "Scripts", "python.exe")
    
    # Ensure pip in venv is up to date
    setup_pip_environment()
    upgrade_pip(python_cmd)
    
    # Install dependencies
    install_requirements(python_cmd)
    
    # Check DLL
    check_dll()
    
    # Launch the main app (without console window) and close after 3 seconds
    process = run_main_app(python_cmd)
    
    if process is not None:
        print_info("Application launched. Console will close in 3 seconds...")
        time.sleep(3)
        
        # Close console window (Windows only) via PowerShell
        try:
            current_pid = os.getpid()
            subprocess.run(["powershell", "-Command", f"Stop-Process -Id {current_pid}"], 
                         creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception:
            pass
    
    sys.exit(0)


if __name__ == "__main__":
    main()