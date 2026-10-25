@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set TK=
for /f "tokens=2 delims==" %%a in ('findstr /b "PLATFORM_TOKEN=" .env') do set TK=%%a
if "%TK%"=="" echo 未在 .env 找到 PLATFORM_TOKEN，将以无令牌模式截图
node scripts/screenshot_ui.mjs "%TK%" "data/ui_shot.png" %*
echo.
echo 截图已保存: data\ui_shot.png
pause
