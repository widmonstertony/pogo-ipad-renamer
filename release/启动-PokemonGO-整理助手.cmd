@echo off
cd /d "%~dp0.."
where pyw.exe >nul 2>&1
if %errorlevel%==0 (
  start "" pyw.exe -3.13 "%CD%\launcher_ipad_landscape_v9.py"
  exit /b 0
)
for %%V in (313 312 311) do (
  if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\pythonw.exe" (
    start "" "%LOCALAPPDATA%\Programs\Python\Python%%V\pythonw.exe" "%CD%\launcher_ipad_landscape_v9.py"
    exit /b 0
  )
)
where pythonw.exe >nul 2>&1
if %errorlevel%==0 (
  start "" pythonw.exe "%CD%\launcher_ipad_landscape_v9.py"
  exit /b 0
)
echo 未找到 Python 3.11 或更高版本。请先安装 Python 后重试。
pause
exit /b 1
