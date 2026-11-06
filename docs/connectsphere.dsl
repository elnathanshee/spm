// connectsphere.dsl
// C4 model of ConnectSphere as released (main at 0a46c9b, deployed 4 to 5 Nov 2026).
// Updated 5 Nov 2026 from the code: backend/main.py and its routers, backend/auth.py (roles),
// backend/database.py, frontend/ (Vite, Alpine.js, Tailwind), vercel.json and .github/workflows/ci.yml.
// Levels: C1 System Context and C2 Containers, as agreed in the Team Working Agreements ("We stop at C2"),
// plus a deployment view of production. Render it at https://playground.structurizr.com or with the Structurizr CLI.

workspace "ConnectSphere" "Event planning and venue booking for ConnectSphere Event Services (IS212 project, Team Machu Picchu)." {

    model {

        // PEOPLE: the roles the release supports (SUPPORTED_APPLICATION_ROLES in backend/auth.py)
        organiser = person "Event Organiser" "Client representative. Captures an event request, submits it, amends it before approval, answers clarification questions and follows its status."
        coordinator = person "Event Coordinator" "Internal staff. Reviews the full request and decides on it, updates routine details, finds a suitable available venue and requests it, and records equipment needs."
        lead = person "Event Coordinator Lead" "Internal staff (Week 7 change 5). Assigns submitted requests to Coordinators and reassigns them."
        venueStaff = person "Venue Staff" "Internal staff. Maintain the venue record, review booking requests with their event context, manage holds, and record venue unavailability."
        techSupport = person "Technical Support Staff" "Internal staff. Maintain the equipment catalogue, reserve equipment for events and update equipment requests in the Technical Support workspace."
        safetyOfficer = person "Safety Officer" "Internal staff (Week 7 change 6). Check events in Safety Review before they are confirmed."
        attendee = person "Attendee" "Views open events, registers, sees their registration status and can withdraw. Gets a notice when the event changes or is cancelled."

        // OUR SYSTEM
        connectsphere = softwareSystem "ConnectSphere" "Takes an event from request through review and coordinator assignment to a booked venue, with in-app notifications at each step." {

            webApp = container "Web Application" "Single-page app with one workspace per role. Calls the API with relative paths on the same domain and polls notifications every 15 seconds." "JavaScript, Alpine.js, Tailwind CSS, built with Vite" "Web Browser"

            api = container "Backend API" "Business rules: sign-in and role checks, the event status model and reviews, the event lifecycle and safety checks, coordinator assignment, venue record, search and suitability, booking clash, setup and turnaround rules, holds and their expiry, venue unavailability, equipment requests, the equipment catalogue and reservations, registrations, in-app notifications. Hold expiry is checked at the start of each request; there is no background worker." "Python, FastAPI, SQLAlchemy"

            db = container "Database" "Users, login sessions, events and their status history, coordinator assignments, venues, bookings, holds, venue unavailability periods, conflict flags, equipment requests, equipment items, unavailable units, reservations, request status history, safety checks, arrangement review flags, registrations, registration settings and notifications." "PostgreSQL on Neon (SQLite for local development and tests)" "Database"
        }

        // RELATIONSHIPS: people to the system (C1) and to the web application (C2)
        organiser -> webApp "Captures, submits and amends event requests, and follows their status using"
        coordinator -> webApp "Reviews requests, updates routine details, searches venues, requests bookings and records equipment needs using"
        lead -> webApp "Assigns and reassigns requests using"
        venueStaff -> webApp "Manages venues, decides on bookings and manages holds using"
        techSupport -> webApp "Maintains the equipment catalogue, reserves equipment and updates equipment requests using"
        safetyOfficer -> webApp "Reviews events and approves, rejects or requests changes using"
        attendee -> webApp "Finds open events, registers and withdraws using"

        // RELATIONSHIPS: inside ConnectSphere
        webApp -> api "Calls /auth, /events, /venues, /coordinator, /lead, /notifications, /equipment-requests, /safety and /registrations, with an HttpOnly session cookie" "JSON over HTTPS"
        api -> db "Reads from and writes to" "SQLAlchemy over TLS"

        // DEPLOYMENT: production, as configured in vercel.json (one Vercel project, two services, one domain)
        production = deploymentEnvironment "Production" {

            deploymentNode "User's device" "Laptop or phone of a ConnectSphere user" "Any modern web browser" {
                deploymentNode "Web browser" "" "Chrome, Edge, Safari or Firefox" {
                    browserApp = containerInstance webApp
                }
            }

            deploymentNode "Vercel" "Hosting platform. Project spm, https://spm-dusky-six.vercel.app" "Vercel, Services mode" {
                frontendService = infrastructureNode "frontend service" "Serves the static Vite build at / and every path the backend does not own." "Vercel CDN"
                deploymentNode "backend service" "Receives only the API paths routed to it by vercel.json." "Vercel Python function (FastAPI entrypoint backend.main:app)" {
                    apiInstance = containerInstance api
                }
            }

            deploymentNode "Neon" "Managed serverless PostgreSQL" "Neon" {
                deploymentNode "neondb" "" "PostgreSQL" {
                    dbInstance = containerInstance db
                }
            }

            deploymentNode "GitHub" "Source control and CI for elnathanshee/spm" "GitHub" {
                repo = infrastructureNode "Repository" "main branch; feature branches merged by pull request." "Git"
                ci = infrastructureNode "GitHub Actions CI" "Backend job: pytest with branch coverage on in-memory SQLite. Frontend job: npm ci, npm test, vite build." "GitHub Actions"
            }

            frontendService -> browserApp "Delivers the built web application to" "HTTPS"
            repo -> ci "Runs CI on every push to main and every pull request into main"
            repo -> frontendService "A push to main deploys production; each pull request gets a preview deployment" "Vercel GitHub integration"
        }
    }

    views {

        // C1: System Context
        systemContext connectsphere "C1_SystemContext" "C1 System Context: who uses ConnectSphere. The release has no external system dependencies at run time; hosting is shown in the deployment view." {
            include *
            autoLayout tb
        }

        // C2: Containers
        container connectsphere "C2_Containers" "C2 Containers: the web application, the backend API and the database." {
            include *
            autoLayout tb
        }

        // Related: production deployment (C2 containers mapped onto Vercel, Neon and GitHub)
        deployment connectsphere production "Deployment_Production" "Production deployment: one Vercel project with two services on one domain, and the database on Neon." {
            include *
            autoLayout lr
        }

        styles {
            element "Person" {
                shape person
                background "#08427b"
                color "#ffffff"
            }
            element "Software System" {
                background "#1168bd"
                color "#ffffff"
            }
            element "Container" {
                background "#438dd5"
                color "#ffffff"
            }
            element "Web Browser" {
                shape WebBrowser
            }
            element "Database" {
                shape cylinder
            }
            element "Infrastructure Node" {
                background "#ffffff"
                color "#000000"
            }
        }
    }
}
