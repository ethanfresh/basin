# Deploying

The dashboard serves a store it never writes. Ingestion runs on a workstation;
the result is copied to the host as a finished artifact. That asymmetry is what
makes the deployment small — the 50GB+ corpus under `data/corpus` is an
ingestion input and never reaches the host at all.

| | Size | On the host? |
|---|---|---|
| `data/basin.db` | ~3.5GB, growing | yes, on the volume |
| `data/corpus/` | 50GB+ | no |
| `data/cache/` | ~300MB | no |

## First deploy

```
fly launch --no-deploy --name basin
fly volumes create basin_data --size 5 --region iad
fly deploy
```

5GB leaves room for the store plus a second copy during a swap. The volume
pins the app to one machine in one region; `fly.toml` sets no scaling because
a volume cannot be shared between machines.

The machine comes up healthy with an empty volume. That is deliberate: the
health check is `HEAD /`, which touches no store, so the machine stays up long
enough to receive one. Until then the `/api/*` routes answer 503 naming the
path they looked at.

## Shipping a store

The read-only path applies no schema migration, so the file has to be current
before it leaves the workstation. Opening it with `connect()` runs the column
migration; `connect_readonly()` deliberately does not.

```
python -c "from basin.store import connect; connect('data/basin.db').close()"
sqlite3 data/basin.db "VACUUM INTO 'data/ship.db'"
fly sftp shell -C "put data/ship.db /data/basin.db.new"
fly ssh console -C "mv /data/basin.db.new /data/basin.db"
fly apps restart basin
```

`VACUUM INTO` rather than `cp`: it takes a consistent snapshot even while an
ingest script holds a transaction, and it compacts free pages, so the shipped
file is smaller than the working one. Copying the file directly while a writer
is mid-transaction captures a hot journal, and a `mode=ro` connection cannot
roll one back — every read then fails with "attempt to write a readonly
database". Restarting is what drops the old connections; the rename alone
leaves them on the unlinked inode.

## The corpus

Only ingestion reads it, so it belongs on object storage rather than the
volume:

```
fly storage create
```

Every function in `basin/documents/corpus.py` takes a `root:` parameter, which
is the seam to point at a bucket. `GET /debug/page/...` is the one route that
reads the corpus and will 404 on the host — it is a development aid, not part
of the dashboard.

## Configuration

`BASIN_DB` sets the store location for both the web app and every script in
`scripts/`, which take it as their `--store`/`--db` default. Precedence is
flag, then environment, then `data/basin.db`.
