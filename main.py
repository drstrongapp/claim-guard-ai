from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import google.generativeai as genai
import pandas as pd
import io
import os
import json
from typing import List, Dict
from pydantic import BaseModel
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Config
GEMINI_KEY = os.getenv("GEMINI_KEY")
if not GEMINI_KEY:
    raise ValueError("GEMINI_KEY environment variable is required. Please set it in your environment or Render dashboard.")

genai.configure(api_key=GEMINI_KEY)
app = FastAPI(title="ClaimGuard AI Auditor")

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

class AuditResult(BaseModel):
    total_claims: int
    flagged: List[Dict] = []
    recovery_estimate: float = 0.0  # $ potential
    appeals: List[str] = []  # Auto-generated letters
    
    class Config:
        extra = "allow"  # Allow extra fields

# Common denial rules (expand with AI)
DENIAL_RULES = {
    "CO-11": "Diagnosis doesn't match procedure – appeal with medical necessity docs.",
    "CO-15": "Invalid auth number – resubmit with correct prior auth.",
    "CO-97": "Bundled service – unbundle or appeal as distinct.",
    "CO-167": "Non-covered service – check policy, appeal if experimental."
}

@app.post("/audit", response_model=AuditResult)
async def audit_claims(file: UploadFile = File(...)):
    if not file.filename.endswith('.csv'):
        raise HTTPException(400, "Upload CSV only")
    
    try:
        # Read CSV (sample format: patient_id, procedure_code, diagnosis_code, amount, auth_num)
        content = await file.read()
        df = pd.read_csv(io.BytesIO(content))
        
        if df.empty:
            raise HTTPException(400, "Empty file")
        
        total_claims = len(df)
        flagged = []
        appeals = []
        recovery_potential = 0.0
        
        prompt_template = """
    You are ClaimGuard AI, expert in medical billing denials.
    Audit these claims for common issues: invalid codes, missing auth, non-covered services, bundling.
    For each flagged claim, suggest denial code (e.g., CO-11), reason, and appeal strategy.
    Claims data: {data}
    Return valid JSON only:
    {{
        "flagged_claims": [
            {{"row": 1, "denial_code": "CO-11", "reason": "Wrong diagnosis", "appeal": "Submit progress notes."}}
        ],
        "total_recovery": 5000.0
    }}
    """
        
        # Process all claims - chunk for large files (Gemini has token limits)
        model = genai.GenerativeModel('models/gemini-2.0-flash')
        
        # Process in chunks of 50 claims at a time for large files
        chunk_size = 50
        all_flagged = []
        total_recovery = 0.0
        
        try:
            # Process all claims in chunks
            for chunk_start in range(0, len(df), chunk_size):
                chunk_end = min(chunk_start + chunk_size, len(df))
                chunk_df = df.iloc[chunk_start:chunk_end]
                chunk_data = chunk_df.to_json(orient='records')
                
                # Update prompt to indicate chunk processing
                chunk_prompt = prompt_template.format(data=chunk_data)
                if chunk_start > 0:
                    chunk_prompt += f"\n\nNote: This is chunk {chunk_start//chunk_size + 1} of {(len(df)-1)//chunk_size + 1}. Process claims {chunk_start+1} to {chunk_end}."
                
                response = model.generate_content(chunk_prompt)
                
                if not response or not hasattr(response, 'text'):
                    raise HTTPException(500, f"No response from AI model for chunk {chunk_start//chunk_size + 1}")
                
                # Extract JSON from response (Gemini may wrap in markdown)
                response_text = response.text.strip()
                
                # Remove markdown code blocks if present
                if response_text.startswith("```json"):
                    response_text = response_text[7:]
                elif response_text.startswith("```"):
                    response_text = response_text[3:]
                if response_text.endswith("```"):
                    response_text = response_text[:-3]
                response_text = response_text.strip()
                
                ai_output = json.loads(response_text)
                chunk_flagged = ai_output.get("flagged_claims", [])
                chunk_recovery = float(ai_output.get("total_recovery", 0))
                
                # Adjust row numbers to match actual dataframe indices
                for claim in chunk_flagged:
                    if 'row' in claim:
                        # Convert row number to actual index (AI returns 1-based, we need 0-based)
                        original_row = int(claim.get('row', 1))
                        claim['row'] = chunk_start + original_row - 1
                    # Add patient_id and other fields from actual dataframe if available
                    row_idx = claim.get('row', chunk_start)
                    if isinstance(row_idx, int) and 0 <= row_idx < len(df):
                        if 'patient_id' not in claim and 'patient_id' in df.columns:
                            claim['patient_id'] = str(df.iloc[row_idx]['patient_id'])
                        if 'procedure_code' not in claim and 'procedure_code' in df.columns:
                            claim['procedure_code'] = str(df.iloc[row_idx]['procedure_code'])
                        if 'diagnosis_code' not in claim and 'diagnosis_code' in df.columns:
                            claim['diagnosis_code'] = str(df.iloc[row_idx]['diagnosis_code'])
                
                all_flagged.extend(chunk_flagged)
                total_recovery += chunk_recovery
            
            # Ensure flagged items are proper dicts
            flagged = [dict(item) if isinstance(item, dict) else item for item in all_flagged]
            recovery_potential = total_recovery
            
            # Generate sample appeals
            for issue in flagged[:3]:  # Top 3
                try:
                    appeal_prompt = f"Write a professional appeal letter for {issue.get('reason', 'denial')}. Patient ID: {df.iloc[0]['patient_id'] if 'patient_id' in df.columns else 'N/A'}. Keep under 200 words."
                    appeal_resp = model.generate_content(appeal_prompt)
                    if appeal_resp and hasattr(appeal_resp, 'text'):
                        appeals.append(appeal_resp.text)
                except Exception as e:
                    # Continue if appeal generation fails
                    appeals.append(f"Appeal generation failed for issue: {issue.get('reason', 'Unknown')}")
        
        except json.JSONDecodeError as e:
            raise HTTPException(500, f"AI response parse error: {str(e)}. Response preview: {response_text[:200] if 'response_text' in locals() else 'N/A'}")
        except Exception as e:
            raise HTTPException(500, f"AI processing error: {str(e)}")
        
        # Ensure all data types are correct
        result = AuditResult(
            total_claims=int(total_claims),
            flagged=flagged if flagged else [],
            recovery_estimate=float(recovery_potential) if recovery_potential else 0.0,
            appeals=appeals if appeals else []
        )
        return result
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Unexpected error: {str(e)}")

@app.get("/")
def root():
    """Serve the main web interface"""
    return FileResponse("static/index.html")

@app.get("/health")
def health_check():
    """Health check endpoint for monitoring"""
    return {
        "status": "healthy",
        "service": "ClaimGuard AI",
        "gemini_configured": bool(GEMINI_KEY)
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

