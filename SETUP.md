# Setup Instructions for GitHub Actions

## Quick Start

1. **Create a new GitHub repository**
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git remote add origin <your-repo-url>
   git push -u origin main
   ```

2. **Add Secrets to GitHub Repository**
   - Go to: Settings → Secrets and variables → Actions
   - Click "New repository secret"
   
   **Secret 1: `GP_AUTH_DATA`**
   ```
   androidId=34c7fb6495e7d198&app=com.google.android.apps.photos&client_sig=24bb24c05e47e0aefa68a58a766179d9b613a600&callerPkg=com.google.android.apps.photos&callerSig=24bb24c05e47e0aefa68a58a766179d9b613a600&device_country=ch&Email=geminilomu%40gmail.com&google_play_services_version=240913000&lang=de_CH&oauth2_foreground=1&operatorCountry=ch&sdk_version=36&service=oauth2%3Aopenid%20https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fmobileapps.native%20https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fphotos.native&source=android&Token=your_token_here
   ```
   
   **Secret 2: `RCLONE_CONFIG`**
   ```
   [3DFlickFix]
   type = ftp
   host = sputnik.whatbox.ca
   user = Lomusire
   port = 13017
   explicit_tls = true
   pass = pjHvl7JadRUkmXez6feoaRvBwn7uTtp56A
   no_check_certificate = true

   [3DFlickFix2]
   type = ftp
   host = tamarind.whatbox.ca
   user = Lomusire
   port = 13017
   explicit_tls = true
   pass = 7B3nTjkkgMIr9HoXbKPEkuAipPtdAx6mrg
   no_check_certificate = true

   [3DFlickFix3]
   type = ftp
   host = challenger.whatbox.ca
   user = Lomusire
   port = 13017
   explicit_tls = true
   pass = U6V6xBei0_O379h9cW3EvhwxYR1vfEyXNg
   no_check_certificate = true

   [3DFF]
   type = combine
   upstreams = Sputnik=3DFlickFix: Tamarind=3DFlickFix2: Challenger=3DFlickFix3:
   ```

3. **Trigger the Workflow**
   - Go to: Actions tab
   - Select "FTP to Google Photos Transfer"
   - Click "Run workflow" → "Run workflow"

## Testing Locally

```bash
# Install Python dependencies
pip install -r requirements.txt

# Install rclone
# Windows: Download from https://rclone.org/downloads/
# Linux: curl https://rclone.org/install.sh | sudo bash

# Configure rclone (copy your config)
mkdir -p ~/.config/rclone
cp rclone.conf ~/.config/rclone/rclone.conf

# Set environment variable
export GP_AUTH_DATA="your_auth_data_here"

# Run script
python3 ftp_to_gphotos.py
```

## Monitoring

- Check Actions tab for workflow runs
- Download logs from Actions artifacts
- View `ftp_to_gphotos.log` for detailed logs

## Troubleshooting

- **Workflow fails immediately**: Check secrets are set correctly
- **Connection timeout**: FTP server may be slow, this is normal for large files
- **Disk space**: GitHub Actions has ~14GB free space, files are cleaned up after upload

