"""
Legal pages: Privacy Policy and Terms of Service.

The HTML text below is a TEMPLATE for an Apple App Store submission.
It is NOT legally reviewed. Have a lawyer review and edit before launch.
Apple requires both URLs to be reachable and accurate.
"""
import os
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from models.db_user import DBUser

router = APIRouter()

APP_NAME = "Game Radar"
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "support@gameradar.app")
LAST_UPDATED = "April 23, 2026"

_BASE_STYLE = """
body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 760px; margin: 40px auto; padding: 0 20px; color: #1a1a2e; line-height: 1.6; }
h1 { color: #16a34a; }
h2 { margin-top: 32px; border-bottom: 1px solid #e5e7eb; padding-bottom: 6px; }
.meta { color: #666; font-size: 14px; margin-bottom: 24px; }
"""


def _wrap(title: str, body_html: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — {APP_NAME}</title>
  <style>{_BASE_STYLE}</style>
</head>
<body>
  <h1>{title}</h1>
  <p class="meta">Last updated: {LAST_UPDATED}</p>
  {body_html}
  <hr style="margin-top:48px">
  <p class="meta">Questions? Contact us at <a href="mailto:{SUPPORT_EMAIL}">{SUPPORT_EMAIL}</a>.</p>
</body>
</html>"""


@router.get("/privacy", response_class=HTMLResponse)
def privacy_policy():
    body = f"""
    <p>{APP_NAME} ("we", "us", "our") respects your privacy. This policy explains what data we collect, how we use it, and the choices you have.</p>

    <h2>Information We Collect</h2>
    <ul>
      <li><strong>Account information:</strong> email address, name, date of birth, nationality, phone number (optional).</li>
      <li><strong>Profile content:</strong> bio, profile photo, sports preferences, social media handles you choose to add.</li>
      <li><strong>Event content:</strong> events you create or join, including title, location, date/time, and description.</li>
      <li><strong>Location data:</strong> we use the location you enter for events to compute distance from other users searching nearby. We do not collect your device's GPS in the background.</li>
      <li><strong>Authentication identifiers:</strong> when you sign in with Apple or Google, we receive an email address (or Apple's private relay address) and a unique identifier.</li>
    </ul>

    <h2>How We Use Your Information</h2>
    <ul>
      <li>To create and manage your account.</li>
      <li>To show your events to other users searching for events near them.</li>
      <li>To send password reset codes when requested.</li>
      <li>To enforce our community guidelines and respond to reports.</li>
    </ul>

    <h2>How We Share Information</h2>
    <ul>
      <li><strong>Other users:</strong> your name, profile photo, sports preferences, host rating, and events you organize are visible to other {APP_NAME} users.</li>
      <li><strong>Service providers:</strong> we use Supabase (database hosting), Apple and Google (sign-in), and OpenStreetMap/Nominatim (geocoding). These providers process data on our behalf.</li>
      <li><strong>Legal:</strong> we may disclose information if required by law or to protect the safety of our users.</li>
      <li>We do <strong>not</strong> sell your personal information.</li>
    </ul>

    <h2>Your Rights and Choices</h2>
    <ul>
      <li><strong>Access and correction:</strong> you can view and edit your profile in the app at any time.</li>
      <li><strong>Deletion:</strong> you can permanently delete your account from the app's settings. This removes your profile, events you organized, and your participation history.</li>
      <li><strong>Apple private relay:</strong> if you signed in with Apple using a private relay email, we do not have your real email address.</li>
    </ul>

    <h2>Children</h2>
    <p>{APP_NAME} is not intended for children under 13 (or the equivalent minimum age in your country). We do not knowingly collect data from children under 13. If you believe a child has provided us information, contact us at {SUPPORT_EMAIL} and we will delete the account.</p>

    <h2>Data Security</h2>
    <p>We use industry-standard practices to protect your data, including encrypted transport (HTTPS) and hashed passwords. No system is perfectly secure, and we cannot guarantee absolute security.</p>

    <h2>Changes to This Policy</h2>
    <p>We may update this policy from time to time. The "Last updated" date at the top of this page reflects the most recent changes. Material changes will be communicated through the app.</p>
    """
    return HTMLResponse(_wrap("Privacy Policy", body))


@router.get("/terms", response_class=HTMLResponse)
def terms_of_service():
    body = f"""
    <p>By using {APP_NAME}, you agree to these Terms of Service. If you do not agree, do not use the app.</p>

    <h2>Eligibility</h2>
    <p>You must be at least 13 years old to use {APP_NAME}. By using the app, you confirm you meet this requirement.</p>

    <h2>Your Account</h2>
    <ul>
      <li>You are responsible for the accuracy of the information in your profile.</li>
      <li>You are responsible for all activity under your account.</li>
      <li>Do not share your account credentials with others.</li>
      <li>Notify us immediately if you believe your account has been compromised.</li>
    </ul>

    <h2>Acceptable Use</h2>
    <p>You agree not to:</p>
    <ul>
      <li>Post content that is unlawful, harassing, threatening, hateful, sexually explicit, or otherwise objectionable.</li>
      <li>Impersonate another person.</li>
      <li>Use the app for spam, scams, or fraudulent activity.</li>
      <li>Attempt to interfere with or compromise the security of the app or its users.</li>
      <li>Use automated tools to access the app without our written permission.</li>
    </ul>

    <h2>User-Generated Content</h2>
    <p>You retain ownership of the content you post (events, profile information, photos). By posting content, you grant {APP_NAME} a non-exclusive, worldwide, royalty-free license to host, display, and distribute that content within the app for the purpose of operating the service.</p>
    <p>We have zero tolerance for objectionable content or abusive users. You can report content or block users from within the app. We will review reports promptly and may remove content or suspend accounts at our discretion.</p>

    <h2>Events</h2>
    <p>Events on {APP_NAME} are organized by users, not by us. {APP_NAME} does not verify event organizers, host events, or guarantee that any event will take place. You participate in events at your own risk. {APP_NAME} is not responsible for any injury, loss, or damage that may result from attending an event.</p>

    <h2>Suspension and Termination</h2>
    <p>We may suspend or terminate your account at any time, with or without notice, if we believe you have violated these terms or if your conduct poses a risk to other users.</p>

    <h2>Disclaimer of Warranties</h2>
    <p>{APP_NAME} is provided "as is" without warranties of any kind, express or implied. We do not warrant that the app will be uninterrupted, error-free, or secure.</p>

    <h2>Limitation of Liability</h2>
    <p>To the maximum extent permitted by law, {APP_NAME} and its operators are not liable for any indirect, incidental, consequential, or punitive damages arising from your use of the app.</p>

    <h2>Changes to These Terms</h2>
    <p>We may update these terms from time to time. Continued use of the app after changes take effect constitutes acceptance of the new terms.</p>
    """
    return HTMLResponse(_wrap("Terms of Service", body))


@router.post("/users/me/accept-terms")
def accept_terms(
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    current_user.terms_accepted_at = datetime.now(timezone.utc)
    db.commit()
    return {"accepted_at": current_user.terms_accepted_at.isoformat()}


@router.get("/users/me/terms-status")
def terms_status(current_user: DBUser = Depends(get_current_user)):
    return {
        "accepted": current_user.terms_accepted_at is not None,
        "accepted_at": current_user.terms_accepted_at.isoformat() if current_user.terms_accepted_at else None,
    }
