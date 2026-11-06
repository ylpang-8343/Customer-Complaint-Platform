# Group Customer Complaint Platform: Development Ideas

Source: `FINAL_Customer_Complaint_Platform_Proposal.pdf` (draft of 3 November 2026, prepared by YL Pang).
Status: ideas for review. Build steps 1 (foundation), 2 (lodging) and 3 (workflow) of Section 7 are done; see the README for how to run it.

## 1. Approach

The proposal leaves platform selection to IT (low-code, off-the-shelf or custom) and the budget is still open. This build is a custom Django application that acts as a **reference implementation** of the requirements in Sections 4–6 of the proposal. The team can click through it, and IT can compare it with the other options. If IT picks a different platform, the data model, workflow rules and test cases still carry over.

Decisions made so far:

| Decision | Choice |
|---|---|
| Framework | Django (Python 3.12) |
| Scope | Full Phase 1 (Phase 2, the traders' view, is out of scope) |
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

Access depends on role and factory. The Coordinator and top management see all factories. Lodgers can come from outside the factories, so lodging cannot be limited to the selected factory.

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
4. **Reminders** and notification log.
5. **Dashboard and complaint log** with Excel export.
6. **PDF export** in the layout of the current report, and the 6-month historical import from a spreadsheet template.
7. **Tests and demo data** (see Section 9).

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
| PDF summary. | A simple summary PDF is built now (ReportLab) for the assignment email. Step 6 replaces it with the layout of the current report. |

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
