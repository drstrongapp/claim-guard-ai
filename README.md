# ClaimGuard AI MVP

A FastAPI-based medical billing denial audit system powered by Google Gemini AI. Automatically identifies potential claim denials and generates appeal letters.

## Features

- **CSV Upload**: Upload medical claims in CSV format
- **AI-Powered Audit**: Uses Google Gemini to identify common denial issues
- **Automatic Appeals**: Generates professional appeal letters for flagged claims
- **Recovery Estimation**: Estimates potential recovery amounts from successful appeals

## Setup

1. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Set Environment Variables**
   ```bash
   cp .env.example .env
   ```
   Then edit `.env` and add your Google Gemini API key:
   ```
   GEMINI_KEY=your_actual_api_key_here
   ```

3. **Run the Application**
   ```bash
   python main.py
   ```
   Or with uvicorn directly:
   ```bash
   uvicorn main:app --reload
   ```

4. **Access the API**
   - API: http://localhost:8000
   - Interactive Docs: http://localhost:8000/docs
   - Alternative Docs: http://localhost:8000/redoc

## CSV Format

Your CSV file should include the following columns:
- `patient_id`: Patient identifier
- `procedure_code`: Medical procedure code
- `diagnosis_code`: Diagnosis code
- `amount`: Claim amount
- `auth_num`: Authorization number (optional)

See `sample_claims.csv` for an example.

## API Endpoints

### `POST /audit`
Upload a CSV file to audit claims.

**Request**: Multipart form data with a CSV file

**Response**: 
```json
{
  "total_claims": 100,
  "flagged": [
    {
      "row": 1,
      "denial_code": "CO-11",
      "reason": "Wrong diagnosis",
      "appeal": "Submit progress notes."
    }
  ],
  "recovery_estimate": 5000.0,
  "appeals": ["Generated appeal letter text..."]
}
```

### `GET /`
Health check endpoint.

## Testing

Use the provided `sample_claims.csv` file to test the API:

```bash
curl -X POST "http://localhost:8000/audit" \
  -H "accept: application/json" \
  -H "Content-Type: multipart/form-data" \
  -F "file=@sample_claims.csv"
```

Or use the interactive Swagger UI at http://localhost:8000/docs

## Notes

- The current implementation processes the first 10 claims as a sample. Scale to full dataset as needed.
- Ensure you have a valid Google Gemini API key from [Google AI Studio](https://makersuite.google.com/app/apikey)
- The AI response parsing handles markdown code blocks that Gemini may wrap around JSON

# claim-guard-ai
