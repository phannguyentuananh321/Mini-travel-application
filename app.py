import time
import streamlit as st
import pyrebase
import firebase_admin
from firebase_admin import credentials, firestore
from firebase_admin import auth as admin_auth
from collections import deque
from datetime import datetime, timezone
from ollama import Client
from streamlit_extras.stylable_container import stylable_container

st.set_page_config(page_title="Travel planner", page_icon="✈️")
MODEL = "gpt-oss:20b"
client = Client(
    host='http://kpkns-34-16-174-38.a.free.pinggy.link'
)

def ollama_stream(history_messages: list[dict]):

    try:
        response = client.chat(
            model=MODEL,
            messages=history_messages
        )
        return response.get('message', {}).get('content', '')
    except Exception as e:
        return f"[LLM error] {e}"

def save_message(uid: str, role: str, content: str):
    doc = {
        "role": role,
        "content": content,
        "ts": datetime.now(timezone.utc)
    }
    db.collection("chats").document(uid).collection("messages").add(doc)

def load_last_messages(uid: str, limit: int = 8):
    q = (db.collection("chats").document(uid)
        .collection("messages")
        .order_by("ts", direction=firestore.Query.DESCENDING)
        .limit(limit))
    docs = list(q.stream())
    docs.reverse()
    out = []
    for d in docs:
        data = d.to_dict()
        out.append({"role": data.get("role", "assistant"),
                    "content": data.get("content", "")})
    return out

@st.cache_resource
def get_firebase_clients():
    # Pyrebase (Auth)
    firebase_cfg = st.secrets["firebase_client"]
    firebase_app = pyrebase.initialize_app(firebase_cfg)
    auth = firebase_app.auth()

    # Admin (Firestore)
    if not firebase_admin._apps:
        cred = credentials.Certificate(dict(st.secrets["firebase_admin"]))
        firebase_admin.initialize_app(cred)
    db = firestore.client()
    return auth, db

auth, db = get_firebase_clients()

if "user" not in st.session_state:
    st.session_state.user = None

if "show_signup" not in st.session_state:
    st.session_state["show_signup"] = False
if "show_login" not in st.session_state:
    st.session_state["show_login"] = True

def login_form():
    st.markdown("<h3 style='text-align: center;'>Sign in</h3>", unsafe_allow_html=True)
    with st.form("login_form", clear_on_submit=False):
        email = st.text_input("Email", key="email_login")
        password = st.text_input("Mật khẩu", type="password", key="password_login")
        col1, _, col2 = st.columns([0.75, 0.75, 0.75])
        with col1:
            with stylable_container(
                "black",
                css_styles="""
                button {
                    background-color: #0DDEAA;
                    color: black;
                }""",
            ):
                login = st.form_submit_button("Sign in")
        with col2:
            goto_signup = st.form_submit_button("No account? Sign up", type="primary")

    if goto_signup:
        st.session_state["show_signup"] = True
        st.session_state["show_login"] = False
        st.rerun()

    if login:
        try:
            user = auth.sign_in_with_email_and_password(email, password)
            st.session_state.user = {
                "email": email,
                "uid": user["localId"],
                "idToken": user["idToken"]
            }
            msgs = load_last_messages(st.session_state.user["uid"], limit=8)
            if msgs:
                st.session_state.messages = deque(msgs, maxlen=8)
            else:
                st.session_state.messages = deque([
                    {"role": "assistant", "content": "Welcome back! You can now create your travel itinerary"}
                ], maxlen=8)
            st.success("Login successful!")
            st.rerun()
        except Exception as e:
            st.error(f"Login failed: {e}")

def signup_form():
    st.subheader("Sign up")
    with st.form("signup_form", clear_on_submit=False):
        email = st.text_input("Email", key="email_signup")
        password = st.text_input("Password (≥6 characters)", type="password", key="password_signup")
        col1, _, col2 = st.columns([0.75, 0.75, 0.75])
        with col1:
            with stylable_container(
                "black-1",
                css_styles="""
                button {
                    background-color: #0DD0DE;
                    color: black;
                }""",
            ):
                signup = st.form_submit_button("Create account")
        with col2:
                goto_login = st.form_submit_button("Already have an account? Log in", type="primary")

    if goto_login:
        st.session_state["show_signup"] = False
        st.session_state["show_login"] = True
        st.rerun()

    if signup:
        try:
            user = auth.create_user_with_email_and_password(email, password)
            st.success("Account created successfully! Please log in.")
            time.sleep(3)
            st.session_state["show_signup"] = False
            st.session_state["show_login"] = True
            st.rerun()
        except Exception as e:
            st.error(f"Sign-up failed: {e}")

def generate_itinerary(origin: str, destination: str, start_date: str, end_date: str, interests: list, pace: str, user_uid: str):
    """Generate day-by-day itinerary (morning/afternoon/evening) using Ollama and save to Firestore."""

    # Build a clear prompt for the LLM
    try:
        from datetime import datetime
        s = datetime.fromisoformat(start_date)
        e = datetime.fromisoformat(end_date)
        days = (e - s).days + 1
    except Exception:
        days = 'N/A'

    system_prompt = f"""
You are a professional travel planner. Create a detailed {days}-day itinerary for a trip.
Output in Markdown only. For each day include sections: Morning, Afternoon, Evening. Provide 1-3 activities per section, each with a 1-2 sentence explanation.
Information:\n- Origin: {origin}\n- Destination: {destination}\n- Dates: {start_date} to {end_date}\n- Interests: {', '.join(interests)}\n- Pace: {pace} (relaxed/normal/tight)

"""

    user_prompt = f"Generate a {days}-day itinerary for a trip from {origin} to {destination} from {start_date} to {end_date}. Interests: {', '.join(interests)}. Pace: {pace}. Include morning/afternoon/evening plans each day with short explanations."

    history = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    # Call Ollama
    assistant_content = ollama_stream(history)

    # Save to Firestore under collection 'itineraries' -> document user_uid -> subcollection 'plans'
    try:
        doc = {
            'role': 'assistant',
            'content': assistant_content,
            'ts': datetime.now(timezone.utc)
        }
        db.collection('itineraries').document(user_uid).collection('plans').add(doc)
    except Exception as e:
        # ignore save errors but show in content
        assistant_content = assistant_content + f"\n\n[Warning: failed to save itinerary: {e}]"

    return assistant_content

def load_itinerary_history(user_uid: str, limit: int = 5):
    """Load previously generated itineraries for a user from Firestore."""
    try:
        q = (
            db.collection('itineraries')
              .document(user_uid)
              .collection('plans')
              .order_by('ts', direction=firestore.Query.DESCENDING)
              .limit(limit)
        )
        docs = list(q.stream())
        return [d.to_dict() for d in docs]
    except Exception as e:
        st.warning(f"Failed to load history: {e}")
        return []

def travel_planner_ui():
    st.header("✈️ AI Travel Planner")
    st.markdown("Enter origin, destination, dates, interests, and pace. The AI will return a day-by-day itinerary (morning/afternoon/evening).")

    with st.form('travel_form'):
        c1, c2 = st.columns(2)
        with c1:
            origin = st.text_input('Origin city', value='Hanoi')
        with c2:
            destination = st.text_input('Destination city', value='Da Nang, Vietnam')

        d1, d2 = st.columns(2)
        with d1:
            start_date = st.date_input('Start date')
        with d2:
            end_date = st.date_input('End date', min_value=start_date)

        interests = st.multiselect('Interests', ['Food', 'Museums', 'Nature', 'Nightlife'], default=['Food', 'Nature'])
        pace = st.radio('Pace', ['relaxed', 'normal', 'tight'], index=1, horizontal=True)

        submitted = st.form_submit_button('Generate Itinerary')

    if submitted:
        if not st.session_state.user:
            st.error('Please login first to generate and save itineraries.')
            return
        sd = start_date.isoformat()
        ed = end_date.isoformat()
        user_uid = st.session_state.user['uid']
        with st.spinner('Generating itinerary...'):
            result = generate_itinerary(origin, destination, sd, ed, interests, pace, user_uid)
        st.markdown(result)

    if st.session_state.user:
        st.subheader("Recent Itinerary History")
        history = load_itinerary_history(st.session_state.user['uid'], limit=5)
        if history:
            for item in history:
                ts = item.get('ts')
                ts_str = ts.strftime('%Y-%m-%d %H:%M') if ts else ''
                with st.expander(f"Itinerary created at {ts_str}"):
                    st.markdown(item.get('content', ''), unsafe_allow_html=True)
        else:
            st.info("No previous itineraries found.")


st.markdown("<h1 style='text-align: center;'>AI Travel Planner</h1>", unsafe_allow_html=True)

if "show_signup" not in st.session_state:
    st.session_state["show_signup"] = False
if "show_login" not in st.session_state:
    st.session_state["show_login"] = True

if st.session_state.user:
    st.success(f"Logged in as: {st.session_state.user['email']}")
    _, col2, _ = st.columns([1.3, 0.75, 1])
    with col2:
        if st.button("Log out", type="primary"):
            st.session_state.user = None
            st.rerun()

    st.divider()
    travel_planner_ui()

else:
    if st.session_state.get("show_signup", False):
        signup_form()
    elif st.session_state.get("show_login", True):
        login_form()
