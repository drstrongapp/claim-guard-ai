import smtplib
from email.mime.text import MIMEText

from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import google.generativeai as genai
import pandas as pd
import io
import os
import sys
import json
from typing import List, Dict, Optional
from pydantic import BaseModel
from dotenv import load_dotenv
import logging
from datetime import datetime

# Configure logging first
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Config
GEMINI_KEY = os.getenv("GEMINI_KEY")
if not GEMINI_KEY:
    logger.error("GEMINI_KEY environment variable is missing!")
    raise ValueError("GEMINI_KEY environment variable is required. Please set it in your environment or Render dashboard.")

try:
    genai.configure(api_key=GEMINI_KEY)
    logger.info("Gemini AI configured successfully")
except Exception as e:
    logger.error(f"Failed to configure Gemini AI: {str(e)}")
    raise

app = FastAPI(title="ClaimGuard AI Auditor")
logger.info("FastAPI app initialized")

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Configuration
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
REQUIRED_COLUMNS = ['patient_id', 'procedure_code', 'diagnosis_code', 'amount']

class AuditResult(BaseModel):
    total_claims: int
    flagged: List[Dict] = []
    recovery_estimate: float = 0.0  # $ potential
    appeals: List[str] = []  # Auto-generated letters
    denial_stats: Dict = {}  # Denial code breakdown
    total_flagged_amount: float = 0.0  # Total amount of flagged claims
    summary: Dict = {}  # High-level summary for UI
    
    class Config:
        extra = "allow"  # Allow extra fields

# Common denial rules (expand with AI)
DENIAL_RULES = {
    "CO-11": "Diagnosis doesn't match procedure – appeal with medical necessity docs.",
    "CO-15": "Invalid auth number – resubmit with correct prior auth.",
    "CO-97": "Bundled service – unbundle or appeal as distinct.",
    "CO-167": "Non-covered service – check policy, appeal if experimental.",
    "CO-4": "Procedure code inconsistent with modifier – verify modifier usage.",
    "CO-18": "Duplicate claim – check if already processed.",
    "CO-19": "Claim denied due to benefit maximum – verify coverage limits.",
    "CO-22": "Care may be covered by another payer – coordinate benefits.",
    "CO-24": "Charges exceed fee schedule – verify allowed amounts.",
    "CO-27": "Expenses incurred after coverage terminated – verify dates.",
    "CO-29": "Time limit for filing has expired – check timely filing rules.",
    "CO-50": "These are non-covered services – review policy exclusions.",
    "CO-96": "Non-covered charge(s) – verify medical necessity.",
    "CO-109": "Claim not covered by this payer – wrong insurance.",
    "CO-110": "Billing date predates service date – verify dates.",
    "CO-119": "Benefit maximum for this time period has been reached.",
    "PR-1": "Deductible amount – patient responsibility.",
    "PR-2": "Coinsurance amount – patient responsibility.",
    "PR-3": "Copayment amount – patient responsibility.",
    "PR-96": "Non-covered charge(s) – patient responsibility.",
    "OA-18": "Claim/service lacks information needed for adjudication.",
    "OA-23": "Impact of prior payer(s) adjudication – coordination of benefits."
}

def build_summary(total_claims: int, flagged: List[Dict], denial_stats: Dict, recovery_potential: float, total_flagged_amount: float) -> Dict:
    """Create a concise summary of audit results for UI display."""
    summary = {}
    flagged_count = len(flagged)
    
    if flagged_count == 0:
        summary["headline"] = "✅ All clear — no potential denials detected."
        summary["details"] = [
            f"Reviewed {total_claims} claims with no issues flagged.",
            "Continue submitting claims as usual and monitor for future denials."
        ]
        summary["recommended_actions"] = [
            "Share this clean audit with your team.",
            "Set a reminder to re-run ClaimGuard AI after the next billing cycle."
        ]
        summary["top_denials"] = []
        return summary
    
    top_denials = sorted(denial_stats.items(), key=lambda item: item[1]["count"], reverse=True)
    top_denials_strings = [
        f"{code} · {data['count']} claims (${data['total_amount']:.2f})"
        for code, data in top_denials[:3]
    ]
    
    avg_flagged_amount = (total_flagged_amount / flagged_count) if flagged_count else 0.0
    
    summary["headline"] = (
        f"⚠️ {flagged_count} of {total_claims} claims flagged "
        f"(${total_flagged_amount:.2f} at risk)."
    )
    summary["details"] = [
        f"Estimated recovery potential: ${recovery_potential:.2f}.",
        f"Average flagged claim amount: ${avg_flagged_amount:.2f}.",
        f"Top denial codes: {', '.join(top_denials_strings) if top_denials_strings else 'n/a'}."
    ]
    
    recommended_actions = []
    for code, data in top_denials[:3]:
        description = data.get("description") or DENIAL_RULES.get(code, "")
        if description:
            recommended_actions.append(f"{code}: {description}")
    
    if not recommended_actions:
        recommended_actions.append("Review flagged claims and submit appeals promptly.")
    
    if recovery_potential > 0:
        recommended_actions.append("Prioritize high-dollar claims to maximize recovered revenue.")
    
    summary["recommended_actions"] = recommended_actions
    summary["top_denials"] = [
        {
            "code": code,
            "count": data["count"],
            "total_amount": data["total_amount"],
            "description": data.get("description")
        }
        for code, data in top_denials[:5]
    ]
    
    return summary

@app.post("/audit", response_model=AuditResult)
async def audit_claims(
    file: UploadFile = File(..., description="CSV file only, maximum size 5MB"),
    email: Optional[str] = Form(None)
):
    logger.info(f"Received file upload: {file.filename}")
    
    # Validate file type
    if not file.filename:
        raise HTTPException(400, "No file provided. Please upload a CSV or Excel file.")
    
    file_ext = file.filename.lower().split('.')[-1] if '.' in file.filename else ''
    if file_ext != 'csv':
        raise HTTPException(400, "Please upload a CSV file. Other formats are not currently supported.")
    
    try:
        # Read and validate file size
        content = await file.read()
        file_size = len(content)
        
        if file_size > MAX_FILE_SIZE:
            raise HTTPException(400, "File too large. Maximum file size is 5MB.")
        
        if file_size == 0:
            raise HTTPException(400, "File is empty. Please upload a valid CSV or Excel file.")
        
        # Read CSV file
        try:
            df = pd.read_csv(io.BytesIO(content))
        except HTTPException:
            raise
        except Exception as e:
            file_type = "Excel" if file_ext in ['xlsx', 'xls'] else "CSV"
            raise HTTPException(400, f"Invalid {file_type} file. Error: {str(e)}. Please check your file format.")
        
        if df.empty:
            raise HTTPException(400, "File is empty or has no data rows.")
        
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
        total_chunks = (len(df) - 1) // chunk_size + 1
        
        try:
            # Process all claims in chunks
            for chunk_idx, chunk_start in enumerate(range(0, len(df), chunk_size), 1):
                chunk_end = min(chunk_start + chunk_size, len(df))
                chunk_df = df.iloc[chunk_start:chunk_end]
                chunk_data = chunk_df.to_json(orient='records')
                
                logger.info(f"Processing chunk {chunk_idx}/{total_chunks} (claims {chunk_start+1}-{chunk_end} of {len(df)})")
                
                # Update prompt to indicate chunk processing
                chunk_prompt = prompt_template.format(data=chunk_data)
                if chunk_start > 0:
                    chunk_prompt += f"\n\nNote: This is chunk {chunk_idx} of {total_chunks}. Process claims {chunk_start+1} to {chunk_end}."
                
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
            
            # Generate enhanced appeals with more context for all flagged claims
            logger.info(f"Generating appeal letters for {len(flagged)} flagged claims...")
            for idx, issue in enumerate(flagged, 1):
                if idx > 20:  # Limit to 20 appeals to avoid timeout
                    logger.info(f"Limiting appeal generation to first 20 claims to avoid timeout")
                    break
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
        summary = build_summary(total_claims, flagged, denial_stats, recovery_potential, total_flagged_amount)
        
        result = AuditResult(
            total_claims=int(total_claims),
            flagged=flagged if flagged else [],
            recovery_estimate=float(recovery_potential) if recovery_potential else 0.0,
            appeals=appeals if appeals else [],
            denial_stats=denial_stats,
            total_flagged_amount=float(total_flagged_amount),
            summary=summary
        )
        if email:
            try:
                email_results(email, result.dict())
            except Exception as e:
                logger.error(f"Deferred email sending failed for {email}: {str(e)}")
        logger.info(f"Audit complete: {len(flagged)} flagged claims, ${recovery_potential:.2f} recovery potential")
        return result
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Unexpected error: {str(e)}")

@app.get("/", response_class=HTMLResponse)
def home():
    """Marketing landing page"""
    return """
    <!DOCTYPE html>
    <html lang=\"en\">
    <head>
        <meta charset=\"UTF-8\" />
        <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />
        <title>ClaimGuard AI | Medical Claim Denial Auditor</title>
        <style>
            body {
                margin: 0;
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: #fff;
                display: flex;
                align-items: center;
                justify-content: center;
                min-height: 100vh;
                text-align: center;
                padding: 40px 20px;
            }
            .card {
                background: rgba(255,255,255,0.08);
                padding: 50px 40px;
                max-width: 640px;
                border-radius: 24px;
                box-shadow: 0 25px 70px rgba(0,0,0,0.3);
                backdrop-filter: blur(18px);
            }
            h1 {
                font-size: 2.75rem;
                margin-bottom: 20px;
                letter-spacing: -0.03em;
            }
            p {
                font-size: 1.2rem;
                line-height: 1.6;
                margin-bottom: 30px;
                color: rgba(255,255,255,0.9);
            }
            .cta {
                display: inline-block;
                padding: 15px 35px;
                border-radius: 50px;
                background: #fff;
                color: #4c51bf;
                font-weight: 600;
                font-size: 1.05rem;
                text-decoration: none;
                transition: transform 0.2s ease, box-shadow 0.2s ease;
            }
            .cta:hover {
                transform: translateY(-3px);
                box-shadow: 0 15px 35px rgba(255,255,255,0.35);
            }
            .meta {
                margin-top: 25px;
                font-size: 0.95rem;
                color: rgba(255,255,255,0.8);
            }
            @media (max-width: 640px) {
                .card { padding: 40px 30px; }
                h1 { font-size: 2.1rem; }
                p { font-size: 1.05rem; }
            }
        </style>
    </head>
    <body>
        <div class=\"card\">
            <h1>ClaimGuard AI</h1>
            <p>Audit 100 claims in under 10 seconds. Surface denial risks and recover $10K+ in missed reimbursement before payers do.</p>
            <a class=\"cta\" href=\"/app\">Try the Free Audit</a>
            <div class=\"meta\">No credit card. Upload a CSV or Excel file and get instant denial insights.</div>
        </div>
    </body>
    </html>
    """


@app.get("/app")
@app.get("/app/")
def app_ui():
    """Serve the interactive audit application"""
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

@app.post("/export/appeals")
async def export_appeals(audit_data: dict):
    """Export generated appeal letters as a downloadable text file"""
    try:
        appeals = audit_data.get("appeals", [])
        if not appeals:
            raise HTTPException(400, "No appeal letters to export")
        
        output = io.StringIO()
        for idx, appeal in enumerate(appeals, 1):
            output.write(f"Appeal Letter #{idx}\n")
            output.write("=" * 60 + "\n")
            output.write(appeal.strip())
            output.write("\n\n")
        
        output.seek(0)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"claimguard_appeals_{timestamp}.txt"
        
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode("utf-8")),
            media_type="text/plain",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Appeals export error: {str(e)}")
        raise HTTPException(500, f"Export appeals failed: {str(e)}")


def email_results(to_email: str, results: dict):
    """Send a simple summary email with audit results (placeholder for future SMTP integration)."""
    if not to_email:
        return
    try:
        subject = "Your ClaimGuard Audit Results"
        body = (
            f"Found ${results.get('recovery_estimate', 0):,.2f} in potential recoverable denials.\n"
            "Appeal letters and detailed findings are available in the ClaimGuard dashboard."
        )
        message = MIMEText(body)
        message['Subject'] = subject
        message['From'] = 'audit@claimguard.ai'
        message['To'] = to_email

        # Placeholder SMTP configuration. Replace with SendGrid/Gmail credentials when ready.
        with smtplib.SMTP('localhost') as smtp:
            smtp.send_message(message)
        logger.info(f"Results email sent to {to_email}")
    except Exception as e:
        logger.error(f"Email delivery failed for {to_email}: {str(e)}")

@app.get("/health")
def health_check():
    """Health check endpoint for monitoring"""
    try:
        return {
            "status": "healthy",
            "service": "ClaimGuard AI",
            "gemini_configured": bool(GEMINI_KEY),
            "max_file_size_mb": MAX_FILE_SIZE / (1024 * 1024),
            "excel_support": False,
            "python_version": sys.version.split()[0]
        }
    except Exception as e:
        logger.error(f"Health check error: {str(e)}")
        return {
            "status": "error",
            "error": str(e)
        }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

