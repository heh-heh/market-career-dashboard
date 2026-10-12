"""Job classification regression cases; no site requests or production data."""
import unittest

from scripts.job_classifier import classify_role, is_developer_posting


class JobClassifierTests(unittest.TestCase):
    def test_real_game_client_roles(self):
        samples = [
            ("게임 클라이언트 프로그래머 (C++ / Unreal)", "기획·디자인 협업"),
            ("Unity C# 클라이언트 개발자", "PM 및 UX 팀과 협업"),
            ("Unreal Engine 게임 개발자", "C++ 엔진 구현"),
            ("게임플레이 프로그래머", "게임 콘텐츠 기획 지원"),
            ("게임 C++ 개발자", "게임 서버 담당과 협업"),
            ("게임 클라이언트 개발 (신입)", ""),
        ]
        for title, detail in samples:
            with self.subTest(title=title):
                self.assertEqual(classify_role(title, detail), "클라이언트")

    def test_non_developer_titles_never_become_client_due_to_skills(self):
        titles = [
            "게임 PM (Unity 프로젝트)",
            "프로덕트 매니저 - C++ / Unreal",
            "게임기획자 (클라이언트 및 엔진 협업)",
            "게임 콘텐츠 기획 · 신입",
            "UI/UX 디자이너 Unity 게임",
            "게임 3D 모델러 / 언리얼",
            "아트 디렉터 / Unreal Designer",
            "프로젝트 매니저 (게임 개발 관리)",
            "웹 마케터 (React·C++)",
            "게임 서비스 운영 (클라이언트 담당)",
            "Producer / Client Team",
            "Game Designer Unity",
            "사업개발 및 전략기획 (개발사)",
        ]
        for title in titles:
            with self.subTest(title=title):
                self.assertIsNone(classify_role(title, "Unity C++ client 개발자 채용 게임 서버 REST API 개발"))
                self.assertFalse(is_developer_posting(title, "Unreal 개발자"))

    def test_title_has_priority_over_neighboring_jobs_or_description(self):
        self.assertEqual(classify_role("Java 백엔드 서버 개발자",
                                       "게임 클라이언트 개발 / C++ Unity 기획자"), "백엔드/서버")
        self.assertEqual(classify_role("React 프론트엔드 개발자",
                                       "Unity 클라이언트 프로그래머 공고 보기"), "프론트엔드")
        self.assertEqual(classify_role("AI 머신러닝 엔지니어",
                                       "Unreal 게임 엔진 개발자 모집"), "AI/데이터")
        self.assertEqual(classify_role("일반 소프트웨어 개발자", "C++ Unity 협업"),
                         "개발", "isolated skills in the description cannot change the role")
        self.assertIsNone(classify_role("채용 공고", "Unity C# 경험자 우대, 디자인 스튜디오"))
        self.assertIsNone(classify_role("Unity UI 디자이너", "클라이언트 개발자와 협업"))

    def test_ambiguous_title_requires_explicit_contextual_role(self):
        self.assertEqual(classify_role("2026 게임 부문 채용",
                                       "모집 분야: 클라이언트 프로그래머 / 우대 C++"), "클라이언트")
        self.assertIsNone(classify_role("2026 게임 부문 채용", "언리얼, Unity, C++"))

    def test_language_boundaries(self):
        self.assertIsNone(classify_role("Client Success Manager", "Unity 개발 경험자"))
        self.assertEqual(classify_role("API 서버 엔지니어", "Unity 프로젝트 협업"), "백엔드/서버")
        self.assertEqual(classify_role("소프트웨어 개발자", "마케팅 PM Unity"), "개발")


if __name__ == "__main__":
    unittest.main()
