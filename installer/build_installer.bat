@echo off
rem Genera el instalador. Doble clic o: build_installer.bat [-Version 1.2.0] [-SkipFrontend] [-SkipPip] [-Clean]
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_installer.ps1" %*
set RESULT=%ERRORLEVEL%
if not "%RESULT%"=="0" (
  echo.
  echo *** La generacion del instalador fallo. Revise los mensajes de arriba. ***
)
pause
exit /b %RESULT%
