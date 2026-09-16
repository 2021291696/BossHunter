"""internship_mode 三态策略的行为回归。

- resolve_internship_mode：新字段优先，兼容旧布尔 allow_internship；
- quick_score：exclude 滤实习、allow 放行、only 反向滤掉非实习，日薪（元/天）作为强信号；
- apply_internship_mode_filter：only 模式在 BOSS 搜索期强制附加实习筛选。
"""

from bosshunter.ai.prefilter import quick_score
from bosshunter.collection.platforms.boss import apply_internship_mode_filter, build_boss_filter_query
from bosshunter.config import resolve_internship_mode


def _job(**overrides) -> dict:
    job = {
        "id": "j1",
        "title": "数据分析师",
        "company": "Example",
        "salary": "10-20K",
        "city": "成都",
        "experience": "1-3年",
        "jd": "Build product features",
        "url": "https://example.com/jobs/j1",
    }
    job.update(overrides)
    return job


class TestResolveInternshipMode:
    def test_defaults_to_exclude(self):
        assert resolve_internship_mode({}) == "exclude"
        assert resolve_internship_mode(None) == "exclude"

    def test_legacy_boolean_mapping(self):
        assert resolve_internship_mode({"allow_internship": True}) == "allow"
        assert resolve_internship_mode({"allow_internship": False}) == "exclude"

    def test_explicit_mode_wins_over_legacy(self):
        assert resolve_internship_mode({"allow_internship": True, "internship_mode": "only"}) == "only"
        assert resolve_internship_mode({"allow_internship": False, "internship_mode": "allow"}) == "allow"

    def test_invalid_mode_falls_back(self):
        assert resolve_internship_mode({"internship_mode": "whatever"}) == "exclude"


class TestQuickScoreInternshipModes:
    def test_exclude_filters_internship_title(self):
        score, reason = quick_score(_job(title="产品实习生"), {"profile": {}})
        assert score == 0
        assert "实习" in reason

    def test_allow_keeps_internship_title(self):
        score, _ = quick_score(_job(title="产品实习生"), {"profile": {"internship_mode": "allow"}})
        assert score == 100

    def test_only_filters_non_internship(self):
        score, reason = quick_score(_job(title="数据分析师"), {"profile": {"internship_mode": "only"}})
        assert score == 0
        assert "非实习" in reason

    def test_only_keeps_internship_title(self):
        score, _ = quick_score(
            _job(title="产品实习生"),
            {"profile": {"internship_mode": "only", "filter_unparsed_salary": False}},
        )
        assert score == 100

    def test_daily_salary_counts_as_internship_signal(self):
        job = _job(title="运营专员", salary="150-200元/天")
        # exclude：日薪岗视为实习被滤
        score, reason = quick_score(job, {"profile": {}})
        assert score == 0 and "实习" in reason
        # only：日薪岗保留（关闭无法解析薪资过滤以到达放行分支）
        score, _ = quick_score(
            job, {"profile": {"internship_mode": "only", "filter_unparsed_salary": False}},
        )
        assert score == 100


class TestApplyInternshipModeFilter:
    def test_only_mode_forces_internship_filter(self):
        filters = apply_internship_mode_filter({}, {"internship_mode": "only"})
        assert filters.get("job_type") == ["实习"]
        assert "jobType=2" in build_boss_filter_query(filters)

    def test_only_mode_keeps_existing_job_type(self):
        filters = apply_internship_mode_filter({"job_type": ["全职"]}, {"internship_mode": "only"})
        assert filters["job_type"] == ["全职"]

    def test_non_only_mode_does_not_inject(self):
        assert apply_internship_mode_filter({}, {"internship_mode": "exclude"}).get("job_type") is None
        assert apply_internship_mode_filter({}, {"internship_mode": "allow"}).get("job_type") is None
        assert apply_internship_mode_filter({}, {}).get("job_type") is None
