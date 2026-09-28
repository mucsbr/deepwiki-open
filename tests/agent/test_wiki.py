"""Wiki navigation reads the existing JSON format without generating embeddings."""

import asyncio
import json

import pytest
from fastapi import HTTPException

from api.agent.repositories import Repository, SourceReader
from api.agent.tools import make_tools
from api.agent.wiki import PAGE_CHARS, WikiReader


@pytest.fixture
def wiki_fixture(tmp_path):
    repo = Repository(
        "go/team/repo_name",
        "https://gitlab.example/go/team/repo_name",
        "a" * 40,
        tmp_path / "repos" / "go_team_repo_name",
    )
    directory = tmp_path / "wikicache"
    directory.mkdir()
    path = directory / "deepwiki_cache_gitlab_go--team_repo_name_en.json"
    data = {
        "repo": {
            "owner": "go/team",
            "repo": "repo_name",
            "type": "gitlab",
            "token": "do-not-return-token",
        },
        "wiki_structure": {
            "title": "Media service",
            "description": "Dispatches WeChat collection tasks.",
        },
        "generated_pages": {
            "overview": {
                "title": "Overview",
                "content": "Media modules.",
                "filePaths": ["main.go"],
            },
            "wechat": {
                "title": "微信任务",
                "content": "微信任务由 UI 提交。" + "x" * (PAGE_CHARS + 200),
                "filePaths": ["handlers/media.go"],
                "relatedPages": ["overview"],
            },
        },
    }
    path.write_text(json.dumps(data, ensure_ascii=False))
    return tmp_path, SourceReader([repo]), path, data


def test_existing_json_language_fallback_page_navigation_and_pagination(wiki_fixture):
    root, reader, path, data = wiki_fixture
    wiki = WikiReader(reader, root, "zh")
    summary = wiki.summary("go/team/repo_name")
    assert summary["language"] == "en"
    assert summary["description"] == data["wiki_structure"]["description"]
    assert summary["pages"][1]["file_paths"] == ["handlers/media.go"]
    assert "unknown" in summary["notice"]
    first = wiki.page("go/team/repo_name", "wechat")
    second = wiki.page("go/team/repo_name", "wechat", first["next_offset"])
    assert (
        first["content"] + second["content"]
        == data["generated_pages"]["wechat"]["content"]
    )
    assert second["next_offset"] is None
    assert first["related_pages"] == ["overview"]
    assert "do-not-return-token" not in json.dumps([summary, first])
    context = json.loads(wiki.prompt_context())
    assert context[0]["description"] == data["wiki_structure"]["description"]
    assert not (root / "databases").exists()

    # Prefer the session language, but fall back if that cache is incomplete/corrupt.
    zh = path.with_name(path.name.replace("_en.json", "_zh.json"))
    zh.write_text("{broken")
    assert (
        WikiReader(reader, root, "zh").overview("go/team/repo_name")["language"] == "en"
    )
    data["wiki_structure"]["description"] = "微信任务分发服务"
    zh.write_text(json.dumps(data, ensure_ascii=False))
    assert (
        WikiReader(reader, root, "zh").overview("go/team/repo_name")["language"] == "zh"
    )


def test_json_search_respects_scope_and_never_loads_an_embedding_database(wiki_fixture):
    root, reader, _, _ = wiki_fixture
    private = root / "wikicache" / "deepwiki_cache_gitlab_private_repo_en.json"
    private.write_text(
        json.dumps(
            {"generated_pages": {"p": {"title": "微信 private", "content": "secret"}}}
        )
    )
    wiki = WikiReader(reader, root)
    result = wiki.search("微信 新媒体")
    assert result["search_mode"] == "keyword"
    assert [match["repo"] for match in result["matches"]] == ["go/team/repo_name"]
    assert result["matches"][0]["page_id"] == "wechat"
    assert "微信" in result["matches"][0]["text"]
    with pytest.raises(ValueError, match="authorized scope"):
        wiki.search("微信", "private/repo")
    with pytest.raises(ValueError, match="authorized scope"):
        wiki.page("private/repo", "p")
    with pytest.raises(ValueError, match="Unknown Wiki page"):
        wiki.page("go/team/repo_name", "../../private")


def test_domain_keyword_coverage_outranks_a_generic_title(wiki_fixture):
    root, reader, path, data = wiki_fixture
    data["generated_pages"]["overview"]["content"] = (
        "这里介绍微信新媒体任务的下发流程。"
    )
    data["generated_pages"]["generic"] = {
        "title": "任务管理",
        "content": "通用任务配置。",
    }
    path.write_text(json.dumps(data, ensure_ascii=False))
    matches = WikiReader(reader, root).search("微信 新媒体 任务")["matches"]
    assert matches[0]["page_id"] == "overview"
    assert matches[0]["matched_keywords"] == ["微信", "新媒体", "任务"]


def test_missing_misidentified_or_symlinked_wiki_does_not_supply_false_context(
    wiki_fixture,
):
    root, reader, path, data = wiki_fixture
    data["repo"]["owner"] = "other"
    path.write_text(json.dumps(data))
    wiki = WikiReader(reader, root)
    assert not wiki.summary("go/team/repo_name")["available"]
    assert wiki.search("微信")["unavailable_repositories"] == ["go/team/repo_name"]
    with pytest.raises(ValueError, match="No readable Wiki"):
        wiki.page("go/team/repo_name", "wechat")
    target = root / "private.json"
    path.rename(target)
    path.symlink_to(target)
    assert not WikiReader(reader, root).overview("go/team/repo_name")["available"]


def test_registered_wiki_tools_and_legacy_source_recheck_authorization(wiki_fixture):
    root, reader, _, _ = wiki_fixture

    async def check():
        denied = False
        checks = []

        async def authorize():
            checks.append(True)
            if denied:
                raise HTTPException(403, "revoked")

        tools = {
            tool.name: tool
            for tool in make_tools(reader, root, None, {"session_id": "s"}, authorize)
        }
        repos = json.loads(await tools["list_repositories"].ainvoke({}))
        assert repos[0]["wiki"]["available"]
        summary = json.loads(
            await tools["get_wiki_summary"].ainvoke({"repo": "go/team/repo_name"})
        )
        assert summary["pages"][1]["id"] == "wechat"
        legacy = json.loads(
            await tools["search_index"].ainvoke({"query": "微信", "source": "wiki"})
        )
        direct = json.loads(await tools["search_wiki"].ainvoke({"query": "微信"}))
        assert legacy == direct
        assert len(checks) == 4
        denied = True
        with pytest.raises(HTTPException):
            await tools["get_wiki_page"].ainvoke(
                {"repo": "go/team/repo_name", "page_id": "wechat"}
            )

    asyncio.run(check())
