"""Conservative developer-job role classifier.

Use a job TITLE to decide the role. Job-board snippets/detail pages may
contain navigation, similar ads, or "collaborates with designers" text;
technologies alone must never make a PM/designer into a client engineer.
"""

import re


def found(pattern, text):
    return bool(re.search(pattern, str(text or ""), re.I))


# Explicit non-engineering titles take precedence over keywords in descriptions.
NON_DEVELOPER_TITLE = (
    r"(?:기획자?|기획\s*(?:직|팀|담당|매니저)|"
    r"디자이너|디자인|아티스트|일러스트|원화가?|모델러|모델링|애니메이터|테스터|테스트\s*담당|"
    r"마케터|마케팅|홍보|퍼블리셔|프로듀서|프로덕트\s*매니저|"
    r"프로젝트\s*매니저|사업\s*개발|사업\s*운영|서비스\s*운영|"
    r"커뮤니티\s*매니저|고객\s*지원|고객\s*상담|사무|경영\s*지원|"
    r"인사\s*담당|채용\s*담당|영업|회계|총무|"
    r"\b(?:pm|po|pd|producer|product manager|product owner|"
    r"project manager|planner|planning|designer|artist|illustrator|"
    r"animator|modeler|marketer|marketing|recruiter|sales|"
    r"community manager|customer success|client success|customer support|manager|qa|tester)\b)"
)
# A role term (rather than a framework/library name) is required.
DEVELOPER_TITLE = (
    r"(?:개발자?|프로그래머|엔지니어|소프트웨어|"
    r"\bs/?w\b|\b(?:developer|programmer|engineer|software|devops)\b|"
    r"클라이언트|게임플레이|게임\s*엔진|백엔드|프론트엔드)"
)
BACKEND = r"(?:백엔드|백\s*엔드|서버|네트워크|인프라|\b(?:backend|back-end|server|devops)\b)"
FRONTEND = r"(?:프론트엔드|프론트\s*엔드|웹\s*(?:프론트|개발)|\b(?:frontend|front-end)\b)"
DATA_AI = r"(?:인공지능|머신러닝|딥러닝|데이터\s*(?:엔지니어|사이언티스트|분석)|\b(?:ai|ml|machine learning|data engineer|data scientist)\b)"
CLIENT = r"(?:클라이언트|게임플레이|게임\s*엔진|유니티|언리얼|렌더링|그래픽스|\b(?:client|gameplay|unity|unreal|directx|opengl)\b)"
GAME = r"(?:게임|모바일\s*게임|콘솔\s*게임|\b(?:game|gaming)\b)"
CPP = r"(?:c\+\+|c#)"
STRONG_CONTEXT_ROLE = (
    r"(?:클라이언트|백엔드|서버|프론트엔드|게임플레이|게임\s*엔진|"
    r"인공지능|머신러닝|데이터\s*엔지니어|"
    r"\b(?:backend|frontend|gameplay|unity|unreal)\b)"
    r".{0,24}?"
    r"(?:개발자?|프로그래머|엔지니어|\b(?:developer|programmer|engineer)\b)"
)


def classify_role(title, description=""):
    """Return a developer area or None (insufficient evidence/non-dev role).

    Title decides specialisms first. Only an ambiguous title may fall back
    to strong *role phrases* in a description, not isolated skill keywords.
    """
    title = re.sub(r"\s+", " ", str(title or "")).strip()
    detail = re.sub(r"\s+", " ", str(description or ""))[:6000]
    if not title:
        return None
    if found(NON_DEVELOPER_TITLE, title):
        return None

    title_developer = found(DEVELOPER_TITLE, title)
    context_developer = found(STRONG_CONTEXT_ROLE, detail)
    if not title_developer and not context_developer:
        return None

    # Explicit job titles override a generic list-page description mentioning
    # Unity, a backend stack, or the names of neighboring job postings.
    if title_developer:
        if found(BACKEND, title):
            return "백엔드/서버"
        if found(FRONTEND, title):
            return "프론트엔드"
        if found(DATA_AI, title):
            return "AI/데이터"
        if found(CLIENT, title) or (found(GAME, title) and found(CPP, title)):
            return "클라이언트"
        if found(r"(?:\b(?:unity|unreal|c\+\+|c#)\b|유니티|언리얼)", title) and found(GAME, detail):
            return "클라이언트"
        return "개발"

    # Job-board titles like "엔씨소프트 신입 채용" have no role; only use
    # corroborating explicit developer role phrases in the item description.
    if context_developer:
        if found(BACKEND + r".{0,24}?(?:개발자?|프로그래머|\bdeveloper\b)", detail):
            return "백엔드/서버"
        if found(FRONTEND + r".{0,24}?(?:개발자?|프로그래머|\bdeveloper\b)", detail):
            return "프론트엔드"
        if found(DATA_AI + r".{0,24}?(?:개발자?|엔지니어|\bengineer\b)", detail):
            return "AI/데이터"
        if found(CLIENT + r".{0,24}?(?:개발자?|프로그래머|\b(?:developer|programmer)\b)", detail):
            return "클라이언트"
    return None


def is_developer_posting(title, description=""):
    return classify_role(title, description) is not None
