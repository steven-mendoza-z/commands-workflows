import re
import subprocess

import click
from . import __version__
from .messages import error_message, info_message, message, success_message, title_message, warning_message
from .storage import load_workflows, save_workflows

PLACEHOLDER_RE = re.compile(r"\{\{\s*(?:<([^>]+)>|([A-Za-z0-9_]+)(?:=([^}]*))?)\s*\}\}")


def _collect_placeholders(commands):
    seen = []
    defaults = {}
    for cmd in commands:
        for m in PLACEHOLDER_RE.finditer(cmd):
            if m.group(1):
                name = m.group(1)
                default = None
            else:
                name = m.group(2)
                default = m.group(3)
            if name is None:
                continue
            if name not in seen:
                seen.append(name)
            if default is not None:
                defaults[name] = default
    return seen, defaults


def _replace_placeholders(cmd, mapping, defaults=None):
    defaults = defaults or {}

    def repl(m):
        if m.group(1):
            key = m.group(1)
        else:
            key = m.group(2)
        if key in mapping:
            return mapping[key]
        if key in defaults:
            return defaults[key]
        return m.group(0)

    return PLACEHOLDER_RE.sub(repl, cmd)


@click.group()
@click.version_option(__version__, prog_name="cmdw")
def cli():
    """Manage named command workflows."""


@cli.command("list")
def list_workflows():
    """List defined workflows."""
    data = load_workflows()
    if not data:
        warning_message("No workflows defined.")
        return
    title_message("Workflows")
    for name, info in data.items():
        desc = info.get("desc", "")
        message(f"- {name}: {desc}")


@cli.command("show")
@click.argument("name")
def show_workflow(name):
    """Show a workflow's commands and description."""
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
    placeholders, defaults = _collect_placeholders(commands)
    if placeholders:
        title_message("Placeholders")
        for placeholder in placeholders:
            default = defaults.get(placeholder)
            if default is not None:
                message(f"  {placeholder}: default={default}")
            else:
                message(f"  {placeholder}: required")


@cli.command("create")
@click.argument("name")
@click.argument("commands", nargs=-1)
@click.option("--desc", default="", help="Workflow description.")
def create_workflow(name, commands, desc):
    """Create a new named workflow."""
    data = load_workflows()
    if name in data:
        error_message("Workflow already exists. Use a different name or delete first.")
    data[name] = {"desc": desc or "", "commands": list(commands)}
    save_workflows(data)
    success_message(f"Workflow '{name}' created successfully.")


@cli.command("edit")
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


@cli.command("delete")
@click.argument("name")
def delete_workflow(name):
    """Delete an existing workflow."""
    data = load_workflows()
    if name not in data:
        error_message("Workflow not found.", exit_code=2)
    del data[name]
    save_workflows(data)
    success_message(f"Workflow '{name}' deleted successfully.")


@cli.command("run")
@click.argument("name")
@click.argument("args", nargs=-1)
@click.option("--set", "sets", multiple=True,
              help="Set placeholder by name: name=value (repeat). Overrides positional values.")
def run_workflow(name, args, sets):
    """Run a workflow, passing values by position or name."""
    data = load_workflows()
    wf = data.get(name)
    if not wf:
        error_message("Workflow not found.", exit_code=2)

    commands = wf.get("commands", [])
    placeholders, defaults = _collect_placeholders(commands)

    mapping = {}
    for pair in sets:
        if "=" in pair:
            k, v = pair.split("=", 1)
            mapping[k] = v

    for idx, placeholder in enumerate(placeholders):
        if placeholder in mapping:
            continue
        if idx < len(args):
            mapping[placeholder] = args[idx]

    missing = [p for p in placeholders if p not in mapping and p not in defaults]
    if missing:
        error_message(f"Not enough arguments for workflow. Missing values for: {missing}", exit_code=2)

    title_message(f"Running workflow: {name}")
    if placeholders:
        message("Resolved placeholders:")
        for placeholder in placeholders:
            value = mapping.get(placeholder, defaults.get(placeholder))
            message(f"  - {placeholder} = {value}")

    for idx, cmd in enumerate(commands, start=1):
        final_cmd = _replace_placeholders(cmd, mapping, defaults)
        info_message(final_cmd, left=f"Command {idx}")
        try:
            subprocess.run(final_cmd, shell=True, check=True)
        except subprocess.CalledProcessError as e:
            error_message(f"Command failed with exit {e.returncode}", exit_code=e.returncode)
    success_message(f"Workflow '{name}' completed successfully.")


def main(argv=None):
    cli.main(args=argv, standalone_mode=True, color=True)


if __name__ == "__main__":
    main()
