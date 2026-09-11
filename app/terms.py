"""Public legal documents for Junction (Terms, Privacy, Customer Addendum).

Source of truth on Render: TERMS_AND_CONDITIONS_JSON.

Preferred JSON shape:
{
  "title": "Junction Legal",
  "version": "2.0",
  "documents": [
    {"id": "terms", "title": "Terms & Conditions", "content": "..."},
    {"id": "privacy", "title": "Privacy Policy", "content": "..."},
    {"id": "addendum", "title": "Customer Addendum", "content": "..."}
  ]
}

Legacy shape still works: {"title","version","content"} — content is one string.
If content is empty and documents are missing, built-in defaults are used.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/terms-and-conditions", tags=["terms-and-conditions"])
logger = logging.getLogger(__name__)

EFFECTIVE_DATE = "11 September 2026"
COMPANY_NAME = "Sunskriti Data Management Company"
APP_NAME = "Junction"
GRIEVANCE_EMAIL = "grievance@junction.today"
SUPPORT_CHANNEL = "the Junction app or website"

TERMS_CONTENT = f"""TERMS AND CONDITIONS
Platform: {APP_NAME}
Operated by: {COMPANY_NAME}
Effective Date: {EFFECTIVE_DATE}

1. Introduction
Welcome to {APP_NAME}, operated by {COMPANY_NAME} (“Platform”, “we”, “our”, “us”). By registering as a user or seller, accessing, or using {APP_NAME}, you agree to these Terms and Conditions, which govern your access and use of our services.

2. Eligibility
- Users: Individuals under the age of 18 may create a profile and use the platform for browsing and limited exchanges, provided they have parental or guardian consent.
- Sellers / shop owners: Only individuals or entities who are 18 years or older and legally capable of entering into binding contracts may register shops, list products, or otherwise sell on the platform.
- By using the platform, all users agree to comply with applicable laws of India and these Terms and Conditions.

3. Seller / Shop-Owner Obligations
- Sellers must list products with accurate descriptions, pricing, and applicable taxes.
- Sellers agree not to list, promote, or sell any banned, counterfeit, or illegal goods under Indian law.
- Sellers must comply with the Consumer Protection (E-Commerce) Rules, 2020 and other applicable laws.
- Sellers remain responsible for GST invoices and tax compliance where applicable.

4. Buyer / Customer Responsibilities
- Buyers must provide accurate information when placing orders.
- Buyers under 18 must have parental or guardian consent for transactions.
- Buyers agree not to misuse the platform or engage in fraudulent activity.

5. Plans, Billing, and Renewals
- {APP_NAME} may offer a free trial and paid plans (including Starter, Serious, Growth, and Premium, as shown in the Plans section).
- Each plan includes the features, limits, pricing, billing period, and renewal terms displayed at the time you subscribe.
- Paid plans renew as described at checkout unless cancelled before renewal according to the process shown in the app.
- You are responsible for accurate billing information and all applicable charges under your selected plan.

6. Prohibited Goods and Acceptable Use
The following are strictly prohibited:
- Narcotics, drugs, and psychotropic substances
- Weapons, explosives, and ammunition
- Counterfeit, pirated, or stolen goods
- Items restricted by Indian law or flagged by authorities
- Spam, abuse, unauthorized access attempts, disruption of the service, or other unlawful use

By agreeing to these Terms, sellers consent that any material violation may be reported to authorities.

7. Platform Rights
- We reserve the right to suspend or terminate accounts that violate these Terms.
- We may remove listings that are misleading, illegal, or harmful.
- We cooperate with government authorities in case of flagged illegal activity.

8. Transactions
- All transactions must comply with applicable tax laws.
- Sellers are responsible for issuing valid GST invoices where applicable.
- Buyers must receive clear order confirmations and receipts through the platform or seller.

9. Returns and Refunds
- Sellers must honour return and refund policies as per the Consumer Protection Act, 2019.
- No unfair cancellation charges may be imposed.

10. Data Protection
- We collect and process personal data in accordance with the Digital Personal Data Protection Act, 2023.
- Please refer to our Privacy Policy and Customer Addendum for details on personal data and shop data.

11. Grievance Redressal
- A Grievance Officer is appointed to handle complaints relating to these Terms and the platform.
- Complaints will be acknowledged within 48 hours and resolved within 30 days as far as practicable.
- Grievance Officer (for {COMPANY_NAME} / {APP_NAME}):
  Email: {GRIEVANCE_EMAIL}
  Channel: {SUPPORT_CHANNEL}

12. Limitation of Liability
- We act as an intermediary technology platform and are not a party to disputes solely between buyers and sellers, except as required by applicable e-commerce or consumer law.
- Our liability is limited to the extent permitted by Indian law.

13. Changes
- We may update these Terms from time to time. Updated versions will be posted in the app with a revised Effective Date / version.
- Continued use after changes take effect constitutes acceptance.

14. Governing Law
- These Terms are governed by the laws of India.
- Subject to applicable law, disputes shall be subject to the jurisdiction of courts having jurisdiction over the principal place of business of {COMPANY_NAME} in India.

15. Acceptance
By registering and using {APP_NAME}, you acknowledge that you have read, understood, and agreed to these Terms and Conditions, the Privacy Policy, and (if you operate a shop) the Customer Addendum.
"""

PRIVACY_CONTENT = f"""PRIVACY POLICY
Platform: {APP_NAME}
Operated by: {COMPANY_NAME}
Effective Date: {EFFECTIVE_DATE}

1. Introduction
{COMPANY_NAME} (“we”, “our”, “us”), operating {APP_NAME}, values your privacy. This Privacy Policy explains how we collect, use, store, and protect personal information when you use our platform.

2. Information We Collect
We may collect:
- Personal details: name, email address, phone number, and related profile information.
- Business details (for sellers / shop owners): GSTIN, business address, shop details, and related verification data.
- Transaction details: orders, invoices, payment status records processed through the platform.
- Technical data: device information, IP address, cookies/similar technologies, and usage logs needed to operate and secure the service.

3. How We Use Your Information
We use your information to:
- Provide, maintain, and improve {APP_NAME}.
- Facilitate transactions and communications between buyers and sellers.
- Verify identities where required and help prevent fraud or abuse.
- Comply with legal obligations under Indian law.
- Communicate regarding orders, account updates, plans/billing, and support.

4. Sharing of Information
We may share information:
- With the other party to a transaction (buyer or seller) as needed to complete an order.
- With government authorities when required by law or when illegal goods/activity are flagged.
- With trusted processors that help us run the service (for example SMS/OTP, hosting, analytics, or payment providers), under appropriate contractual safeguards.
- We do not sell or rent your personal data to third parties for their independent marketing.

5. Data Protection and Retention
- We implement reasonable security measures to protect personal data.
- Access is restricted to authorized personnel and processors with a need to know.
- Data is retained only as long as necessary for the purposes described, or as required by law.

6. Your Rights
Under the Digital Personal Data Protection Act, 2023, you may have the right to:
- Access your personal data.
- Request correction or erasure, subject to legal retention needs.
- Withdraw consent where processing is based on consent.
- Lodge complaints with the Data Protection Board of India.
Requests may be made through {SUPPORT_CHANNEL} or {GRIEVANCE_EMAIL}.

7. Cookies and Tracking
We may use cookies and similar technologies to operate the service, remember preferences, and understand usage. You may disable cookies in your browser; some features may not work fully.

8. Grievance Officer
Grievance Officer for privacy concerns:
{COMPANY_NAME} — {APP_NAME}
Email: {GRIEVANCE_EMAIL}
Channel: {SUPPORT_CHANNEL}

9. Changes to this Policy
We may update this Privacy Policy from time to time. Changes will be posted on the platform with an updated effective date or version.

10. Governing Law
This Privacy Policy is governed by the laws of India. Disputes are subject to the jurisdiction of courts having jurisdiction over the principal place of business of {COMPANY_NAME} in India.

11. Acceptance
By using {APP_NAME}, you acknowledge this Privacy Policy and consent to processing of personal data as described, to the extent required under applicable law.
"""

ADDENDUM_CONTENT = f"""CUSTOMER / SHOP-DATA ADDENDUM
Platform: {APP_NAME}
Operated by: {COMPANY_NAME}
Effective Date: {EFFECTIVE_DATE}

This Customer Addendum (“Addendum”) is between {COMPANY_NAME} (“Platform”) and the shop owner / seller (“Customer”) who registers a shop or lists products on {APP_NAME}. It supplements the Terms and Conditions and Privacy Policy.

1. Customer Eligibility
- Only individuals or entities who are 18 years or older and legally capable of entering into binding contracts may register as Customers operating shops on {APP_NAME}.
- Customers must provide accurate business and contact details (including GSTIN where applicable).

2. Data Ownership
- The Customer retains ownership of shop, product, inventory, order, and other business content they enter into {APP_NAME} (“Customer Data”).
- The Platform does not claim ownership of Customer Data.

3. Processing Terms
- The Customer appoints the Platform to process Customer Data only as needed to operate, maintain, secure, and improve {APP_NAME}, and to provide support and legal compliance.
- The Platform will not use Customer Data for unrelated independent commercial sale of that data.
- The Customer is responsible for the lawfulness of Customer Data they upload and for notices/permissions owed to their own end customers under applicable law.

4. Customer Responsibilities
- Ensure products listed are genuine, accurately described, and lawful.
- Issue valid GST invoices where applicable.
- Honour return and refund obligations under the Consumer Protection Act, 2019 and related rules.
- Not list prohibited goods (narcotics, weapons/explosives, counterfeit/pirated goods, or other items banned under Indian law).

5. Compliance and Cooperation
- Material violations may result in suspension or termination of access.
- Flagged illegal activity may be reported to authorities.
- Customers must cooperate with lawful requests for information.

6. Platform Rights
- The Platform may remove misleading, illegal, or harmful listings.
- The Platform may suspend or terminate Customer accounts for breaches of this Addendum, the Terms, or the Privacy Policy.

7. Limitation of Liability
- The Platform provides intermediary technology services and is not liable for disputes solely between Customers and their buyers beyond what Indian law requires.
- Customers remain solely responsible for their compliance with applicable trade, tax, and consumer laws.

8. Governing Law
- This Addendum is governed by the laws of India.
- Disputes are subject to the jurisdiction of courts having jurisdiction over the principal place of business of {COMPANY_NAME} in India.

9. Acceptance
By registering a shop or acting as a seller on {APP_NAME}, you acknowledge that you have read, understood, and agreed to this Customer Addendum, in addition to the Terms and Conditions and Privacy Policy.
"""


def _default_documents() -> list[dict[str, str]]:
    return [
        {"id": "terms", "title": "Terms & Conditions", "content": TERMS_CONTENT.strip()},
        {"id": "privacy", "title": "Privacy Policy", "content": PRIVACY_CONTENT.strip()},
        {"id": "addendum", "title": "Customer Addendum", "content": ADDENDUM_CONTENT.strip()},
    ]


DEFAULT_TERMS = {
    "title": "Junction Legal",
    "version": "2.0",
    "documents": _default_documents(),
}


class LegalDocument(BaseModel):
    id: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1)


class TermsAndConditions(BaseModel):
    title: str
    version: str
    content: str
    documents: list[LegalDocument] = Field(default_factory=list)
    updated_at: datetime


def _combine_documents(documents: list[LegalDocument]) -> str:
    blocks: list[str] = []
    for doc in documents:
        blocks.append(f"{doc.title}\n\n{doc.content.strip()}")
    return "\n\n---\n\n".join(blocks)


def _parse_documents(raw: object) -> list[LegalDocument]:
    if not isinstance(raw, list):
        return []
    parsed: list[LegalDocument] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        doc_id = str(row.get("id") or "").strip()
        title = str(row.get("title") or "").strip()
        content = str(row.get("content") or "").strip()
        if not doc_id or not title or not content:
            continue
        parsed.append(LegalDocument(id=doc_id, title=title, content=content))
    return parsed


def _from_parts(title: str, version: str, content: str, documents: list[LegalDocument]) -> TermsAndConditions:
    docs = documents or [LegalDocument(**row) for row in DEFAULT_TERMS["documents"]]
    body = (content or "").strip() or _combine_documents(docs)
    return TermsAndConditions(
        title=(title or DEFAULT_TERMS["title"]).strip() or DEFAULT_TERMS["title"],
        version=(version or DEFAULT_TERMS["version"]).strip() or DEFAULT_TERMS["version"],
        content=body,
        documents=docs,
        updated_at=datetime.now(timezone.utc),
    )


def load_terms_and_conditions() -> TermsAndConditions:
    terms_json = os.getenv("TERMS_AND_CONDITIONS_JSON", "").strip()
    if terms_json:
        try:
            data = json.loads(terms_json)
            if isinstance(data, dict):
                documents = _parse_documents(data.get("documents"))
                content = str(data.get("content") or "").strip()
                # Empty content alone used to look like a successful env load while
                # login still showed local fallback text. Prefer documents, else defaults.
                if not content and not documents:
                    logger.warning(
                        "TERMS_AND_CONDITIONS_JSON has empty content and no documents; using defaults"
                    )
                    return _from_parts(
                        str(data.get("title") or DEFAULT_TERMS["title"]),
                        str(data.get("version") or DEFAULT_TERMS["version"]),
                        "",
                        [],
                    )
                return _from_parts(
                    str(data.get("title") or DEFAULT_TERMS["title"]),
                    str(data.get("version") or DEFAULT_TERMS["version"]),
                    content,
                    documents,
                )
            logger.warning("TERMS_AND_CONDITIONS_JSON must be a JSON object; using defaults")
        except json.JSONDecodeError:
            logger.warning("TERMS_AND_CONDITIONS_JSON is invalid JSON; using defaults")

    title = os.getenv("TERMS_AND_CONDITIONS_TITLE", DEFAULT_TERMS["title"]).strip() or DEFAULT_TERMS["title"]
    version = os.getenv("TERMS_AND_CONDITIONS_VERSION", DEFAULT_TERMS["version"]).strip() or DEFAULT_TERMS["version"]
    content = os.getenv("TERMS_AND_CONDITIONS_CONTENT", "").strip()
    return _from_parts(title, version, content, [])


@router.get("", response_model=TermsAndConditions)
def get_terms_and_conditions() -> TermsAndConditions:
    return load_terms_and_conditions()
