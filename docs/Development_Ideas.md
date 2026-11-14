# Group Customer Complaint Platform: Development Ideas

Source: `FINAL_Customer_Complaint_Platform_Proposal.pdf` (draft of 3 November 2026, prepared by YL Pang).
Status: ideas for review. Build steps 1 (foundation), 2 (lodging), 3 (workflow), 4 (reminders and notification log), 5 (dashboard and complaint log), 6 (PDF report and import of past complaints) and 7 (tests and demo data) of Section 7 are done, so the Phase 1 build is complete; see the README for how to run it.

## 1. Approach

The proposal leaves platform selection to IT (low-code, off-the-shelf or custom) and the budget is still open. This build is a custom Django application that acts as a **reference implementation** of the requirements in Sections 4–6 of the proposal. The team can click through it, and IT can compare it with the other options. If IT picks a different platform, the data model, workflow rules and test cases still carry over.

Decisions made so far:

| Decision | Choice |
|---|---|
| Framework | Django (Python 3.12) |
| Scope | Full Phase 1, and after the demo feedback a read-only traders' view (a Trader sees the factories assigned to them; see section 3) |
| Location | Project root `D:\user\Projects\Customer-Complaint-Platform`, with the proposal staying in `docs/` |

## 2. Stack

| Layer | Choice | Reason |
|---|---|---|
| Backend | Django | Built-in auth, permissions, forms, migrations and file uploads. |
| Admin | Django admin | The MR maintains complaint types, lab tests, PIC lists and reminder days without IT (Section 8, "drop-down lists the MR can maintain"). |
| UI | Django templates, a small vanilla JavaScript file for the conditional form fields, and a hand-written stylesheet (HTMX or Tailwind CSS may be added later) | Conditional fields (Pack size, Vessel/Container, Others) without a heavy SPA. The server enforces the same rules, so the form works without the script. Avoids a CDN or a Node build step for now. |
| Database | SQLite in dev, PostgreSQL in production | No setup now, easy switch later. |
| Email | SMTP; Mailpit locally | Every notification can be tested without sending real mail. |
| PDF | ReportLab (or xhtml2pdf) | Complaint summary PDF, SCAR from template, audit export in the layout of the current report. |
| Excel | openpyxl | Complaint log export in the current log's column order. |
| Reminders | Daily scheduled job (management command) | Sends the 3-day reminders. Days are configurable per complaint type. |
| Tests | pytest-django | State machine, access rules, mandatory-field checks. |

If Phase 2 needs a richer interface later, a REST API can be added to the same backend.

## 3. Roles and access

| Role | What they do |
|---|---|
| Lodger | Any internal stakeholder (trader, marketeer, sales, factory staff, management, finance, shared service). Lodges and submits complaints. |
| Coordinator (MR) | Group-level. Assigns PICs, deletes with a reason, checks effectiveness, closes the case, and owns the settings. |
| PIC | Investigates, issues SCARs, and replies to the customer. A complaint can have several PICs (for example Lab PIC and Lab manager). |
| Lab staff | Among the assigned PICs. Fills in the laboratory section. |
| HOD | Sets Valid or Invalid and submits to the FM, or returns to the PIC. |
| FM | One per factory. Approves or rejects the response. |
| Top management | Read-only view of all factories and the dashboard. |
| Trader | Handles more than one factory or entity. Sees the complaints of the factories assigned to them, read-only; can still lodge a complaint. |
| Group PIC | A PIC, HOD or FM of a department (for example Group Shipping) who covers several or all factories. Sees and acts on the factories assigned to them. |
| IT administrator | Uses the admin panel to create users and to assign each user's role, department and factories. |

Access depends on role, factory and department. The Coordinator and top management see all factories. A user can be given more than one factory (`assigned_factories`), so a trader or a group PIC sees exactly those factories and no other; a user with none sees nothing of the factories. Lodgers can come from outside the factories, so lodging cannot be limited to the selected factory.

## 4. Data model

- **Factory:** code (FA–FE), name, default FM email, deputy, and a running-number counter per factory per year.
- **UserProfile:** role, factory, department.
- **FactoryPIC:** the PIC email list per factory that the Coordinator selects from.
- **Complaint:**
  - Auto fields: lodger, date and time, reference number (for example `FA/2026/0001`).
  - Form fields a–p (Section 5.1).
  - `status` (stage), `hod_decision` (Valid/Invalid with reason), and `cause` (Internal/External).
  - Soft-delete flag with reason and who deleted.
- **CoordinatorSection:** received by, acknowledgement date, assigned PICs, remarks, attachments.
- **Investigation:** root cause, correction, corrective action, respondent PIC.
- **LabSection / LabResult:** opens only when Sample sent = Yes. The test list (FFA, M&I, PV, IV, Colour, Appearance and odour) is configurable.
- **SCAR:** reference number (same as the complaint's), party type, issued-to, contact email, dates, generated PDF, returned file.
- **ComplaintEvent** (built instead of a separate Review model): every step with who did it, when, the status before and after, and any comment. HOD returns, FM rejections and "not effective" results are events, so the repeat cycle keeps its full history.
- **CustomerReply:** saved copy of the sent email, with sent date.
- **Verification:** first and second verified dates, results, effective yes/no, closed date and closed by.
- **Attachment** and **AuditLog** (who changed what and when).
- **ComplaintType / SubType, ReminderSetting** (days per complaint type, default 3).

A complaint is never removed. Deleting marks it Deleted with a reason, and it stays in the log.

## 5. Workflow (state machine)

```
Draft → Submitted → Assigned / PIC investigation ⇄ Pending SCAR from Supplier
      → HOD review ──return──► PIC investigation
      → FM review ──reject──► PIC investigation → HOD review (repeat until approved)
      → Approved → Customer Response Sent → Closed
                                   └ not effective → back to PIC
```

Rules enforced in code:

1. Submit is blocked until fields a–k and the customer email are filled. Drafts can be saved incomplete.
2. Only the Coordinator can assign. Assignment records who received it and the acknowledgement date, and emails each PIC the reference number, a PDF summary and a link.
3. **Issue SCAR** is available when the cause is external. It sets the status to Pending SCAR from Supplier, and **Submit to HOD** stays locked until the returned SCAR is uploaded.
4. The HOD decides Valid or Invalid after any SCAR is returned. Invalid needs a reason, and returning to the PIC needs a comment. Submission goes straight to the FM, with the Coordinator and PIC copied.
5. FM rejection needs a comment and emails the PIC, HOD and Coordinator. The loop repeats until the FM approves.
6. The customer reply is held until any SCAR is back and the FM has approved. It is built from a template, editable by the PIC, and a copy is saved when sent. Status becomes Customer Response Sent, and the HOD, FM and Coordinator are notified.
7. The Coordinator records the first and second verified dates and closes the case.
8. Any action idle for 3 days (configurable per complaint type) triggers a reminder to the person responsible.

Valid/Invalid is stored separately from the stage, so the dashboard can show "Valid, in progress" and "Valid, closed".

## 6. Screens (from Figures 2–11)

1. Lodge and submit complaint (conditional fields, draft/submit).
2. Coordinator assignment (PIC multi-select, remarks, attachments, Delete).
3. PIC investigation with laboratory section.
4. SCAR to external party (preview, generate again, email, upload returned SCAR).
5. HOD review.
6. FM approval.
7. Reply to customer.
8. Effectiveness review and closing.
9. Dashboard: factory and period filters, totals, complaints by type, valid by type, where open complaints are waiting, open aging, list of complaints.
10. Complaint log with Excel export and the summary counts below it.

## 7. Build order

1. **Foundation (done):** project setup, users and roles, factory-scoped access, audit log, MR-maintained settings, seed data for the five factories.
2. **Lodging (done):** complaint form, conditional fields, draft/submit validation, reference numbers.
3. **Workflow (done):** assignment, PIC investigation, lab section, SCAR, HOD review, FM approval, customer reply, closing, with the emails for each step.
4. **Reminders and notification log (done).**
5. **Dashboard and complaint log (done)** with Excel export.
6. **PDF export and import (done):** the PDF report and audit pack, and the 6-month historical import from a spreadsheet template.
7. **Tests and demo data (done):** 227 automated tests, the fictional demo users from `seed --demo`, and the demo complaints and browser checks in `tools/screenshots/`. See `docs/TestGuide.md`.

## 8. Gaps in the proposal and assumed handling

| Gap | Assumption |
|---|---|
| Step 8 "not effective": Figure 9 returns the case to the PIC, but Section 4 and Figure 1 do not show this loop. | Build it. Customer Response Sent returns to PIC investigation, and the history records it. |
| Sign-in method is not specified. | Email and password first, kept swappable for company single sign-on. |
| Open point 1: who approves when the FM or HOD is away. | Deputy field per factory, with routing to the deputy when set. |
| Open point 2: format of the past-complaint upload. | One spreadsheet template, 6 months of data. |
| Open point 4: Business Type and Type of Management fields. | Left out of the form until explained. |
| Open point 5: IMS section is done by the Coordinator. | Yes. |
| Open point 6: Complaint method choices and lab test list. | MR-configurable. The six tests are the default. |
| Open point 7: who may delete. | Coordinator only, with a reason. |
| Open point 8: who keeps the PIC email list. | Coordinator, with each factory naming its PICs. |
| Open point 9: HOD decision after SCAR. | HOD decides after the SCAR is received. The SCAR stays in the record even if the complaint is Invalid. |
| Open point 10: verified dates without CAR numbers. | Still recorded by the Coordinator when closing. |
| Step 3: the reply is held until "any SCAR is back and the FM has approved". | A SCAR can only be pending before the HOD stage, so the check is made again when the PIC sends the reply, against the latest SCAR. |
| Step 4: the laboratory section must be "complete" before the PIC submits. | When a sample was sent, the lab acknowledgement date and at least one test result are required. Only laboratory staff among the assigned PICs can fill it in, so the Coordinator is asked to include one. |
| Step 4a: SCAR contents. | The PIC chooses the party type (supplier, transporter, other), name and email. The wording comes from a template the PIC can edit; the PDF carries the complaint details but not the buyer's name. If the SCAR email cannot be sent, nothing changes. Re-issuing is allowed after an FM rejection, and the latest SCAR counts. |
| Step 5: the HOD of a factory. | Any HOD of the complaint's factory can act; if a HOD is away, the deputy gets the email and can act. The FM is the factory's default FM, or its deputy while away. |
| Step 7: who the customer reply goes to. | The email address on the complaint, with a copy to the PIC who sends it. If it cannot be sent the status does not change. The lodger is not emailed. |
| Step 8: closing. | The first verified date and its result are required to close; the second check is optional. "Not effective" needs a comment and goes back to PIC investigation. |
| Deleting. | The Coordinator can delete any submitted complaint (never a draft). It keeps its stage, shows as Deleted with the reason, and no further action is possible. Nobody is emailed. |
| Step 4: what "not done within 3 days" means. | Calendar days at the current stage, counted from the moment the complaint reached it. The first reminder goes at the number of days set for the complaint type (default 3), then again every that-many days until the stage changes. Weekends and public holidays are not skipped. |
| Demo feedback: customer, product and pack size. | Chosen from lists (code and name) that the Coordinator loads from a spreadsheet on the Master data page, or edits in Settings. A row whose code is already there updates it; nothing is deleted; Active = No stops it being offered. The name is also copied onto the complaint, so an old complaint keeps its name. When a list is empty the form falls back to free text. |
| Demo feedback: complaint received date, response time and ageing. | A mandatory date, never in the future, next to the complaint method. Days Open, ageing and the response time are counted from it (not from the day it was typed in). Complaints that existed before get their lodging date. |
| Demo feedback: delivery date. | It moved to the Transport section. A shipment has a loading date and a discharge date (From / To are the loading and discharge port or city). A local delivery has a delivery date, and the places are labelled Place of loading and Destination. Existing shipment delivery dates became discharge dates. No date can be in the future, on the form or in the verification and laboratory dates. |
| Demo feedback: the description. | Five parts: issue identified (mandatory), findings by the customer, test result by the customer, what the customer requests, and other information. The old single description is kept as the composed text of the five. |
| Demo feedback: automatic assignment. | On submit the routing table picks the department: Quality to QA, Packaging to Production, Logistics and Documentation to Operation (local) or Shipping (shipment), Others to the Coordinator. The PICs of that department at the factory (or covering it) are assigned and emailed, with only the Coordinator copied. The Coordinator can reassign from the complaint. If no rule or no PIC is found, the complaint waits for the Coordinator as before. `AUTO_ASSIGN=false` switches this off. The routing table and each PIC's department are kept in Settings. |
| Demo feedback: who reviews as HOD. | The HOD of the department the complaint was routed to; if that department has no HOD at the factory, every HOD of the factory. |
| Demo feedback: My complaints and the dashboard. | Ten rows a page unless changed (10, 25, 50 or 100), with Page X of Y and the total. My complaints has Product and Days Open, names the person each complaint waits for (PIC - Name) and filters by category, buyer and product. The dashboard adds complaints by factory, a short reason for invalid and deleted complaints that links to the complaint, and a paged table of every open complaint (the old cap was dropped). The log, the dashboard and the Excel export share the same filters, so the file holds what the screen shows. |
| Demo feedback: the reply on the complaint page. | Shown as a PDF to download (and in the audit pack) instead of the whole email. The text is still kept, and the notification log has the email as sent. |
| Step 4: who is reminded. | The person responsible for the stage: the Coordinator (assignment, effectiveness check), the PIC (investigation, pending SCAR, reply due), the HOD or the FM, or their deputy while they are away. If nobody is set up for the stage, the reminder goes to the Coordinator and says so. Nobody is copied. |
| Step 4: the notification log. | Every email is logged with recipients, text, attachments and result, including failures and emails with no address to go to. It is visible to the Coordinator and top management only. |
| Step 5: how a complaint is classed as Valid, Invalid, Under investigation or Deleted. | Deleted comes first (a deleted complaint is never also counted as valid or invalid). Otherwise the HOD's decision decides; with no decision yet it is Under investigation. The four add up to the total. Drafts are not counted. A valid complaint is In progress until it is Closed. |
| Step 5: the "Remarks" column of the log. | The proposal lists Remarks without saying whose. It is filled from the Coordinator's closing comment. IMS remarks (the Coordinator's remarks when assigning) have their own column. |
| Step 5: what "complaint date" means for the period filter, and who may export. | The date the complaint was lodged. Every role gets the dashboard, the log and the export for the complaints it may already see, so a factory PIC exports only their factory, and a lodger only their own. |
| Step 5: open complaint aging. | Calendar days since the complaint was submitted, in bands of 0-3, 4-7, 8-14, 15-30 and over 30 days. |
| Step 6: the layout of the current Customer Complaint Report. | The current report was not supplied. The PDF follows what the proposal says it holds: the complaint, a laboratory section, an IMS (Coordinator) section, the response, approvals, the reply and the closing, then the documents and the history. To match the old layout, change the order or wording in `complaints/report_pdf.py`; the Business Type and Type of Management fields stay out (open point 4). |
| Step 6: PDF and audit pack for audits. | Anyone who can see a complaint can download its PDF report and audit pack. The audit pack is a zip: the PDF, the SCARs as sent, the reply as a PDF and every attached file by section. A file missing from storage is listed, not fatal. |
| Step 6: text in the PDF. | The PDF uses the standard fonts. A character outside Western European writing (for example Chinese in a buyer's name) prints as `?`. Fonts for other scripts can be added if the factories need them. |
| Step 6: what an imported complaint becomes. | A new platform reference in date order (the running numbers carry on), with the old number kept in "Number in the previous log". The person who received it is the name in the old log, never the importing Coordinator. The stage comes from the dates (closing date: Closed; reply date: Customer Response Sent; any investigation or decision: PIC investigation; otherwise Submitted). A reply or closing date needs Valid or invalid, so the dashboard classes stay right. |
| Step 6: how the import is controlled. | Coordinator only. The file is checked first and nothing is saved until it has no errors and the Coordinator confirms; then every row is saved together or none. A row older than 6 months is a warning, not an error. A date in the future, an unknown factory, type, sub-type or choice, a bad email, a number used twice for a factory, and a deleted row without a reason are errors. Up to 2000 rows and 5 MB. No emails are sent. |
| Step 6: reminders for imported complaints. | An imported complaint that is still open starts its reminder clock at the import, not at its old issue date, so importing does not send a flood of reminders. Aging on the dashboard still counts from the old issue date. |
| Deployment: where the demo runs. | Render's free plan (a free web service and a free PostgreSQL database), from `render.yaml`. The free plan sleeps when idle, expires the database after 30 days, has no persistent disk (uploaded files are lost on restart) and has no scheduler: reminders are sent from a button on the Notifications page. See `docs/Deployment.md`. |
| Deployment: settings. | Everything comes from the environment: `DJANGO_DEBUG=false` turns on HTTPS-only cookies and redirect, WhiteNoise for the static files and logging to the console; `DATABASE_URL` selects PostgreSQL. The suite was run on PostgreSQL 16 as well as SQLite. A real launch still needs the items in section 6 of `docs/Deployment.md` (mail, backups, file storage, scheduler, password reset, sign-in lockout). |
| Reply to the customer: the wording. | It follows the recommended template in `docs/emailTemplate.md`: the subject "Investigation Findings for Complaint FA/2026/0005 - Product / Contract", the shipment details, then Investigation Summary, Root Cause Assessment, Conclusion and Corrective and Preventive Actions, then the signature. The PIC can edit all of it before sending. |
| Reply to the customer: the data behind it. | The investigation now also records an investigation summary and a conclusion (both optional), because the template has sections for them. A section left empty is left out of the email. The signature is the Factory Manager who approved the response (name, position and factory), then the company name (`COMPANY_NAME`, default BEST Group). The complaint form has no loading date or discharge date, so the shipment details list the delivery date, the loading port and the port of discharge instead; the vessel, vehicle and container are listed when present. The correction (not in the template) stays in the record but is not in the email. Customer name: the contact person, else "{buyer} Team". |
| Email delivery. | With no mail server set up the email is only written to the server log, and the log now says so (Not delivered) with a warning on the Notifications page. Real email: SMTP, or a mail service over HTTPS (Brevo, Resend, SendGrid, Mailgun, Postmark) chosen by which key is set, because Render's free plan blocks SMTP. Mail services only send from a verified address or domain, so mail should come from a domain the group owns. The Coordinator can send a test email from the Notifications page. |
| PDF summary. | A short summary PDF is attached to the email that assigns PICs (as the proposal says). The full PDF report for audits is separate (step 6). |

## 9. Quality and risks

- Tests cover the state machine, mandatory-field blocking, role and factory access, the SCAR lock, and reference numbering per factory and year.
- Emails from a new platform can be blocked as spam (proposal risk). Production needs a company mailbox or mail service, set up and tested before the pilot.
- The proposal is a draft for review, so the workflow lives in one module and the roles, complaint types, tests and reminder days are settings. Changing the process later should not need a rewrite.
- All mockup data in the proposal is fictional, and seed data will be the same.

## 10. Suggested folder layout

```
Customer-Complaint-Platform/
├── docs/                      proposal and this file
├── manage.py
├── config/                    Django settings and URLs
├── complaints/                models, workflow, views, forms, emails, PDF/Excel
├── accounts/                  users, roles, factories, PIC lists, factory-scoped access
├── audit/                     append-only audit log (who changed what and when)
├── templates/
├── static/
├── tests/
└── requirements.txt
```
