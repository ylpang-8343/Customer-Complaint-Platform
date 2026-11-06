"""Text of the Help page. Update it as each step of the process is built."""

from accounts.models import Role

# (key, title, description, who). The guide that matches the signed-in role is highlighted.
GUIDES = [
    ("lodger", "Lodgers", "Record a complaint, save it as a draft, attach files and submit it to the Coordinator.", "Every employee"),
    ("coordinator", "Coordinator (MR)", "Receives each submitted complaint, assigns the PICs and closes the case.", "Management Representative"),
    ("pic", "PICs and laboratory", "Investigate a complaint, record the root cause and corrective action, and reply to the customer.", "PIC · Lab staff"),
    ("approver", "HOD and Factory Manager", "Decide whether a complaint is valid, then approve or reject the response.", "HOD · FM"),
    ("management", "Top management", "See the complaints of every factory.", "Group management"),
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
        ],
    ),
    (
        "Can I change a complaint after I submit it?",
        [
            "No. A submitted complaint is read-only. Tell the Coordinator if something needs to be corrected.",
        ],
    ),
    (
        "How do I find a complaint?",
        [
            "Use the filters on My complaints: factory, status, the date it was lodged, or a search for the reference, buyer, product or contract number.",
        ],
    ),
    (
        "Who is told when I submit?",
        [
            "The Coordinator receives an email with a summary and a link to the complaint.",
        ],
    ),
]
