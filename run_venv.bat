@echo off
chcp 65001 >nul

:: Получить путь к директории скрипта (всегда рабочая директория)
set "SCRIPT_DIR=%~dp0"

:: Перейти в рабочую директорию скрипта
cd /d "%SCRIPT_DIR%"

echo [INFO] Start venv_manager.py...
python "%SCRIPT_DIR%venv_manager.py"

pause