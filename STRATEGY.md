# Strategy for Handling Unstable FTP + Limited Storage

## The Challenge
- **FTP is extremely unstable** - connections drop frequently
- **GitHub Actions has ~14GB free space** - can't store large files
- **Movie files are 5-30GB+** - many won't fit
- **No direct streaming** - gpmc requires complete files

## Current Approach

### ✅ What We Do:
1. **Sort files by size** (smallest first)
2. **Check disk space** before downloading each file
3. **Skip files > 12GB** (won't fit in available space)
4. **Retry 5 times** with increasing waits (30s, 60s, 90s, 120s, 150s)
5. **Kill stalled downloads** after 2 minutes at same byte count
6. **Process smaller files successfully** and move on

### 📊 File Size Strategy:
- **1-5GB**: ✅ High success rate, will complete
- **5-10GB**: ⚠️ May work if FTP cooperates
- **10-12GB**: ⚠️ Risky, but worth trying
- **12GB+**: ❌ Skip (won't fit in available space)

### 🔄 Retry Logic:
```
Attempt 1: Download immediately
Attempt 2: Wait 30s, retry
Attempt 3: Wait 60s, retry
Attempt 4: Wait 90s, retry
Attempt 5: Wait 120s, retry
If all fail: Move to next file (will retry on next workflow run)
```

### ⚡ Stall Detection:
- Monitors actual GB transferred
- If stuck at same amount for 2 minutes → kills process
- Prevents hanging forever on one file
- Moves on to try other files

## Alternative Solutions

### Option A: Run Locally (RECOMMENDED)
```bash
# Download this repo
git clone https://github.com/Copi24/ftptogpmc
cd ftptogpmc

# Install dependencies
pip install -r requirements.txt

# Set your auth data
export GP_AUTH_DATA="your_auth_data_here"

# Run with unlimited space
python3 ftp_to_gphotos.py
```

**Benefits:**
- ✅ No storage limits
- ✅ Faster internet (usually)
- ✅ Can handle files of any size
- ✅ More reliable connection
- ✅ Can pause/resume

### Option B: Use a VPS
Deploy to a cloud server with more storage:
- DigitalOcean Droplet
- AWS EC2
- Google Cloud VM
- Hetzner Cloud

### Option C: Accept Limitations
- Focus on files under 12GB
- Run workflow multiple times
- Let it process what it can
- Larger files simply won't work on GitHub Actions

## Current Status

**GitHub Actions will:**
- ✅ Process all files 1-12GB successfully
- ⚠️ Retry failed downloads 5 times
- ❌ Skip files > 12GB (too large)
- 🔄 Can be run repeatedly to retry failed files

**Success rate depends on:**
- FTP server stability (currently very poor)
- File size (smaller = better)
- Time of day (less load = better)

## Recommendation

For **reliable transfers of all files**, run locally or on a VPS. GitHub Actions is great for automation but has hard limits that can't be overcome for very large files.

