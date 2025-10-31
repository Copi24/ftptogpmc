@echo off
REM Setup script for GitHub repository initialization (Windows)

echo FTP to Google Photos - GitHub Repository Setup
echo ==============================================
echo.

REM Check if gh CLI is installed
where gh >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: GitHub CLI (gh) is not installed.
    echo Install from: https://cli.github.com/
    exit /b 1
)

REM Check if logged in
gh auth status >nul 2>&1
if %errorlevel% neq 0 (
    echo Please log in to GitHub CLI:
    gh auth login
)

REM Get repository name
set /p repo_name="Enter repository name (default: ftptogpmc): "
if "%repo_name%"=="" set repo_name=ftptogpmc

REM Get repository description
set /p repo_desc="Enter repository description (default: FTP to Google Photos transfer): "
if "%repo_desc%"=="" set repo_desc=FTP to Google Photos transfer

REM Create repository
echo.
echo Creating GitHub repository: %repo_name%
gh repo create %repo_name% --public --description "%repo_desc%" --source=. --remote=origin --push

echo.
echo Repository created successfully!
echo.
echo Next steps:
echo 1. Go to: https://github.com/your-username/%repo_name%/settings/secrets/actions
echo 2. Add secret 'GP_AUTH_DATA' with your Google Photos auth data
echo 3. Add secret 'RCLONE_CONFIG' with your rclone configuration
echo.
echo Then trigger the workflow manually from the Actions tab.
pause

