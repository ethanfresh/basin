# Deploying

The dashboard serves a store it never writes. Ingestion runs on a workstation;
what reaches the host is a build artifact, not the working database.

The working store is two databases wearing one filename. `document_line` and
its full-text index are the parsed corpus — ~3GB, 13M rows, the input to
verification and table extraction. The facts they produce, with every citation
the dashboard renders, are ~15MB. `basin.store.queries` — the only module the
web app talks to — names neither document table.

| | Size | On the host? |
|---|---|---|
| `data/corpus/` | 50GB+ | no |
| `data/basin.db` | ~3.2GB, growing | no |
| ├ `document_line` + FTS | ~3.1GB | no |
| └ facts, citations, verification | ~15MB | **yes, inside the image** |
| `data/cache/` | ~300MB | no |

That asymmetry is what keeps the deployment stateless. There is no volume, so
nothing pins the app to one machine in one region, and a deploy is the whole
update mechanism — no upload-and-swap between building a store and serving it.

## Where the working data lives

`data/` is not in the checkout. It is a symlink to a store kept outside it —
26GB of database, corpus and cache does not belong in a git working tree, and
`git status` should not have to ignore it:

```
ln -s ~/BasinData /path/to/basin/data
```

Scripts default to `data/basin.db` and follow the link. `BASIN_DB` overrides
that for both the web app and every script in `scripts/`, which take it as
their `--store`/`--db` default. Precedence is flag, then environment, then
`data/basin.db`.

## Shipping

```
python scripts/build_serving_store.py     # -> build/serving.db, ~15MB
fly deploy
```

`build_serving_store.py` copies every table except the document index into a
fresh database carrying the full schema, so the two omitted tables are present
and empty rather than missing — a query that reaches for them on the host
returns no rows instead of raising. It runs `PRAGMA foreign_key_check` before
it will hand back a store.

It reads the source read-only and writes a new file, so it is safe to run while
an ingest holds a transaction: it never sees a hot journal the way `cp` does.

`build/` is gitignored and excepted from the `*.db` rule in `.dockerignore`,
which is the one path by which a database enters the build context.

The health check is `GET /api/cohorts` rather than `HEAD /`. With the store in
the image there is no window where the app is up but has no data, so the check
can prove the store opens rather than only that the process is listening. On a
machine without a store the `/api/*` routes answer 503 naming the path they
looked at.

## First deploy

```
fly launch --no-deploy --name basin
fly deploy
```

No volume to create, and no region to pin. `min_machines_running = 0` in
`fly.toml` trades a cold start for not paying to sit idle; raise it, or add
regions, without anything to keep in sync.

## The corpus

Only ingestion reads it, and it is the largest thing on the workstation's disk,
so it belongs on object storage:

```
fly storage create
```

Every function in `basin/documents/corpus.py` takes a `root:` parameter, which
is the seam to point at a bucket. `GET /debug/page/...` is the one route that
reads the corpus and will 404 on the host — it is a development aid, not part
of the dashboard.
