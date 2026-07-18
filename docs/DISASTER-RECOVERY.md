# Disaster Recovery — RAG OS

Kurzes Runbook für Backup-Wiederherstellung. Ergänzt das automatische
Nacht-Backup (`app/backup/engine.py`, 02:00 UTC).

## Was gesichert wird

| Komponente | Datei in `./data/backups/` | Erzeugt durch |
|---|---|---|
| Postgres (Metadaten, User, API-Keys) | `postgres_<ts>.dump` | `pg_dump -F c` (Client **16**, passend zum Server) |
| Qdrant-Collection `rag_documents` (Vektoren) | `<name>.snapshot` | Collection-Snapshot, heruntergeladen ins Bind-Mount |

`./data/backups` ist ein **Bind-Mount** und überlebt `docker compose down -v`.
Beide Backup-Hälften liegen dort — der Qdrant-Snapshot wird bewusst aus dem
Qdrant-Volume herausgeladen, damit er einen Volume-Verlust übersteht.

## Off-Site auf externe Festplatte

`./data/backups` liegt auf derselben Platte wie die Live-Daten — schützt also
**nicht** gegen Hardware-/Host-Verlust. Die Off-Site-Kopie geht auf eine externe
Platte, die am Host (Server) angesteckt wird.

### Konfiguration — eine Datei

Alles, was du anpassen musst, steht in **`scripts/offsite.conf`**:

```bash
RAG_BACKUP_LABEL="RAG-BACKUP"        # Name (Label) der externen Platte
RAG_BACKUP_GPG_RECIPIENT=""          # leer = unverschlüsselt; sonst GPG-Key-ID
RAG_BACKUP_EXTERNAL_KEEP_DAYS=90     # Aufbewahrung auf der Platte
```

Plattennamen ändern = nur diese Zeile ändern. **Keine Neuinstallation nötig** —
sowohl der manuelle als auch der automatische Weg lesen diese Datei.

### Platte vorbereiten (einmalig)

```bash
sudo e2label /dev/sdX1 RAG-BACKUP     # ext4: Label setzen (muss zur Config passen)
#   exFAT:  sudo exfatlabel /dev/sdX1 RAG-BACKUP
```

### Backup manuell auslösen

Externe Platte anstecken, dann:

```bash
cd /opt/<projekt>
./scripts/offsite-backup-now.sh       # findet die Platte per Label, synct, hängt aus
```

### Backup automatisch beim Anstecken (optional, einmalig einrichten)

```bash
cd /opt/<projekt>
sudo ./scripts/install-offsite-autotrigger.sh   # udev-Regel + systemd-Service
```

Danach genügt **Platte anstecken** → der Sync läuft von selbst (mount → spiegeln
→ aushängen), die Platte kann wieder abgezogen werden.

Auf der Platte liegen die Backups unter `rag-os-backups/`. Der Sync verweigert
die Arbeit, wenn das Ziel **kein echter Mountpoint** ist (Schutz davor,
versehentlich auf die Systemplatte zu schreiben); kopiert wird nur Neues
(idempotent), verifiziert per Checksumme.

> **Verschlüsselung (empfohlen):** In `offsite.conf` `RAG_BACKUP_GPG_RECIPIENT`
> auf deine GPG-Key-ID setzen — die Dumps enthalten Passwort- und
> API-Key-Hashes; auf einer Platte, die verloren gehen kann, gehören sie
> verschlüsselt. GPG-Schlüssel erzeugen: `gpg --gen-key`.

**Restore aus der externen Platte:** die gewünschten Dateien zurück nach
`./data/backups` kopieren (bei GPG: vorher `gpg --decrypt`), dann `restore.sh`
wie unten.

## Wiederherstellung

Voraussetzung: Stack läuft (`docker compose up -d`), Backup-Dateien in
`./data/backups`.

```bash
cd /opt/<projekt>
./scripts/restore.sh            # nutzt jeweils neueste Dateien
# oder explizit:
./scripts/restore.sh data/backups/postgres_20260706_140150.dump \
                     data/backups/rag_documents-<...>.snapshot
```

Das Skript:
1. spielt den Postgres-Dump per `pg_restore --clean --if-exists` ein,
2. lädt den Qdrant-Snapshot in die Collection `rag_documents`
   (`/collections/rag_documents/snapshots/recover`, `priority=snapshot`),
3. gibt zur Kontrolle Dokument- und Punktzahl aus.

## Nach dem Restore prüfen

- **Konsistenz:** Dokumentzahl (Postgres) und Punktzahl (Qdrant) müssen
  plausibel zueinander passen. Grobe Abweichung → Split-Brain, erneut
  restaurieren oder betroffene Dokumente reindizieren.
- **Stichprobe:** eine bekannte Suche über `POST /api/retrieve` liefert wieder
  Treffer.

## Versions-Hinweis (wichtig)

Der Postgres-Dump wird mit **pg_dump 16** erzeugt (siehe `app/Dockerfile`,
`postgresql-client-16`). Der Dump ist damit vom `postgres:16`-Server
restaurierbar. Wird das Server-Image je auf eine neue Major-Version angehoben,
muss der Client im API-Image **gleichzeitig** mitgezogen werden, sonst schlägt
der Restore mit `unsupported version` fehl.
