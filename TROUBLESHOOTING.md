# Render Deployment Troubleshooting

## Common Issues and Solutions

### 1. Build Fails

**Error**: `ModuleNotFoundError` or `pip install` fails

**Solution**:
- Check that `requirements.txt` has all dependencies
- Ensure Python version is compatible (3.9+)
- Try updating pip: Add `pip install --upgrade pip` to build command

### 2. App Won't Start

**Error**: `Application failed to respond` or timeout

**Solution**:
- Verify start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Check that `main.py` exists and `app` is defined
- Ensure port uses `$PORT` environment variable (Render provides this)

### 3. Environment Variables Not Set

**Error**: `GEMINI_KEY` is None or missing

**Solution**:
- Go to Render Dashboard → Your Service → Environment
- Add `GEMINI_KEY` with your API key value
- Redeploy after adding environment variables

### 4. Import Errors

**Error**: `ImportError` or `ModuleNotFoundError` at runtime

**Solution**:
- Verify all imports are in `requirements.txt`
- Check for typos in import statements
- Ensure Python version supports all packages

### 5. File Upload Issues

**Error**: File upload fails or times out

**Solution**:
- Render has request size limits (check your plan)
- Consider chunking large files
- Add timeout configuration if needed

## Manual Deployment Steps

If `render.yaml` isn't working, deploy manually:

1. **Create Web Service**
   - Dashboard → New + → Web Service
   - Connect GitHub repo: `drstrongapp/claim-guard-ai`

2. **Settings**:
   - **Name**: `claimguard-ai`
   - **Region**: Choose closest to you
   - **Branch**: `main`
   - **Root Directory**: (leave empty)
   - **Environment**: `Python 3`
   - **Build Command**: `pip install --upgrade pip && pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`

3. **Environment Variables**:
   - Add: `GEMINI_KEY` = `your_api_key_here`

4. **Deploy**

## Check Logs

1. Go to Render Dashboard
2. Click on your service
3. Click "Logs" tab
4. Look for error messages in build or runtime logs

## Test Locally First

Before deploying, test locally:

```bash
# Set environment variable
export GEMINI_KEY=your_key_here

# Run locally
uvicorn main:app --host 0.0.0.0 --port 8000
```

If it works locally but not on Render, check:
- Environment variables are set correctly
- Port configuration uses `$PORT`
- All dependencies are in requirements.txt

