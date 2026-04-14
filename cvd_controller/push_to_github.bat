@echo off
echo ================================================
echo  CVD Controller - Push to GitHub
echo ================================================
echo.

:: Check git is installed
where git >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Git is not installed.
    echo Download it from: https://git-scm.com/download/win
    pause
    exit /b 1
)

echo Git found. Setting up repository...
echo.

:: Initialize git if not already done
if not exist ".git" (
    git init
    echo Initialized new git repository.
) else (
    echo Git repository already initialized.
)

:: Set remote (replace if already exists)
git remote remove origin 2>nul
git remote add origin https://github.com/justinlin3451/wanglab.git
echo Remote set to: https://github.com/justinlin3451/wanglab.git
echo.

:: Stage all files
git add .
echo Files staged.

:: Commit
git commit -m "Initial commit - CVD Controller core"
echo.

:: Push
echo Pushing to GitHub...
echo (A browser window or login prompt may appear)
echo.
git push -u origin main

if %errorlevel% neq 0 (
    echo.
    echo If push failed, try running:
    echo   git push -u origin master
    echo.
    echo Or check that your repo exists at:
    echo   https://github.com/justinlin3451/wanglab
)

echo.
echo ================================================
echo  Done! Check your repo at:
echo  https://github.com/justinlin3451/wanglab
echo ================================================
pause
