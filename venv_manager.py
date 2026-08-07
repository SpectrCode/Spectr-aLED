"""
Менеджер виртуального окружения для Spectr aLED
Автоматически проверяет и создает venv, устанавливает зависимости и запускает приложение.
"""

import sys
import os
import subprocess
import shutil
import time

# Получить путь к директории скрипта (где будет находиться venv)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def print_info(message):
    """Print info message"""
    print(f"[INFO] {message}")


def print_ok(message):
    """Print success message"""
    print(f"[OK] {message}")


def print_error(message):
    """Print error message"""
    print(f"[ERROR] {message}")


def check_python():
    """Check if Python is installed"""
    print_info("Проверка наличия Python...")
    
    try:
        result = subprocess.run([sys.executable, "--version"], 
                              capture_output=True, text=True, timeout=5)
        version = result.stdout.strip()
        if not version:
            version = result.stderr.strip()
        print_ok(f"Найден Python: {version}")
        return True
    except Exception as e:
        print_error(f"Python не найден в системе!")
        print(f"\nПожалуйста, установите Python 3.8 или выше:")
        print("https://www.python.org/downloads/")
        print("\nОбязательно поставьте галочку 'Add Python to PATH' при установке!")
        return False


def check_venv():
    """Check if virtual environment exists"""
    venv_path = os.path.join(SCRIPT_DIR, "venv")
    
    if os.path.exists(os.path.join(venv_path, "Scripts", "python.exe")):
        print_ok(f"Виртуальное окружение найдено: {venv_path}")
        return True
    else:
        print_info("Виртуальное окружение не найдено.")
        return False


def create_venv():
    """Create virtual environment"""
    venv_path = os.path.join(SCRIPT_DIR, "venv")
    
    print_info(f"Создание виртуального окружения: {venv_path}")
    
    try:
        subprocess.run([sys.executable, "-m", "venv", venv_path], 
                      check=True, timeout=120)
        print_ok(f"Виртуальное окружение создано: {venv_path}")
        return os.path.join(venv_path, "Scripts", "python.exe")
    except subprocess.CalledProcessError as e:
        print_error(f"Не удалось создать виртуальное окружение! {e}")
        return None
    except Exception as e:
        print_error(f"Ошибка при создании venv: {e}")
        return None


def check_pip(python_cmd):
    """Check if pip is available in virtual environment"""
    print_info("Проверка наличия pip...")
    
    try:
        result = subprocess.run([python_cmd, "-m", "pip", "--version"],
                              capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            version_line = result.stdout.strip()
            print_ok(f"Pip найден: {version_line}")
            return True
        else:
            print_error("Pip не найден в виртуальном окружении")
            return False
    except Exception as e:
        print_error(f"Ошибка при проверке pip: {e}")
        return False


def install_requirements(python_cmd):
    """Install dependencies from requirements.txt"""
    requirements_file = os.path.join(SCRIPT_DIR, "requirements", "requirements.txt")
    
    if not os.path.exists(requirements_file):
        print_error(f"Файл requirements не найден: {requirements_file}")
        return False
    
    print_info(f"Установка зависимостей из {requirements_file}...")
    
    try:
        result = subprocess.run([python_cmd, "-m", "pip", "install", "-r", requirements_file],
                              capture_output=True, text=True, timeout=300)
        
        if result.returncode == 0:
            print_ok("Зависимости установлены/обновлены")
            return True
        else:
            print_info("Некоторые зависимости не удалось установить. Попробуйте запустить скрипт повторно.")
            # Не возвращаем False, чтобы приложение могло запуститься с частичными зависимостями
            
    except Exception as e:
        print_info(f"Ошибка при установке зависимостей: {e}")
    
    return True


def check_dll():
    """Check if DLL file exists"""
    dll_path = os.path.join(SCRIPT_DIR, "dll", "capture_bridge.dll")
    
    if os.path.exists(dll_path):
        print_ok(f"DLL файл найден: {dll_path}")
        return True
    else:
        print_info(f"DLL файл не найден: {dll_path}")
        
        build_script = os.path.join(SCRIPT_DIR, "dll", "buildDLL.bat")
        if os.path.exists(build_script):
            print_info("Попытка сборки DLL...")
            
            # Перейти в папку dll и запустить скрипт
            original_dir = os.getcwd()
            try:
                os.chdir(os.path.join(SCRIPT_DIR, "dll"))
                result = subprocess.run(["buildDLL.bat"], 
                                      shell=True, capture_output=True, text=True, timeout=300)
                
                if result.returncode == 0 and os.path.exists(dll_path):
                    print_ok("DLL успешно собрана!")
                    return True
                else:
                    print_error("Не удалось собрать DLL. Проверьте файл buildDLL.bat")
                    
            except Exception as e:
                print_error(f"Ошибка при сборке DLL: {e}")
            finally:
                os.chdir(original_dir)
        else:
            print_error("Файл buildDLL.bat не найден! Поместите capture_bridge.dll в папку dll\\")
        
        return False


def run_main_app(python_cmd):
    """Run main application without console window"""
    script_path = os.path.join(SCRIPT_DIR, "script", "main.py")
    
    if not os.path.exists(script_path):
        print_error(f"Файл main.py не найден в {os.path.dirname(script_path)}\\")
        return False
    
    print_info("Запуск основного приложения...")
    print("=" * 50)
    
    try:
        # Настроить STARTUPINFO для скрытия окна консоли (только для Windows)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        
        # Используем Popen для запуска без ожидания завершения
        process = subprocess.Popen([python_cmd, script_path], 
                                  cwd=os.path.dirname(script_path),
                                  startupinfo=startupinfo,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        return process
    except Exception as e:
        print_error(f"Ошибка при запуске main.py: {e}")
        return None


def main():
    """Main function"""
    print("=" * 40)
    print("Spectr aLED - Запуск приложения")
    print("=" * 40)
    
    # Проверить наличие Python
    if not check_python():
        input("\nНажмите Enter для выхода...")
        return
    
    # Проверить наличие venv
    venv_exists = check_venv()
    
    # Если venv не существует - создать его
    if not venv_exists:
        python_cmd = create_venv()
        if python_cmd is None:
            input("\nНажмите Enter для выхода...")
            return
    else:
        python_cmd = os.path.join(SCRIPT_DIR, "venv", "Scripts", "python.exe")
    
    # Проверить наличие pip
    check_pip(python_cmd)
    
    # Установить зависимости
    install_requirements(python_cmd)
    
    # Проверить DLL
    check_dll()
    
    # Запустить главное приложение (без консольного окна) и закрыться через 3 секунды
    process = run_main_app(python_cmd)
    
    if process is not None:
        print_info("Приложение запущено. Консоль будет закрыта через 3 секунды...")
        time.sleep(3)
        
        # Закрыть окно консоли (только для Windows) через PowerShell
        try:
            current_pid = os.getpid()
            subprocess.run(["powershell", "-Command", f"Stop-Process -Id {current_pid}"], 
                         creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception:
            pass
    
    sys.exit(0)


if __name__ == "__main__":
    main()