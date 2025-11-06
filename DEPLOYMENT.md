# Deployment Guide for Render

## Quick Deploy to Render

### Option 1: Deploy via Render Dashboard

1. **Go to Render Dashboard**
   - Visit: https://dashboard.render.com
   - Sign in/up with GitHub

2. **Create New Web Service**
   - Click "New +" → "Web Service"
   - Connect your GitHub repository: `drstrongapp/claim-guard-ai`
   - Select the repository

3. **Configure Service**
   - **Name**: `claimguard-ai` (or your preferred name)
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`

4. **Set Environment Variables**
   - Click "Environment" tab
   - Add: `GEMINI_KEY` = `your_gemini_api_key_here`
   - (Do NOT commit your API key to git!)

5. **Deploy**
   - Click "Create Web Service"
   - Render will automatically build and deploy
   - Your app will be available at: `https://claimguard-ai.onrender.com`

### Option 2: Deploy via Render.yaml (Automatic)

If you've pushed `render.yaml` to your repo:
1. Go to Render Dashboard
2. Click "New +" → "Blueprint"
3. Connect your GitHub repo
4. Render will automatically detect `render.yaml` and configure everything

## Environment Variables

Required environment variable:
- `GEMINI_KEY`: Your Google Gemini API key

## Testing Your Deployment

Once deployed, test your API:

```bash
# Health check
curl https://claimguard-ai.onrender.com/

# Test audit endpoint
curl -X POST "https://claimguard-ai.onrender.com/audit" \
  -H "accept: application/json" \
  -H "Content-Type: multipart/form-data" \
  -F "file=@sample_claims.csv"
```

Or visit: https://claimguard-ai.onrender.com/docs for interactive API docs

## Notes

- Render free tier may spin down after 15 minutes of inactivity
- First request after spin-down may take 30-60 seconds
- Consider upgrading to paid tier for always-on service
- File uploads are limited by Render's request size limits

