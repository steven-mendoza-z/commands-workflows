# commands-workflows

`commands-workflows` provides `cmdw`, a command workflow manager for developers
who want to turn recurring terminal routines into reliable, reusable workflows.

Use it to standardize project setup, testing, releases, deployments, API calls,
maintenance tasks, and other command sequences that need structure without
requiring a full automation platform. Workflows can include multiple commands,
reusable placeholders, default values, global variables, file references, and
list expansion.

## Installation

```bash
pip install commands-workflows
```

After installation, run:

```bash
cmdw --help
```

## Quick Start

Create a workflow:

```bash
cmdw create deploy \
  "git checkout {{branch=main}}" \
  "git pull" \
  "npm run build" \
  "npm run deploy -- --target {{target}}" \
  --desc "Build and deploy a project"
```

Run it with a required value:

```bash
cmdw run deploy production
```

Override a value by name:

```bash
cmdw run deploy --set target=staging --set branch=release
```

Show the saved workflow and the placeholders it expects:

```bash
cmdw show deploy
```

## Core Concepts

### Workflows

A workflow is a named list of shell commands. Commands run in the order they
were saved.

```bash
cmdw create test "python -m unittest" --desc "Run the test suite"
cmdw run test
```

Useful commands:

```bash
cmdw list
cmdw show test
cmdw edit test --desc "Run all unit tests"
cmdw delete test
```

You can also use the long command names:

```bash
cmdw list-workflow
cmdw show-workflow test
cmdw run-workflow test
```

### Placeholders

Use placeholders inside commands with `{{name}}`.

```bash
cmdw create greet "echo Hello {{name}}"
cmdw run greet Steven
```

Placeholders can have default values:

```bash
cmdw create branch "git checkout {{branch=main}}"
cmdw run branch
cmdw run branch develop
```

When a workflow has multiple placeholders, positional values are assigned in the
order the placeholders appear:

```bash
cmdw create release "git tag v{{version}}" "git push origin v{{version}}"
cmdw run release 1.2.0
```

Use an explicit index when you want to control the positional order:

```bash
cmdw create api-call \
  "curl https://api.example.com/{{resource:1}}/{{id:2}}"

cmdw run api-call users 42
```

Named values passed with `--set name=value` override positional values:

```bash
cmdw run api-call --set resource=users --set id=42
```

### Global Variables

Global variables let you keep shared values outside a single workflow. They are
stored locally and can be reused by any workflow.

```bash
cmdw add-var account_id 123456
cmdw add-var env.production_url https://example.com
cmdw list-var
```

Reference them in commands:

```bash
cmdw create open-prod "curl {{global.env.production_url}}/health"
cmdw run open-prod
```

Nested variables are supported with dot notation:

```bash
cmdw edit-var env.production_url https://api.example.com
cmdw delete-var env.production_url
```

If your `globals.json` contains a key with dots, quote that segment inside the
reference:

```bash
cmdw create dns "curl zones/global.cloudflare.zones.'{{domain}}'/dns_records"
cmdw run dns example.com
```

You can also wrap global references in percent signs when that makes a command
easier to read:

```bash
cmdw create dns "curl zones/%global.cloudflare.zones.'{{domain}}'%/dns_records"
```

### File and Script References

`cmdw` can resolve local file and helper script references at run time.

Use `sys.` to read a file:

```bash
cmdw create print-config "cat {{sys./path/to/config.json}}"
```

Use `sys.ssh.` to read from `~/.ssh`:

```bash
cmdw create show-key "cat {{sys.ssh.id_rsa.pub}}"
```

Use `scripts.` to reference a file in `~/.cmdw/scripts`:

```bash
cmdw create fetch "python {{scripts.fetch_data.py}}"
```

Use `%...%` syntax when a direct token is easier to place inside a command:

```bash
cmdw create fetch "python %scripts.fetch_data.py%"
```

### List Expansion

A placeholder ending in `[]` expands a command once per value.

```bash
cmdw create ping-all "ping -c 1 {{host[]}}"
cmdw run ping-all --set "host[]=api.example.com,db.example.com"
```

List values can also come from global variables when the list reference is used
as a default value:

```bash
cmdw add-var hosts "['api.example.com', 'db.example.com']"
cmdw create ping-hosts "ping -c 1 {{host[]=global.hosts}}"
cmdw run ping-hosts
```

### Error Handling

By default, a workflow stops when a command fails.

Continue after failed commands:

```bash
cmdw run deploy production --continue-on-error
```

Reduce output:

```bash
cmdw run deploy production --mute
```

## Command Reference

| Command | Description |
| --- | --- |
| `cmdw create NAME [COMMANDS]... --desc TEXT` | Create a workflow. |
| `cmdw list` | List saved workflows. |
| `cmdw show NAME` | Show workflow commands and placeholders. |
| `cmdw run NAME [ARGS]...` | Run a workflow. |
| `cmdw run NAME --set key=value` | Run with named placeholder values. |
| `cmdw edit NAME --new-name NAME` | Rename a workflow. |
| `cmdw edit NAME --desc TEXT` | Update a workflow description. |
| `cmdw edit NAME --command TEXT` | Replace workflow commands. Repeat for multiple commands. |
| `cmdw delete NAME` | Delete a workflow. |
| `cmdw add-var NAME VALUE` | Add a global variable. |
| `cmdw edit-var NAME VALUE` | Update a global variable. |
| `cmdw delete-var NAME` | Delete a global variable. |
| `cmdw list-var` | List global variables. |

## Local Storage

`cmdw` stores data in your home directory:

```text
~/.cmdw/workflows.json
~/.cmdw/globals.json
~/.cmdw/scripts/
```

The files are local JSON files. You can back them up, inspect them, or sync them
with your own tooling.

## Practical Examples

### Run a Project Setup Sequence

```bash
cmdw create setup \
  "python -m venv .venv" \
  "pip install -r requirements.txt" \
  "python -m unittest" \
  --desc "Create a virtual environment and run tests"

cmdw run setup
```

### Reuse a Deployment Target

```bash
cmdw add-var deploy.target production

cmdw create deploy \
  "npm run build" \
  "npm run deploy -- --target {{global.deploy.target}}"

cmdw run deploy
```

### Run the Same Command for Several Items

```bash
cmdw create check-domains "curl -I https://{{domain[]}}"
cmdw run check-domains --set "domain[]=example.com,example.org"
```

## Notes

- Commands are executed by your system shell.
- Review workflows before running them, especially if they include destructive
  shell commands.
- Secrets saved as global variables are stored as plain text in
  `~/.cmdw/globals.json`.

## License

MIT
