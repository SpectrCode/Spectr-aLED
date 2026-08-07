@echo off

cl /LD /O2 /EHsc capture_bridge.cpp d3d11.lib dxgi.lib /Fe:capture_bridge.dll

pause
