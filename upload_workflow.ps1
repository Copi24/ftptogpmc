# PowerShell script to upload workflow file
$repo = "Copi24/ftptogpmc"
$path = ".github/workflows/transfer.yml"
$filePath = Join-Path $PSScriptRoot $path

# Read and encode file
$content = [System.IO.File]::ReadAllBytes($filePath)
$base64Content = [Convert]::ToBase64String($content)

# Create JSON payload
$body = @{
    message = "Add workflow file"
    content = $base64Content
    branch = "master"
} | ConvertTo-Json

# Upload via GitHub API
gh api repos/$repo/contents/$path -X PUT --input - <<< $body

