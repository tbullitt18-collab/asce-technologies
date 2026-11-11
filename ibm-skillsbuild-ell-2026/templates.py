"""DRAFT WORDING — requires founder approval.

Fixed, rule-based templates that replace AI drafting. Nothing in this module calls a model or the network.

Purpose: ASCE is a preventive secretary. A HARD LOCK blocks the risky ACTION (an approval that could cause a
clawback, or a duplicate document request that causes a delay loop). It never blocks the applicant. Every
case therefore gets an UNBLOCK PLAN, and no case is left in a silent hold:
  (1) what is needed to clear it,
  (2) the owner (APPLICANT, CASEWORKER or SUPERVISOR),
  (3) a due date (from a confirmed regulatory date when the engine has one, otherwise an ASCE policy default
      that is clearly labelled as such),
  (4) an auto-escalation target if it is not resolved by the due date,
  (5) a plain-language applicant notice explaining what is needed and what happens next.

Lookup rules (deterministic):
  * The decision is a human reviewer decision (APPROVE_FOR_REVIEW / RETURN / ESCALATE) or the engine's own
    route for a case (engine_route(), which can also return SUPERVISOR_ESCALATION for stalled cases).
  * Each engine flag type maps to one template, first by (program family, flag type), then by
    ("*", flag type). Unknown flag types use the UNMAPPED template, which forces supervisor handling.
  * Templates apply in engine flag order. Only byte-equivalent canonical flags are de-duplicated; distinct
    findings that share a template id remain separate work items.
  * Placeholders are filled only from the case record and the engine flag details. Missing values render
    as "[see case file]". Due dates are computed from an explicit as_of date. Identical input always gives
    identical output.

All wording, owners and policy-default day counts below are DRAFTS for founder / program-counsel approval.
They are not legal advice and have not been reviewed by any agency.
"""
import datetime
import json
import string

from compliance_engine import ADVISORY_ENGINE_FLAG_TYPES, NON_LOCKING_FLAG_TYPES, approval_locked

TEMPLATE_VERSION = "DRAFT-2026-10-05.3"
TEMPLATE_LABEL = "set by rules engine (template)"
DRAFT_NOTICE_FOOTER = ("DRAFT NOTICE. Authorized staff must review and approve this notice before it is sent. "
                       "If you have questions, contact the office that handles your case and give your case number.")
# Requirement 0: the engine triages, blocks risky actions, drafts and escalates. A person makes every decision.
HUMAN_DECISION_LINE = ("A person, not a computer, makes every decision on your case. No automated system has "
                       "approved or denied your case.")
HUMAN_DECISION_REQUIRED = ("Human decision required: the rules engine prepared this case (triage, document checks, "
                           "drafted notice, routing). It did not decide it.")

LOCK_PROTECTS_APPLICANT = ("This lock protects you, the applicant, from a clawback (being asked to pay benefits back "
                           "later) and from repeat requests for documents you already sent. It stops a risky action; "
                           "it does not stop your case.")
NO_LOCK_PROTECTS_APPLICANT = ("Your case is not locked. A report that conflicts with information we already verified, "
                              "or that may be about a different person, does not stop your case, does not stop your "
                              "benefits and is never treated as fraud by an automated system.")

# Flag types that never block a case on their own.
ADVISORY_FLAG_TYPES = ADVISORY_ENGINE_FLAG_TYPES
# Blocking flag types whose next step is evidence development rather than a merits decision.
EVIDENCE_ONLY_FLAG_TYPES = frozenset({"POLICY_BLOCK", "EVIDENCE_REQUIRED", "DUTY_TO_ASSIST"})
# Flags that go to human reconciliation (never a lock, never fraud).
RECONCILIATION_FLAG_TYPES = frozenset({"VERIFIED_FACT_CONFLICT", "POSSIBLE_IDENTITY_MISMATCH"})
# Flags that send the case straight to a supervisor.
SUPERVISOR_ROUTE_FLAG_TYPES = frozenset({"STALLED_CASE_ESCALATION", "HOUSING_AT_RISK"})
# Flags where approving now could pay benefits that later have to be clawed back.
CLAWBACK_RISK_FLAG_TYPES = frozenset({
    "POLICY_BLOCK", "DATA_CONFLICT", "INSPECTOR_CONFLICT",
    "DUA_APPLICATION_AFTER_DAP", "DUA_APPLICATION_LATE", "DUA_LATE_GOOD_CAUSE_REVIEW",
    "DUA_EMPLOYMENT_PROOF_LATE", "DUA_EMPLOYMENT_PROOF_OVERDUE", "DUA_WEEK_OUTSIDE_DAP",
})
# Non-locking flags whose plan items still apply after a human approves (a stall is resolved by acting).
APPROVAL_CARRYOVER_FLAG_TYPES = NON_LOCKING_FLAG_TYPES - {"STALLED_CASE_ESCALATION"}

# Every flag type the engine can emit, by program family (used by tests and reports).
_PROTECTION = ("REDUNDANT_REQUEST_BLOCKED", "STALLED_CASE_ESCALATION", "HOUSING_AT_RISK",
               "VULNERABLE_HOUSEHOLD_MEMBER", "VERIFIED_FACT_CONFLICT", "POSSIBLE_IDENTITY_MISMATCH")
_COMMON = ("SLA_CRITICAL", "EVIDENCE_REQUIRED", "PRIORITY_EXPEDITE", "DATA_CONFLICT") + _PROTECTION
ENGINE_FLAG_TYPES = {
    "FEMA": ("SLA_CRITICAL", "POLICY_BLOCK", "PRIORITY_EXPEDITE", "INSPECTOR_CONFLICT",
             "EVIDENCE_REQUIRED", "IHP_PERIOD_EXTENSION_RECORDED", "INPUT_INVALID") + _PROTECTION,
    "VA": ("EVIDENCE_DEADLINE_REVIEW", "EVIDENCE_REQUIRED", "DUTY_TO_ASSIST",
           "INPUT_INVALID") + _PROTECTION,
    "SSA": _COMMON,
    "MEDICAID": _COMMON + ("EX_PARTE_RENEWAL_REQUIRED",),
    "SNAP": _COMMON + ("SNAP_NON_RECURRING_OVERTIME",),
    "HUD": _COMMON,
    "DUA": ("DUA_APPLICATION_AFTER_DAP", "DUA_APPLICATION_LATE", "DUA_LATE_GOOD_CAUSE_REVIEW",
            "DUA_EMPLOYMENT_PROOF_LATE", "DUA_EMPLOYMENT_PROOF_OVERDUE", "DUA_EMPLOYMENT_PROOF_PENDING",
             "DUA_WEEK_OUTSIDE_DAP", "INPUT_INVALID") + _COMMON,
    "UNKNOWN": ("INPUT_INVALID",),
}

DECISION_QUEUES = {
    "ESCALATE": "Senior Reviewer Queue",
    "RETURN": "Evidence Request Queue",
    "APPROVE_FOR_REVIEW": "Next-Stage Processing",
    "SUPERVISOR_ESCALATION": "Supervisor Queue",          # engine-only route; not a reviewer choice
    "HUMAN_RECONCILIATION": "Reconciliation Queue (human)",  # engine-only route; not a reviewer choice
}

# ---- owners, policy-default due windows and auto-escalation targets (DRAFT ASCE operating policy) ----------
OWNERS = ("APPLICANT", "CASEWORKER", "SUPERVISOR")
ESCALATION_TARGETS = {
    "APPLICANT": "Senior Reviewer Queue (decides on the information available; no silent hold)",
    "CASEWORKER": "Supervisor Queue",
    "SUPERVISOR": "Program Manager Review",
}
POLICY_DAYS = {"IMMEDIATE": 0, "URGENT": 1, "CASEWORKER": 2, "SUPERVISOR": 5, "APPLICANT": 10}
POLICY_BASIS = "ASCE policy default (DRAFT), not a regulatory deadline: {days} calendar day(s)"

# Per-decision defaults. next_step text for the three reviewer decisions is identical to app.DECISION_NEXT_STEPS.
DECISION_DEFAULTS = {
    "ESCALATE": {
        "next_step": "Hand the case to a senior reviewer for independent review before any action is taken.",
        "notice_header": ("Case {case_id}: your case has been sent to a senior reviewer. This is a normal step when "
                          "a case needs a closer look. No decision has been made yet."),
        "checklist": ["Assign the case to the Senior Reviewer Queue.",
                      "Confirm every deterministic flag against the case file before any action.",
                      "Record the senior reviewer's decision and reason in the case file."],
        "item": ("SUPERVISOR", "SUPERVISOR", "Senior reviewer reviews the flags and decides the case.",
                 "A senior reviewer will review your case and decide the next step"),
    },
    "RETURN": {
        "next_step": "Prepare a request to the applicant for the missing evidence listed in this case.",
        "notice_header": ("Case {case_id}: we need more information before we can keep working on your case. "
                          "Please see the items below."),
        "checklist": ["Assign the case to the Evidence Request Queue.",
                      "List each missing item and the date it is due in the request to the applicant.",
                      "Do not request any document that is already in the file."],
        "item": ("CASEWORKER", "URGENT", "Send the evidence request (with due dates) to the applicant.",
                 "We will send you a written list of what we need"),
    },
    "APPROVE_FOR_REVIEW": {
        "next_step": "Forward the case to next-stage processing.",
        "notice_header": ("Case {case_id}: your case has moved to the next stage of processing. You do not need to "
                          "do anything right now. We will contact you if we need more information."),
        "checklist": ["Forward the case to Next-Stage Processing.",
                      "Confirm the case file is complete before forwarding."],
        "item": ("CASEWORKER", "CASEWORKER", "Forward the case to next-stage processing.",
                 "We will move your case to the next stage of processing"),
    },
    "SUPERVISOR_ESCALATION": {
        "next_step": "Supervisor reviews the case today, assigns an owner and sets the next action.",
        "notice_header": ("Case {case_id}: your case has been sent to a supervisor for priority attention. We are "
                          "making sure it does not sit on hold. No decision has been made yet."),
        "checklist": ["Assign the case to the Supervisor Queue.",
                      "Record why the case was escalated and who owns the next action."],
        "item": ("SUPERVISOR", "URGENT", "Supervisor assigns an owner and the next action for the escalated case.",
                 "A supervisor will assign your case to a staff member and set the next step"),
    },
    "HUMAN_RECONCILIATION": {
        "next_step": ("A caseworker reconciles the conflicting or unconfirmed report against the verified record "
                      "before any action is taken on it."),
        "notice_header": ("Case {case_id}: a staff member is checking a report in your file against information we "
                          "already verified. No decision has been made yet."),
        "checklist": ["Assign the case to the Reconciliation Queue.",
                      "Show the prior verification and the new report side by side.",
                      "Record the reconciliation result, who made it and why."],
        "item": ("CASEWORKER", "CASEWORKER", "Reconcile the report against the verified record and record the result.",
                 "A staff member will compare the records"),
    },
}

OVERRIDE_CHECKLIST_ITEM = ("Attach the supervisor override record (supervisor initials, reason and time) to the case "
                           "file; the rules engine had placed this case under HARD LOCK to prevent a clawback.")


def _T(tid, notice, checklist, owner, due, need, applicant_text, due_from_detail=None, due_basis=None):
    """One flag template. owner in OWNERS; due is a POLICY_DAYS key; due_from_detail names a flag-detail date
    (confirmed regulatory date) that is used instead when it is today or later."""
    return {"id": tid, "notice": notice, "checklist": list(checklist), "owner": owner, "due": due,
            "need": need, "applicant_text": applicant_text, "due_from_detail": due_from_detail,
            "due_basis": due_basis}


FLAG_TEMPLATES = {
    # ---- shared (any program) -------------------------------------------------------------
    ("*", "SLA_CRITICAL"): _T(
        "GENERIC.SLA_CRITICAL",
        "A required time limit for this case has passed. We are giving your case priority.",
        ["Verify the start date and the time limit used by the rules engine against the case file.",
         "Record whether the delay was caused by the agency or the applicant."],
        "SUPERVISOR", "SUPERVISOR", "Supervisor completes or reassigns the overdue step.",
        "A supervisor will make sure the overdue step is completed"),
    ("*", "POLICY_BLOCK"): _T(
        "GENERIC.POLICY_BLOCK",
        "We are missing the following item(s) and cannot make a decision without them: {missing_items}.",
        ["Request each missing item by name: {missing_items}.",
         "Set and record a due date for the missing item(s)."],
        "APPLICANT", "APPLICANT", "Applicant provides: {missing_items}.",
        "Please send us: {missing_items}"),
    ("*", "EVIDENCE_REQUIRED"): _T(
        "GENERIC.EVIDENCE_REQUIRED",
        "The record needs additional evidence development before a human can decide the case.",
        ["Identify the exact missing item from the rule flag and check whether the agency can obtain it directly.",
         "Request information from the applicant only when it is not already in the file and cannot be obtained "
         "through the agency's development process."],
        "CASEWORKER", "CASEWORKER", "Complete the evidence-development step identified by the rule flag.",
        "We will review the record and tell you exactly what, if anything, is still needed"),
    ("*", "INPUT_INVALID"): _T(
        "GENERIC.INPUT_INVALID",
        "The automated review could not evaluate this case because required program data is missing or invalid. "
        "No compliance pass was issued.",
        ["Correct every schema issue recorded in the INPUT_INVALID flag.",
         "Run the deterministic review again before any approval or adverse action."],
        "SUPERVISOR", "URGENT", "Correct the canonical case data and rerun the rules engine.",
        "A staff member must correct the case record before the automated review can continue"),
    ("*", "PRIORITY_EXPEDITE"): _T(
        "GENERIC.PRIORITY_EXPEDITE",
        "Your case has been marked for priority handling.",
        ["Place the case in the priority (senior / vulnerable applicant) routing order."],
        "CASEWORKER", "CASEWORKER", "Handle the case in priority order.",
        "We will handle your case in priority order"),
    ("*", "REDUNDANT_REQUEST_BLOCKED"): _T(
        "GENERIC.REDUNDANT_REQUEST_BLOCKED",
        "You do not need to send again any document we already have on file.",
        ["Cancel the request for any document that is already in the file.",
         "Confirm the existing document is legible and current."],
        "CASEWORKER", "URGENT", "Cancel the duplicate document request and use the copy already on file.",
        "We will use the documents you already sent; you do not need to send them again"),
    ("*", "DATA_CONFLICT"): _T(
        "GENERIC.DATA_CONFLICT",
        "Information from two different sources in your file does not match. A reviewer will look at both and "
        "may contact you to clear it up.",
        ["Compare both sources named in the flag and record which one is correct and why.",
         "Contact the applicant only if the file cannot resolve the difference."],
        "CASEWORKER", "SUPERVISOR", "Reconcile the two conflicting sources and record which one is correct.",
        "A staff member will compare the two records and contact you only if needed"),
    ("*", "STALLED_CASE_ESCALATION"): _T(
        "GENERIC.STALLED_CASE_ESCALATION",
        "Your case has not moved forward in time, so it has been sent to a supervisor.",
        ["Supervisor: record why the case stalled ({reason}) and assign the next owner and action."],
        "SUPERVISOR", "URGENT", "Supervisor resolves the stall ({reason}) and assigns the next action.",
        "A supervisor will assign your case and set the next step"),
    ("*", "TENANT_UNKNOWN"): _T(
        "GENERIC.TENANT_UNKNOWN",
        "We could not match your case to a program. A staff member will review it.",
        ["Identify the correct program for this case manually before any action."],
        "SUPERVISOR", "URGENT", "Identify the correct program for the case.",
        "A supervisor will match your case to the right program"),
    # ---- applicant protection (any program; ASCE internal policy, DRAFT) ----------------------
    ("*", "VERIFIED_FACT_CONFLICT"): _T(
        "GENERIC.VERIFIED_FACT_CONFLICT",
        "Your {fact} was already verified on {verified_date} ({verified_method}). A later report from "
        "{report_source} ({report_date}) does not match it. The verified information stays on your file. This "
        "report alone does not stop your case or your benefits and is not treated as fraud. A staff member will "
        "compare both records.",
        ["Compare side by side: prior verification of {fact} ({verified_by}, {verified_method}, {verified_date}) "
         "versus report {report_id} ({report_source}, {report_date}).",
         "Do not lock the case, suspend payments or start a fraud referral on the strength of this report.",
         "Record which record is correct, who decided and why."],
        "CASEWORKER", "CASEWORKER",
        "Reconcile report {report_id} against the {verified_date} verification of {fact} and record the result.",
        "A staff member will compare the new report with the {fact} verification already on your file"),
    ("*", "POSSIBLE_IDENTITY_MISMATCH"): _T(
        "GENERIC.POSSIBLE_IDENTITY_MISMATCH",
        "A report in your file from {report_source} may be about a different person. We will not rely on it "
        "unless a staff member confirms it is about you.",
        ["Confirm whether report {report_id} from {report_source} concerns this applicant (fields compared: "
         "{compared_fields}; mismatched: {mismatched_fields}).",
         "Until a human confirms identity, treat the report as unreliable and take no action based on it."],
        "CASEWORKER", "URGENT", "Confirm the identity of the subject of report {report_id} ({report_source}).",
        "A staff member will check whether that report is really about you"),
    ("*", "HOUSING_AT_RISK"): _T(
        "GENERIC.HOUSING_AT_RISK",
        "Your file shows your housing is at risk ({housing_reasons}). Your case has been moved to the top of the "
        "queue and sent to a supervisor.",
        ["Supervisor: review the case now - housing at risk ({housing_reasons}); {escalation}.",
         "Record any referral to emergency or transitional housing resources."],
        "SUPERVISOR", "URGENT", "Supervisor acts on the housing risk ({housing_reasons}) and sets the next action.",
        "A supervisor will review your case as a priority because your housing is at risk"),
    ("*", "VULNERABLE_HOUSEHOLD_MEMBER"): _T(
        "GENERIC.VULNERABLE_HOUSEHOLD_MEMBER",
        "We noted that your household includes someone who may need extra help or accommodations. Your case has "
        "been given priority.",
        ["Review accommodations for the household member(s): {member_summary}.",
         "Keep the household's vulnerability (age 55+, disability, veteran status) visible to every reviewer."],
        "CASEWORKER", "CASEWORKER", "Review accommodations for the household member(s): {member_summary}.",
        "We will review any accommodations your household needs"),
    # ---- FEMA (44 CFR) ----------------------------------------------------------------------
    ("FEMA", "SLA_CRITICAL"): _T(
        "FEMA.SLA_CRITICAL",
        "A FEMA regulatory time period identified in your file requires human review before the next case action.",
        ["Verify the flag's citation, triggering date and elapsed period against the source case record.",
         "For 44 CFR 206.110(e), determine whether documented extraordinary circumstances support an extension "
         "in the public interest; for 44 CFR 206.115(e), verify the appeal-receipt date and the 90-day boundary."],
        "SUPERVISOR", "SUPERVISOR", "Resolve the specific FEMA timing flag and record the governing citation.",
        "A supervisor will review the specific timing rule identified in your case"),
    ("FEMA", "POLICY_BLOCK"): _T(
        "FEMA.POLICY_BLOCK",
        "A pre-March 22, 2024 declaration has an unresolved SBA-related Other Needs Assistance workflow item. "
        "A reviewer must apply the policy in effect for that declaration date.",
        ["Verify that the declaration date precedes March 22, 2024 and identify the exact pre-2024 SBA-related "
         "Other Needs Assistance requirement.",
         "Do not apply this workflow to a declaration on or after March 22, 2024."],
        "CASEWORKER", "CASEWORKER", "Resolve the pre-2024 SBA workflow under the policy then in effect.",
        "A reviewer will check the SBA-related rule that applied on your disaster's declaration date"),
    ("FEMA", "INSPECTOR_CONFLICT"): _T(
        "FEMA.INSPECTOR_CONFLICT",
        "The inspection notes and the damage status in your file do not match. A supervisor will review the "
        "inspection before a decision is made.",
        ["Compare the inspector notes with the claimed structure status.",
         "Request a supervisory review or re-inspection before any payment decision."],
        "SUPERVISOR", "SUPERVISOR", "Supervisor resolves the inspector-versus-claim conflict (re-inspect if needed).",
        "A supervisor will review the inspection and may schedule a new one"),
    ("FEMA", "IHP_PERIOD_EXTENSION_RECORDED"): _T(
        "FEMA.IHP_PERIOD_EXTENSION_RECORDED",
        "The file contains an approved extension of the standard Individual and Households Program period. A "
        "reviewer will verify the extension record and end date.",
        ["Verify the approving authority, extraordinary-circumstances basis, public-interest determination, and "
         "extension end date recorded under 44 CFR 206.110(e)."],
        "CASEWORKER", "CASEWORKER", "Verify and retain the recorded IHP-period extension metadata.",
        "We will verify the extension already recorded in your file"),
    # ---- VA (38 CFR) ------------------------------------------------------------------------
    ("VA", "EVIDENCE_DEADLINE_REVIEW"): _T(
        "VA.EVIDENCE_DEADLINE_REVIEW",
        "Evidence requested after an incomplete-application notice remains outstanding after the applicable "
        "response period. A reviewer will consider any extension for good cause under 38 CFR 3.109.",
        ["Verify the incomplete-application notice date, requested evidence and any approved extension.",
         "Document the good-cause determination before taking an adverse action."],
        "SUPERVISOR", "SUPERVISOR", "Review the evidence-response period and any good-cause extension.",
        "A reviewer will check the evidence-request dates and any extension that applies"),
    ("VA", "EVIDENCE_REQUIRED"): _T(
        "VA.EVIDENCE_REQUIRED",
        "The record needs additional service or service-connection evidence. This is an evidence-development "
        "step, not an automated denial.",
        ["Check whether the needed record can be obtained through VA or another federal records source.",
         "If applicant action is necessary, identify the exact item and explain available assistance."],
        "CASEWORKER", "CASEWORKER", "Develop the missing service or service-connection evidence.",
        "We will help identify or obtain the service evidence still needed"),
    ("VA", "DUTY_TO_ASSIST"): _T(
        "VA.DUTY_TO_ASSIST",
        "Medical nexus evidence is not in the record. The case must be routed for duty-to-assist and applicable "
        "presumptive-service-connection review before an adverse action.",
        ["Review the condition for presumptive service connection.",
         "Determine whether a VA medical examination, opinion or other development is required under 38 CFR 3.159."],
        "CASEWORKER", "CASEWORKER", "Complete the duty-to-assist and medical-nexus development review.",
        "VA will review whether it must obtain a medical examination, opinion or other evidence"),
    # ---- SSA / Medicaid / SNAP / HUD timeliness wording ------------------------------------
    ("SSA", "SLA_CRITICAL"): _T(
        "SSA.SLA_CRITICAL",
        "Your request appears to have been filed after the time allowed. A reviewer will consider any reason you "
        "had for filing late.",
        ["Verify the notice date, the presumed receipt date and the filing date.",
         "Ask the applicant for any good-cause reason and record it."],
        "APPLICANT", "APPLICANT", "Applicant states any good-cause reason for filing late.",
        "If something kept you from filing on time, please tell us what happened"),
    ("MEDICAID", "SLA_CRITICAL"): _T(
        "MEDICAID.SLA_CRITICAL",
        "Your eligibility decision is taking longer than the time standard. We are giving your case priority.",
        ["Verify the application date and the applicable time standard.",
         "Prioritize the eligibility determination."],
        "SUPERVISOR", "CASEWORKER", "Supervisor ensures the overdue eligibility determination is completed.",
        "We will complete your eligibility decision"),
    ("SNAP", "SLA_CRITICAL"): _T(
        "SNAP.SLA_CRITICAL",
        "Processing of your application has taken longer than the required time. We are giving your case "
        "priority.",
        ["Verify the application date and the standard and expedited processing clocks.",
         "Prioritize the eligibility determination."],
        "SUPERVISOR", "URGENT", "Supervisor ensures the overdue SNAP determination is completed.",
        "We will complete your SNAP decision"),
    ("HUD", "SLA_CRITICAL"): _T(
        "HUD.SLA_CRITICAL",
        "Your annual review (reexamination) is overdue.",
        ["Schedule the overdue annual reexamination with the household."],
        "CASEWORKER", "CASEWORKER", "Schedule the overdue annual reexamination with the household.",
        "We will contact you to schedule your annual review"),
    ("MEDICAID", "EX_PARTE_RENEWAL_REQUIRED"): _T(
        "MEDICAID.EX_PARTE_RENEWAL_REQUIRED",
        "Reliable wage data is available for the renewal. The agency must complete the ex-parte renewal review "
        "before requiring a paper packet or taking procedural termination action.",
        ["Use the available state wage data to complete the ex-parte determination under 42 CFR 435.916.",
         "Do not terminate coverage for failure to return a paper packet when the available reliable information "
         "is sufficient for the renewal determination."],
        "CASEWORKER", "URGENT", "Complete the ex-parte Medicaid renewal before any paper-packet termination step.",
        "We will use the reliable information already available before asking for a paper renewal packet"),
    ("SNAP", "SNAP_NON_RECURRING_OVERTIME"): _T(
        "SNAP.SNAP_NON_RECURRING_OVERTIME",
        "A one-time overtime payment was identified. It must not be silently projected as ongoing monthly income "
        "for a proposed adverse action.",
        ["Verify that the overtime amount is non-recurring.",
         "Recalculate the ongoing income projection without treating the one-time amount as recurring, and record "
         "the human review under 7 CFR 273.12."],
        "CASEWORKER", "CASEWORKER", "Correct the SNAP income projection and review the proposed adverse action.",
        "A reviewer will separate the one-time overtime payment from your ongoing income"),
    # ---- DUA (20 CFR 625) -------------------------------------------------------------------
    ("DUA", "DUA_APPLICATION_AFTER_DAP"): _T(
        "DUA.APPLICATION_AFTER_DAP",
        "Your Disaster Unemployment Assistance (DUA) application was filed on {filed}. The Disaster Assistance "
        "Period ended on {dap_end}. Federal rules (20 CFR 625.8(a)) do not allow an application filed after that "
        "date to be accepted. You may still be able to get other help, such as regular unemployment insurance.",
        ["Verify the filing date and the end of the Disaster Assistance Period.",
         "Refer the applicant to regular unemployment insurance and other assistance.",
         "Include the applicant's appeal rights in the written determination."],
        "CASEWORKER", "CASEWORKER",
        "Issue the written determination with appeal rights and referrals to other assistance.",
        "We will send you a written decision that explains your appeal rights and other help you can ask for"),
    ("DUA", "DUA_APPLICATION_LATE"): _T(
        "DUA.APPLICATION_LATE",
        "Your DUA application was filed on {filed}. The last day to file on time was {deadline}. A late "
        "application can be accepted if you had a good reason (good cause) for filing late.",
        ["Verify the announcement date, the filing deadline ({deadline}) and the filing date ({filed}).",
         "Ask the applicant whether they had good cause for filing late and record the answer."],
        "APPLICANT", "APPLICANT", "Applicant states any good-cause reason for filing after {deadline}.",
        "If something kept you from filing by {deadline}, please tell us what happened"),
    ("DUA", "DUA_LATE_GOOD_CAUSE_REVIEW"): _T(
        "DUA.LATE_GOOD_CAUSE_REVIEW",
        "Your DUA application was filed on {filed}, after the {deadline} deadline. You told us you had a reason "
        "for filing late. A reviewer will decide whether it counts as good cause.",
        ["Review the applicant's stated good-cause reason and decide it under 20 CFR 625.8(a).",
         "Record the good-cause decision and reason in the case file."],
        "SUPERVISOR", "SUPERVISOR", "Supervisor decides good cause for the late filing under 20 CFR 625.8(a).",
        "A reviewer will decide whether your reason counts as good cause and tell you in writing"),
    ("DUA", "DUA_EMPLOYMENT_PROOF_LATE"): _T(
        "DUA.EMPLOYMENT_PROOF_LATE",
        "We received proof of your work or self-employment on {submitted}. It was due by {proof_due}. A reviewer "
        "will look at how this affects your claim.",
        ["Verify the filing date, the 21-day due date ({proof_due}) and the date received ({submitted}).",
         "Determine eligibility consequences under 20 CFR 625.6(e)(2) with a senior reviewer."],
        "SUPERVISOR", "SUPERVISOR", "Senior reviewer determines the effect of late proof under 20 CFR 625.6(e)(2).",
        "A reviewer will decide how the late proof affects your claim and tell you in writing"),
    ("DUA", "DUA_EMPLOYMENT_PROOF_OVERDUE"): _T(
        "DUA.EMPLOYMENT_PROOF_OVERDUE",
        "We have not received proof of your work or self-employment. It was due by {proof_due} (21 days after you "
        "applied). Without it, federal rules may make you ineligible for DUA.",
        ["Confirm no employment or self-employment documentation is in the file.",
         "Determine eligibility consequences under 20 CFR 625.6(e)(2) with a senior reviewer.",
         "Check whether any DUA weeks were already paid on this application."],
        "SUPERVISOR", "SUPERVISOR", "Senior reviewer determines eligibility under 20 CFR 625.6(e)(2).",
        "Please send pay stubs, tax records or a letter from your employer right away; a reviewer will then "
        "decide your eligibility"),
    ("DUA", "DUA_EMPLOYMENT_PROOF_PENDING"): _T(
        "DUA.EMPLOYMENT_PROOF_PENDING",
        "Please send proof of your work or self-employment, for example pay stubs, tax records or a letter from "
        "your employer.",
        ["Track the employment documentation due date ({proof_due}).",
         "Send a reminder to the applicant before the due date."],
        "APPLICANT", "APPLICANT", "Applicant provides employment or self-employment documentation.",
        "Please send proof of your work or self-employment",
        due_from_detail="proof_due", due_basis="20 CFR 625.6(e)(1): within 21 calendar days of filing"),
    ("DUA", "DUA_WEEK_OUTSIDE_DAP"): _T(
        "DUA.WEEK_OUTSIDE_DAP",
        "DUA can be paid only for weeks that begin during the Disaster Assistance Period. These week(s) you "
        "claimed fall outside that period: {weeks_outside}.",
        ["Verify each claimed week against the Disaster Assistance Period (after {disaster_began} through {dap_end}).",
         "Remove weeks outside the period from payment and explain why in the determination."],
        "CASEWORKER", "CASEWORKER", "Remove the out-of-period week(s) from payment: {weeks_outside}.",
        "We will remove the week(s) outside the period and explain why in writing"),
}

UNMAPPED_TEMPLATE = _T(
    "UNMAPPED",
    "A reviewer will look at your case.",
    ["The rules engine produced a flag with no approved template. Handle this case manually."],
    "SUPERVISOR", "URGENT", "Handle the unmapped flag manually and assign the next action.",
    "A supervisor will review your case")

_FAMILY_PREFIXES = (("FEMA", "FEMA"), ("VA_", "VA"), ("SSA_", "SSA"), ("MEDICAID_", "MEDICAID"),
                    ("SNAP_", "SNAP"), ("HUD_", "HUD"), ("DUA_", "DUA"))


def program_family(tenant_id):
    tenant_id = tenant_id or ""
    for prefix, family in _FAMILY_PREFIXES:
        if tenant_id.startswith(prefix):
            return family
    return "UNKNOWN"


def template_for(tenant_id, flag_type):
    family = program_family(tenant_id)
    return FLAG_TEMPLATES.get((family, flag_type)) or FLAG_TEMPLATES.get(("*", flag_type)) or UNMAPPED_TEMPLATE


def engine_route(status, flags):
    """Deterministic route for a case with no human decision yet (the route is a work queue, never a decision).
    Stalled or housing at risk -> SUPERVISOR_ESCALATION; PASS -> APPROVE_FOR_REVIEW (a human still decides);
    INPUT_INVALID -> ESCALATE; any locking flag other than evidence requests -> ESCALATE; only reconciliation
    flags (verified-fact conflict, identity mismatch) left -> HUMAN_RECONCILIATION; only evidence-request flags ->
    RETURN; else ESCALATE."""
    types = [f.get("type") for f in flags or []]
    if any(t in SUPERVISOR_ROUTE_FLAG_TYPES for t in types):
        return "SUPERVISOR_ESCALATION"
    if status == "PASS":
        return "APPROVE_FOR_REVIEW"
    if status == "INPUT_INVALID":
        return "ESCALATE"
    if status != "ESCALATE":
        return "ESCALATE"
    blocking = [t for t in types if t not in ADVISORY_FLAG_TYPES]
    other = [t for t in blocking if t not in RECONCILIATION_FLAG_TYPES and t not in EVIDENCE_ONLY_FLAG_TYPES]
    if other:
        return "ESCALATE"
    if any(t in RECONCILIATION_FLAG_TYPES for t in blocking):
        return "HUMAN_RECONCILIATION"
    if blocking:
        return "RETURN"
    return "ESCALATE"


def queue_priority(flags):
    """(rank, label): rank 0 is the top of the queue. Housing at risk outranks everything; housing at risk with a
    vulnerable household member is IMMEDIATE. Internal ASCE priority rule (DRAFT)."""
    types = {f.get("type") for f in flags or []}
    housing = "HOUSING_AT_RISK" in types
    vulnerable = "VULNERABLE_HOUSEHOLD_MEMBER" in types
    if housing and vulnerable:
        return 0, "P0_IMMEDIATE_HOUSING_AND_VULNERABLE_MEMBER"
    if housing:
        return 1, "P1_HOUSING_AT_RISK"
    if vulnerable or "PRIORITY_EXPEDITE" in types:
        return 2, "P2_VULNERABLE_OR_SENIOR"
    if "STALLED_CASE_ESCALATION" in types:
        return 3, "P3_STALLED"
    return 4, "P4_STANDARD"


def proposed_action_gate(status, flags, proposed_decision):
    """HARD LOCK gate for a caseworker's proposed decision. Approving is blocked (supervisor override needed) only
    when a LOCKING flag is present; non-locking flags (verified-fact conflict, identity mismatch, housing at risk,
    stall, duplicate request, advisories) never block approval. RETURN and ESCALATE are always allowed.
    Returns None when nothing was proposed."""
    if proposed_decision is None:
        return None
    if status == "ESCALATE" and proposed_decision == "APPROVE_FOR_REVIEW" and approval_locked(flags):
        risky = [t for t in (f.get("type") for f in flags or []) if t in CLAWBACK_RISK_FLAG_TYPES]
        return {"proposed_decision": proposed_decision, "decision": "BLOCKED_HARD_LOCK",
                "clawback_risk_flags": risky,
                "reason": "HARD LOCK: approving now could cause a clawback. Supervisor override required."}
    return {"proposed_decision": proposed_decision, "decision": "ALLOWED", "clawback_risk_flags": [], "reason": None}


class _SafeValues(dict):
    def __missing__(self, key):
        return "[see case file]"


def _fmt_value(value):
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value) if value else "[none listed]"
    return "[see case file]" if value is None else str(value)


def _fill(text, values):
    return string.Formatter().vformat(text, (), values)


def _due(as_of, tpl_due_key, detail_date=None, regulatory_basis=None):
    """Return (due_date_iso, basis). A confirmed regulatory date wins when it is today or later."""
    if detail_date:
        try:
            parsed = datetime.date.fromisoformat(detail_date)
            if parsed >= as_of:
                return parsed.isoformat(), regulatory_basis
        except ValueError:
            pass
    days = POLICY_DAYS[tpl_due_key]
    return (as_of + datetime.timedelta(days=days)).isoformat(), POLICY_BASIS.format(days=days)


def _item(owner, due, basis, need, applicant_text, source):
    return {"need": need, "owner": owner, "due_date": due, "due_basis": basis,
            "escalate_to_if_unresolved": ESCALATION_TARGETS[owner], "applicant_text": applicant_text,
            "source": source}


def _what_happens_next(item):
    if item["owner"] == "APPLICANT":
        return (f"- {item['applicant_text']} by {item['due_date']}. If we do not hear from you by then, a senior "
                f"reviewer will look at your case with the information we have. Your case will not be left on hold.")
    return (f"- {item['applicant_text']} by {item['due_date']}. If that has not happened by then, your case moves "
            f"automatically to the {item['escalate_to_if_unresolved'].split(' (')[0]}.")


def build_action_plan(decision, flags, tenant_id, case=None, supervisor_override=False, as_of=None):
    """Return the unblock plan, notice, checklist and next step for a decision. Pure and deterministic.
    The plan prepares work for people; it never records a final decision (human_decision_required is always True)."""
    if decision not in DECISION_DEFAULTS:
        raise ValueError(f"Unknown decision {decision!r}")
    as_of = as_of or datetime.datetime.now(datetime.timezone.utc).date()
    case = case if isinstance(case, dict) else {}
    flags = flags or []
    defaults = DECISION_DEFAULTS[decision]
    base_values = {"case_id": case.get("case_id", "[see case file]")}
    evidence = case.get("evidence") if isinstance(case.get("evidence"), dict) else {}
    base_values["missing_items"] = _fmt_value(list(evidence.get("missing") or []))
    approving = decision == "APPROVE_FOR_REVIEW"
    immediate = any((f.get("details") or {}).get("policy_due_key") == "IMMEDIATE" for f in flags)

    owner, due_key, need, applicant_text = defaults["item"]
    if immediate and not approving:
        due_key = "IMMEDIATE"
    due, basis = _due(as_of, due_key)
    plan = [_item(owner, due, basis, need, applicant_text, f"DECISION.{decision}")]
    paragraphs, checklist, template_ids, seen = [], list(defaults["checklist"]), [], set()
    for flag in flags:
        flag_type = flag.get("type")
        tpl = template_for(tenant_id, flag_type)
        flag_key = json.dumps(flag, sort_keys=True, separators=(",", ":"), default=str)
        if flag_key in seen:
            continue
        seen.add(flag_key)
        template_ids.append(tpl["id"])
        details = flag.get("details") or {}
        values = _SafeValues(base_values)
        for k, v in flag.items():
            if k != "details":
                values[k] = _fmt_value(v)
        for k, v in details.items():
            values[k] = _fmt_value(v)
        checklist.extend(_fill(item, values) for item in tpl["checklist"])
        # An approval carries only advisory / non-locking items; locking items belong to RETURN / ESCALATE paths.
        if approving and flag_type not in APPROVAL_CARRYOVER_FLAG_TYPES:
            continue
        paragraphs.append(_fill(tpl["notice"], values))
        detail_date = details.get(tpl["due_from_detail"]) if tpl["due_from_detail"] else None
        tpl_due = details.get("policy_due_key") if details.get("policy_due_key") in POLICY_DAYS else tpl["due"]
        due, basis = _due(as_of, tpl_due, detail_date, tpl["due_basis"])
        plan.append(_item(tpl["owner"], due, basis, _fill(tpl["need"], values),
                          _fill(tpl["applicant_text"], values), tpl["id"]))
    if supervisor_override:
        checklist.append(OVERRIDE_CHECKLIST_ITEM)
        plan.append(_item("SUPERVISOR", as_of.isoformat(), POLICY_BASIS.format(days=0),
                          "Attach the supervisor override record to the case file.",
                          "A supervisor has reviewed your case", "OVERRIDE"))
    checklist.append(HUMAN_DECISION_REQUIRED)

    locked = approval_locked(flags)
    protected = any(f.get("type") in RECONCILIATION_FLAG_TYPES for f in flags)
    sections = [_fill(defaults["notice_header"], _SafeValues(base_values))]
    if locked and not approving:
        sections.append(LOCK_PROTECTS_APPLICANT)
    elif protected:
        sections.append(NO_LOCK_PROTECTS_APPLICANT)
    sections += paragraphs
    sections.append("What happens next:\n" + "\n".join(_what_happens_next(i) for i in plan))
    sections.append(HUMAN_DECISION_LINE)
    sections.append(DRAFT_NOTICE_FOOTER)
    rank, label = queue_priority(flags)
    return {
        "template_version": TEMPLATE_VERSION,
        "label": TEMPLATE_LABEL,
        "decision": decision,
        "assigned_queue": DECISION_QUEUES[decision],
        "next_step": defaults["next_step"],
        "unblock_plan": plan,
        "next_due_date": min(i["due_date"] for i in plan),
        "notice": "\n\n".join(sections),
        "checklist": checklist,
        "template_ids": template_ids,
        "priority_rank": rank,
        "priority": label,
        "human_decision_required": True,
        "as_of": as_of.isoformat(),
    }
