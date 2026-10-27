# Clinical Trial Kit Manager — MVP starter

A Django starter for a **dummy-data prototype** that combines:
- Kit lots and expiry tracking
- Stock/reorder thresholds
- Dashboard warnings
- Daily, per-user kit expiry reminders

## Recommended architecture
- **Django**: Python web framework with built-in authentication and permission foundations
- **PostgreSQL**: shared database for multiple SCs (use SQLite only for local development)
- **CRM-approved hosting**: deploy only after IT/security/site approval
- **Email**: SMTP or approved mail service configured by IT
- **Scheduled alerts**: run `python manage.py send_kit_alerts` daily using an approved scheduler

This starter is not production-ready and must not be used with real patient/study data until it has been reviewed and approved.

## Run locally (development only)

1. Install Python 3.11+.
2. Create and activate a virtual environment.
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Copy `.env.example` to `.env` and set a secret key for local development. Keep `.env` private; never commit email passwords or app passwords.
5. Create the database and an admin user:
   ```bash
   python manage.py migrate
   python manage.py createsuperuser
   ```
6. Add fake demo data (the seed command adds missing demo records and does not replace existing records):
   ```bash
   python manage.py seed_demo --owner-username <your-username>
   ```
7. Start the development server:
   ```bash
   python manage.py runserver
   ```
8. Open http://127.0.0.1:8000/. First-time users can choose **Sign up** from the login page. Only email addresses in `AUTHORIZED_SIGNUP_EMAILS` are eligible to register; add approved addresses to this comma-separated environment variable before inviting coordinators. New accounts remain inactive until the coordinator confirms the link sent to that email. Registration creates a standard account with no admin or staff privileges, and each user can manage only their own workspace.

The workspace migration requires an explicit owner for every existing kit lot, and expiry reminders must belong to the kit owner's account. It stops rather than guessing or assigning existing records if ownership is missing or inconsistent. Assign ownership from a verified mapping before retrying such a migration.

## MVP scope
- Dashboard: expiry and low-stock warnings
- Kit lots: study name, lot, expiry, quantity, supplier/lab and notes
- One low-stock threshold: 5 kits; lots with 5 or fewer kits are flagged
- Expiry email: one reminder per inventory manager, kit lot and expiry date, sent when 0–30 days remain

## Important next steps before any shared or production deployment
- Use CRM-approved identity/SSO if available; do not share accounts.
- Add/confirm role permissions and audit logging for stock edits and alert acknowledgements.
- Use PostgreSQL and encrypted approved hosting; configure backups and test restore.
- Configure HTTPS, secure cookies, CSRF protection, secret management, logging, monitoring and least-privilege database access.
- Get the site/CRM/sponsor approval required for intended data and workflow.
- Confirm expiry thresholds, stock deduction rules, and ordering lead times with the site.
- Do not use this tool as a source of truth for IP accountability, IRT, protocol compliance, or clinical decisions.
- Test date/time zones, duplicate lots, expired stock, and concurrent edits.

## Email alerts
Each account has a private inventory workspace. Low-stock alerts and expiry reminders are sent only to the active owner's valid registered email address; other users do not receive inventory details. New sign-ups receive no admin, staff, or inventory-management permissions, but can manage their own workspace immediately. Low-stock emails are triggered when an active lot first crosses to the threshold or below; further edits while it remains low do not resend, and a later crossing after stock recovers can notify again.

### Configure a real SMTP service

Copy `.env.example` to `.env`, then fill in the values supplied by your organization or email provider:

- `EMAIL_HOST` and `EMAIL_PORT` — provider's SMTP server and port
- `EMAIL_HOST_USER` and `EMAIL_HOST_PASSWORD` — SMTP login and provider-issued app password if required (store the secret only in `.env` or a deployment secret manager)
- `EMAIL_USE_TLS=True` for the provider's STARTTLS setup (follow its instructions)
- `DEFAULT_FROM_EMAIL` — sender address authorized by that email service; it is not the recipient

Restart Django after changing `.env`. The **Send one-time test email** button is optional and only tests whether the email service accepts a message to the signed-in inventory manager's registered account. It is not how daily reminders are triggered, and acceptance does not prove inbox delivery. If the account has no registered address or SMTP settings are incomplete, the app reports an error. Check the server output/logs for failures.

### Test reminders locally

After setting up SMTP and a manager account with an email address, run the command manually from the project directory:

```bash
.venv/bin/python manage.py send_kit_alerts
```

It checks active kit lots expiring today through 30 days from today and sends each reminder only to that lot's owner. Run it again to verify it does not send a duplicate for the same lot, owner and expiry date. If sending fails, the attempt and error are logged, the reminder remains unsent and another run can retry it. Changing a lot's expiry date creates a new reminder event. No email is sent for kits more than 30 days from expiry. Low-stock emails are triggered by a stock update crossing the threshold; the dashboard and inventory-list warnings remain available as well.

For dummy-data checks without an SMTP account, the test suite uses Django's in-memory email backend and fake users/kits:

```bash
.venv/bin/python manage.py test inventory
```

### Schedule daily on your Mac

To schedule reminders on this Mac, install the provided LaunchAgent. It runs the daily management command at 9:00 a.m. local time without anyone opening the website or clicking the test-email button. First configure SMTP in `.env`, then run:

```bash
mkdir -p "$HOME/Library/LaunchAgents"
cp deploy/mac/com.clinicaltrialkitmanager.expiry-reminders.plist "$HOME/Library/LaunchAgents/"
plutil -lint "$HOME/Library/LaunchAgents/com.clinicaltrialkitmanager.expiry-reminders.plist"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.clinicaltrialkitmanager.expiry-reminders.plist"
launchctl print "gui/$(id -u)/com.clinicaltrialkitmanager.expiry-reminders"
```

The LaunchAgent file uses this project's current absolute folder path. If you move the project, update the Python, working-directory, and log paths in `deploy/mac/com.clinicaltrialkitmanager.expiry-reminders.plist` before installing it. The Mac must be logged in, awake, and connected to the network; macOS may run a missed calendar event after waking. To request an immediate scheduler run (not an email test), use `launchctl kickstart -k "gui/$(id -u)/com.clinicaltrialkitmanager.expiry-reminders"`. Review `kit-alerts.log` and `kit-alerts-error.log` in the project folder. To stop and remove the schedule, run `launchctl bootout "gui/$(id -u)/com.clinicaltrialkitmanager.expiry-reminders"` and remove the plist from `~/Library/LaunchAgents`.

### Hosted server

Use the hosting platform's scheduled-job service, or an administrator-managed cron/systemd timer, to run `.venv/bin/python manage.py send_kit_alerts` once per day. Configure SMTP secrets in the host's secret/environment settings (not in source control), use the approved database and application settings, collect command output and failures centrally, and prevent overlapping scheduled runs. A production scheduler must run even when no one visits the website. Coordinate SMTP sender authorization, approved recipients, retention of error logs, and operational monitoring with your organization before real use.
