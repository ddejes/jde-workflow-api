import csv
import re
from datetime import datetime
from io import StringIO

import pandas as pd
import streamlit as st
from snowflake.snowpark.context import get_active_session

DB = "TEST_DB"
SCHEMA = "JDE_WORKFLOW_APP"
REQUESTS = f"{DB}.{SCHEMA}.REQUESTS"
WORKFLOW_STEPS = f"{DB}.{SCHEMA}.WORKFLOW_STEPS"
AUDIT_EVENTS = f"{DB}.{SCHEMA}.AUDIT_EVENTS"
VALID_PATHCODES = ["DV", "PY", "QA", "PD"]
TERMINAL_STATUSES = {"COMPLETED", "REJECTED", "TERMINATED", "CANCELLED", "EXPIRED"}

st.set_page_config(page_title="JDE Workflow", page_icon="✅", layout="wide")
session = get_active_session()

st.markdown(
    """
    <style>
      .block-container {padding-top: 1.4rem; padding-bottom: 2rem;}
      [data-testid="stSidebar"] {background: #ffffff; border-right: 1px solid #eef2f7;}
      div[data-testid="stMetric"] {background: white; border: 1px solid #eef2f7; padding: 18px; border-radius: 20px; box-shadow: 0 18px 60px -45px rgba(15,23,42,.5);}
      .soft-card {background:white;border:1px solid #eef2f7;border-radius:22px;padding:20px;box-shadow:0 18px 60px -45px rgba(15,23,42,.5);}
      .brand {font-size:26px;font-weight:900;letter-spacing:.28em;color:#e81939;margin-bottom:2px;}
      .subbrand {font-size:11px;font-weight:700;letter-spacing:.12em;color:#94a3b8;text-transform:uppercase;margin-bottom:24px;}
      .pill {display:inline-block;padding:4px 10px;border-radius:999px;font-size:12px;font-weight:800;background:#eee9ff;color:#5b35f5;}
    </style>
    """,
    unsafe_allow_html=True,
)


def sql_quote(value):
    if value is None:
        return "NULL"
    text = str(value).replace("'", "''")
    return f"'{text}'"


def sql_now():
    return "CURRENT_TIMESTAMP()"


def run(sql):
    return session.sql(sql).collect()


def df(sql):
    return session.sql(sql).to_pandas()


def is_valid_email(value):
    return bool(re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", str(value or "").strip().lower()))


def is_valid_url(value):
    text = str(value or "").strip().lower()
    return text.startswith("http://") or text.startswith("https://")


def next_request_number():
    result = df(f"SELECT COALESCE(MAX(REQUEST_NUMBER), 1000) + 1 AS NEXT_NUM FROM {REQUESTS}")
    return int(result.iloc[0]["NEXT_NUM"])


def next_id(table_name):
    result = df(f"SELECT COALESCE(MAX(ID), 0) + 1 AS NEXT_ID FROM {table_name}")
    return int(result.iloc[0]["NEXT_ID"])


def add_audit(request_id, event_type, actor_email, message):
    audit_id = next_id(AUDIT_EVENTS)
    run(
        f"""
        INSERT INTO {AUDIT_EVENTS}
        (ID, REQUEST_ID, EVENT_TYPE, ACTOR_EMAIL, MESSAGE, CREATED_AT)
        VALUES
        ({audit_id}, {request_id}, {sql_quote(event_type)}, {sql_quote(actor_email)}, {sql_quote(message)}, {sql_now()})
        """
    )


def validate_request(payload):
    errors = []
    required_text = [
        ("Jira Ticket", payload["jira_project"]),
        ("Jira URL", payload["jira_ticket_url"]),
        ("OMW Project", payload["omw_project"]),
        ("OMW Project Description", payload["omw_project_desc"]),
    ]
    for label, value in required_text:
        if not str(value or "").strip():
            errors.append(f"{label} is required.")

    if payload["jira_ticket_url"] and not is_valid_url(payload["jira_ticket_url"]):
        errors.append("Jira URL must start with http:// or https://.")

    if not payload["pathcodes"]:
        errors.append("Select at least one JDE Pathcode.")

    people = [
        ("Developer", payload["developer_name"], payload["developer_email"], True),
        ("Requestor", payload["requestor_name"], payload["requestor_email"], True),
        ("Business Manager", payload["business_manager_name"], payload["business_manager_email"], False),
        ("Technical Manager", payload["technical_manager_name"], payload["technical_manager_email"], True),
        ("Project Manager", payload["project_manager_name"], payload["project_manager_email"], False),
        ("Peer Reviewer", payload["peer_review_name"], payload["peer_review_email"], False),
        ("CNC", payload["cnc_name"], payload["cnc_email"], True),
        ("CNC2", payload["cnc2_name"], payload["cnc2_email"], True),
    ]

    seen = {}
    for label, name, email, required in people:
        email = str(email or "").strip().lower()
        name = str(name or "").strip()
        if required and not name:
            errors.append(f"{label} name is required.")
        if required and not email:
            errors.append(f"{label} email is required.")
        elif email and not is_valid_email(email):
            errors.append(f"{label} email is invalid.")
        elif email:
            if email in seen:
                errors.append(f"{label} email duplicates {seen[email]}.")
            else:
                seen[email] = label
    return errors


def create_steps(request_id, pathcodes, people):
    seq = 10

    def add_step(role, step_type, email, name, env=None, status="PENDING"):
        nonlocal seq
        step_id = next_id(WORKFLOW_STEPS)
        run(
            f"""
            INSERT INTO {WORKFLOW_STEPS}
            (ID, REQUEST_ID, SEQUENCE_NO, ROLE_NAME, STEP_TYPE, ENVIRONMENT_CODE, ASSIGNED_EMAIL, ASSIGNED_NAME, STATUS, RESPONSE, COMMENTS, STARTED_AT, RESPONDED_AT, CREATED_AT)
            VALUES
            ({step_id}, {request_id}, {seq}, {sql_quote(role)}, {sql_quote(step_type)}, {sql_quote(env)}, {sql_quote(email)}, {sql_quote(name)}, {sql_quote(status)}, NULL, NULL, NULL, NULL, {sql_now()})
            """
        )
        seq += 10

    add_step("Developer", "APPROVAL", people["developer_email"], people["developer_name"])
    if people["peer_review_email"]:
        add_step("Peer Reviewer", "APPROVAL", people["peer_review_email"], people["peer_review_name"])
    add_step("Requestor", "APPROVAL", people["requestor_email"], people["requestor_name"])
    if people["business_manager_email"]:
        add_step("Business Manager", "APPROVAL", people["business_manager_email"], people["business_manager_name"])
    add_step("Technical Manager", "APPROVAL", people["technical_manager_email"], people["technical_manager_name"])
    if people["project_manager_email"]:
        add_step("Project Manager", "APPROVAL", people["project_manager_email"], people["project_manager_name"])

    for code in pathcodes:
        add_step(f"CNC-{code}", "CNC_COMPLETION", people["cnc_email"], people["cnc_name"], code, "PENDING_CNC")
        add_step(f"CNC2-{code}", "CNC_COMPLETION", people["cnc2_email"], people["cnc2_name"], code, "PENDING_CNC")


def create_request(payload):
    request_id = next_id(REQUESTS)
    request_number = next_request_number()
    env_string = ",".join(payload["pathcodes"])

    run(
        f"""
        INSERT INTO {REQUESTS}
        (ID, REQUEST_NUMBER, JIRA_PROJECT, JIRA_TICKET_URL, OMW_PROJECT, OMW_PROJECT_DESC, ENVIRONMENT_STRING,
         EMERGENCY, SOX_SOD, SPECIAL_INSTRUCTIONS, STATUS, CURRENT_STAGE, CREATED_AT, SUBMITTED_AT, COMPLETED_AT)
        VALUES
        ({request_id}, {request_number}, {sql_quote(payload['jira_project'])}, {sql_quote(payload['jira_ticket_url'])},
         {sql_quote(payload['omw_project'])}, {sql_quote(payload['omw_project_desc'])}, {sql_quote(env_string)},
         {sql_quote(payload['emergency'])}, {sql_quote(payload['sox_sod'])}, {sql_quote(payload['special_instructions'])},
         'DRAFT', 'Draft', {sql_now()}, NULL, NULL)
        """
    )
    create_steps(request_id, payload["pathcodes"], payload)
    add_audit(request_id, "REQUEST_CREATED", payload["developer_email"], f"REQ-{request_number} created.")
    return request_id, request_number


def get_requests():
    return df(f"SELECT * FROM {REQUESTS} ORDER BY CREATED_AT DESC")


def get_steps(request_id):
    return df(f"SELECT * FROM {WORKFLOW_STEPS} WHERE REQUEST_ID = {request_id} ORDER BY SEQUENCE_NO")


def get_audit(request_id):
    return df(f"SELECT * FROM {AUDIT_EVENTS} WHERE REQUEST_ID = {request_id} ORDER BY CREATED_AT")


def update_request_status(request_id, status, stage, completed=False):
    completed_sql = f", COMPLETED_AT = {sql_now()}" if completed else ""
    run(f"UPDATE {REQUESTS} SET STATUS={sql_quote(status)}, CURRENT_STAGE={sql_quote(stage)}{completed_sql} WHERE ID={request_id}")


def submit_request(request_id):
    pending = df(f"SELECT * FROM {WORKFLOW_STEPS} WHERE REQUEST_ID={request_id} AND STEP_TYPE='APPROVAL' AND STATUS='PENDING' ORDER BY SEQUENCE_NO LIMIT 1")
    if pending.empty:
        st.error("No pending approval steps found.")
        return
    step = pending.iloc[0]
    run(f"UPDATE {WORKFLOW_STEPS} SET STATUS='ACTIVE', STARTED_AT={sql_now()} WHERE ID={int(step['ID'])}")
    run(f"UPDATE {REQUESTS} SET STATUS='IN_APPROVAL', CURRENT_STAGE={sql_quote(step['ROLE_NAME'])}, SUBMITTED_AT={sql_now()} WHERE ID={request_id}")
    add_audit(request_id, "REQUEST_SUBMITTED", step["ASSIGNED_EMAIL"], f"Request routed to {step['ROLE_NAME']}.")


def advance_after_approval(request_id):
    pending = df(f"SELECT * FROM {WORKFLOW_STEPS} WHERE REQUEST_ID={request_id} AND STEP_TYPE='APPROVAL' AND STATUS='PENDING' ORDER BY SEQUENCE_NO LIMIT 1")
    if not pending.empty:
        step = pending.iloc[0]
        run(f"UPDATE {WORKFLOW_STEPS} SET STATUS='ACTIVE', STARTED_AT={sql_now()} WHERE ID={int(step['ID'])}")
        update_request_status(request_id, "IN_APPROVAL", step["ROLE_NAME"])
    else:
        update_request_status(request_id, "APPROVED_PENDING_CNC", "Approved - Pending CNC")


def step_action(request_id, step_id, action, actor_email, comments=""):
    step = df(f"SELECT * FROM {WORKFLOW_STEPS} WHERE ID={step_id}").iloc[0]
    role = step["ROLE_NAME"]
    action = action.upper()

    if action == "APPROVE":
        run(f"UPDATE {WORKFLOW_STEPS} SET STATUS='APPROVED', RESPONSE='APPROVED', COMMENTS={sql_quote(comments)}, RESPONDED_AT={sql_now()} WHERE ID={step_id}")
        add_audit(request_id, "STEP_APPROVED", actor_email, f"{role} approved the request.")
        advance_after_approval(request_id)
    elif action == "REJECT":
        run(f"UPDATE {WORKFLOW_STEPS} SET STATUS='REJECTED', RESPONSE='REJECTED', COMMENTS={sql_quote(comments)}, RESPONDED_AT={sql_now()} WHERE ID={step_id}")
        update_request_status(request_id, "REJECTED", "Rejected", completed=True)
        add_audit(request_id, "REQUEST_REJECTED", actor_email, f"{role} rejected the request.")
    elif action == "HOLD":
        run(f"UPDATE {WORKFLOW_STEPS} SET STATUS='HOLD', RESPONSE='HOLD', COMMENTS={sql_quote(comments)}, RESPONDED_AT={sql_now()} WHERE ID={step_id}")
        update_request_status(request_id, "ON_HOLD", "Developer Hold Decision")
        add_audit(request_id, "STEP_HELD", actor_email, f"{role} placed the request on hold.")


def start_cnc(request_id):
    pending = df(f"SELECT * FROM {WORKFLOW_STEPS} WHERE REQUEST_ID={request_id} AND STEP_TYPE='CNC_COMPLETION' AND STATUS='PENDING_CNC' ORDER BY SEQUENCE_NO LIMIT 1")
    if pending.empty:
        update_request_status(request_id, "COMPLETED", "Completed", completed=True)
        return
    step = pending.iloc[0]
    run(f"UPDATE {WORKFLOW_STEPS} SET STATUS='ACTIVE', STARTED_AT={sql_now()} WHERE ID={int(step['ID'])}")
    update_request_status(request_id, "CNC_IN_PROGRESS", step["ROLE_NAME"])
    add_audit(request_id, "CNC_STARTED", step["ASSIGNED_EMAIL"], f"CNC started at {step['ENVIRONMENT_CODE']}.")


def complete_cnc(request_id, step_id, actor_email, comments=""):
    run(f"UPDATE {WORKFLOW_STEPS} SET STATUS='COMPLETED', RESPONSE='COMPLETED', COMMENTS={sql_quote(comments)}, RESPONDED_AT={sql_now()} WHERE ID={step_id}")
    add_audit(request_id, "CNC_COMPLETED", actor_email, "CNC step completed.")
    pending = df(f"SELECT * FROM {WORKFLOW_STEPS} WHERE REQUEST_ID={request_id} AND STEP_TYPE='CNC_COMPLETION' AND STATUS='PENDING_CNC' ORDER BY SEQUENCE_NO LIMIT 1")
    if pending.empty:
        update_request_status(request_id, "COMPLETED", "Completed", completed=True)
    else:
        start_cnc(request_id)


def export_audit_csv():
    audit_df = df(
        f"""
        SELECT
          r.REQUEST_NUMBER,
          r.JIRA_PROJECT AS JIRA_TICKET,
          r.OMW_PROJECT AS JDE_PROJECT,
          r.OMW_PROJECT_DESC AS PROJECT_NAME,
          COALESCE(s.ENVIRONMENT_CODE, r.ENVIRONMENT_STRING) AS PATHCODES,
          s.ASSIGNED_NAME AS APPROVER_NAME,
          s.ASSIGNED_EMAIL AS APPROVER_EMAIL,
          s.ROLE_NAME AS ROLE,
          COALESCE(s.RESPONSE, s.STATUS) AS RESPONSE,
          s.RESPONDED_AT AS COMPLETED,
          r.EMERGENCY AS EMERGENCY_BUILD,
          r.SOX_SOD AS SEGREGATION_OF_DUTY,
          r.STATUS AS REQUEST_STATUS,
          r.CREATED_AT,
          r.COMPLETED_AT
        FROM {REQUESTS} r
        LEFT JOIN {WORKFLOW_STEPS} s ON r.ID = s.REQUEST_ID
        ORDER BY r.CREATED_AT DESC, s.SEQUENCE_NO
        """
    )
    return audit_df.to_csv(index=False).encode("utf-8")


st.sidebar.markdown('<div class="brand">DOREL</div><div class="subbrand">JDE Workflow</div>', unsafe_allow_html=True)
page = st.sidebar.radio(
    "Navigation",
    ["Dashboard", "New Request", "Requests", "Workflow", "Audit", "Analytics"],
    label_visibility="collapsed",
)

requests_df = get_requests()

if page == "Dashboard":
    st.title("Hello, David! 👋")
    st.caption("Welcome back to JDE Workflow System")
    c1, c2, c3, c4 = st.columns(4)
    total = len(requests_df)
    completed = int((requests_df["STATUS"] == "COMPLETED").sum()) if not requests_df.empty else 0
    open_count = int((~requests_df["STATUS"].isin(list(TERMINAL_STATUSES))).sum()) if not requests_df.empty else 0
    attention = int((requests_df["STATUS"].isin(["ON_HOLD", "APPROVED_PENDING_CNC", "CNC_IN_PROGRESS"])).sum()) if not requests_df.empty else 0
    c1.metric("Total Requests", total)
    c2.metric("In Progress", open_count)
    c3.metric("Completed", completed)
    c4.metric("Attention", attention)

    st.subheader("Recent Requests")
    if requests_df.empty:
        st.info("No requests yet. Create one from New Request.")
    else:
        st.dataframe(requests_df[["REQUEST_NUMBER", "JIRA_PROJECT", "OMW_PROJECT", "ENVIRONMENT_STRING", "STATUS", "CURRENT_STAGE", "CREATED_AT"]], use_container_width=True)

elif page == "New Request":
    st.title("Build Request Form")
    st.caption("A unique request number is assigned automatically.")

    with st.form("create_request_form"):
        col1, col2 = st.columns(2)
        jira_project = col1.text_input("Jira Ticket *", "JDE-1001")
        jira_ticket_url = col2.text_input("Jira URL *", "https://djgusa.atlassian.net/browse/JDE-1001")
        omw_project = col1.text_input("OMW Project *", "OMW001")
        pathcodes = col2.multiselect("JDE Pathcodes *", VALID_PATHCODES, default=[])
        emergency = col1.selectbox("Emergency Build", ["No", "Yes"])
        sox_sod = col2.selectbox("SOX SOD", ["No", "Yes"], index=0)
        omw_project_desc = st.text_area("OMW Project Description *", "Test JDE Build Request")
        special_instructions = st.text_area("Special Instructions", "Deploy after business hours.")

        st.markdown("### Routing")
        p1, p2, p3 = st.columns(3)
        developer_name = p1.text_input("Developer Name *", "Developer")
        developer_email = p1.text_input("Developer Email *", "developer@djgusa.com")
        requestor_name = p2.text_input("Requestor Name *", "Requestor")
        requestor_email = p2.text_input("Requestor Email *", "requestor@djgusa.com")
        business_manager_name = p3.text_input("Business Manager Name", "Business Manager")
        business_manager_email = p3.text_input("Business Manager Email", "business.manager@djgusa.com")

        p4, p5, p6 = st.columns(3)
        technical_manager_name = p4.text_input("Technical Manager Name *", "Technical Manager")
        technical_manager_email = p4.text_input("Technical Manager Email *", "technical.manager@djgusa.com")
        peer_review_name = p5.text_input("Peer Reviewer Name", "Peer Reviewer")
        peer_review_email = p5.text_input("Peer Reviewer Email", "peer.review@djgusa.com")
        project_manager_name = p6.text_input("Project Manager Name", "Project Manager")
        project_manager_email = p6.text_input("Project Manager Email", "project.manager@djgusa.com")

        p7, p8 = st.columns(2)
        cnc_name = p7.text_input("CNC Name *", "CNC")
        cnc_email = p7.text_input("CNC Email *", "cnc@djgusa.com")
        cnc2_name = p8.text_input("CNC2 Name *", "CNC2")
        cnc2_email = p8.text_input("CNC2 Email *", "cnc2@djgusa.com")

        submitted = st.form_submit_button("Create Request", type="primary")

    if submitted:
        payload = locals().copy()
        errors = validate_request(payload)
        if errors:
            for error in errors:
                st.error(error)
        else:
            request_id, request_number = create_request(payload)
            st.success(f"Created REQ-{request_number}")
            st.session_state["selected_request_id"] = request_id

elif page == "Requests":
    st.title("Requests")
    query = st.text_input("Search requests")
    view = requests_df.copy()
    if query and not view.empty:
        q = query.lower()
        view = view[view.astype(str).apply(lambda row: q in " ".join(row).lower(), axis=1)]
    st.dataframe(view, use_container_width=True)

elif page == "Workflow":
    st.title("Workflow")
    if requests_df.empty:
        st.info("No requests available.")
    else:
        options = {f"REQ-{int(row.REQUEST_NUMBER)} | {row.JIRA_PROJECT} | {row.STATUS}": int(row.ID) for row in requests_df.itertuples()}
        default_id = st.session_state.get("selected_request_id")
        selected_label = st.selectbox("Select Request", list(options.keys()))
        request_id = options[selected_label]
        req = requests_df[requests_df["ID"] == request_id].iloc[0]
        steps_df = get_steps(request_id)
        audit_df = get_audit(request_id)

        c1, c2, c3 = st.columns(3)
        c1.metric("Request", f"REQ-{int(req['REQUEST_NUMBER'])}")
        c2.metric("Status", req["STATUS"])
        c3.metric("Current Stage", req["CURRENT_STAGE"])

        actions = st.columns(4)
        if req["STATUS"] == "DRAFT":
            if actions[0].button("Start Routing", type="primary"):
                submit_request(request_id)
                st.rerun()
        if req["STATUS"] == "APPROVED_PENDING_CNC":
            if actions[1].button("Start CNC", type="primary"):
                start_cnc(request_id)
                st.rerun()

        st.subheader("Approval Route")
        for step in steps_df.itertuples():
            with st.expander(f"{int(step.SEQUENCE_NO)} · {step.ROLE_NAME} · {step.STATUS}", expanded=step.STATUS == "ACTIVE"):
                st.write(f"Assigned: {step.ASSIGNED_EMAIL}")
                if step.ENVIRONMENT_CODE:
                    st.write(f"Pathcode: {step.ENVIRONMENT_CODE}")
                comments = st.text_input("Comments", key=f"comments_{step.ID}")
                if step.STATUS == "ACTIVE" and step.STEP_TYPE == "APPROVAL":
                    a1, a2, a3 = st.columns(3)
                    if a1.button("Approve", key=f"approve_{step.ID}"):
                        step_action(request_id, int(step.ID), "APPROVE", step.ASSIGNED_EMAIL, comments)
                        st.rerun()
                    if a2.button("Hold", key=f"hold_{step.ID}"):
                        step_action(request_id, int(step.ID), "HOLD", step.ASSIGNED_EMAIL, comments)
                        st.rerun()
                    if a3.button("Reject", key=f"reject_{step.ID}"):
                        step_action(request_id, int(step.ID), "REJECT", step.ASSIGNED_EMAIL, comments)
                        st.rerun()
                if step.STATUS == "ACTIVE" and step.STEP_TYPE == "CNC_COMPLETION":
                    if st.button("Complete CNC", key=f"complete_cnc_{step.ID}"):
                        complete_cnc(request_id, int(step.ID), step.ASSIGNED_EMAIL, comments)
                        st.rerun()

        st.subheader("Audit Timeline")
        st.dataframe(audit_df, use_container_width=True)

elif page == "Audit":
    st.title("Audit Dashboard")
    csv_bytes = export_audit_csv()
    st.download_button("Export CSV", csv_bytes, file_name="jde_promotion_approval_history.csv", mime="text/csv")

    audit_export = df(
        f"""
        SELECT
          r.REQUEST_NUMBER,
          r.JIRA_PROJECT,
          r.OMW_PROJECT,
          r.OMW_PROJECT_DESC,
          COALESCE(s.ENVIRONMENT_CODE, r.ENVIRONMENT_STRING) AS PATHCODES,
          s.ASSIGNED_EMAIL,
          s.ROLE_NAME,
          COALESCE(s.RESPONSE, s.STATUS) AS RESPONSE,
          s.RESPONDED_AT,
          r.EMERGENCY,
          r.SOX_SOD,
          r.STATUS AS REQUEST_STATUS,
          r.CREATED_AT,
          r.COMPLETED_AT
        FROM {REQUESTS} r
        LEFT JOIN {WORKFLOW_STEPS} s ON r.ID = s.REQUEST_ID
        ORDER BY r.CREATED_AT DESC, s.SEQUENCE_NO
        """
    )

    f1, f2, f3, f4 = st.columns(4)
    request_filter = f1.text_input("Request #")
    project_filter = f2.text_input("JDE Project")
    status_filter = f3.selectbox("Status", ["ALL"] + sorted(audit_export["REQUEST_STATUS"].dropna().unique().tolist()) if not audit_export.empty else ["ALL"])
    path_filter = f4.selectbox("Pathcode", ["ALL", "DV", "PY", "QA", "PD"])

    view = audit_export.copy()
    if request_filter:
        view = view[view["REQUEST_NUMBER"].astype(str).str.contains(request_filter, case=False, na=False)]
    if project_filter:
        view = view[view["OMW_PROJECT"].astype(str).str.contains(project_filter, case=False, na=False)]
    if status_filter != "ALL":
        view = view[view["REQUEST_STATUS"] == status_filter]
    if path_filter != "ALL":
        view = view[view["PATHCODES"].astype(str).str.contains(path_filter, case=False, na=False)]

    st.dataframe(view, use_container_width=True)

elif page == "Analytics":
    st.title("Analytics")
    total = len(requests_df)
    completed = int((requests_df["STATUS"] == "COMPLETED").sum()) if not requests_df.empty else 0
    open_count = int((~requests_df["STATUS"].isin(list(TERMINAL_STATUSES))).sum()) if not requests_df.empty else 0
    emergency = int((requests_df["EMERGENCY"] == "Yes").sum()) if not requests_df.empty else 0
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Requests", total)
    c2.metric("Completed", completed)
    c3.metric("Open", open_count)
    c4.metric("Emergency", emergency)

    if not requests_df.empty:
        st.subheader("Requests by Status")
        st.bar_chart(requests_df.groupby("STATUS").size())

        st.subheader("Requests by Pathcode")
        path_rows = []
        for row in requests_df.itertuples():
            for code in str(row.ENVIRONMENT_STRING or "").split(","):
                if code:
                    path_rows.append(code)
        if path_rows:
            st.bar_chart(pd.Series(path_rows).value_counts())
