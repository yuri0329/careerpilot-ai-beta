
import os
import sqlite3
import fcntl
import time
from functools import wraps
from pathlib import Path
import streamlit as st

STATE = Path(__file__).resolve().parent / ".beta_private"
MAX_BUNDLES = 50
MAX_AI_CALLS = 500

def connect_db():
    STATE.mkdir(mode=0o700, exist_ok=True)
    os.chmod(STATE, 0o700)
    path = STATE / "usage.sqlite3"
    conn = sqlite3.connect(str(path), timeout=30)
    os.chmod(path, 0o600)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS counters "
        "(name TEXT PRIMARY KEY, n INTEGER NOT NULL)"
    )
    conn.commit()
    return conn

def get_count(name):
    conn = connect_db()
    try:
        row = conn.execute(
            "SELECT n FROM counters WHERE name=?", (name,)
        ).fetchone()
        return row[0] if row else 0
    finally:
        conn.close()

def claim(name, limit):
    conn = connect_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT OR IGNORE INTO counters VALUES (?, 0)",
            (name,)
        )
        n = conn.execute(
            "SELECT n FROM counters WHERE name=?",
            (name,)
        ).fetchone()[0]
        if n >= limit:
            conn.rollback()
            return False
        conn.execute(
            "UPDATE counters SET n=n+1 WHERE name=?",
            (name,)
        )
        conn.commit()
        return True
    finally:
        conn.close()

def beta_banner():
    used = get_count("bundle_starts")
    st.info(
        f"**CareerPilot AI Open Beta** | "
        f"체험 시작 {used}/{MAX_BUNDLES}회"
    )
    st.caption(
        "50명의 고유 사용자가 아니라 총 생성 시작 "
        "50회 제한입니다. 실패한 생성도 횟수에 포함됩니다."
    )
    st.warning(
        "실제 이름, 연락처, 주소, 회사 기밀정보를 제거한 "
        "가상 또는 비식별화된 이력서만 등록해 주세요."
    )

    url = os.environ.get(
        "CAREERPILOT_FEEDBACK_URL", ""
    ).strip()
    if url.startswith("https://"):
        st.link_button("피드백 남기기", url)

    if used >= MAX_BUNDLES:
        st.error("베타 체험 50회가 마감되었습니다.")
        st.stop()

    if not st.checkbox(
        "비식별 테스트 자료만 사용하겠습니다.",
        key="cp_beta_agree"
    ):
        st.stop()

def limit_bundle(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        n = st.session_state.get("cp_beta_bundle_used", 0)

        if n >= 2:
            raise RuntimeError(
                "한 브라우저 세션에서 최대 2회까지 "
                "맞춤 지원서를 생성할 수 있습니다."
            )

        if not claim("bundle_starts", MAX_BUNDLES):
            raise RuntimeError(
                "베타 체험 50회가 마감되었습니다."
            )

        st.session_state["cp_beta_bundle_used"] = n + 1
        return fn(*args, **kwargs)
    return wrapped

def limit_ai(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        n = st.session_state.get("cp_beta_ai_used", 0)

        if n >= 30:
            raise RuntimeError(
                "이번 브라우저 세션의 AI 요청 한도에 "
                "도달했습니다."
            )

        if not claim("ai_calls", MAX_AI_CALLS):
            raise RuntimeError(
                "전체 베타 AI 요청 한도에 도달했습니다."
            )

        st.session_state["cp_beta_ai_used"] = n + 1

        lock_path = STATE / "inference.lock"

        with lock_path.open("a+b") as handle:
            os.chmod(lock_path, 0o600)
            deadline = time.monotonic() + 90

            while True:
                try:
                    fcntl.flock(
                        handle,
                        fcntl.LOCK_EX | fcntl.LOCK_NB
                    )
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError(
                            "AI가 혼잡합니다. 잠시 후 다시 시도해 주세요."
                        )
                    time.sleep(1)

            try:
                return fn(*args, **kwargs)
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    return wrapped
