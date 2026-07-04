# TICKET: 03-wire-cli-agent-runner
Title: Wire CLI agent runner for stage execution
Project: loregarden
Created By: loregarden-export
Created On: 2026-07-04T19:47:00.383846

---

## Description
Spawn local CLI agents via subprocess for each workflow stage. Runs emit artifacts and domain events.

---

## Acceptance Criteria
- Start run invokes agent registry + CLI executor
- Run completion updates ticket workflow stage status
- Failed runs set ticket to blocked with stderr evidence

---

## Dependencies
- None

---

# WORKFLOW STATE (DO NOT FREEFORM EDIT)

## Stage
DONE

## Revision
0

## Last Updated By
seed

## Validation Status
- Tests: N/A
- Static QA: N/A
- Integration: N/A

## Blocking Issues
None

## Escalation Notes
- None

---

# NEXT ACTION

## Next Responsible Agent
Human

## Required Input Schema
```json
{}
```

## Status
Proceed

## Reason
Exported from SQLite at 2026-07-04T19:47:00.427404+00:00
