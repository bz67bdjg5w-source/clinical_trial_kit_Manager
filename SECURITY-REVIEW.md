# Security review: privacy and access control

## Scope and limitations

The initial review was read-only. Follow-up fixes added a pre-approved email registration gate and expanded access-control tests. No `.env` contents or secrets were inspected. No production database was accessed, and the application was not deployed.

## Finding addressed

| # | Severity | File | Lines | Finding | Confidence |
|---|----------|------|-------|---------|------------|
| 1 | 🟠 HIGH* (fixed) | `inventory/forms.py`, `inventory/views.py` | 27, 33, 73 | Registration requires an exact case-insensitive match in `AUTHORIZED_SIGNUP_EMAILS` and confirmation through a link delivered to that mailbox. New accounts remain inactive until a user submits the confirmation form; confirmation is not performed by GET, and expired links can be resent without account enumeration. An empty allowlist denies registration and confirmation. | 10/10 |

`*` Severity is conditional on the intended account policy. The selected policy is a pre-approved email list plus proof of mailbox control. Render must be configured with the coordinator addresses and working SMTP settings before they can register and confirm accounts. The public signup page remains viewable, but unapproved addresses cannot create active accounts.

## Confirmed protections

- Dashboard, inventory list/create/edit/delete, Quick Update Stock, stock history, and profile require authentication.
- Inventory and stock-history reads are filtered by the current user's owner ID. Edit and delete lookups also include the owner, preventing record-ID substitution from accessing another user's lot.
- Kit ownership on creation is assigned from the authenticated user. Quick Update Stock restricts kit selection to active lots owned by the user, and the stock service rechecks ownership before updating.
- Stock history records are scoped to the owner. The test-email endpoint is separately permission-gated and targets the signed-in user's own address.
- Tests cover owner isolation, cross-owner ID tampering on edit/delete and Quick Update Stock, owner assignment, history isolation, and authentication for all private page/action routes.
- Approved registrants receive ordinary inactive accounts without staff, superuser, or inventory-management permissions. After email confirmation, they can manage only their own workspace.
- Confirmation requires a POST after opening the link, reducing activation from automatic link-preview GETs. Resend responses do not disclose whether an eligible pending account exists.

## Verification completed

- `manage.py test inventory`: 50 tests passed. This includes anonymous GET/POST denial across private routes, checks that private kit details do not appear in anonymous responses, unapproved and empty-list registration denial, email confirmation/activation, resend behavior, and tests verifying cross-workspace read, edit, delete, and stock-update attempts do not disclose or change another user's records.
- `manage.py check`: no issues.
- Test run used local SQLite with dotenv loading disabled. Email tests use the in-memory backend; no real messages were sent.

## Policy and deployment notes

- Email confirmation proves control of an approved mailbox, not broader employment status. Confirm that the allowlist is maintained through an approved coordinator roster; stronger identity assurance requires an approved identity provider.
- The README now documents that each approved account can manage its own workspace without administrator permission grants.
- The Render configuration sets `DJANGO_DEBUG=False`, but no live Render environment was inspected. Verify the deployed value before exposing the service; Django debug pages can disclose diagnostic details if debug mode is enabled.

## Remaining deployment check

Configure `AUTHORIZED_SIGNUP_EMAILS` with the approved coordinator addresses and SMTP settings in Render. The default empty list fails closed. Keep `DEBUG=False` in production and verify all production environment settings before launch.
