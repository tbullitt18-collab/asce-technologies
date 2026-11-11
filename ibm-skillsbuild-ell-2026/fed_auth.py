"""
ASCE Federal Authentication Simulation Module
=============================================

ARCHITECTURAL DEMONSTRATION LAYER ONLY (SIMULATED PIV/CAC & SAML/OIDC)

Addresses integration architecture for Critical Gap #4: PIV/CAC Smartcard
Authentication aligned with NIST 800-63B AAL3 patterns.

IMPORTANT IV&V HONESTY NOTICE:
- This module provides an in-memory *simulation* of PIV/CAC certificate parsing,
  SAML 2.0 assertion issuance, OIDC ID token issuance, and stateful server-side
  session lifecycle management (issuance, lookup, expiration, revocation).
- It does NOT communicate with PC/SC smartcard hardware readers, PKCS#11 modules,
  OCSP/CRL responders, or a live federal Identity Provider (Login.gov / MAX.gov).
- Every authentication and session-validation payload explicitly includes:
    "simulated": True
    "real_idp_verified": False
- Fail-closed gate: Set `ASCE_REQUIRE_REAL_IDP=true` or
  `ASCE_ALLOW_SIMULATED_AUTH=false` (or `allow_simulation=False`) to disable
  simulated smartcard logins and fail closed when no real IdP is integrated.
"""

import hashlib
import json
import os
import secrets
import sqlite3
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


SIMULATION_WARNING = (
    "SYNTHETIC SIMULATION — NO HARDWARE SMARTCARD OR LIVE FEDERAL IdP VERIFIED"
)


def _env_flag(name: str, default: Optional[bool] = None) -> Optional[bool]:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    val = raw.strip().lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return default


def is_simulation_enabled(allow_simulation: Optional[bool] = None) -> bool:
    """
    Determine whether synthetic PIV/CAC simulation is permitted.

    Fails closed if `allow_simulation=False`, `ASCE_REQUIRE_REAL_IDP=true`,
    or `ASCE_ALLOW_SIMULATED_AUTH=false`.
    """
    if allow_simulation is not None:
        return bool(allow_simulation)
    if _env_flag("ASCE_REQUIRE_REAL_IDP", False):
        return False
    return bool(_env_flag("ASCE_ALLOW_SIMULATED_AUTH", True))


class AuthenticationLevel(Enum):
    """NIST 800-63B Authenticator Assurance Levels."""
    AAL1 = "AAL1"  # Single-factor (password)
    AAL2 = "AAL2"  # Two-factor (password + OTP)
    AAL3 = "AAL3"  # Hardware cryptographic authenticator (PIV/CAC)


class CardType(Enum):
    """Federal smartcard types."""
    PIV = "PIV"    # Personal Identity Verification (civilian)
    CAC = "CAC"    # Common Access Card (military/DoD)
    PIV_I = "PIV-I"  # PIV-Interoperable (contractors)


@dataclass
class X509Certificate:
    """
    Simulated X.509 certificate metadata from a demo PIV/CAC smartcard.
    """
    subject_dn: str
    issuer_dn: str
    serial_number: str
    not_before: str
    not_after: str
    public_key_fingerprint: str
    key_usage: List[str]
    card_type: str

    def is_valid(self) -> bool:
        """Check if certificate is within validity period."""
        now = datetime.now(timezone.utc)
        not_before = datetime.fromisoformat(self.not_before.replace("Z", "+00:00"))
        not_after = datetime.fromisoformat(self.not_after.replace("Z", "+00:00"))
        return not_before <= now <= not_after

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


@dataclass
class SAMLAssertion:
    """
    Simulated SAML 2.0 assertion structure (not XML-signed by a live IdP).
    """
    assertion_id: str
    issuer: str
    subject: str
    audience: str
    not_before: str
    not_on_or_after: str
    session_index: str
    attributes: Dict[str, Any]
    authentication_method: str
    assurance_level: str

    def is_valid(self) -> bool:
        """Check if assertion is within validity period."""
        now = datetime.now(timezone.utc)
        not_before = datetime.fromisoformat(self.not_before.replace("Z", "+00:00"))
        not_after = datetime.fromisoformat(self.not_on_or_after.replace("Z", "+00:00"))
        return not_before <= now <= not_after

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


@dataclass
class OIDCToken:
    """
    Simulated OpenID Connect ID token claim structure (not signed by a live IdP).
    """
    token_id: str
    issuer: str
    subject: str
    audience: str
    issued_at: str
    expires_at: str
    nonce: str
    acr: str  # Authentication Context Class Reference (AAL level pattern)
    amr: List[str]  # Authentication Methods References
    claims: Dict[str, Any]

    def is_valid(self) -> bool:
        """Check if token is not expired."""
        now = datetime.now(timezone.utc)
        expires = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        return now <= expires

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


class PIVCardReader:
    """
    Simulated PIV/CAC smartcard reader.

    NOTE: Does NOT interface with PC/SC or PKCS#11 hardware tokens; issues
    synthetic demo certificate metadata only when simulation is enabled.
    """

    def __init__(self, card_present: bool = True, simulated: bool = True):
        """Initialize card reader simulation."""
        self.connected_card = None
        self.card_present = card_present
        self.simulated = simulated

    def detect_card(self) -> bool:
        """Detect whether simulated smartcard is present."""
        return bool(self.card_present)

    def read_certificate(
        self, pin: str, card_type: str = "CAC"
    ) -> Optional[X509Certificate]:
        """
        Read simulated X.509 certificate after validating PIN format.

        Args:
            pin: Personal Identification Number (4-8 digits)
            card_type: Simulated card type ("PIV", "CAC", or "PIV-I")

        Returns:
            Simulated X509Certificate if PIN valid, None otherwise
        """
        if not self.detect_card():
            return None

        if not self._verify_pin(pin):
            return None

        return self._generate_demo_certificate(card_type=card_type)

    def _verify_pin(self, pin: str) -> bool:
        """
        Verify simulated smartcard PIN format (4-8 numeric digits).
        """
        if not isinstance(pin, str):
            return False
        return 4 <= len(pin) <= 8 and pin.isdigit()

    def _generate_demo_certificate(self, card_type: str = "CAC") -> X509Certificate:
        """Generate synthetic demo certificate metadata for simulation."""
        now = datetime.now(timezone.utc)
        not_before = now - timedelta(days=365)
        not_after = now + timedelta(days=1095)
        normalized_type = card_type if card_type in ("PIV", "CAC", "PIV-I") else "CAC"

        return X509Certificate(
            subject_dn="CN=SMITH.JOHN.DOE.123456789,OU=CONTRACTOR,OU=PKI,OU=DoD,O=U.S. Government,C=US",
            issuer_dn="CN=SIMULATED Demo DoD ID CA-59,OU=PKI,OU=Demo,O=ASCE Simulation,C=US",
            serial_number=secrets.token_hex(16),
            not_before=not_before.isoformat().replace("+00:00", "Z"),
            not_after=not_after.isoformat().replace("+00:00", "Z"),
            public_key_fingerprint=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
            key_usage=["digitalSignature", "keyEncipherment", "nonRepudiation"],
            card_type=normalized_type,
        )

    def sign_challenge(self, challenge: bytes, pin: str) -> Optional[bytes]:
        """
        Simulate signing a challenge digest (demonstration placeholder only).
        """
        if not self._verify_pin(pin):
            return None

        return hashlib.sha256(challenge + b"SIMULATED_DEMO_SIGNATURE").digest()


class SAMLIdentityProvider:
    """
    Simulated SAML 2.0 Identity Provider (IdP) for local demonstration only.
    """

    def __init__(self, idp_entity_id: str, idp_sso_url: str):
        self.entity_id = idp_entity_id
        self.sso_url = idp_sso_url

    def authenticate_with_piv(
        self, certificate: X509Certificate, sp_entity_id: str
    ) -> Optional[SAMLAssertion]:
        """Validate certificate timestamps and issue a simulated SAML assertion."""
        if not certificate.is_valid():
            return None

        attributes = self._extract_attributes_from_dn(certificate.subject_dn)
        attributes["card_type"] = certificate.card_type
        attributes["cert_serial"] = certificate.serial_number

        now = datetime.now(timezone.utc)
        not_before = now - timedelta(minutes=5)
        not_after = now + timedelta(hours=8)

        return SAMLAssertion(
            assertion_id=f"_saml_{secrets.token_hex(16)}",
            issuer=self.entity_id,
            subject=attributes.get("uid", "unknown"),
            audience=sp_entity_id,
            not_before=not_before.isoformat().replace("+00:00", "Z"),
            not_on_or_after=not_after.isoformat().replace("+00:00", "Z"),
            session_index=secrets.token_hex(16),
            attributes=attributes,
            authentication_method="urn:oasis:names:tc:SAML:2.0:ac:classes:SmartcardPKI",
            assurance_level="AAL3",
        )

    def _extract_attributes_from_dn(self, dn: str) -> Dict[str, Any]:
        """Extract user attributes from certificate Distinguished Name."""
        attributes: Dict[str, Any] = {}

        for component in dn.split(","):
            if "=" in component:
                key, value = component.split("=", 1)
                key = key.strip()
                value = value.strip()

                if key == "CN":
                    parts = value.split(".")
                    if len(parts) >= 3:
                        attributes["last_name"] = parts[0]
                        attributes["first_name"] = parts[1]
                        attributes["middle_name"] = parts[2] if len(parts) > 3 else ""
                        attributes["edipi"] = parts[-1]
                    attributes["uid"] = value
                elif key == "OU":
                    attributes.setdefault("organizational_units", []).append(value)
                elif key == "O":
                    attributes["organization"] = value
                elif key == "C":
                    attributes["country"] = value

        return attributes


class OIDCIdentityProvider:
    """
    Simulated OpenID Connect (OIDC) Identity Provider for local demonstration only.
    """

    def __init__(
        self,
        issuer: str,
        authorization_endpoint: str,
        token_endpoint: str,
        userinfo_endpoint: str,
    ):
        self.issuer = issuer
        self.authorization_endpoint = authorization_endpoint
        self.token_endpoint = token_endpoint
        self.userinfo_endpoint = userinfo_endpoint

    def authenticate_with_piv(
        self,
        certificate: X509Certificate,
        client_id: str,
        redirect_uri: str,
        nonce: str,
    ) -> Optional[OIDCToken]:
        """Validate certificate timestamps and issue a simulated OIDC token."""
        if not certificate.is_valid():
            return None

        claims = self._extract_claims_from_certificate(certificate)

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=1)

        return OIDCToken(
            token_id=secrets.token_hex(16),
            issuer=self.issuer,
            subject=claims.get("sub", "unknown"),
            audience=client_id,
            issued_at=now.isoformat().replace("+00:00", "Z"),
            expires_at=expires_at.isoformat().replace("+00:00", "Z"),
            nonce=nonce,
            acr="http://idmanagement.gov/ns/assurance/aal/3",
            amr=["hwk", "pki"],
            claims=claims,
        )

    def _extract_claims_from_certificate(self, cert: X509Certificate) -> Dict[str, Any]:
        """Extract OIDC claims from certificate."""
        dn_parts: Dict[str, str] = {}
        for component in cert.subject_dn.split(","):
            if "=" in component:
                key, value = component.split("=", 1)
                dn_parts[key.strip()] = value.strip()

        cn = dn_parts.get("CN", "")
        parts = cn.split(".")

        return {
            "sub": cert.serial_number,
            "name": cn,
            "given_name": parts[1] if len(parts) > 1 else "",
            "family_name": parts[0] if len(parts) > 0 else "",
            "middle_name": parts[2] if len(parts) > 2 else "",
            "edipi": parts[-1] if len(parts) > 3 else "",
            "card_type": cert.card_type,
            "cert_serial": cert.serial_number,
            "cert_fingerprint": cert.public_key_fingerprint,
        }


class FederalAuthenticationManager:
    """
    Stateful simulation authentication manager with fail-closed session validation.

    Security invariants:
    - Session tokens are only valid if issued by this manager (or a worker sharing
      the same session DB) and present in active session state.
    - Arbitrary non-empty tokens are rejected.
    - Expired sessions fail validation and are purged.
    - `logout()` permanently revokes the token (`self._revoked_tokens` + shared SQLite store).
    - Setting `allow_simulation=False`, `ASCE_REQUIRE_REAL_IDP=true`, or
      `ASCE_ALLOW_SIMULATED_AUTH=false` fails closed on authentication.
    """

    def __init__(
        self,
        sp_entity_id: str,
        protocol: str = "SAML",
        allow_simulation: Optional[bool] = None,
        session_db_path: Optional[str] = None,
    ):
        self.sp_entity_id = sp_entity_id
        self.protocol = protocol
        self._allow_simulation_override = allow_simulation
        self.card_reader = PIVCardReader(simulated=True)
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._revoked_tokens: set = set()
        default_db = os.path.join(tempfile.gettempdir(), "asce_fed_auth_sessions.db")
        self._session_db_path = (
            session_db_path
            or os.environ.get("ASCE_FED_AUTH_DB_PATH")
            or default_db
        )
        self._init_session_db()

        if protocol == "SAML":
            self.idp = SAMLIdentityProvider(
                idp_entity_id="https://idp.agency.gov/saml",
                idp_sso_url="https://idp.agency.gov/saml/sso",
            )
        else:
            self.idp = OIDCIdentityProvider(
                issuer="https://idp.agency.gov",
                authorization_endpoint="https://idp.agency.gov/oauth2/authorize",
                token_endpoint="https://idp.agency.gov/oauth2/token",
                userinfo_endpoint="https://idp.agency.gov/oauth2/userinfo",
            )

    def _connect_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._session_db_path, timeout=5.0)
        return conn

    def _init_session_db(self) -> None:
        db_dir = os.path.dirname(os.path.abspath(self._session_db_path))
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        conn = self._connect_db()
        try:
            with conn:
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                except sqlite3.Error:
                    pass
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sessions (
                        sp_entity_id TEXT NOT NULL,
                        protocol TEXT NOT NULL,
                        session_token TEXT PRIMARY KEY,
                        record_json TEXT NOT NULL,
                        expires_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS revoked_tokens (
                        sp_entity_id TEXT NOT NULL,
                        protocol TEXT NOT NULL,
                        session_token TEXT PRIMARY KEY,
                        revoked_at TEXT NOT NULL
                    )
                    """
                )
        finally:
            conn.close()

    def _persist_session(self, record: Dict[str, Any]) -> None:
        conn = self._connect_db()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO sessions
                        (sp_entity_id, protocol, session_token, record_json, expires_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        self.sp_entity_id,
                        self.protocol,
                        record["session_token"],
                        json.dumps(record, sort_keys=True),
                        record["expires_at"],
                    ),
                )
        finally:
            conn.close()

    def _is_token_revoked_in_db(self, session_token: str) -> bool:
        conn = self._connect_db()
        try:
            row = conn.execute(
                """
                SELECT 1 FROM revoked_tokens
                WHERE sp_entity_id = ? AND protocol = ? AND session_token = ?
                """,
                (self.sp_entity_id, self.protocol, session_token),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def _load_session_from_db(self, session_token: str) -> Optional[Dict[str, Any]]:
        conn = self._connect_db()
        try:
            row = conn.execute(
                """
                SELECT record_json FROM sessions
                WHERE sp_entity_id = ? AND protocol = ? AND session_token = ?
                """,
                (self.sp_entity_id, self.protocol, session_token),
            ).fetchone()
            if not row:
                return None
            return json.loads(row[0])
        finally:
            conn.close()

    def _delete_session_from_db(self, session_token: str) -> bool:
        conn = self._connect_db()
        try:
            with conn:
                cur = conn.execute(
                    """
                    DELETE FROM sessions
                    WHERE sp_entity_id = ? AND protocol = ? AND session_token = ?
                    """,
                    (self.sp_entity_id, self.protocol, session_token),
                )
                return cur.rowcount > 0
        finally:
            conn.close()

    def _revoke_token_in_db(self, session_token: str, had_local_session: bool = False) -> bool:
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        conn = self._connect_db()
        try:
            with conn:
                already_revoked = conn.execute(
                    """
                    SELECT 1 FROM revoked_tokens
                    WHERE sp_entity_id = ? AND protocol = ? AND session_token = ?
                    """,
                    (self.sp_entity_id, self.protocol, session_token),
                ).fetchone()
                cur = conn.execute(
                    """
                    DELETE FROM sessions
                    WHERE sp_entity_id = ? AND protocol = ? AND session_token = ?
                    """,
                    (self.sp_entity_id, self.protocol, session_token),
                )
                db_deleted = cur.rowcount > 0
                if already_revoked is not None:
                    return False
                if not (had_local_session or db_deleted):
                    return False
                conn.execute(
                    """
                    INSERT OR REPLACE INTO revoked_tokens
                        (sp_entity_id, protocol, session_token, revoked_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (self.sp_entity_id, self.protocol, session_token, now_iso),
                )
                return True
        finally:
            conn.close()

    @property
    def simulation_enabled(self) -> bool:
        return is_simulation_enabled(self._allow_simulation_override)

    def authenticate_caseworker(
        self, pin: str, card_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Authenticate a caseworker using simulated smartcard flow when simulation is enabled.
        Fails closed when simulation is disabled (`ASCE_REQUIRE_REAL_IDP=true` or
        `ASCE_ALLOW_SIMULATED_AUTH=false`).
        """
        result: Dict[str, Any] = {
            "success": False,
            "assurance_level": None,
            "session_token": None,
            "user_attributes": None,
            "simulated": True,
            "real_idp_verified": False,
            "warning": SIMULATION_WARNING,
            "error": None,
        }

        if not self.simulation_enabled:
            result["simulated"] = False
            result["error"] = (
                "Real hardware PIV/CAC reader and federal IdP verification required "
                "(simulated authentication is disabled on this deployment)."
            )
            return result

        if not self.card_reader.detect_card():
            result["error"] = "No smartcard detected. Please insert PIV/CAC card."
            return result

        requested_type = card_type or "CAC"
        certificate = self.card_reader.read_certificate(pin, card_type=requested_type)
        if not certificate:
            result["error"] = "Invalid PIN or card read error."
            return result

        if not certificate.is_valid():
            result["error"] = "Certificate expired or not yet valid."
            return result

        if self.protocol == "SAML":
            assertion = self.idp.authenticate_with_piv(certificate, self.sp_entity_id)
            if not assertion or not assertion.is_valid():
                result["error"] = "SAML authentication failed."
                return result

            session_token = assertion.assertion_id
            assurance_level = assertion.assurance_level
            user_attributes = dict(assertion.attributes)
            session_expires = assertion.not_on_or_after
        else:
            nonce = secrets.token_hex(16)
            token = self.idp.authenticate_with_piv(
                certificate,
                client_id=self.sp_entity_id,
                redirect_uri="https://asce.agency.gov/callback",
                nonce=nonce,
            )
            if not token or not token.is_valid():
                result["error"] = "OIDC authentication failed."
                return result

            session_token = token.token_id
            assurance_level = token.acr
            user_attributes = dict(token.claims)
            session_expires = token.expires_at

        user_attributes["simulated"] = True
        user_attributes["real_idp_verified"] = False

        record = {
            "session_token": session_token,
            "protocol": self.protocol,
            "assurance_level": assurance_level,
            "user_attributes": dict(user_attributes),
            "issued_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "expires_at": session_expires,
            "simulated": True,
            "real_idp_verified": False,
        }
        self._sessions[session_token] = record
        self._persist_session(record)

        result["success"] = True
        result["assurance_level"] = assurance_level
        result["session_token"] = session_token
        result["user_attributes"] = user_attributes
        result["session_expires"] = session_expires
        return result

    def validate_session(self, session_token: str) -> Dict[str, Any]:
        """
        Validate an issued session token against the server-side session store.

        Rejects empty, unknown, expired, or logged-out tokens across workers sharing
        the persistent SQLite session backend.
        """
        base = {
            "valid": False,
            "simulated": True,
            "real_idp_verified": False,
            "warning": SIMULATION_WARNING,
        }
        if not session_token or not isinstance(session_token, str):
            return {**base, "error": "No session token provided"}

        if session_token in self._revoked_tokens or self._is_token_revoked_in_db(session_token):
            self._revoked_tokens.add(session_token)
            self._sessions.pop(session_token, None)
            return {**base, "error": "Session has been revoked"}

        record = self._sessions.get(session_token)
        if not record:
            record = self._load_session_from_db(session_token)
            if record:
                self._sessions[session_token] = record
        if not record:
            return {**base, "error": "Unknown or unauthenticated session token"}

        expires_at = datetime.fromisoformat(record["expires_at"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) >= expires_at:
            self._sessions.pop(session_token, None)
            self._delete_session_from_db(session_token)
            return {**base, "error": "Session expired"}

        return {
            "valid": True,
            "assurance_level": record["assurance_level"],
            "user_attributes": dict(record["user_attributes"]),
            "expires_at": record["expires_at"],
            "simulated": record["simulated"],
            "real_idp_verified": record["real_idp_verified"],
            "warning": SIMULATION_WARNING,
        }

    def logout(self, session_token: str) -> Dict[str, Any]:
        """
        Invalidate and permanently revoke an active session token.
        """
        if not session_token or not isinstance(session_token, str):
            return {
                "success": False,
                "error": "No session token provided",
                "simulated": True,
                "real_idp_verified": False,
            }

        removed = self._sessions.pop(session_token, None)
        if session_token in self._revoked_tokens:
            self._delete_session_from_db(session_token)
            return {
                "success": False,
                "error": "Session token not found or already invalidated",
                "simulated": True,
                "real_idp_verified": False,
            }

        revoked_now = self._revoke_token_in_db(
            session_token,
            had_local_session=(removed is not None),
        )
        if not revoked_now:
            return {
                "success": False,
                "error": "Session token not found or already invalidated",
                "simulated": True,
                "real_idp_verified": False,
            }

        self._revoked_tokens.add(session_token)
        return {
            "success": True,
            "message": "Session invalidated and token revoked",
            "simulated": True,
            "real_idp_verified": False,
        }


if __name__ == "__main__":
    print("=== ASCE Federal Authentication Simulation Module ===")
    print(SIMULATION_WARNING)
    auth_manager = FederalAuthenticationManager(
        sp_entity_id="https://asce.agency.gov/saml",
        protocol="SAML",
    )
    res = auth_manager.authenticate_caseworker(pin="12345678")
    print(f"Auth success={res['success']}, simulated={res['simulated']}, real_idp_verified={res['real_idp_verified']}")
