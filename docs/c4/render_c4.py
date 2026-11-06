"""Draw the C1, C2 and deployment views of connectsphere.dsl as PNGs with Graphviz.

The text here is the same model as connectsphere.dsl (names, descriptions, technologies,
relationships). Run: python3 render_c4.py <output_dir>
"""
import html
import subprocess
import sys
import textwrap

OUT = sys.argv[1] if len(sys.argv) > 1 else "."

PEOPLE = [
    ("organiser", "Event Organiser", "Client representative. Captures an event request, submits it, amends it before approval, answers clarification questions and follows its status."),
    ("coordinator", "Event Coordinator", "Internal staff. Reviews the full request and decides on it, updates routine details, finds a suitable available venue and requests it, and records equipment needs."),
    ("lead", "Event Coordinator Lead", "Internal staff (Week 7 change 5). Assigns submitted requests to Coordinators and reassigns them."),
    ("venueStaff", "Venue Staff", "Internal staff. Maintain the venue record, review booking requests with their event context, manage holds, and record venue unavailability."),
    ("techSupport", "Technical Support Staff", "Internal staff. Work through the equipment requests in the Technical Support workspace."),
    ("safetyOfficer", "Safety Officer", "Internal staff (Week 7 change 6). Check events in Safety Review before they are confirmed."),
    ("attendee", "Attendee", "Can sign in. Registration is not in this release, so the Attendee workspace is a placeholder."),
]
USES = {
    "organiser": "Captures, submits and amends event requests, and follows their status",
    "coordinator": "Reviews requests, updates routine details, searches venues, requests bookings and records equipment needs",
    "lead": "Assigns and reassigns requests",
    "venueStaff": "Manages venues, decides on bookings and manages holds",
    "techSupport": "Works through equipment requests",
    "safetyOfficer": "Reviews events and approves, rejects or requests changes",
    "attendee": "Signs in to the placeholder Attendee workspace",
}
SYSTEM = ("ConnectSphere", "Takes an event from request through review and coordinator assignment to a booked venue, with in-app notifications at each step.")
CONTAINERS = [
    ("webApp", "Web Application", "JavaScript, Alpine.js, Tailwind CSS, built with Vite", "Single-page app with one workspace per role. Calls the API with relative paths on the same domain and polls notifications every 15 seconds.", "box"),
    ("api", "Backend API", "Python, FastAPI, SQLAlchemy", "Business rules: sign-in and role checks, the event status model and reviews, the event lifecycle and safety checks, coordinator assignment, venue record, search and suitability, booking clash, setup and turnaround rules, holds and their expiry, venue unavailability, equipment requests, in-app notifications. Hold expiry is checked at the start of each request; there is no background worker.", "box"),
    ("db", "Database", "PostgreSQL on Neon (SQLite for local development and tests)", "Users, login sessions, events and their status history, coordinator assignments, venues, bookings, holds, venue unavailability periods, conflict flags, equipment requests, safety checks, arrangement review flags and notifications.", "cylinder"),
]
INTERNAL = [
    ("webApp", "api", "Calls /auth, /events, /venues, /coordinator, /lead, /notifications, /equipment-requests and /safety, with an HttpOnly session cookie", "JSON over HTTPS"),
    ("api", "db", "Reads from and writes to", "SQLAlchemy over TLS"),
]


def wrap(text, width=38):
    return "<br/>".join(html.escape(line) for line in textwrap.wrap(text, width))


def label(name, kind, desc, width=38):
    return (f'<<b>{html.escape(name)}</b><br/><font point-size="10">[{html.escape(kind)}]</font>'
            f'<br/><br/><font point-size="10">{wrap(desc, width)}</font>>')


def edge_label(text, tech=None, width=30):
    body = wrap(text, width)
    if tech:
        body += f'<br/><font point-size="9">[{html.escape(tech)}]</font>'
    return f"<{body}>"


HEAD = """digraph C4 {
  graph [fontname="Helvetica" fontsize=12 rankdir=TB nodesep=0.5 ranksep=0.9 pad=0.4 dpi=150 labelloc=t];
  node [fontname="Helvetica" fontsize=12 shape=box style="rounded,filled" color="#2e6295" fontcolor="#ffffff" margin="0.2,0.12"];
  edge [fontname="Helvetica" fontsize=10 color="#707070" fontcolor="#404040" arrowsize=0.8];
"""

KEY = ('  legend [shape=plaintext style="" fontcolor="#404040" fontsize=10 label=<{}>];\n')


def person_nodes():
    lines = []
    for pid, name, desc in PEOPLE:
        lines.append(f'  {pid} [label={label(name, "Person", desc, 34)} fillcolor="#08427b" color="#073b6f"];')
    lines.append("  {rank=same; " + " ".join(p[0] for p in PEOPLE) + "}")
    return "\n".join(lines)


def render(name, dot):
    path = f"{OUT}/{name}"
    with open(path + ".dot", "w", encoding="utf-8") as f:
        f.write(dot)
    subprocess.run(["dot", "-Tpng", path + ".dot", "-o", path + ".png"], check=True)
    print("wrote", path + ".png")


# C1 System Context
c1 = [HEAD, '  label=<<b>C1 System Context: ConnectSphere</b><br/><font point-size="10">Who uses ConnectSphere. The release has no external system dependencies at run time; hosting is in the deployment view.</font>>;']
c1.append(person_nodes())
c1.append(f'  system [label={label(SYSTEM[0], "Software System", SYSTEM[1], 44)} fillcolor="#1168bd" color="#0b4884"];')
for pid, _, _ in PEOPLE:
    c1.append(f"  {pid} -> system [label={edge_label('Uses: ' + USES[pid], width=26)}];")
c1.append("}")
render("C1_SystemContext", "\n".join(c1))

# C2 Containers
c2 = [HEAD, '  label=<<b>C2 Containers: ConnectSphere</b><br/><font point-size="10">The web application, the backend API and the database.</font>>;']
c2.append(person_nodes())
c2.append('  subgraph cluster_cs {\n    label=<<b>ConnectSphere</b><br/><font point-size="10">[Software System]</font>>; style="dashed,rounded"; color="#1168bd"; fontcolor="#1168bd"; margin=24;')
for cid, name, tech, desc, shape in CONTAINERS:
    c2.append(f'    {cid} [label={label(name, "Container: " + tech, desc, 44)} shape={shape} fillcolor="#438dd5" color="#3c7fc0"{" style=filled" if shape == "cylinder" else ""}];')
c2.append("  }")
for pid, _, _ in PEOPLE:
    c2.append(f"  {pid} -> webApp [label={edge_label(USES[pid] + ' using', width=26)}];")
for a, b, text, tech in INTERNAL:
    c2.append(f"  {a} -> {b} [label={edge_label(text, tech, 34)}];")
c2.append("}")
render("C2_Containers", "\n".join(c2))

# Deployment: Production
dep = [HEAD.replace("rankdir=TB", "rankdir=LR"),
       '  label=<<b>Deployment: Production</b><br/><font point-size="10">One Vercel project with two services on one domain (vercel.json), and the database on Neon.</font>>;']


def cluster(cid, name, tech, desc=""):
    d = f'<br/><font point-size="9">{wrap(desc, 46)}</font>' if desc else ""
    return (f'  subgraph cluster_{cid} {{\n    label=<<b>{html.escape(name)}</b><br/><font point-size="9">[{html.escape(tech)}]</font>{d}>;'
            ' style="rounded"; color="#888888"; fontcolor="#333333"; margin=18;')


def infra(nid, name, tech, desc):
    return f'    {nid} [label={label(name, "Infrastructure Node: " + tech, desc, 34)} fillcolor="#ffffff" fontcolor="#000000" color="#888888"];'


def instance(nid, cid):
    for c in CONTAINERS:
        if c[0] == cid:
            return f'    {nid} [label={label(c[1], "Container: " + c[2], c[3], 40)} shape={c[4]} fillcolor="#438dd5" color="#3c7fc0"{" style=filled" if c[4] == "cylinder" else ""}];'
    raise KeyError(cid)


dep.append(cluster("device", "User's device", "Any modern web browser", "Laptop or phone of a ConnectSphere user"))
dep.append(cluster("browser", "Web browser", "Chrome, Edge, Safari or Firefox"))
dep.append(instance("browserApp", "webApp"))
dep.append("  }\n  }")
dep.append(cluster("vercel", "Vercel", "Vercel, Services mode", "Hosting platform. Project spm, https://spm-dusky-six.vercel.app"))
dep.append(infra("frontendService", "frontend service", "Vercel CDN", "Serves the static Vite build at / and every path the backend does not own."))
dep.append(cluster("backend", "backend service", "Vercel Python function (FastAPI entrypoint backend.main:app)", "Receives only the API paths routed to it by vercel.json."))
dep.append(instance("apiInstance", "api"))
dep.append("  }\n  }")
dep.append(cluster("neon", "Neon", "Neon", "Managed serverless PostgreSQL"))
dep.append(cluster("neondb", "neondb", "PostgreSQL"))
dep.append(instance("dbInstance", "db"))
dep.append("  }\n  }")
dep.append(cluster("github", "GitHub", "GitHub", "Source control and CI for elnathanshee/spm"))
dep.append(infra("repo", "Repository", "Git", "main branch; feature branches merged by pull request."))
dep.append(infra("ci", "GitHub Actions CI", "GitHub Actions", "Backend job: pytest with branch coverage on in-memory SQLite. Frontend job: npm ci, npm test, vite build."))
dep.append("  }")
dep.append(f"  frontendService -> browserApp [label={edge_label('Delivers the built web application to', 'HTTPS')}];")
dep.append(f"  browserApp -> apiInstance [label={edge_label(INTERNAL[0][2], INTERNAL[0][3], 30)}];")
dep.append(f"  apiInstance -> dbInstance [label={edge_label(INTERNAL[1][2], INTERNAL[1][3])}];")
dep.append(f"  repo -> ci [label={edge_label('Runs CI on every push to main and every pull request into main')}];")
dep.append(f"  repo -> frontendService [label={edge_label('A push to main deploys production; each pull request gets a preview deployment', 'Vercel GitHub integration')}];")
dep.append("}")
render("Deployment_Production", "\n".join(dep))
