@echo off
cd /d "%~dp0"
echo Building Spectr_aLED as a folder (onedir mode, windowed)...
cd ..
pyinstaller --clean --noconfirm build\Spectr_aLED.spec
echo.
echo Build complete! Output is in: dist\SpectrLed\
echo This folder contains Spectr_aLEDd.exe + all dependencies.
pause
