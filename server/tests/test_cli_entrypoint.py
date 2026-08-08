"""The `loregarden` CLI is the entry point external callers get, so it is pinned here.

`loregarden mcp` dispatches `execute_tool` in-process, with no server involved, so these
tests exercise the real argument parsing and the real dispatcher against the isolated
test database.
"""

import json
from importlib import import_module
from pathlib import Path

import pytest
import tomllib
from loregarden.cli import errors, mcp_tools
from loregarden.cli.main import build_parser, main
from loregarden.mcp.tools import TOOL_DEFINITIONS, tool_names

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _console_scripts() -> dict[str, str]:
    return tomllib.loads(PYPROJECT.read_text())["project"]["scripts"]


def test_loregarden_is_the_single_external_entrypoint():
    """One command, subcommands underneath — external callers depend on this name."""
    assert _console_scripts() == {"loregarden": "loregarden.cli.main:main"}


@pytest.mark.parametrize("target", sorted(_console_scripts().values()))
def test_every_console_script_resolves_to_a_callable(target):
    """A typo'd entry point only fails at install time, on someone else's machine."""
    module_path, _, attribute = target.partition(":")
    assert callable(getattr(import_module(module_path), attribute))


@pytest.mark.parametrize(
    "argv",
    [
        ["mcp", "list"],
        ["mcp", "describe", "loregarden_get_ticket"],
        ["mcp", "call", "loregarden_get_ticket"],
        ["mcp", "serve"],
        ["db", "init", "--empty"],
    ],
)
def test_every_subcommand_parses_and_binds_a_runner(argv):
    """Each advertised subcommand must reach a callable, not an AttributeError."""
    args = build_parser().parse_args(mcp_tools.hoist_call_flags(argv))
    assert callable(args.run)


def test_root_requires_a_subcommand(capsys):
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == errors.EXIT_USAGE


def test_list_covers_every_advertised_tool(capsys):
    assert main(["mcp", "list"]) == errors.EXIT_OK
    out = capsys.readouterr().out
    for name in tool_names():
        assert name in out


def test_list_json_emits_the_definitions(capsys):
    assert main(["mcp", "list", "--json"]) == errors.EXIT_OK
    assert json.loads(capsys.readouterr().out) == TOOL_DEFINITIONS


@pytest.mark.parametrize("name", tool_names())
def test_describe_reaches_every_tool(name, capsys):
    """A tool the CLI cannot describe is a tool an operator cannot call blind."""
    assert main(["mcp", "describe", name]) == errors.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == name
    assert payload["inputSchema"]["type"] == "object"


def test_describe_accepts_the_unprefixed_name(capsys):
    assert main(["mcp", "describe", "get_ticket"]) == errors.EXIT_OK
    assert json.loads(capsys.readouterr().out)["name"] == "loregarden_get_ticket"


def test_unknown_tool_is_a_usage_error(capsys):
    assert main(["mcp", "describe", "loregarden_nope"]) == errors.EXIT_USAGE
    assert "Unknown tool" in capsys.readouterr().err


def test_undeclared_argument_is_rejected_with_the_accepted_names(capsys):
    assert main(["mcp", "call", "loregarden_get_ticket", "nope=1"]) == errors.EXIT_USAGE
    assert "has no argument 'nope'" in capsys.readouterr().err


def test_malformed_pair_is_rejected(capsys):
    assert main(["mcp", "call", "loregarden_get_ticket", "ticket_id"]) == errors.EXIT_USAGE
    assert "Expected key=value" in capsys.readouterr().err


def _tool(name: str) -> dict:
    return next(t for t in TOOL_DEFINITIONS if t["name"] == name)


def test_pairs_are_typed_by_the_tool_schema():
    args = mcp_tools.build_arguments(
        _tool("loregarden_list_tickets"),
        json_arg=None,
        pairs=["workspace_slug=loregarden", "limit=5", "roots_only=true"],
    )
    assert args == {"workspace_slug": "loregarden", "limit": 5, "roots_only": True}


def test_non_integer_value_for_an_integer_property_is_a_usage_error():
    with pytest.raises(errors.UsageError, match="limit must be an integer"):
        mcp_tools.build_arguments(
            _tool("loregarden_list_tickets"), json_arg=None, pairs=["limit=many"]
        )


def test_json_and_pairs_merge_with_pairs_winning():
    args = mcp_tools.build_arguments(
        _tool("loregarden_list_tickets"),
        json_arg='{"workspace_slug": "a", "limit": 3}',
        pairs=["limit=9"],
    )
    assert args == {"workspace_slug": "a", "limit": 9}


def test_at_prefixed_value_reads_from_a_file(tmp_path):
    body = tmp_path / "search.txt"
    body.write_text("payment retry")
    args = mcp_tools.build_arguments(
        _tool("loregarden_list_tickets"), json_arg=None, pairs=[f"search=@{body}"]
    )
    assert args == {"search": "payment retry"}


def test_missing_value_file_is_a_usage_error():
    with pytest.raises(errors.UsageError, match="No such file"):
        mcp_tools.build_arguments(
            _tool("loregarden_list_tickets"), json_arg=None, pairs=["search=@/nope/missing.txt"]
        )


def test_flags_after_key_value_arguments_are_hoisted():
    """`mcp call tool a=b --orchestrated` is how people type it; argparse alone rejects it."""
    assert mcp_tools.hoist_call_flags(
        ["mcp", "call", "t", "a=b", "--orchestrated", "--json", "{}", "c=d"]
    ) == ["mcp", "call", "--orchestrated", "--json", "{}", "t", "a=b", "c=d"]


def test_hoisting_leaves_other_subcommands_alone():
    assert mcp_tools.hoist_call_flags(["mcp", "list", "--json"]) == ["mcp", "list", "--json"]
    assert mcp_tools.hoist_call_flags(["db", "init", "--empty"]) == ["db", "init", "--empty"]


def test_flags_after_key_value_arguments_survive_a_real_parse():
    args = build_parser().parse_args(
        mcp_tools.hoist_call_flags(
            ["mcp", "call", "loregarden_get_ticket", "ticket_id=42", "--orchestrated"]
        )
    )
    assert args.tool == "loregarden_get_ticket"
    assert args.args == ["ticket_id=42"]
    assert args.orchestrated is True


def test_call_runs_the_real_dispatcher_against_the_database(client, capsys):
    """`client` seeds the isolated DB; the CLI must read it with no server involved."""
    assert (
        main(["mcp", "call", "loregarden_list_tickets", "workspace_slug=loregarden"])
        == errors.EXIT_OK
    )
    payload = json.loads(capsys.readouterr().out)
    assert "tickets" in payload


def test_tool_error_exits_nonzero_and_reports_on_stderr(client, capsys):
    assert (
        main(["mcp", "call", "loregarden_get_ticket", "ticket_id=does-not-exist"])
        == errors.EXIT_ERROR
    )
    assert capsys.readouterr().err.strip()


def test_orchestrated_flag_applies_the_pipeline_tool_policy(client, capsys):
    assert (
        main(
            [
                "mcp",
                "call",
                "loregarden_create_ticket",
                "--orchestrated",
                "workspace_slug=loregarden",
                "title=x",
            ]
        )
        == errors.EXIT_ERROR
    )
    assert "not available to orchestrated" in capsys.readouterr().err
