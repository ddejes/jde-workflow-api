# JDE Approval Workflow API

## Overview
This project is a modern replacement for the existing Power Automate-based JDE approval workflow.

It provides a Python (FastAPI) backend service that:
- Accepts structured request inputs (replacing Jira JSON payloads)
- Dynamically builds approval routing based on JDE pathcodes
- Tracks approval progress and state transitions
- Maintains an audit trail of all actions
- Supports approval, rejection, and workflow progression

This is the foundation for a full workflow system including:
- React frontend UI
- Azure-hosted API
- Integration with Jira, Outlook, and Azure AD

---

## Features (Current)
- Create approval requests
- Dynamic approval routing (DV, PY, QA, PD)
- Submit requests for approval
- Step-by-step approval processing
- Basic workflow state tracking
- In-memory data storage (for development/testing)

---

## Tech Stack
- Python
- FastAPI
- Uvicorn (local runtime)
- Gunicorn (Azure deployment)
- Pydantic (data validation)

---

## Getting Started

### 1. Install dependencies
```bash
pip install -r requirements.txt
