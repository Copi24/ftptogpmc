#!/bin/bash
# Setup script for GitHub repository initialization

set -e

echo "FTP to Google Photos - GitHub Repository Setup"
echo "=============================================="
echo ""

# Check if gh CLI is installed
if ! command -v gh &> /dev/null; then
    echo "ERROR: GitHub CLI (gh) is not installed."
    echo "Install from: https://cli.github.com/"
    exit 1
fi

# Check if logged in
if ! gh auth status &> /dev/null; then
    echo "Please log in to GitHub CLI:"
    gh auth login
fi

# Get repository name
read -p "Enter repository name (default: ftptogpmc): " repo_name
repo_name=${repo_name:-ftptogpmc}

# Get repository description
read -p "Enter repository description (default: FTP to Google Photos transfer): " repo_desc
repo_desc=${repo_desc:-FTP to Google Photos transfer}

# Create repository
echo ""
echo "Creating GitHub repository: $repo_name"
gh repo create "$repo_name" --public --description "$repo_desc" --source=. --remote=origin --push

echo ""
echo "Repository created successfully!"
echo ""
echo "Next steps:"
echo "1. Go to: https://github.com/$(gh api user --jq .login)/$repo_name/settings/secrets/actions"
echo "2. Add secret 'GP_AUTH_DATA' with your Google Photos auth data"
echo "3. Add secret 'RCLONE_CONFIG' with your rclone configuration"
echo ""
echo "Then trigger the workflow:"
echo "  gh workflow run transfer.yml"
echo ""
echo "Or visit: https://github.com/$(gh api user --jq .login)/$repo_name/actions"

