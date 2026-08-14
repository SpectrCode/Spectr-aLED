@echo off
echo ========================================
echo Spectr_aLED Installer Builder
echo ========================================
echo.

set ISCC_PATH=C:\Program Files\Inno Setup 7\iscc.exe

if not exist "%ISCC_PATH%" (
    echo [ERROR] Inno Setup compiler not found at: %ISCC_PATH%
    echo Please install Inno Setup or update the path in this script.
    echo.
    pause
    exit /b 1
)

if not exist "..\dist\Spectr_aLed\Spectr_aLED.exe" (
    echo [ERROR] Spectr_aLED.exe not found in ..\dist\Spectr_aLed\
    echo Please build the application first.
    echo.
    pause
    exit /b 1
)

echo [INFO] Building installer...
"%ISCC_PATH%" installer.iss

if %errorlevel% equ 0 (
    echo.
    echo [SUCCESS] Installer created successfully!
    echo Output: ..\dist\Spectr_aLED_Setup.exe
) else (
    echo.
    echo [FAILED] Installer build failed with error code: %errorlevel%
)

echo.
pause
