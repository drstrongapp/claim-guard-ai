from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import google.generativeai as genai
import pandas as pd
import io
import os
import json
from typing import List, Dict
from pydantic import BaseModel
from dotenv import load_dotenv
import logging
from datetime import datetime

# Load environment variables
load_dotenv()

# Config
GEMINI_KEY = os.getenv("GEMINI_KEY")
if not GEMINI_KEY:
    raise ValueError("GEMINI_KEY environment variable is required. Please set it in your environment or Render dashboard.")

genai.configure(api_key=GEMINI_KEY)
app = FastAPI(title="ClaimGuard AI Auditor")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Configuration
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
REQUIRED_COLUMNS = ['patient_id', 'procedure_code', 'diagnosis_code', 'amount']

class AuditResult(BaseModel):
    total_claims: int
    flagged: List[Dict] = []
    recovery_estimate: float = 0.0  # $ potential
    appeals: List[str] = []  # Auto-generated letters
    denial_stats: Dict = {}  # Denial code breakdown
    total_flagged_amount: float = 0.0  # Total amount of flagged claims
    
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
    logger.info(f"Received file upload: {file.filename}")
    
    # Validate file type
    if not file.filename or not file.filename.endswith('.csv'):
        raise HTTPException(400, "Only CSV files are supported. Please upload a .csv file.")
    
    try:
        # Read and validate file size
        content = await file.read()
        file_size = len(content)
        
        if file_size > MAX_FILE_SIZE:
            raise HTTPException(400, f"File too large. Maximum size is {MAX_FILE_SIZE / (1024*1024):.1f}MB. Your file is {file_size / (1024*1024):.1f}MB.")
        
        if file_size == 0:
            raise HTTPException(400, "File is empty. Please upload a valid CSV file.")
        
        # Read CSV
        try:
            df = pd.read_csv(io.BytesIO(content))
        except Exception as e:
            raise HTTPException(400, f"Invalid CSV file. Error: {str(e)}. Please check your file format.")
        
        if df.empty:
            raise HTTPException(400, "CSV file is empty or has no data rows.")
        
        # Validate required columns
        missing_columns = [col for col in REQUIRED_COLUMNS if col not in df.columns]
        if missing_columns:
            raise HTTPException(400, f"Missing required columns: {', '.join(missing_columns)}. Required columns are: {', '.join(REQUIRED_COLUMNS)}")
        
        # Validate data types
        if 'amount' in df.columns:
            try:
                df['amount'] = pd.to_numeric(df['amount'], errors='coerce')
                if df['amount'].isna().any():
                    raise HTTPException(400, "Invalid amount values found. Amount column must contain numbers.")
            except Exception:
                raise HTTPException(400, "Amount column must contain numeric values.")
        
        logger.info(f"Processing {len(df)} claims from file {file.filename}")
        
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
                
                # Adjust row numbers and enrich with actual data
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
                        # Add actual claim amount for better recovery estimation
                        if 'amount' in df.columns:
                            claim['claim_amount'] = float(df.iloc[row_idx]['amount'])
                
                all_flagged.extend(chunk_flagged)
                total_recovery += chunk_recovery
            
            # Ensure flagged items are proper dicts
            flagged = [dict(item) if isinstance(item, dict) else item for item in all_flagged]
            
            # Calculate recovery based on actual claim amounts (more accurate)
            total_flagged_amount = 0.0
            for claim in flagged:
                if 'claim_amount' in claim:
                    total_flagged_amount += float(claim['claim_amount'])
                elif 'amount' in claim:
                    try:
                        total_flagged_amount += float(claim['amount'])
                    except:
                        pass
            
            # Use AI estimate if provided and reasonable, otherwise use 70% of flagged amounts (typical appeal success rate)
            if total_recovery > 0 and total_recovery <= total_flagged_amount * 1.5:  # Sanity check
                recovery_potential = total_recovery
            elif total_flagged_amount > 0:
                recovery_potential = total_flagged_amount * 0.70  # 70% success rate estimate
            else:
                recovery_potential = total_recovery if total_recovery > 0 else 0.0
            
            # Calculate denial code statistics
            denial_stats = {}
            for claim in flagged:
                denial_code = claim.get('denial_code', 'UNKNOWN')
                if denial_code not in denial_stats:
                    denial_stats[denial_code] = {
                        'count': 0,
                        'total_amount': 0.0,
                        'description': DENIAL_RULES.get(denial_code, 'Unknown denial code')
                    }
                denial_stats[denial_code]['count'] += 1
                claim_amt = claim.get('claim_amount') or claim.get('amount', 0)
                try:
                    denial_stats[denial_code]['total_amount'] += float(claim_amt)
                except:
                    pass
            
            # Generate enhanced appeals with more context
            for issue in flagged[:3]:  # Top 3
                try:
                    patient_id = issue.get('patient_id', df.iloc[0]['patient_id'] if 'patient_id' in df.columns else 'N/A')
                    procedure = issue.get('procedure_code', 'N/A')
                    diagnosis = issue.get('diagnosis_code', 'N/A')
                    denial_code = issue.get('denial_code', 'N/A')
                    reason = issue.get('reason', 'denial')
                    claim_amt = issue.get('claim_amount') or issue.get('amount', 'N/A')
                    
                    appeal_prompt = f"""Write a professional, detailed appeal letter for a medical billing denial.

Patient ID: {patient_id}
Procedure Code: {procedure}
Diagnosis Code: {diagnosis}
Denial Code: {denial_code}
Reason for Denial: {reason}
Claim Amount: ${claim_amt}

The appeal letter should:
1. Be professional and courteous
2. Clearly state the reason for the appeal
3. Provide supporting evidence or documentation needed
4. Request reconsideration of the claim
5. Include contact information placeholders

Keep it under 250 words and make it ready to use with minimal editing."""
                    
                    appeal_resp = model.generate_content(appeal_prompt)
                    if appeal_resp and hasattr(appeal_resp, 'text'):
                        appeals.append(appeal_resp.text)
                except Exception as e:
                    logger.error(f"Appeal generation failed: {str(e)}")
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
            appeals=appeals if appeals else [],
            denial_stats=denial_stats,
            total_flagged_amount=float(total_flagged_amount)
        )
        logger.info(f"Audit complete: {len(flagged)} flagged claims, ${recovery_potential:.2f} recovery potential")
        return result
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Unexpected error: {str(e)}")

@app.get("/")
def root():
    """Serve the main web interface"""
    return FileResponse("static/index.html")

@app.post("/export")
async def export_results(audit_data: dict):
    """Export audit results as CSV"""
    try:
        flagged = audit_data.get("flagged", [])
        if not flagged:
            raise HTTPException(400, "No flagged claims to export")
        
        # Create DataFrame from flagged claims
        export_df = pd.DataFrame(flagged)
        
        # Create CSV in memory
        output = io.StringIO()
        export_df.to_csv(output, index=False)
        output.seek(0)
        
        # Generate filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"claimguard_audit_{timestamp}.csv"
        
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode()),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        logger.error(f"Export error: {str(e)}")
        raise HTTPException(500, f"Export failed: {str(e)}")

@app.get("/health")
def health_check():
    """Health check endpoint for monitoring"""
    return {
        "status": "healthy",
        "service": "ClaimGuard AI",
        "gemini_configured": bool(GEMINI_KEY),
        "max_file_size_mb": MAX_FILE_SIZE / (1024 * 1024)
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

