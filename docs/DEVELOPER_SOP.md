# ConnectSphere Developer SOP (Standard Operating Procedures)

This document defines the team development workflows, Test-Driven Development (TDD) conventions, Trunk-Based Development rules, and testing/documentation standards for **ConnectSphere** (IS212 Software Project Management).

---

## 1. Trunk-Based Development & Git Workflow

We follow **Trunk-Based Development (TBD)** with short-lived feature branches to ensure continuous integration, fast feedback, and zero merge conflicts.

### Branching Rules
- **Trunk (`main`):** Always deployable and green. Direct pushes to `main` are reserved only for docs/fixes agreed upon by the team. All feature work goes through branches and Pull Requests.
- **Feature Branches:** Branch directly from the latest `main`. Use standard naming:
  - `feature/SCRUM-<id>-<short-description>` (e.g. `feature/SCRUM-33-search-venues`)
  - Keep branch lifetimes short (< 1–2 days).

### Step-by-Step Feature Workflow
1. **Sync `main`:**
   ```bash
   git checkout main
   git pull origin main
   ```
2. **Create Feature Branch:**
   ```bash
   git checkout -b feature/SCRUM-<id>-<name>
   ```
3. **Execute TDD Workflow (RED $\rightarrow$ GREEN $\rightarrow$ REFACTOR):** See Section 2 below.
4. **Push Branch & Open Pull Request:**
   ```bash
   git push -u origin feature/SCRUM-<id>-<name>
   ```
5. **Peer Review & Merge:**
   - A review by at least one teammate is required where possible (the team rule since 8 Oct 2026, applied to all sprints; see the Definition of Done).
   - All automated test suites (`pytest`) and CI checks must pass.
   - Merge into `main` using standard PR merge / squash merge.
   - Delete the remote feature branch after merging.

---

## 2. Test-Driven Development (TDD) Workflow

Every story assigned to a developer must follow strict **TDD commit traceability**:

```
[RED Phase Commit]                  [GREEN Phase Commit]
test(SCRUM-XX): write unit tests -> feat(SCRUM-XX): implement feature & pass tests
```

### 1. RED Phase (Write Tests First)
- Create the test file in `backend/tests/test_scrum<id>_<name>.py`.
- Write unit tests covering **all Acceptance Criteria (ACs)**, edge cases, and role guards.
- Run `pytest backend/tests/test_scrum<id>_<name>.py` and verify tests fail or error (**RED**).
- **Commit tests:**
  ```bash
  git add backend/tests/test_scrum<id>_<name>.py
  git commit -m "test(SCRUM-<id>): write unit tests for <feature description>"
  git push -u origin feature/SCRUM-<id>-<name>
  ```

### 2. GREEN Phase (Implement Minimum Code)
- Write the minimal code in `backend/` (e.g. models, schemas, endpoints) needed to satisfy the test cases.
- Run `pytest backend/tests/test_scrum<id>_<name>.py` and verify all tests pass (**GREEN**).
- Run the full suite `pytest backend/tests/` to guarantee **zero regressions**.
- **Commit implementation:**
  ```bash
  git add backend/<modified_files>.py
  git commit -m "feat(SCRUM-<id>): implement <feature description>"
  git push origin feature/SCRUM-<id>-<name>
  ```

### 3. REFACTOR Phase (Clean Code & Polish)
- Clean up variable names, optimize queries, remove duplication, and ensure adherence to coding standards without breaking any existing tests.

---

## 3. Test Case Design & 5-Path Classification

All test cases must be designed systematically and classified into one of the **5 Standard Test Paths** taught in IS212:

| Path Classification | Description | Example Scenarios |
| :--- | :--- | :--- |
| **1. Happy Path** | Standard, successful execution of the core workflow meeting all acceptance criteria with valid data. | Successful login, creating a valid draft, approving a submitted request. |
| **2. Negative Path** | Expected failures, invalid inputs, business logic rejections, or duplicate conflicts. | Wrong password (401), duplicate venue name (409), non-existent ID (404), amending an approved event (400). |
| **3. Boundary Path** | Edge-case boundary values, zero/negative quantities, empty payloads, or timing thresholds. | Capacity $\le 0$ (422), negative buffer times (422), empty title strings, submission with 0 active coordinators. |
| **4. Workflow Variation** | Alternative valid paths, resubmissions, multi-stage state transitions, or cyclical flows. | Resubmitting an event after return for clarification, reassigning an event multiple times, round-robin coordinator rotation. |
| **5. Cross-Cutting** | System-wide quality attributes: Authentication, RBAC (Role-Based Access Control), Data Isolation/Entitlement, and Audit Logging. | Non-venue staff blocked from POST /venues (403), organiser cannot see another organiser's notifications, unauthenticated requests (401). |

---

## 4. Documentation & Artifacts Maintenance SOP

Whenever changes or new features are introduced, developers must maintain project artifacts:

1. **`README.md`:**
   - Keep the Tech Stack table up to date with agreed choices.
   - Update getting started / run commands whenever new dependencies or configurations are added.
2. **C4 Architecture (`docs/connectsphere.dsl`):**
   - Update Structurizr C4 DSL when new components, database models, or external services are added.
3. **Sequence Diagrams (`docs/diagrams/*.mmd`):**
   - Update Mermaid sequence diagrams whenever core business flows (submission, review, reassignment, venue booking) are modified.
   - Re-export high-resolution PNGs to `docs/diagrams/`.
4. **Test Case Register (`docs/TEST_CASES.md` & Google Sheet):**
   - Log all automated test cases with their ID, Story, Path Classification, Acceptance Criterion, Test Steps, Test Data, and Results to match the team's testing documentation.
