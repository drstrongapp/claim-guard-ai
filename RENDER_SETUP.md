# Critical: Render Python Version Setup

## The Problem
Render is defaulting to Python 3.13, which causes pandas to compile from source and fail.

## The Solution - Manual Setup Required

**You MUST manually set Python version in Render Dashboard:**

1. Go to: https://dashboard.render.com
2. Click on your `claimguard-ai` service
3. Go to **Settings** tab
4. Scroll to **Environment** section
5. **IMPORTANT**: Add or update these environment variables:
   - `PYTHON_VERSION` = `3.11.9`
6. **Also check**: In the **Build & Deploy** section:
   - Make sure **Python Version** is set to `3.11.9` (if there's a dropdown)
7. Click **Save Changes**
8. Go to **Manual Deploy** → **Deploy latest commit**

## Alternative: If Python 3.11 option not available

If Render doesn't offer Python 3.11, try:
1. Delete the current service
2. Create a new Web Service
3. When creating, look for **Python Version** option
4. Select `3.11` or `3.11.9`
5. Use these settings:
   - **Build Command**: `pip install --upgrade pip && pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`

## Why This Matters

- Python 3.13 is too new - pandas doesn't have pre-built wheels for it
- Python 3.11.9 has pre-built wheels for pandas 2.1.0
- This avoids compilation errors entirely

## Verify It Worked

After redeploy, check logs - you should see:
- `Python 3.11.9` (not 3.13)
- `Successfully installed pandas-2.1.0` (not compiling from source)

