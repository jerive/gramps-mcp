# Tutorial: talk to a Gramps tree through MCP

In this tutorial you'll start a throwaway Gramps Web API instance on your own
machine, loaded with Gramps' bundled example family tree, then run
`gramps-mcp` in front of it and ask the tree for a few people through an MCP
client. It takes about 15 minutes, and nothing you do here touches a real
tree.

It follows one concrete path so that every step works the same way for
everyone: a Debian/Ubuntu machine, no Docker, no Google sign-in. Once it
works, the [how-to guides](how-to/service-account.md) cover the other setups (an
existing Gramps Web deployment, Docker Compose, Google OAuth).

You will need:

- Debian or Ubuntu (22.04 or newer), with `sudo`.
- Python 3.12 or newer for `gramps-mcp` (`python3 --version`).

You'll use two terminals: one for Gramps Web API, one for `gramps-mcp`. Steps
1 to 5 are in the first one.

## Watch it first

This is a real run of the tutorial on a fresh Ubuntu 24.04 container, about
three minutes long. Press play, or skip to the steps below.

<div id="tutorial-demo"></div>
<script>
  document.addEventListener('DOMContentLoaded', function () {
    AsciinemaPlayer.create('../assets/tutorial.cast', document.getElementById('tutorial-demo'), {
      idleTimeLimit: 2, fit: 'width', terminalFontSize: 'small', poster: 'npt:2:50'
    });
  });
</script>

The recording differs from the steps in three ways, to fit in one terminal and
one short video: Gramps Web API and `gramps-mcp` are started in the background
with `&` (with their logs redirected) instead of in their own terminals, the
long installs are sped up, and the commands are typed by a script.

## 1. Install the system packages Gramps needs

Gramps itself is a desktop application, so even its headless mode needs a few
GTK/GObject libraries:

```sh
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  git python3-venv python3-dev python3-gi build-essential pkg-config \
  gettext appstream libicu-dev libcairo2-dev libgirepository1.0-dev \
  gir1.2-glib-2.0 gir1.2-gtk-3.0 gir1.2-pango-1.0
```

## 2. Install Gramps and Gramps Web API

Create a virtual environment that can see the system `python3-gi`, and
install both packages into it:

```sh
python3 -m venv --system-site-packages ~/gramps-venv
~/gramps-venv/bin/pip install gramps gramps_webapi
```

This compiles a couple of packages and takes a few minutes.

## 3. Import the example tree

Gramps Web API is configured through environment variables. Set them now, in
this terminal, and keep using the same terminal for the next steps:

```sh
export GRAMPSWEB_TREE=example
export GRAMPSWEB_SECRET_KEY=tutorial-only-not-a-secret
export GRAMPSWEB_USER_DB_URI=sqlite:////tmp/users.sqlite
export GRAMPSWEB_MEDIA_BASE_DIR=/tmp/media
mkdir -p /tmp/media
```

Then import the example file that ships with Gramps into a tree called
`example`:

```sh
EXAMPLE=$(~/gramps-venv/bin/python -c "import os; from gramps.gen.utils.resourcepath import ResourcePath; print(os.path.join(ResourcePath().doc_dir, 'example', 'gramps', 'example.gramps'))")
~/gramps-venv/bin/gramps -C example -i "$EXAMPLE"
```

Gramps prints the import progress and ends with something like
`Import Complete`.

## 4. Create the user `gramps-mcp` will log in as

`gramps-mcp` acts as a regular Gramps Web user, with exactly that user's
permissions. Find the ID of the tree you just imported:

```sh
~/gramps-venv/bin/python -m gramps_webapi tree list
```

The output is a small table; copy the value in the `Tree ID` column of the
`example` row (a short hex string such as `6ad0a9ff`). Create an Editor
(role `3`) in that tree, replacing `<tree-id>` with it:

```sh
~/gramps-venv/bin/python -m gramps_webapi user add editor editorpass123 --role 3 --tree <tree-id>
```

## 5. Start Gramps Web API

```sh
~/gramps-venv/bin/python -m gramps_webapi run --host 127.0.0.1 --port 5555 --use-wsgi
```

Leave it running. In a **second terminal**, check it answers:

```sh
curl -s http://127.0.0.1:5555/api/openapi.json | head -c 200
```

You should see the start of a JSON document: the API's OpenAPI description,
which is what `gramps-mcp` reads to generate its tools.

## 6. Install gramps-mcp

Still in the second terminal:

```sh
python3 -m venv ~/mcp-venv
~/mcp-venv/bin/pip install "gramps-mcp @ git+https://github.com/jerive/gramps-mcp@v0.5.0"
```

## 7. Start gramps-mcp

Tell it where Gramps Web API lives, which tree to mount, and which user to
log in as. For a tree named `example`, the variable names are
`GRAMPS_MCP_TREE_EXAMPLE_USERNAME` and `_PASSWORD` (the tree name,
uppercased):

```sh
export GRAMPS_BACKEND_URL=http://127.0.0.1:5555
export GRAMPS_MCP_TREES=example
export GRAMPS_MCP_TREE_EXAMPLE_USERNAME=editor
export GRAMPS_MCP_TREE_EXAMPLE_PASSWORD=editorpass123
export MCP_HOST=127.0.0.1
~/mcp-venv/bin/gramps-mcp
```

When the log shows the server listening on port 8000, leave it running. The
MCP endpoint is `http://127.0.0.1:8000/mcp`. (Since Google sign-in isn't
configured, anyone who can reach that port can use the tree, which is why
`MCP_HOST=127.0.0.1` restricts it to your machine. Keep it that way.)

## 8. Ask the tree for some people

Open a **third terminal** and run a tiny MCP client with the `fastmcp`
library that came along with `gramps-mcp`:

```sh
~/mcp-venv/bin/python - <<'PY'
import asyncio
from fastmcp import Client

async def main():
    async with Client("http://127.0.0.1:8000/mcp") as client:
        tools = await client.list_tools()
        print(f"{len(tools)} tools available, for example:")
        for tool in tools[:3]:
            print(" -", tool.name)
        result = await client.call_tool("tree-example_get_people", {"page": 1, "pagesize": 2})
        print(result.content[0].text[:400])

asyncio.run(main())
PY
```

You'll see the number of generated tools, three of their names, and the start
of the JSON for two people from the example tree. Every endpoint of the Gramps
Web API has become an MCP tool, namespaced by tree (`tree-example_...`), and
this call went through the whole chain: MCP client, `gramps-mcp`, Gramps Web
API, the tree.

You can stop the three processes with `Ctrl+C`.

## Where to go from here

- To connect a real MCP client such as Claude instead of this script, see
  [Connect an MCP client](how-to/connect-client.md).
- To point `gramps-mcp` at your own Gramps Web deployment, start with
  [Create a Gramps Web service account](how-to/service-account.md).
  Prefer **Member** over **Editor** if you don't want an agent to modify the
  tree.
- To expose the server beyond `localhost`, set up
  [Google OAuth](how-to/google-oauth.md) and see the
  [Docker Compose example](how-to/docker-compose.md).
