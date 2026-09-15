// connectsphere.dsl

workspace "ConnectSphere" "Event Planning & Venue Booking System" {

  model {

    // PEOPLE (roles from the briefing)
    organiser = person "Event Organiser" "External client representative who requests and manages events."
    coordinator = person "Event Coordinator" "Internal staff who review requests and coordinate events."
    venueStaff = person "Venue Staff" "Internal staff who manage venue information and bookings."
    techSupport = person "Technical Support Staff" "Internal staff who manage equipment and technical support."
    attendee = person "Attendee" "External participant who registers for and attends events."

    // OUR SYSTEM
    connectsphere = softwareSystem "ConnectSphere" "Lets staff and clients plan events, book venues/equipment, and manage attendee registration." {

      // CONTAINERS (high-level building blocks)
      webApp = container "Web Application" "User interface for all roles to manage events, venues, equipment and registrations." "Undecided web frontend"
      api = container "Backend API" "Implements business rules for events, venues, equipment, and registration." "Python / FastAPI"
      db = container "Database" "Stores events, venues, equipment, bookings, registrations and audit history." "SQLite (Dev) / Postgres (Prod)"
      notifier = container "Notification Worker" "Sends emails/notifications for status changes, bookings and registration updates." "Undecided (background worker)"

    }

    // EXTERNAL SYSTEMS (things you do NOT build)
    idp = softwareSystem "Identity Provider" "Authenticates internal staff and external users." {
      tags "External System"
    }

    emailSms = softwareSystem "Email / SMS Gateway" "Delivers notification emails and SMS messages to users." {
      tags "External System"
    }

    // RELATIONSHIPS – PEOPLE -> CONTAINERS
    organiser -> webApp "Creates and manages event requests; views status and confirmed arrangements using"
    coordinator -> webApp "Reviews requests, manages venues/equipment and confirms events using"
    venueStaff -> webApp "Reviews and manages venue availability and bookings using"
    techSupport -> webApp "Reviews and manages equipment availability and reservations using"
    attendee -> webApp "Views events and manages registrations using"

    // RELATIONSHIPS – WITHIN CONNECTSPHERE
    webApp -> api "Makes REST API calls to" "JSON / HTTPS"
    api -> db "Reads from and writes to" "SQL / TCP"
    notifier -> db "Reads upcoming events and pending notifications from" "SQL / TCP"
    notifier -> emailSms "Sends emails/SMS for status changes, bookings and registration events via" "SMTP / HTTPS"

    // RELATIONSHIPS – AUTH
    webApp -> idp "Redirects users for sign-in; validates identity with" "OIDC / HTTPS"
    api -> idp "Validates access tokens from" "OIDC / HTTPS"

  }

  views {

    // C1 – System Context
    systemContext connectsphere systemContextView "Who uses ConnectSphere and what it depends on." {
      include *
      autoLayout tb
    }

    // C2 – Containers
    container connectsphere containersView "The main containers that make up ConnectSphere." {
      include *
      autoLayout tb
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
      element "External System" {
        background "#999999"
        color "#ffffff"
      }
      element "Container" {
        background "#438dd5"
        color "#ffffff"
      }
      element "Database" {
        shape cylinder
      }
    }

  }

}
