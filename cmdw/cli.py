import ast
import json
import re
import subprocess
from pathlib import Path
from urllib.request import urlopen

import click
from . import __version__
from .messages import error_message, info_message, message, success_message, title_message, warning_message
from .storage import load_globals, load_workflows, save_globals, save_workflows

PLACEHOLDER_RE = re.compile(r"\{\{\s*(?:<([^>]+)>|([A-Za-z0-9_.\[\]]+?)(?::(\d+))?\s*(?:=\s*([^}]*))?)\s*\}\}")
INLINE_GLOBAL_RE = re.compile(r"(?<![\w.])(globals?\.[A-Za-z0-9_.'\"\[\]-]+)")
PERCENT_TOKEN_RE = re.compile(r"%\s*((?:globals?|sys|scripts|mssh)\.[^%]+?)\s*%")


class CategorizedGroup(click.Group):
    def __init__(self, *args, categories=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.categories = categories or []
        self.primary_commands = set()
        self.aliases = {}

    def format_commands(self, ctx, formatter):
        commands = []
        for name in self.list_commands(ctx):
            cmd = self.get_command(ctx, name)
            if cmd is None or cmd.hidden:
                continue
            if name not in self.primary_commands:
                continue
            commands.append((name, cmd))

        categorized = {}
        for name, cmd in commands:
            category = getattr(cmd, "category", "Other")
            categorized.setdefault(category, []).append((name, cmd))

        ordered_categories = [c for c in self.categories if c in categorized]
        ordered_categories += [c for c in categorized if c not in ordered_categories]

        for category in ordered_categories:
            entries = categorized[category]
            if not entries:
                continue
            with formatter.section(category):
                rows = []
                for name, cmd in entries:
                    aliases = self.aliases.get(name, [])
                    if aliases:
                        display_name = f"{name}, {', '.join(aliases)}"
                    else:
                        display_name = name
                    rows.append((display_name, cmd.get_short_help_str()))
                formatter.write_dl(rows)


def _collect_placeholders(commands):
    seen = []
    defaults = {}
    indices = {}
    for cmd in commands:
        for m in PLACEHOLDER_RE.finditer(cmd):
            if m.group(1):
                name = m.group(1)
                idx = None
                default = None
            else:
                name = m.group(2)
                idx = m.group(3)
                default = m.group(4)
            if name is None:
                continue
            name = name.strip()
            if default is not None:
                default = default.strip()
            if name not in seen:
                seen.append(name)
            if default is not None:
                defaults[name] = default
            if idx is not None:
                try:
                    indices[name] = int(idx)
                except Exception:
                    indices[name] = None
    return seen, defaults, indices


def _order_placeholders(placeholders, defaults, indices):
    """Return placeholders ordered by explicit indices if any present, otherwise required-first.

    If any placeholder has an explicit index, placeholders are sorted by that index (missing index -> large).
    Otherwise, preserve previous behavior: placeholders without defaults first, then with defaults.
    """
    if any(v is not None for v in indices.values()):
        INF = 10 ** 9
        name_pos = [(name, pos) for pos, name in enumerate(placeholders)]
        def sort_key(np):
            name, pos = np
            idx = indices.get(name)
            if idx is None:
                idx = INF
            return (idx, pos)
        ordered = [name for name, _ in sorted(name_pos, key=sort_key)]
        return ordered
    # fallback: required-first then defaults
    return [p for p in placeholders if p not in defaults] + [p for p in placeholders if p in defaults]


def _parse_value(value):
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return ""
    if text.startswith(("{", "[", '"', "'")) or ":" in text:
        try:
            if text.startswith(("{", "[")):
                return json.loads(text)
        except Exception:
            pass
        try:
            normalized = re.sub(r'(?<=\{|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s*:', r'"\1":', text)
            return ast.literal_eval(normalized)
        except Exception:
            try:
                return ast.literal_eval(text)
            except Exception:
                return value
    return value


def _split_path(path):
    parts = []
    current = []
    quote = None
    escape = False

    for ch in str(path):
        if escape:
            current.append(ch)
            escape = False
            continue
        if quote is not None:
            if ch == "\\":
                escape = True
                continue
            if ch == quote:
                quote = None
                continue
            current.append(ch)
            continue
        if ch in ("'", '"'):
            quote = ch
            continue
        if ch == ".":
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
            continue
        current.append(ch)

    part = "".join(current).strip()
    if part:
        parts.append(part)
    return parts


def _walk_nested(current, parts, idx):
    if idx >= len(parts):
        return current
    if not isinstance(current, dict):
        return None

    # Support keys that contain dots by trying longest chunk first.
    for end in range(len(parts), idx, -1):
        key = ".".join(parts[idx:end])
        if key not in current:
            continue
        value = current[key]
        if end == len(parts):
            return value
        nested = _walk_nested(value, parts, end)
        if nested is not None:
            return nested
    return None


def _get_nested(data, path):
    parts = _split_path(path)
    if not parts:
        return None
    return _walk_nested(data, parts, 0)


def _set_nested(data, path, value):
    parts = path.split(".")
    current = data
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value


def _delete_nested(data, path):
    parts = path.split(".")
    current = data
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            raise KeyError(path)
        current = current[part]
    if parts[-1] not in current:
        raise KeyError(path)
    del current[parts[-1]]


def _resolve_global_reference(value, global_vars):
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.startswith("global.") or text.startswith("globals."):
        path = text.split(".", 1)[1]
        path = _replace_placeholders(path, {}, {}, global_vars).strip()
        return _get_nested(global_vars, path)
    if text in global_vars:
        return global_vars[text]
    if "." in text:
        nested = _get_nested(global_vars, text)
        if nested is not None:
            return nested
    return None


def _read_sys_value(value):
    path = value.split(".", 1)[1]
    if path.startswith("http://") or path.startswith("https://"):
        with urlopen(path) as response:
            return response.read().decode("utf-8")

    if path.startswith("ssh."):
        ssh_path = path.split(".", 1)[1]
        file_path = Path.home() / ".ssh" / ssh_path
    elif path.startswith("mssh."):
        mssh_path = path.split(".", 1)[1]
        file_path = Path.home() / ".mssh" / mssh_path
    else:
        file_path = Path(path).expanduser()

    with file_path.open("r", encoding="utf-8") as f:
        return f.read()


def _read_scripts_path(value):
    script_path = value.split(".", 1)[1]
    file_path = Path.home() / ".cmdw" / "scripts" / script_path
    return str(file_path)


def _read_mssh_value(value):
    mssh_path = value.split(".", 1)[1]
    file_path = Path.home() / ".mssh" / "keys" / mssh_path
    with file_path.open("r", encoding="utf-8") as f:
        return f.read()


def _resolve_default_value(value, global_vars=None, defaults=None, mapping=None):
    if value is None:
        return value
    global_vars = global_vars or {}
    defaults = defaults or {}
    mapping = mapping or {}

    if isinstance(value, str):
        resolved = _replace_placeholders(value, mapping, defaults, global_vars)
        global_ref = _resolve_global_reference(resolved, global_vars)
        if global_ref is not None:
            return global_ref
        if resolved.startswith("sys."):
            try:
                return _read_sys_value(resolved)
            except Exception:
                return resolved
        if resolved.startswith("scripts."):
            try:
                return _read_scripts_path(resolved)
            except Exception:
                return resolved
        if resolved.startswith("mssh."):
            try:
                return _read_mssh_value(resolved)
            except Exception:
                return resolved
        return resolved
    return value


def _parse_list_value(value, global_vars=None, defaults=None, mapping=None):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        parsed = _resolve_default_value(value, global_vars, defaults, mapping)
        if isinstance(parsed, list):
            return parsed
        parsed_str = str(parsed).strip()
        parsed_obj = _parse_value(parsed_str)
        if isinstance(parsed_obj, list):
            return [str(item) for item in parsed_obj]
        if parsed_str.endswith("[]"):
            alias = parsed_str[:-2]
            alias_val = _resolve_global_reference(alias, global_vars)
            if isinstance(alias_val, list):
                return alias_val
        if parsed_str.startswith("[") and parsed_str.endswith("]"):
            try:
                return json.loads(parsed_str)
            except Exception:
                pass
        if "," in parsed_str:
            return [item.strip() for item in parsed_str.split(",") if item.strip()]
        return [parsed_str]
    return [value]


def _expand_command(cmd, mapping, defaults, global_vars):
    list_placeholders = [p for p in PLACEHOLDER_RE.findall(cmd) if p[1] and p[1].endswith("[]")]
    if not list_placeholders:
        return [_replace_placeholders(cmd, mapping, defaults, global_vars)]

    placeholder_names = [p[1] for p in list_placeholders]
    item_lists = []
    for name in placeholder_names:
        actual_name = name
        if actual_name in mapping:
            item_lists.append(_parse_list_value(mapping[actual_name], global_vars, defaults, mapping))
        elif actual_name in defaults:
            item_lists.append(_parse_list_value(defaults[actual_name], global_vars, defaults, mapping))
        else:
            list_key = actual_name[:-2]
            if list_key.startswith("global."):
                global_name = list_key.split(".", 1)[1]
                nested = _get_nested(global_vars, global_name)
                if nested is not None:
                    item_lists.append(_parse_list_value(nested, global_vars, defaults, mapping))
                    continue
            elif list_key in global_vars:
                item_lists.append(_parse_list_value(global_vars[list_key], global_vars, defaults, mapping))
                continue
            item_lists.append([""])

    commands = []
    from itertools import product
    for combo in product(*item_lists):
        combo_mapping = mapping.copy()
        for name, value in zip(placeholder_names, combo):
            combo_mapping[name] = str(value)
        commands.append(_replace_placeholders(cmd, combo_mapping, defaults, global_vars))
    return commands


def _replace_placeholders(cmd, mapping, defaults=None, global_vars=None):
    defaults = defaults or {}
    global_vars = global_vars or {}

    def repl(m):
        if m.group(1):
            key = m.group(1)
        else:
            key = m.group(2)
        if key in mapping:
            return str(mapping[key])
        if key in defaults:
            default_value = defaults[key]
            return str(_resolve_default_value(default_value, global_vars, defaults, mapping))
        if key.startswith("global.") or key.startswith("globals."):
            value = _resolve_global_reference(key, global_vars)
            return str(value) if value is not None else m.group(0)
        if key.startswith("sys."):
            try:
                return _read_sys_value(key)
            except Exception:
                return m.group(0)
        if key.startswith("scripts."):
            try:
                return _read_scripts_path(key)
            except Exception:
                return m.group(0)
        if key.startswith("mssh."):
            try:
                return _read_mssh_value(key)
            except Exception:
                return m.group(0)
        return m.group(0)

    replaced = PLACEHOLDER_RE.sub(repl, cmd)

    # Resolve explicit %token.path% wrappers after all other substitutions.
    def resolve_percent_token(match):
        token = match.group(1).strip()
        if token.startswith("global.") or token.startswith("globals."):
            value = _resolve_global_reference(token, global_vars)
            return str(value) if value is not None else match.group(0)
        if token.startswith("sys."):
            try:
                return _read_sys_value(token)
            except Exception:
                return match.group(0)
        if token.startswith("scripts."):
            try:
                return _read_scripts_path(token)
            except Exception:
                return match.group(0)
        if token.startswith("mssh."):
            try:
                return _read_mssh_value(token)
            except Exception:
                return match.group(0)
        return match.group(0)

    replaced = PERCENT_TOKEN_RE.sub(resolve_percent_token, replaced)

    # Resolve inline global references after arg/default placeholders are expanded.
    def resolve_inline_global(match):
        token = match.group(1)
        value = _resolve_global_reference(token, global_vars)
        return str(value) if value is not None else token

    return INLINE_GLOBAL_RE.sub(resolve_inline_global, replaced)


def _resolve_global_placeholders(placeholders, mapping, global_vars, defaults=None):
    defaults = defaults or {}
    for placeholder in placeholders:
        if placeholder in mapping:
            continue
        if placeholder.startswith("global.") or placeholder.startswith("globals."):
            resolved = _resolve_global_reference(placeholder, global_vars)
            if resolved is not None:
                mapping[placeholder] = resolved
        elif placeholder.startswith("sys."):
            try:
                mapping[placeholder] = _read_sys_value(placeholder)
            except Exception:
                pass
        elif placeholder.startswith("scripts."):
            try:
                mapping[placeholder] = _read_scripts_path(placeholder)
            except Exception:
                pass
        elif placeholder.startswith("mssh."):
            try:
                mapping[placeholder] = _read_mssh_value(placeholder)
            except Exception:
                pass
    return mapping


@click.group(cls=CategorizedGroup, categories=["Workflows", "Variables"])
@click.version_option(__version__, prog_name="cmdw")
def cli():
    """Manage named command workflows."""


# Register primary command names for help display
cli.primary_commands.update({
    "list", "show", "create", "edit", "delete", "run", "add-var", "edit-var", "delete-var", "list-var"
})


@cli.command("list-workflow")
def list_workflows():
    """List all workflows."""
    data = load_workflows()
    if not data:
        warning_message("No workflows defined.")
        return
    title_message("Workflows")
    for name, info in data.items():
        desc = info.get("desc", "")
        message(f"- {name}: {desc}")


list_workflows.category = "Workflows"

@cli.command("show-workflow")
@click.argument("name")
def show_workflow(name):
    """Show workflow details and commands."""
    data = load_workflows()
    wf = data.get(name)
    if not wf:
        error_message("Workflow not found.", exit_code=2)
    title_message(f"Workflow: {name}")
    desc = wf.get('desc', '')
    if desc:
        info_message(desc, left="Description")
    else:
        info_message("No description provided.", left="Description")
    commands = wf.get("commands", [])
    title_message("Commands")
    if commands:
        for idx, c in enumerate(commands, start=1):
            message(f"  {idx}. {c}")
    else:
        warning_message("No commands defined.")
    placeholders, defaults, indices = _collect_placeholders(commands)
    ordered_placeholders = _order_placeholders(placeholders, defaults, indices)
    if ordered_placeholders:
        title_message("Placeholders")
        for placeholder in ordered_placeholders:
            default = defaults.get(placeholder)
            if default is not None:
                message(f"  {placeholder}: default={default}")
            else:
                message(f"  {placeholder}: required")


show_workflow.category = "Workflows"

@cli.command("create-workflow")
@click.argument("name")
@click.argument("commands", nargs=-1)
@click.option("--desc", default="", help="Workflow description.")
def create_workflow(name, commands, desc):
    """Create a new workflow."""
    data = load_workflows()
    if name in data:
        error_message("Workflow already exists. Use a different name or delete first.")
    data[name] = {"desc": desc or "", "commands": list(commands)}
    save_workflows(data)
    success_message(f"Workflow '{name}' created successfully.")


create_workflow.category = "Workflows"

@cli.command("edit-workflow")
@click.argument("name")
@click.option("--new-name", help="Rename the workflow.")
@click.option("--desc", help="Update workflow description.")
@click.option("--command", "commands", multiple=True,
              help="Replace workflow commands. Repeat for each command.")
def edit_workflow(name, new_name, desc, commands):
    """Edit an existing workflow."""
    data = load_workflows()
    if name not in data:
        error_message("Workflow not found.", exit_code=2)

    wf = data[name]
    updated = False

    if desc is not None:
        wf["desc"] = desc
        updated = True
        info_message("Description updated.")

    if commands:
        wf["commands"] = list(commands)
        updated = True
        info_message(f"Commands replaced with {len(commands)} command(s).")

    if new_name:
        if new_name in data and new_name != name:
            error_message("Cannot rename workflow: target name already exists.")
        data[new_name] = wf
        if new_name != name:
            del data[name]
        updated = True
        info_message(f"Renamed workflow to '{new_name}'.")

    if not updated:
        error_message("No changes provided. Use --new-name, --desc, or --command.")

    save_workflows(data)
    success_message(f"Workflow '{new_name or name}' updated successfully.")


edit_workflow.category = "Workflows"

@cli.command("delete-workflow")
@click.argument("name")
def delete_workflow(name):
    """Delete a workflow."""
    data = load_workflows()
    if name not in data:
        error_message("Workflow not found.", exit_code=2)
    del data[name]
    save_workflows(data)
    success_message(f"Workflow '{name}' deleted successfully.")


delete_workflow.category = "Workflows"

@cli.command("add-var")
@click.argument("name")
@click.argument("value")
def add_var(name, value):
    """Add a new global variable or nested subvariable."""
    variables = load_globals()
    parsed_value = _parse_value(value)
    if "." in name:
        try:
            _set_nested(variables, name, parsed_value)
        except Exception:
            error_message(f"Cannot set nested variable '{name}'.")
    else:
        if name in variables:
            error_message(f"Global variable '{name}' already exists.")
        variables[name] = parsed_value
    save_globals(variables)
    success_message(f"Global variable '{name}' added successfully.")


add_var.category = "Variables"

@cli.command("edit-var")
@click.argument("name")
@click.argument("value")
def edit_var(name, value):
    """Edit an existing global variable or nested subvariable."""
    variables = load_globals()
    parsed_value = _parse_value(value)
    if "." in name:
        if _get_nested(variables, name) is None:
            error_message(f"Global variable '{name}' not found.", exit_code=2)
        try:
            _set_nested(variables, name, parsed_value)
        except Exception:
            error_message(f"Cannot update nested variable '{name}'.", exit_code=2)
    else:
        if name not in variables:
            error_message(f"Global variable '{name}' not found.", exit_code=2)
        variables[name] = parsed_value
    save_globals(variables)
    success_message(f"Global variable '{name}' updated successfully.")


edit_var.category = "Variables"

@cli.command("delete-var")
@click.argument("name")
def delete_var(name):
    """Delete a global variable or nested subvariable."""
    variables = load_globals()
    try:
        if "." in name:
            _delete_nested(variables, name)
        else:
            if name not in variables:
                raise KeyError(name)
            del variables[name]
    except KeyError:
        error_message(f"Global variable '{name}' not found.", exit_code=2)
    save_globals(variables)
    success_message(f"Global variable '{name}' deleted successfully.")


delete_var.category = "Variables"

@cli.command("list-var")
def list_var():
    """List all global variables."""
    variables = load_globals()
    if not variables:
        warning_message("No global variables defined.")
        return
    title_message("Global Variables")
    for name, value in variables.items():
        message(f"- {name}: {value}")


list_var.category = "Variables"

@cli.command("run-workflow")
@click.argument("name")
@click.argument("args", nargs=-1)
@click.option("--set", "sets", multiple=True,
              help="Set placeholder by name: name=value (repeat). Overrides positional values.")
@click.option("--mute", is_flag=True,
              help="Show only commands and final success/error message.")
@click.option("--continue-on-error", "continue_on_error", is_flag=True,
              help="Continue executing remaining commands even if one command fails.")
def run_workflow(name, args, sets, mute, continue_on_error):
    """Run a workflow."""
    data = load_workflows()
    wf = data.get(name)
    if not wf:
        error_message("Workflow not found.", exit_code=2)

    commands = wf.get("commands", [])
    placeholders, defaults, indices = _collect_placeholders(commands)
    global_vars = load_globals()

    mapping = {}
    for pair in sets:
        if "=" in pair:
            k, v = pair.split("=", 1)
            mapping[k] = v

    ordered_placeholders = _order_placeholders(placeholders, defaults, indices)
    for idx, placeholder in enumerate(ordered_placeholders):
        if placeholder in mapping:
            continue
        if idx < len(args):
            mapping[placeholder] = args[idx]

    _resolve_global_placeholders(placeholders, mapping, global_vars, defaults)

    missing = [p for p in placeholders if p not in mapping and p not in defaults]
    if missing:
        error_message(f"Not enough arguments for workflow. Missing values for: {missing}", exit_code=2)

    if not mute:
        title_message(f"Running workflow: {name}")
        if ordered_placeholders:
            message("Resolved placeholders:")
            for placeholder in ordered_placeholders:
                value = mapping.get(placeholder, defaults.get(placeholder))
                value = _resolve_default_value(value, global_vars, defaults, mapping)
                message(f"  - {placeholder} = {value}")

    run_commands = []
    for cmd in commands:
        run_commands.extend(_expand_command(cmd, mapping, defaults, global_vars))

    failures = []
    for idx, final_cmd in enumerate(run_commands, start=1):
        if mute:
            message(f"Command {idx}: {final_cmd}")
        else:
            info_message(final_cmd, left=f"Command {idx}")
        try:
            if mute:
                subprocess.run(
                    final_cmd,
                    shell=True,
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.run(final_cmd, shell=True, check=True)
        except subprocess.CalledProcessError as e:
            failures.append((idx, e.returncode))
            if not continue_on_error:
                message("")
                error_message(f"Command failed with exit {e.returncode}", exit_code=e.returncode)
            if not mute:
                warning_message(
                    f"Command {idx} failed with exit {e.returncode}. Continuing (--continue-on-error)."
                )

    if failures:
        message("")
        if len(failures) == 1:
            idx, code = failures[0]
            error_message(
                f"Workflow '{name}' completed with errors: command {idx} failed with exit {code}",
                exit_code=code,
            )
        first_code = failures[0][1]
        failed_commands = ", ".join(str(i) for i, _ in failures)
        error_message(
            f"Workflow '{name}' completed with errors: {len(failures)} command(s) failed ({failed_commands})",
            exit_code=first_code,
        )

    message("")
    success_message(f"Workflow '{name}' completed successfully.")


run_workflow.category = "Workflows"

# Register command aliases
cli.add_command(list_workflows, "list")
cli.add_command(show_workflow, "show")
cli.add_command(create_workflow, "create")
cli.add_command(edit_workflow, "edit")
cli.add_command(delete_workflow, "delete")
cli.add_command(run_workflow, "run")

# Register aliases mapping for help display
cli.aliases["list"] = ["list-workflow"]
cli.aliases["show"] = ["show-workflow"]
cli.aliases["create"] = ["create-workflow"]
cli.aliases["edit"] = ["edit-workflow"]
cli.aliases["delete"] = ["delete-workflow"]
cli.aliases["run"] = ["run-workflow"]


def main(argv=None):

    cli.main(args=argv, standalone_mode=True, color=True)


if __name__ == "__main__":
    main()
