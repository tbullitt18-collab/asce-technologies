import os
import re
import json
import requests
from dotenv import load_dotenv

load_dotenv()



DECISION_SUPPORT_DISCLAIMER = "ASCE recommendations are decision-support outputs. All eligibility determinations require trained human reviewer approval before any action is taken."

class WatsonxClient:
    """
    IBM Watsonx AI Client for ASCE.
    Handles IAM authentication and foundation model inference.
    Endpoint: IBM Cloud us-south enterprise endpoint: us-south.ml.cloud.ibm.com
    """

    # Models confirmed available on this account (queried 2026-07-31)
    AVAILABLE_MODELS = [
        "ibm/granite-3-8b-instruct",
        "ibm/granite-3-1-8b-base",
        "ibm/granite-4-h-small",
        "ibm/granite-guardian-3-8b",
        "meta-llama/llama-3-3-70b-instruct",
        "meta-llama/llama-4-maverick-17b-128e-instruct-fp8",
        "mistralai/mistral-medium-2505",
        "mistralai/mistral-small-3-1-24b-instruct-2503",
    ]

    DEFAULT_MODEL = "ibm/granite-4-h-small"

    def __init__(self, api_key: str = None, project_id: str = None, model_id: str = None):
        self.api_key = api_key or os.environ.get("IBM_CLOUD_API_KEY")
        self.project_id = project_id or os.environ.get("IBM_WATSONX_PROJECT_ID")
        self.model_id = model_id or os.environ.get("IBM_WATSONX_MODEL", self.DEFAULT_MODEL)
        self.endpoint = "https://us-south.ml.cloud.ibm.com/ml/v1/text/generation"
        self._cached_token = None

        if not self.api_key:
            raise ValueError("IBM_CLOUD_API_KEY is required. Set it as an environment variable or pass it directly.")
        if not self.project_id:
            raise ValueError("IBM_WATSONX_PROJECT_ID is required. Set it as an environment variable or pass it directly.")

        # Validate project_id is a v4 UUID
        uuid_pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        if not re.match(uuid_pattern, self.project_id, re.IGNORECASE):
            raise ValueError(f"project_id must be a valid UUID, got: {self.project_id}")


    def validate_citation(self, citation_text: str) -> dict:
        valid_sections = ["206.110", "206.111", "206.113", "206.114", "206.115", "206.117", "206.119", "206.120"]
        for section in valid_sections:
            if section in citation_text:
                return {"valid": True, "matched_section": section, "warning": ""}
        return {"valid": False, "matched_section": None, "warning": f"Citation '{citation_text}' does not match known valid 44 CFR sections."}

    def generate_iam_token(self) -> str:
        """Generates an IAM bearer token using the IBM Cloud API key."""
        url = "https://iam.cloud.ibm.com/identity/token"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        data = f"grant_type=urn:ibm:params:oauth:grant-type:apikey&apikey={self.api_key}"
        response = requests.post(url, headers=headers, data=data, timeout=15)
        response.raise_for_status()
        self._cached_token = response.json()["access_token"]
        return self._cached_token

    def infer(self, prompt: str, max_new_tokens: int = 500, temperature: float = 0.2) -> dict:
        """
        Run a single inference request against the Watsonx foundation model.

        Args:
            prompt: The input text prompt.
            max_new_tokens: Maximum tokens to generate.
            temperature: Sampling temperature (0.0 = greedy).

        Returns:
            dict with keys: status, model_id, generated_text, input_token_count, generated_token_count
        """
        token = self.generate_iam_token()

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }

        params = {"decoding_method": "greedy", "max_new_tokens": max_new_tokens}
        if temperature > 0:
            params["decoding_method"] = "sample"
            params["temperature"] = temperature

        payload = {
            "model_id": self.model_id,
            "input": prompt,
            "parameters": params,
            "project_id": self.project_id
        }

        response = requests.post(
            self.endpoint,
            headers=headers,
            json=payload,
            params={"version": "2023-05-29"},
            timeout=60
        )

        if response.status_code == 200:
            result = response.json()
            gen = result.get("results", [{}])[0]
            return {
                "status": "success",
                "model_id": self.model_id,
                "generated_text": gen.get("generated_text", "").strip(),
                "input_token_count": gen.get("input_token_count", 0),
                "generated_token_count": gen.get("generated_token_count", 0),
            }
        else:
            err = response.json() if response.headers.get("content-type", "").startswith("application/json") else {"raw": response.text}
            return {
                "status": "error",
                "http_code": response.status_code,
                "error": err,
            }

    CHAT_ENDPOINT = "https://us-south.ml.cloud.ibm.com/ml/v1/text/chat"

    def chat(self, user_prompt: str, system_prompt: str = None, max_tokens: int = 600, temperature: float = 0.0) -> dict:
        """
        Run inference via the current watsonx.ai chat API (/ml/v1/text/chat).
        IBM has deprecated /text/generation; instruction-tuned models (Granite 4, Llama 3.3)
        follow instructions reliably only through the chat endpoint.

        Returns the same shape as infer(): status, model_id, generated_text, token counts.
        """
        token = self.generate_iam_token()
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        payload = {
            "model_id": self.model_id,
            "project_id": self.project_id,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        response = requests.post(
            self.CHAT_ENDPOINT,
            headers={"Accept": "application/json", "Content-Type": "application/json",
                     "Authorization": f"Bearer {token}"},
            json=payload,
            params={"version": "2024-10-08"},
            timeout=60,
        )
        if response.status_code == 200:
            body = response.json()
            choice = (body.get("choices") or [{}])[0]
            usage = body.get("usage", {})
            return {
                "status": "success",
                "model_id": body.get("model_id", self.model_id),
                "generated_text": (choice.get("message", {}).get("content") or "").strip(),
                "input_token_count": usage.get("prompt_tokens", 0),
                "generated_token_count": usage.get("completion_tokens", 0),
            }
        err = response.json() if response.headers.get("content-type", "").startswith("application/json") else {"raw": response.text}
        return {"status": "error", "http_code": response.status_code, "error": err}

    def score_displacement(self, applicant_data: dict) -> dict:
        """
        Analyze a FEMA applicant's displacement eligibility under 44 CFR § 206.117.

        Args:
            applicant_data: dict with keys like applicant_id, disaster_number,
                            damage_level, displacement_type, etc.

        Returns:
            Structured compliance verdict from the model.
        """
        prompt = f"""You are a FEMA compliance auditor. Analyze the following applicant record
and determine displacement eligibility under 44 CFR § 206.117 (Individual and Households Program).

APPLICANT RECORD:
{json.dumps(applicant_data, indent=2)}

Respond in this exact JSON format:
{{
  "eligible": true or false,
  "confidence": 0-100,
  "statutory_basis": "cite the specific 44 CFR subsection",
  "reasoning": "2-3 sentence explanation",
  "recommended_action": "APPROVE / DENY / ESCALATE",
  "flags": ["list any compliance flags"]
}}

Respond ONLY with the JSON object, no other text."""

        result = self.infer(prompt, max_new_tokens=400, temperature=0.0)

        if result["status"] == "success":
            try:
                # Try direct parse first
                verdict = json.loads(result["generated_text"])
            except json.JSONDecodeError:
                # Extract first JSON object from output (model may append extra text)
                match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', result["generated_text"], re.DOTALL)
                if match:
                    try:
                        verdict = json.loads(match.group())
                    except json.JSONDecodeError:
                        return {
                            "eligible": None,
                            "confidence": 0,
                            "error": "Model returned unparseable JSON",
                            "raw_output": result["generated_text"][:500],
                            "case_id": applicant_data.get("applicant_id", "unknown"),
                            "disclaimer": DECISION_SUPPORT_DISCLAIMER
                        }
                else:
                    return {
                        "eligible": None,
                        "confidence": 0,
                        "error": "No JSON found in model output",
                        "raw_output": result["generated_text"][:500],
                        "case_id": applicant_data.get("applicant_id", "unknown"),
                        "disclaimer": DECISION_SUPPORT_DISCLAIMER
                    }
            verdict["model_id"] = result["model_id"]
            verdict["case_id"] = applicant_data.get("applicant_id", "unknown")
            verdict["disclaimer"] = DECISION_SUPPORT_DISCLAIMER
            verdict["citation_check"] = self.validate_citation(verdict.get("statutory_basis", ""))
            return verdict
        else:
            return {
                "eligible": None,
                "confidence": 0,
                "error": result.get("error", "Unknown API error"),
                "case_id": applicant_data.get("applicant_id", "unknown"),
                "disclaimer": DECISION_SUPPORT_DISCLAIMER
            }


if __name__ == "__main__":
    client = WatsonxClient()

    # Quick connectivity check
    print("=== ASCE Watsonx Connectivity Check ===")
    result = client.infer("Reply only: ASCE connectivity test passed.", max_new_tokens=20)
    print(f"Status: {result['status']}")
    if result["status"] == "success":
        print(f"Model: {result['model_id']}")
        print(f"Output: {result['generated_text'][:100]}")
    else:
        print(f"Error: {result.get('error')}")

    print("\n=== ASCE Displacement Scoring Test ===")
    test_case = {
        "applicant_id": "FEMA-TEST-001",
        "disaster_number": "DR-4782-KY",
        "damage_level": "Major",
        "displacement_type": "Red Tag - Condemned",
        "insurance_coverage": False,
        "current_housing": "Shelter",
        "days_since_disaster": 12
    }
    verdict = client.score_displacement(test_case)
    print(json.dumps(verdict, indent=2))
