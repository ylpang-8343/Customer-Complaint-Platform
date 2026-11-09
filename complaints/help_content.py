"""Text of the Help page. Update it as each step of the process is built."""

from accounts.models import Role

# (key, title, description, who). The guide that matches the signed-in role is highlighted.
GUIDES = [
    ("lodger", "Lodgers", "Record a complaint, save it as a draft, attach files and submit it to the Coordinator.", "Every employee"),
    ("coordinator", "Coordinator (MR)", "Receives each submitted complaint, assigns the PICs, checks that the action worked and closes the case. Sets the reminder days and reads the notifications.", "Management Representative"),
    ("pic", "PICs and laboratory", "Investigate a complaint, fill in the laboratory section, issue a SCAR if the cause is external, and reply to the customer.", "PIC · Lab staff"),
    ("approver", "HOD and Factory Manager", "The HOD decides whether a complaint is valid; the FM approves or rejects the response.", "HOD · FM"),
    ("management", "Top management", "See the complaints of every factory, the dashboard and the complaint log, and where each complaint is waiting.", "Group management"),
]

GUIDE_FOR_ROLE = {
    Role.LODGER: "lodger",
    Role.COORDINATOR: "coordinator",
    Role.PIC: "pic",
    Role.LAB: "pic",
    Role.HOD: "approver",
    Role.FM: "approver",
    Role.MANAGEMENT: "management",
}

# (question, answer paragraphs)
TASKS = [
    (
        "How do I lodge a new complaint?",
        [
            "Choose New complaint in the menu. Choose the factory first: it sets the reference number, for example FA/2026/0001.",
            "Fill in the form and choose Save draft at any time, or Submit to Coordinator when it is complete.",
        ],
    ),
    (
        "Which fields must I fill in before I can submit?",
        [
            "The fields marked * : factory, buyer, contract number, product, delivery date, packing type, affected quantity and unit, "
            "transport type, loading port, port of discharge, complaint type, sample sent, description and the buyer's email address.",
            "Some fields open depending on your choices: the pack size for Pack, the vessel, vehicle or container for the transport type, "
            "the sub-type for the complaint type, and short details for Others.",
        ],
    ),
    (
        "Why did my submission not go through?",
        [
            "The form lists every missing field in red. Your answers are kept as a draft, so fill in the fields and submit again.",
        ],
    ),
    (
        "How do I attach photos or documents?",
        [
            "Use the Supporting documents and pictures box on the form. PDF, images, Word, Excel, PowerPoint, text, zip and email files are accepted, up to 10 MB each.",
            "Files can be removed from a draft only. Save your changes first, because removing a file reloads the page.",
        ],
    ),
    (
        "Who can see my complaint?",
        [
            "A draft is visible only to you. After you submit, the Coordinator and top management can see it, and so can the staff of the factory it was lodged for.",
            "A HOD or FM who is away is replaced by their deputy, who can see and act on that factory's complaints until the HOD or FM is back.",
        ],
    ),
    (
        "Can I change a complaint after I submit it?",
        [
            "No. The complaint form is read-only once it is submitted. Tell the Coordinator if something needs to be corrected, or ask for the complaint to be deleted and lodged again.",
        ],
    ),
    (
        "How do I find a complaint?",
        [
            "Use the filters on My complaints: factory, status, the date it was lodged, or a search for the reference, buyer, product or contract number.",
            "Tick Waiting for my action to see only the complaints that need you. The Waiting for column shows who each complaint is with.",
        ],
    ),
    (
        "Who is told when I submit?",
        [
            "The Coordinator receives an email with a summary and a link to the complaint.",
        ],
    ),
    (
        "As Coordinator, how do I assign a complaint?",
        [
            "Open the complaint from My complaints and choose Assign PICs. Tick one or more PICs of that factory, add remarks and files, and choose Assign and send.",
            "Each PIC is emailed the reference number, a PDF summary and a link. The platform records you as the person who received it and the date and time. If a sample was sent, include the laboratory PIC.",
            "Use Change PICs to add or remove a PIC later: only newly added PICs are emailed. Delete marks a complaint Deleted with a reason; it stays in the log.",
        ],
    ),
    (
        "As a PIC, how do I record my investigation?",
        [
            "Open the complaint and choose Investigation. Enter the root cause analysis, the correction and the corrective and preventive action, say whether the cause is internal or external, and attach files.",
            "Save at any time. Choose Submit to HOD when the findings are complete: the platform emails the HOD. If the HOD or FM sends it back, their comment is shown at the top and you improve the response and submit again.",
        ],
    ),
    (
        "As laboratory staff, how do I fill in the laboratory section?",
        [
            "The section opens only when a sample was sent and you are one of the assigned PICs. Choose Laboratory section, enter the date the laboratory received the sample, the result of each test for the buyer's sample and the retained sample, remarks and the test report.",
            "The findings cannot go to the HOD until the acknowledgement date and at least one result are entered.",
        ],
    ),
    (
        "The cause is a supplier or transporter. What do I do?",
        [
            "Choose Issue SCAR on the investigation. Enter who it goes to and their email. The wording comes from a template that you can edit, and Generate again rebuilds it. The SCAR carries the complaint reference and is emailed with a PDF; replies come to you.",
            "The status becomes Pending SCAR from Supplier and Submit to HOD is locked. When the supplier replies, choose Upload returned SCAR, then complete the findings and submit them to the HOD. The reply to the customer is held until the SCAR is back.",
        ],
    ),
    (
        "As HOD, how do I review a complaint?",
        [
            "Choose HOD review. Read the findings, then choose Valid or Invalid, state the cause and give a reason if it is invalid. Submit to FM sends it straight to the FM, with the Coordinator and PIC copied.",
            "If you do not agree with the findings, choose Return to PIC and say what to improve: the PIC is emailed.",
        ],
    ),
    (
        "As Factory Manager, how do I approve or reject?",
        [
            "Choose FM approval and read the response. Approve, or Reject with a comment. The PIC, HOD and Coordinator are emailed your decision.",
            "After a rejection the PIC amends the response, or asks the supplier to improve the SCAR, and it goes through the HOD to you again until you approve.",
        ],
    ),
    (
        "As a PIC, how do I reply to the customer?",
        [
            "After the FM approves, choose Reply to customer. The email is built from the findings, the corrective action and the approval. Edit it, add files and send it to the email address on the complaint.",
            "The platform keeps a copy, sets the status to Customer Response Sent and tells the HOD, FM and Coordinator.",
        ],
    ),
    (
        "As Coordinator, how do I close a complaint?",
        [
            "After the reply is sent, choose Effectiveness check and closing. Record the first verified date and its result, and the second if there is one, then Close the case.",
            "If the corrective action did not work, choose Not effective: return to PIC and say why. The case goes back to the PIC.",
        ],
    ),
    (
        "What does the Dashboard show?",
        [
            "The Dashboard counts the complaints you are allowed to see: the total, how many are valid, invalid, under investigation and deleted, and how many valid complaints are in progress or closed. A valid complaint is In progress until the Coordinator closes the case.",
            "Below the figures: complaints by type, where the complaints are in the process (Pending SCAR from Supplier and Customer Response Sent are stages), valid complaints by type, and how long the open complaints have been open. A table lists where each open complaint is waiting and who it is waiting for, and another gives the reason for every invalid or deleted complaint.",
            "Choose factories and a lodged-from and lodged-to date above, then Apply. Everything on the page follows the same filter. Top management and the Coordinator can choose any factory; factory staff see their own.",
        ],
    ),
    (
        "How do I get the complaint log in Excel?",
        [
            "Open Complaint log in the menu. It lists every complaint in the columns of the current log, oldest first, with the summary under the table. Choose the factories and dates, then Export to Excel.",
            "The file has the same columns in the same order, and the summary under the table. The CAR number is not used: the complaint reference takes its place. Only complaints you are allowed to see are included.",
        ],
    ),
    (
        "What reminders will I get?",
        [
            "When a complaint has waited for you longer than 3 days, the platform emails you a reminder. The Coordinator can set a different number of days for each complaint type in Settings.",
            "You are reminded again every 3 days until you act. Acting, which moves the complaint to the next stage, starts the count again. The reminder goes to whoever the complaint is waiting for: the Coordinator, the PIC (also while a SCAR is pending), the HOD or the FM. If a HOD or FM is away, their deputy is reminded.",
            "On My complaints, an Overdue tag with the number of days shows next to a complaint that has waited longer than its reminder days. Tick Overdue to list them.",
        ],
    ),
    (
        "Where can I see which emails were sent?",
        [
            "The Coordinator and top management have Notifications in the menu. It lists every email the platform sent: who it went to, the subject and text, the attachments, and whether it was delivered.",
            "A failed email never stops a step, but it is marked Failed with the reason. Tell the people concerned that the complaint is waiting for them. Each complaint also shows its own emails at the bottom of its page.",
        ],
    ),
]
