"""internship_mode 三态策略的行为回归。

- resolve_internship_mode：新字段优先，兼容旧布尔 allow_internship；
- quick_score：exclude 滤实习、allow 放行、only 反向滤掉非实习，日薪（元/天）作为强信号；
- apply_internship_mode_filter：only 模式在 BOSS 搜索期强制附加实习筛选；
- 三大平台（猎聘/51job/智联）原生实习筛选参数：only 模式注入，exclude/allow 不注入。
"""

from unittest.mock import Mock

from bosshunter.ai.prefilter import quick_score
from bosshunter.collection.models import PlatformCollectionRequest
from bosshunter.collection.platforms.boss import apply_internship_mode_filter, build_boss_filter_query
from bosshunter.collection.platforms.job51 import (
    Job51Browser,
    Job51Collector,
    internship_search_suffix as job51_internship_suffix,
)
from bosshunter.collection.platforms.liepin import (
    LiepinCollector,
    internship_search_suffix as liepin_internship_suffix,
)
from bosshunter.collection.platforms.zhilian import (
    ZhilianCollector,
    append_internship_search_param,
    internship_search_param as zhilian_internship_param,
)
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


class TestNativeInternshipFilters:
    """猎聘/51job/智联原生实习筛选参数：only 模式注入，exclude/allow 模式 URL 不变。"""

    _NON_ONLY_PROFILES = (
        {},
        None,
        {"internship_mode": "exclude"},
        {"internship_mode": "allow"},
        {"allow_internship": True},   # 旧布尔：allow
        {"allow_internship": False},  # 旧布尔：exclude
    )

    @staticmethod
    def _request(platform: str, city: str, code: str) -> PlatformCollectionRequest:
        return PlatformCollectionRequest(platform, ["数据分析"], [city], {city: code}, max_pages=1)

    # ------------------------------------------------------------------ 猎聘
    def test_liepin_only_mode_appends_work_year_code(self):
        url = LiepinCollector.build_search_url(
            self._request("liepin", "北京", "010"), "北京", "数据分析", page=1,
            profile={"internship_mode": "only"},
        )
        assert url.endswith("&workYearCode=2")
        assert liepin_internship_suffix({"internship_mode": "only"}) == "&workYearCode=2"

    def test_liepin_non_only_mode_url_unchanged(self):
        args = (self._request("liepin", "北京", "010"), "北京", "数据分析")
        base = LiepinCollector.build_search_url(*args, page=1)
        assert "workYearCode" not in base
        for profile in self._NON_ONLY_PROFILES:
            url = LiepinCollector.build_search_url(*args, page=1, profile=profile)
            assert url == base
            assert "workYearCode" not in url

    # ------------------------------------------------------------------ 51job
    def test_job51_only_mode_api_fetch_carries_job_type(self):
        evaluate = Mock(return_value=None)
        collector = Job51Collector(
            browser=Job51Browser(evaluate=evaluate),
            config={"profile": {"internship_mode": "only"}},
        )
        collector._fetch_page("host", "kw", "020000", 1)
        js = evaluate.call_args[0][1]
        assert "jobType=03" in js
        assert "__JOBTYPE__" not in js
        assert job51_internship_suffix({"internship_mode": "only"}) == "&jobType=03"

    def test_job51_only_mode_host_search_url_carries_job_type(self):
        new_tab = Mock(return_value="new-tab")
        collector = Job51Collector(
            browser=Job51Browser(get_page_targets=Mock(return_value=[]), new_tab=new_tab),
            sleep=Mock(),
            config={"profile": {"internship_mode": "only"}},
        )
        collector._ensure_host_tab(self._request("51job", "上海", "020000"))
        opened_url = new_tab.call_args[0][0]
        assert "jobType=03" in opened_url

    def test_job51_non_only_mode_has_no_job_type(self):
        for profile in self._NON_ONLY_PROFILES:
            evaluate = Mock(return_value=None)
            collector = Job51Collector(
                browser=Job51Browser(evaluate=evaluate),
                config={"profile": profile},
            )
            collector._fetch_page("host", "kw", "020000", 1)
            js = evaluate.call_args[0][1]
            assert "jobType" not in js
            assert "__JOBTYPE__" not in js
            assert job51_internship_suffix(profile) == ""

    def test_job51_non_only_mode_host_search_url_unchanged(self):
        new_tab = Mock(return_value="new-tab")
        collector = Job51Collector(
            browser=Job51Browser(get_page_targets=Mock(return_value=[]), new_tab=new_tab),
            sleep=Mock(),
            config={"profile": {"internship_mode": "allow"}},
        )
        collector._ensure_host_tab(self._request("51job", "上海", "020000"))
        opened_url = new_tab.call_args[0][0]
        assert opened_url == "https://we.51job.com/pc/search?jobArea=020000&keyword=%E6%95%B0%E6%8D%AE%E5%88%86%E6%9E%90"

    # ------------------------------------------------------------------ 智联
    def test_zhilian_only_mode_appends_et_param(self):
        url = ZhilianCollector.build_search_url(
            self._request("zhilian", "深圳", "765"), "深圳", "数据分析", 1,
            profile={"internship_mode": "only"},
        )
        assert url == "https://www.zhaopin.com/sou/jl765/?et=4"
        assert zhilian_internship_param({"internship_mode": "only"}) == "et=4"

    def test_zhilian_non_only_mode_url_unchanged(self):
        args = (self._request("zhilian", "深圳", "765"), "深圳", "数据分析", 1)
        base = ZhilianCollector.build_search_url(*args)
        assert base == "https://www.zhaopin.com/sou/jl765/"
        for profile in self._NON_ONLY_PROFILES:
            url = ZhilianCollector.build_search_url(*args, profile=profile)
            assert url == base
            assert "et=4" not in url

    def test_zhilian_param_separator_follows_existing_query(self):
        # 智联当前搜索页无 query → 用 ?；若未来 URL 已带 query → 用 &
        assert append_internship_search_param(
            "https://www.zhaopin.com/sou/jl765/", {"internship_mode": "only"},
        ) == "https://www.zhaopin.com/sou/jl765/?et=4"
        assert append_internship_search_param(
            "https://www.zhaopin.com/sou/jl765/?x=1", {"internship_mode": "only"},
        ) == "https://www.zhaopin.com/sou/jl765/?x=1&et=4"
        assert append_internship_search_param(
            "https://www.zhaopin.com/sou/jl765/", {"internship_mode": "exclude"},
        ) == "https://www.zhaopin.com/sou/jl765/"
