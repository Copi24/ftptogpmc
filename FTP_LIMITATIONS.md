# FTP Resume Limitations

## The Problem

Your FTP server appears to be extremely slow and unstable. From the logs:
- Downloads stall frequently (every 1-2 minutes)
- Speed drops from 45 MB/s → 0 B/s
- Connection hangs without closing
- This happens consistently across all attempts

## What "Smart Continuation" Actually Means

### ✅ **WORKS: Between Workflow Runs**
The state tracking system works PERFECTLY for resuming **between different workflow runs**:

```
Run 1:
  ❌ File A - failed (marked in state)
  ✅ File B - success (marked in state)
  ⏱️ Workflow times out

Run 2:
  ⏭️ File A - retry (was marked failed)
  ⏭️ File B - skip (already completed)
  ✅ File C - success
  ⏱️ Workflow times out

Run 3:
  ⏭️ File A - retry again
  ⏭️ File B - skip (already completed)
  ⏭️ File C - skip (already completed)
  ...continues...
```

This is the **primary value** of smart continuation - the workflow can run many times and never re-process successfully uploaded files.

### ❌ **DOESN'T WORK: Within a Single Download**
FTP protocol has NO reliable resume capability like HTTP does:

```
Attempt 1: Download 1.5GB → stalls → kill
Attempt 2: Download restarts from 0GB (not 1.5GB)
```

**Why?**
- FTP REST command is not widely supported
- rclone's FTP backend doesn't support partial file resume
- Your FTP servers don't expose resume capability

This means each retry attempt starts the download from scratch.

## Current Strategy

### What We're Doing Now:

1. **Clean Restart Per Attempt**
   - Delete partial file
   - Start download from scratch
   - Use aggressive stall detection (90 seconds)
   - Kill and retry if stalled

2. **Quick Retries**
   - 3 attempts max per file
   - 30/60 second waits between attempts
   - If all 3 fail → mark as FAILED in state

3. **State Persistence**
   - Failed files tracked in `upload_state.json`
   - Next workflow run retries failed files
   - Completed files never retried

4. **More Aggressive rclone Settings**
   ```
   --buffer-size 8M          # Smaller buffer for stability
   --low-level-retries 20    # More retries
   --timeout 120s            # 2min timeout
   --contimeout 60s          # 1min connection timeout
   --tpslimit 5              # Lower transaction rate
   --stats 15s               # More frequent updates
   ```

## The Real Solution

Given your FTP server's extreme instability, here are the realistic outcomes:

### Best Case Scenario
- Small files (< 5GB) might complete in 1-3 attempts
- Medium files (5-15GB) will take multiple workflow runs
- Large files (15-50GB) may require many workflow runs
- Each workflow run = 6 hours max

### Example Timeline
```
Day 1 (Run 1-4):
  ✅ 10 small files completed
  ❌ 5 large files failed
  
Day 2 (Run 5-8):
  ⏭️ Skip 10 completed
  ✅ 2 of the failed files succeed
  ❌ 3 still failing
  ✅ 8 new files completed

Day 3 (Run 9-12):
  ⏭️ Skip 20 completed
  ✅ 1 more succeeds
  ❌ 2 persistently failing
  ...
```

### When Files Fail Repeatedly (3+ times)
Files that fail 3 times in a row across workflow runs are marked as "exceeded retry limit" and skipped. This prevents infinite loops on corrupted files.

## What You Can Do

### Option 1: Let It Run (Recommended)
- Schedule workflow to run every 2 hours
- Let it process files gradually
- Check back weekly to see progress
- Small/medium files will complete
- Large files may eventually succeed or fail permanently

### Option 2: Better FTP Server
Your current FTP setup is the bottleneck:
- Connections stall constantly
- Speed is unpredictable
- No resume support

Consider:
- Different FTP server software
- HTTP/HTTPS server instead (supports resume)
- Direct cloud-to-cloud transfer (if possible)
- rclone serve (turn one server into rclone backend)

### Option 3: Download Locally First
If you have a good internet connection:
1. Download files to your PC using rclone
2. Upload to Google Photos from your PC
3. Much more reliable than GitHub Actions → FTP → Google Photos

```bash
# On your PC
rclone copy 3DFF: ./local_movies --progress
# Then upload to Google Photos using gpmc locally
```

## Why Not Fix FTP Resume?

**Technical Reality:**
- FTP protocol: Designed in 1971, before HTTP
- Resume support: Optional, poorly implemented
- Your servers: Don't support it reliably
- rclone FTP: Doesn't implement resume for good reason

**To add resume would require:**
1. FTP servers to support REST command properly
2. Servers to maintain stable connections
3. rclone to implement FTP resume (currently doesn't)
4. Servers to allow seeking in open files
5. Network not to drop connections constantly

None of these are under our control.

## Bottom Line

✅ **State tracking works GREAT** - no re-uploading completed files
❌ **FTP resume doesn't work** - each attempt starts fresh
🐌 **Your FTP is very slow** - this is the real problem
⏱️ **Time will help** - run workflow regularly, files will gradually complete
📊 **Track progress** - download state file to see what's completed

The "smart continuation" is working as designed for what's actually possible with your FTP setup. The workflow will keep trying, tracking progress, and eventually transfer everything it can.

