@echo off
rem ============================================================
rem  setup-ai.bat - AI dev structure installer (generic)
rem  Source : this project (script location = <source>\tools\)
rem  Usage  : setup-ai.bat [target-project-path]
rem           no arg -> setup/verify current project itself
rem  Example: setup-ai.bat D:\workspace\kisaBoho
rem ============================================================
setlocal

set "SRC=%~dp0.."
if "%~1"=="" ( set "TGT=%SRC%" ) else ( set "TGT=%~1" )

echo.
echo [setup-ai] Source : %SRC%
echo [setup-ai] Target : %TGT%
echo.

if not exist "%TGT%" (
    echo [ERROR] Target folder not found: %TGT%
    exit /b 1
)

rem ---- 1. directories -----------------------------------------
for %%D in (.ai .ai\agents .ai\designs .ai\tasks .ai\state .ai\backup tools) do (
    if not exist "%TGT%\%%D" (
        mkdir "%TGT%\%%D"
        echo [mkdir] %%D
    )
)

rem ---- 2. copy files (never overwrite existing) ---------------
call :copy_if_new ".ai\conduct.md"
call :copy_if_new ".ai\agents\architect.md"
call :copy_if_new ".ai\agents\coder.md"
call :copy_if_new ".ai\agents\runner.md"
call :copy_if_new ".ai\tasks\M-0000.template.json"
call :copy_if_new "tools\dispatcher.py"

rem ---- 3. python check -----------------------------------------
where python >nul 2>nul
if errorlevel 1 (
    echo [WARN ] python not found in PATH - dispatcher.py needs Python 3.8+
) else (
    for /f "tokens=*" %%V in ('python --version 2^>^&1') do echo [ok   ] %%V
)

rem ---- 4. ollama check ------------------------------------------
curl -s -o nul -m 3 http://localhost:11434/api/tags
if errorlevel 1 (
    echo [WARN ] Ollama not responding at localhost:11434 - run "ollama serve" or start Ollama app
) else (
    echo [ok   ] Ollama is running
    ollama list 2>nul | findstr /i "qwen2.5-coder:14b" >nul
    if errorlevel 1 (
        echo [WARN ] model qwen2.5-coder:14b not installed - run: ollama pull qwen2.5-coder:14b
    ) else (
        echo [ok   ] model qwen2.5-coder:14b installed
    )
)

rem ---- 5. Continue config (optional) ----------------------------
if not exist "%SRC%\ai-setting\config.yaml" goto :after_continue
if exist "%USERPROFILE%\.continue\config.yaml" (
    echo [skip ] %USERPROFILE%\.continue\config.yaml already exists
    goto :after_continue
)
set /p ANS="[ask  ] Copy Continue config.yaml to %USERPROFILE%\.continue ? (y/n): "
if /i not "%ANS%"=="y" goto :after_continue
if not exist "%USERPROFILE%\.continue" mkdir "%USERPROFILE%\.continue"
copy /y "%SRC%\ai-setting\config.yaml" "%USERPROFILE%\.continue\config.yaml" >nul
echo [copy ] Continue config.yaml installed
:after_continue

echo.
echo [setup-ai] DONE.
echo   NEXT STEPS:
echo   1) Edit "%TGT%\.ai\conduct.md" - fix env constants (paths, build, Java ver) for the target project
echo   2) Create a task:   copy .ai\tasks\M-0000.template.json  .ai\tasks\M-0001.json
echo   3) Write design:    .ai\designs\M-0001\design.md  (use Claude / architect.md)
echo   4) Dry run first:   python tools\dispatcher.py run M-0001 --dry
echo.
endlocal
exit /b 0

rem ---- subroutine: copy only when target file does not exist ----
:copy_if_new
if exist "%TGT%\%~1" (
    echo [skip ] %~1 exists
) else (
    copy "%SRC%\%~1" "%TGT%\%~1" >nul
    if errorlevel 1 ( echo [ERROR] copy failed: %~1 ) else ( echo [copy ] %~1 )
)
exit /b 0
