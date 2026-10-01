"""The user-experience gate, exercised as the thing it is: a node script.

Every other frontend gate here is unpinned, and this one makes claims that are
easy to get subtly wrong in either direction — a check that accuses a labelled
button is noise nobody keeps, and a check that misses an unlabelled one is a
gate that reports a vacuous pass. Both failure modes are represented below.

Deliberately black-box, over a real git repo and real `--scope` flags, because
the diff scoping is half of what the gate does: a violation on a line the change
did not touch must not fail the commit that touched a different line.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_GATE = _ROOT / ".lefthook" / "scripts" / "ts_ux_states_check.cjs"
_ESTREE = _ROOT / "client" / "node_modules" / "@typescript-eslint" / "typescript-estree"

pytestmark = pytest.mark.skipif(
    not _ESTREE.exists() or shutil.which("node") is None,
    reason="needs node and client/node_modules (cd client && npm ci)",
)


def _git(repo: Path, *args: str) -> None:
    # GIT_DIR/GIT_WORK_TREE beat cwd, and a run nested in a worktree's hook
    # inherits them pointing at the real repository.
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A workspace the gate recognises: `client/src`, on `main`, with a commit.

    Built here rather than in a test body so a broken fixture is an ERROR and
    cannot be mistaken for a gate that found nothing.
    """
    _git(tmp_path, "init", "-q", "-b", "main", ".")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "client" / "src").mkdir(parents=True)
    (tmp_path / "client" / "src" / ".keep").write_text("")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def _write(repo: Path, name: str, body: str) -> Path:
    path = repo / "client" / "src" / name
    path.write_text(body)
    return path


def _run(repo: Path, scope: str = "worktree") -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    return subprocess.run(
        ["node", str(_GATE), "--repo", str(repo), "--scope", scope],
        capture_output=True,
        text=True,
        env=env,
    )


def _findings(result: subprocess.CompletedProcess) -> str:
    return result.stderr


# --------------------------------------------------------------------------- #
# 1. a control with no accessible name
# --------------------------------------------------------------------------- #


def test_icon_only_button_is_reported(repo: Path):
    _write(repo, "A.tsx", "export const A = () => <button onClick={go}><Icon /></button>;\n")
    result = _run(repo)
    assert result.returncode == 1
    assert "no text and no aria-label" in _findings(result)


@pytest.mark.parametrize(
    "attr",
    ['aria-label="Close"', 'title="Close"', 'aria-labelledby="x"'],
)
def test_a_named_button_passes(repo: Path, attr: str):
    _write(repo, "A.tsx", f"export const A = () => <button {attr}><Icon /></button>;\n")
    assert _run(repo).returncode == 0


def test_a_button_with_text_passes(repo: Path):
    _write(repo, "A.tsx", "export const A = () => <button onClick={go}>Save</button>;\n")
    assert _run(repo).returncode == 0


def test_a_button_whose_child_is_an_expression_is_not_accused(repo: Path):
    """`{label}` may well be the name. A gate that cannot tell must not accuse."""
    _write(repo, "A.tsx", "export const A = () => <button onClick={go}>{label}</button>;\n")
    assert _run(repo).returncode == 0


def test_spread_props_are_not_accused(repo: Path):
    """`aria-label` can arrive in the spread; the gate cannot see inside it."""
    _write(repo, "A.tsx", "export const A = () => <button {...rest}><Icon /></button>;\n")
    assert _run(repo).returncode == 0


# --------------------------------------------------------------------------- #
# 2. clickable by mouse, unreachable by keyboard
# --------------------------------------------------------------------------- #


def test_div_onclick_without_keyboard_affordances_is_reported(repo: Path):
    _write(repo, "A.tsx", "export const A = () => <div onClick={go}>Open</div>;\n")
    result = _run(repo)
    assert result.returncode == 1
    assert "role, tabIndex, a key handler" in _findings(result)


def test_a_partially_wired_div_names_only_what_is_missing(repo: Path):
    _write(repo, "A.tsx", 'export const A = () => <div role="button" onClick={go}>Open</div>;\n')
    result = _run(repo)
    assert result.returncode == 1
    findings = _findings(result)
    assert "missing tabIndex, a key handler" in findings
    assert "missing role" not in findings


def test_a_fully_wired_div_passes(repo: Path):
    _write(
        repo,
        "A.tsx",
        'export const A = () => <div role="button" tabIndex={0} onKeyDown={go} '
        "onClick={go}>Open</div>;\n",
    )
    assert _run(repo).returncode == 0


def test_a_native_button_is_never_asked_for_tabindex(repo: Path):
    _write(repo, "A.tsx", "export const A = () => <button onClick={go}>Open</button>;\n")
    assert _run(repo).returncode == 0


def test_a_dialog_container_is_not_asked_for_a_key_handler(repo: Path):
    """`onClick={(e) => e.stopPropagation()}` on the panel is not an action.

    Every modal in this app carries it, to stop the click reaching the backdrop
    underneath. Demanding a key handler there asks a container to be a control,
    and four such findings were the gate's entire false-positive rate on the
    real tree.
    """
    _write(
        repo,
        "A.tsx",
        "export const A = () => (\n"
        '  <div role="dialog" aria-modal="true" tabIndex={-1} '
        "onClick={(e) => e.stopPropagation()}>x</div>\n"
        ");\n",
    )
    result = _run(repo)
    assert result.returncode == 0, _findings(result)


def test_a_widget_role_is_still_asked(repo: Path):
    """The exemption is for structure. A `role="button"` still owes an answer."""
    _write(repo, "A.tsx", 'export const A = () => <div role="button" onClick={go}>Go</div>;\n')
    result = _run(repo)
    assert result.returncode == 1
    assert "missing tabIndex, a key handler" in _findings(result)


def test_a_presentational_backdrop_is_not_asked_for_tabindex(repo: Path):
    """`role="presentation"` exists to be skipped by the tab order.

    Demanding tabIndex on it asks for the opposite of what it is for. Its
    keyboard obligation is Escape, which check 3 owns.
    """
    _write(
        repo,
        "A.tsx",
        'const onKey = (e) => e.key === "Escape" && close();\n'
        'export const A = () => <div className="overlay" role="presentation" onClick={close} />;\n',
    )
    result = _run(repo)
    assert result.returncode == 0, _findings(result)


# --------------------------------------------------------------------------- #
# 3. dismissable by mouse, not by keyboard
# --------------------------------------------------------------------------- #


def test_a_backdrop_in_a_file_with_no_escape_handler_is_reported(repo: Path):
    _write(
        repo,
        "A.tsx",
        'export const A = () => <div role="presentation" onClick={onClose} />;\n',
    )
    result = _run(repo)
    assert result.returncode == 1
    assert "handles Escape" in _findings(result)


@pytest.mark.parametrize(
    "escape",
    [
        'const k = (e) => { if (e.key === "Escape") onClose(); };',
        "const k = (e) => { if (e.keyCode === 27) onClose(); };",
        "useDialogDismiss(onClose);",
    ],
)
def test_any_recognised_escape_handling_clears_the_backdrop_finding(repo: Path, escape: str):
    _write(
        repo,
        "A.tsx",
        f'{escape}\nexport const A = () => <div role="presentation" onClick={{onClose}} />;\n',
    )
    result = _run(repo)
    assert result.returncode == 0, _findings(result)


# --------------------------------------------------------------------------- #
# 4. a fetched list with no empty state
# --------------------------------------------------------------------------- #


def test_a_fetched_list_with_no_empty_state_is_reported(repo: Path):
    _write(
        repo,
        "A.tsx",
        "export const A = () => {\n"
        "  const rows = useQuery(k, f).data ?? [];\n"
        "  return <ul>{rows.map((r) => <li key={r.id}>{r.name}</li>)}</ul>;\n"
        "};\n",
    )
    result = _run(repo)
    assert result.returncode == 1
    assert "handles the empty case" in _findings(result)


@pytest.mark.parametrize(
    "guard",
    [
        "if (rows.length === 0) return <Empty />;",
        "if (!rows.length) return <Empty />;",
        "if (!rows?.length) return null;",
    ],
)
def test_an_empty_case_in_any_spelling_clears_the_finding(repo: Path, guard: str):
    """`?.` is written as often as `.`, and a guard the gate cannot see is a
    finding it invented — the exact false positive this pattern set was widened
    for."""
    _write(
        repo,
        "A.tsx",
        "export const A = () => {\n"
        "  const rows = useQuery(k, f).data ?? [];\n"
        f"  {guard}\n"
        "  return <ul>{rows.map((r) => <li key={r.id}>{r.name}</li>)}</ul>;\n"
        "};\n",
    )
    result = _run(repo)
    assert result.returncode == 0, _findings(result)


def test_a_handwritten_array_is_not_a_missing_empty_state(repo: Path):
    _write(
        repo,
        "A.tsx",
        "const TABS = ['a', 'b'];\n"
        "export const A = () => {\n"
        "  useEffect(() => {}, []);\n"
        "  return <ul>{TABS.map((t) => <li key={t}>{t}</li>)}</ul>;\n"
        "};\n",
    )
    result = _run(repo)
    assert result.returncode == 0, _findings(result)


def test_select_options_are_not_a_missing_empty_state(repo: Path):
    """A `<select>` with no options is a disabled picker, not a blank pane."""
    _write(
        repo,
        "A.tsx",
        "export const A = () => {\n"
        "  const ws = useQuery(k, f).data ?? [];\n"
        "  return <select aria-label='Workspace'>{ws.map((w) => <option key={w.slug}>{w.name}</option>)}</select>;\n"
        "};\n",
    )
    result = _run(repo)
    assert result.returncode == 0, _findings(result)


def test_a_file_with_no_async_source_is_left_alone(repo: Path):
    _write(
        repo,
        "A.tsx",
        "export const A = ({ rows }) => <ul>{rows.map((r) => <li key={r}>{r}</li>)}</ul>;\n",
    )
    assert _run(repo).returncode == 0


# --------------------------------------------------------------------------- #
# waivers and diff scoping
# --------------------------------------------------------------------------- #


def test_a_substantive_waiver_clears_the_finding(repo: Path):
    _write(
        repo,
        "A.tsx",
        "export const A = () => (\n"
        "  // ux-ok: decorative chrome; the real control is the button beside it\n"
        "  <div onClick={go}>Open</div>\n"
        ");\n",
    )
    result = _run(repo)
    assert result.returncode == 0, _findings(result)


def test_a_multi_line_jsx_comment_waiver_applies(repo: Path):
    """In JSX, `{/* … */}` is the only way to comment above an element.

    The prefix-matching walk this replaces read the block's last line — ordinary
    prose, no leading `*` — as code and stopped there, so a waiver written the
    only way JSX allows silently did not apply. A waiver that looks applied and
    is not is exactly the failure a waiver exists to prevent.
    """
    _write(
        repo,
        "A.tsx",
        "export const A = () => (\n"
        "  <>\n"
        "    {/* ux-ok: a pointer-position probe in a developer overlay; there is\n"
        "        no cursor and nothing to report without a pointer, and the\n"
        "        overlay is not part of the product surface. */}\n"
        "    <div onClick={go} />\n"
        "  </>\n"
        ");\n",
    )
    result = _run(repo)
    assert result.returncode == 0, _findings(result)


def test_a_single_line_jsx_comment_waiver_applies(repo: Path):
    _write(
        repo,
        "A.tsx",
        "export const A = () => (\n"
        "  <>\n"
        "    {/* ux-ok: decorative chrome; the real control is beside it */}\n"
        "    <div onClick={go} />\n"
        "  </>\n"
        ");\n",
    )
    result = _run(repo)
    assert result.returncode == 0, _findings(result)


def test_a_comment_block_that_is_not_a_waiver_still_reports(repo: Path):
    """The walk widening must not turn every commented element into a pass."""
    _write(
        repo,
        "A.tsx",
        "export const A = () => (\n"
        "  <>\n"
        "    {/* The row the user clicks to open the detail pane. */}\n"
        "    <div onClick={go} />\n"
        "  </>\n"
        ");\n",
    )
    result = _run(repo)
    assert result.returncode == 1
    assert "cannot reach or fire it" in _findings(result)


def test_a_waiver_with_no_reason_is_itself_the_finding(repo: Path):
    _write(
        repo,
        "A.tsx",
        "export const A = () => (\n  // ux-ok: fine\n  <div onClick={go}>Open</div>\n);\n",
    )
    result = _run(repo)
    assert result.returncode == 1
    assert "no substantive reason" in _findings(result)


def test_an_untouched_violation_does_not_fail_a_change_elsewhere(repo: Path):
    """Diff scoping is half the gate: inherited debt is not this commit's.

    Without this, editing one line of a file with a pre-existing finding would
    fail on the finding, which is how a gate gets bypassed instead of fixed.
    """
    _write(repo, "A.tsx", "export const A = () => <div onClick={go}>Open</div>;\nconst z = 1;\n")
    # Control: uncommitted, the same line is reported. Without this the two
    # passes below could mean "correctly scoped" or "read nothing at all".
    assert _run(repo).returncode == 1

    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "inherit the violation")
    assert _run(repo).returncode == 0  # nothing changed yet

    _write(repo, "A.tsx", "export const A = () => <div onClick={go}>Open</div>;\nconst z = 2;\n")
    result = _run(repo)
    assert result.returncode == 0, _findings(result)

    # And touching the violating line itself makes it this change's problem.
    _write(repo, "A.tsx", "export const A = () => <div onClick={run}>Open</div>;\nconst z = 2;\n")
    assert _run(repo).returncode == 1


def test_tests_are_not_gated(repo: Path):
    _write(repo, "A.test.tsx", "it('x', () => { render(<div onClick={go}>Open</div>); });\n")
    assert _run(repo).returncode == 0


def test_an_untracked_file_is_read_under_worktree_scope(repo: Path):
    """A file an agent just wrote is the least-reviewed code in the change, and
    `git diff` never lists it."""
    _write(repo, "New.tsx", "export const N = () => <div onClick={go}>Open</div>;\n")
    result = _run(repo, scope="worktree")
    assert result.returncode == 1
    assert "New.tsx" in _findings(result)


# --------------------------------------------------------------------------- #
# 5. a field nothing names — `form_input`/`find` locate fields by name
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "field",
    [
        "<input value={v} onChange={set} />",
        '<input placeholder="Search…" value={v} onChange={set} />',
        "<select value={v} onChange={set}>{opts}</select>",
        "<textarea value={v} onChange={set} />",
        '<div role="textbox" contentEditable aria-multiline="true" />',
        '<div role="slider" aria-valuenow={3} tabIndex={0} />',
    ],
)
def test_an_unlabelled_field_is_reported(repo: Path, field: str):
    """A placeholder is a hint that vanishes on the first keystroke, not a name."""
    _write(repo, "A.tsx", f"export const A = () => {field};\n")
    result = _run(repo)
    assert result.returncode == 1
    assert "has no label" in _findings(result)


def test_a_sibling_div_styled_as_a_label_is_not_a_label(repo: Path):
    """The shape that accounts for most of the client: it reads as labelled and is not."""
    _write(
        repo,
        "A.tsx",
        'export const A = () => (\n  <div>\n    <div className="field-label">Type</div>\n'
        "    <select value={v} onChange={set}>{opts}</select>\n  </div>\n);\n",
    )
    assert "has no label" in _findings(_run(repo))


@pytest.mark.parametrize(
    "markup",
    [
        '<input aria-label="Search" />',
        '<input aria-labelledby="h" />',
        '<input title="Search" />',
        "<label>Search <input /></label>",
        '<><label htmlFor="q">Search</label><input id="q" /></>',
        "<><label htmlFor={id}>Search</label><input id={id} /></>",
        "<input {...field} />",
        '<input type="hidden" name="t" />',
        '<input type="submit" value="Save" />',
    ],
)
def test_a_labelled_or_self_named_field_passes(repo: Path, markup: str):
    _write(repo, "A.tsx", f"export const A = () => {markup};\n")
    result = _run(repo)
    assert result.returncode == 0, _findings(result)


def test_a_label_pointing_elsewhere_does_not_name_the_field(repo: Path):
    _write(
        repo,
        "A.tsx",
        'export const A = () => <><label htmlFor="a">A</label><input id="b" /></>;\n',
    )
    assert "has no label" in _findings(_run(repo))


def test_a_content_editable_region_needs_a_role_as_well_as_a_name(repo: Path):
    _write(repo, "A.tsx", 'export const A = () => <div contentEditable aria-label="Notes" />;\n')
    result = _run(repo)
    assert result.returncode == 1
    findings = _findings(result)
    assert "has no role" in findings
    assert "has no label" not in findings


def test_content_editable_false_is_not_a_field(repo: Path):
    _write(repo, "A.tsx", "export const A = () => <div contentEditable={false}>x</div>;\n")
    assert _run(repo).returncode == 0


# --------------------------------------------------------------------------- #
# 6. a widget that does not say what state it is in
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("role", "attr"),
    [
        ("tab", "aria-selected"),
        ("option", "aria-selected"),
        ("switch", "aria-checked"),
        ("checkbox", "aria-checked"),
        ("radio", "aria-checked"),
        ("menuitemradio", "aria-checked"),
        ("combobox", "aria-expanded"),
    ],
)
def test_a_widget_role_without_its_state_is_reported(repo: Path, role: str, attr: str):
    _write(
        repo, "A.tsx", f'export const A = () => <button role="{role}" aria-label="X">X</button>;\n'
    )
    result = _run(repo)
    assert result.returncode == 1
    assert f'role="{role}" without {attr}' in _findings(result)

    _write(
        repo,
        "A.tsx",
        f'export const A = () => <button role="{role}" aria-label="X" {attr}={{on}}>X</button>;\n',
    )
    result = _run(repo)
    assert result.returncode == 0, _findings(result)


def test_a_native_checkbox_reports_its_own_state(repo: Path):
    _write(
        repo,
        "A.tsx",
        'export const A = () => <label><input type="checkbox" role="switch" checked={on} />'
        " On</label>;\n",
    )
    result = _run(repo)
    assert result.returncode == 0, _findings(result)


def test_a_popup_button_must_say_whether_it_is_open(repo: Path):
    _write(repo, "A.tsx", 'export const A = () => <button aria-haspopup="menu">More</button>;\n')
    assert "aria-haspopup without aria-expanded" in _findings(_run(repo))


@pytest.mark.parametrize(
    "handler",
    ["() => setOpen(!open)", "() => setOpen((o) => !o)", "toggle"],
)
def test_a_button_that_flips_state_must_expose_it(repo: Path, handler: str):
    """`toggle` is read through to its local definition."""
    body = (
        "const toggle = () => setOpen((o) => { return !o; });\n"
        f"export const A = () => <button onClick={{{handler}}}>Details</button>;\n"
    )
    _write(repo, "A.tsx", body)
    result = _run(repo)
    assert result.returncode == 1
    assert "flips state on click" in _findings(result)

    _write(repo, "A.tsx", body.replace("<button ", "<button aria-expanded={open} "))
    result = _run(repo)
    assert result.returncode == 0, _findings(result)


def test_a_setter_that_does_not_negate_is_not_a_toggle(repo: Path):
    _write(
        repo,
        "A.tsx",
        "export const A = () => <button onClick={() => setOpen(true)}>Open</button>;\n",
    )
    assert _run(repo).returncode == 0


# --------------------------------------------------------------------------- #
# 7. an action behind a hover, or behind a drag
# --------------------------------------------------------------------------- #


def test_hover_without_focus_is_reported(repo: Path):
    _write(
        repo,
        "A.tsx",
        "export const A = () => <nav onMouseEnter={show} onMouseLeave={hide}>x</nav>;\n",
    )
    result = _run(repo)
    assert result.returncode == 1
    assert "with no onFocus" in _findings(result)

    _write(
        repo,
        "A.tsx",
        "export const A = () => <nav onMouseEnter={show} onFocus={show} onBlur={hide}>x</nav>;\n",
    )
    result = _run(repo)
    assert result.returncode == 0, _findings(result)


def test_hovering_a_listbox_row_is_not_the_only_way_to_it(repo: Path):
    """The listbox moves the highlight from the keyboard already."""
    _write(
        repo,
        "A.tsx",
        'export const A = () => <button role="option" aria-selected={a} '
        "onMouseEnter={hl}>x</button>;\n",
    )
    result = _run(repo)
    assert result.returncode == 0, _findings(result)


@pytest.mark.parametrize("attrs", ["draggable", "draggable={canDrag}", "onDragStart={grab}"])
def test_a_drag_source_without_a_key_handler_is_reported(repo: Path, attrs: str):
    _write(repo, "A.tsx", f"export const A = () => <li {attrs}>x</li>;\n")
    result = _run(repo)
    assert result.returncode == 1
    assert "can be dragged but has no key handler" in _findings(result)

    _write(repo, "A.tsx", f"export const A = () => <li {attrs} onKeyDown={{move}}>x</li>;\n")
    result = _run(repo)
    assert result.returncode == 0, _findings(result)


def test_draggable_false_is_not_a_drag_source(repo: Path):
    _write(repo, "A.tsx", 'export const A = () => <img alt="" draggable={false} />;\n')
    assert _run(repo).returncode == 0


def test_a_drop_zone_with_no_other_way_in_is_reported(repo: Path):
    _write(repo, "A.tsx", "export const A = () => <div onDrop={take}>Drop files</div>;\n")
    result = _run(repo)
    assert result.returncode == 1
    assert "onDrop> takes dropped data" in _findings(result)


@pytest.mark.parametrize(
    "alternative",
    [
        '<input type="file" aria-label="Attach" />',
        "<li draggable onDragStart={g} onKeyDown={m}>r</li>",
    ],
)
def test_a_drop_zone_with_another_way_in_passes(repo: Path, alternative: str):
    """A file input is the other way in; a drag source in the file makes it an internal move."""
    _write(
        repo,
        "A.tsx",
        f"export const A = () => <div onDrop={{take}}>{alternative}</div>;\n",
    )
    result = _run(repo)
    assert result.returncode == 0, _findings(result)


# --------------------------------------------------------------------------- #
# 8. a canvas you operate
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("handler", ["onClick", "onPointerDown", "onWheel", "onMouseMove"])
def test_a_canvas_with_input_handlers_is_reported(repo: Path, handler: str):
    _write(repo, "A.tsx", f"export const A = () => <canvas {handler}={{go}} />;\n")
    result = _run(repo)
    assert result.returncode == 1
    assert "<canvas> takes input" in _findings(result)


def test_a_canvas_wired_through_add_event_listener_is_reported(repo: Path):
    _write(
        repo,
        "A.tsx",
        "export function A() {\n"
        "  useEffect(() => { ref.current.addEventListener('pointerdown', go); }, []);\n"
        "  return <canvas ref={ref} />;\n"
        "}\n",
    )
    assert "addEventListener on its ref" in _findings(_run(repo))


def test_a_canvas_that_only_draws_passes(repo: Path):
    _write(repo, "A.tsx", 'export const A = () => <canvas ref={ref} className="grid" />;\n')
    assert _run(repo).returncode == 0


def test_a_canvas_waiver_naming_its_dom_controls_clears_it(repo: Path):
    _write(
        repo,
        "A.tsx",
        "export const A = () => (\n"
        "  // ux-ok: every node is also a button in the NodeList panel beside it\n"
        "  <canvas onClick={go} />\n"
        ");\n",
    )
    result = _run(repo)
    assert result.returncode == 0, _findings(result)
