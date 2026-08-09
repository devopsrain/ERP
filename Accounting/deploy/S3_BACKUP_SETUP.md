# Off-site backup to S3 — setup guide

The nightly `deploy/backup.sh` optionally syncs `/opt/ebms/Accounting/backups`
to an S3 bucket after the local backup completes. It activates automatically
when `S3_BACKUP_BUCKET` is set in `/opt/ebms/Accounting/.env` and the `aws`
CLI is installed. If either is missing, the local backup runs exactly as
before — the sync is strictly optional and non-fatal.

You already use AWS for Route 53 (with a dedicated IAM certbot user); this
follows the same pattern: one bucket, one minimal-permission IAM user.

## 1. Create the bucket

In the AWS console (S3 → Create bucket), or via CLI from any machine with
admin credentials:

```bash
aws s3api create-bucket \
  --bucket ebms-backups-banknorwegian \
  --region eu-north-1 \
  --create-bucket-configuration LocationConstraint=eu-north-1
```

- Name: `ebms-backups-<something unique>` — bucket names are global, so pick
  a suffix like your org name. Used below as `ebms-backups-banknorwegian`;
  replace throughout.
- Region: `eu-north-1` (Stockholm) keeps data in the EU and close to the
  server; any region works.
- Versioning: leave **off**. The backup script never overwrites files (each
  dump has a date + timestamp in its name), so versioning would only add cost.
- Block Public Access: leave all four settings **on** (the default).

## 2. Lifecycle rule (Glacier after 30 days, expire after 365)

Local retention is 30 days; S3 keeps a full year, with anything older than
30 days moved to Glacier for near-zero storage cost.

Console: bucket → Management → Create lifecycle rule → apply to all objects →
transition to Glacier Flexible Retrieval after 30 days, expire after 365 days.

Or via CLI:

```bash
aws s3api put-bucket-lifecycle-configuration \
  --bucket ebms-backups-banknorwegian \
  --lifecycle-configuration '{
    "Rules": [{
      "ID": "ebms-backup-retention",
      "Status": "Enabled",
      "Filter": {},
      "Transitions": [{ "Days": 30, "StorageClass": "GLACIER" }],
      "Expiration": { "Days": 365 }
    }]
  }'
```

## 3. Create the IAM user `ebms-backup`

IAM → Users → Create user → `ebms-backup` → no console access. After
creation: Security credentials → Create access key → "Application running
outside AWS". Note the access key ID and secret.

Attach this **inline policy** (Permissions → Add inline policy → JSON). It
grants only what `aws s3 sync` needs, and only on this bucket:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListBucket",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::ebms-backups-banknorwegian"
    },
    {
      "Sid": "ReadWriteObjects",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject",
        "s3:DeleteObject"
      ],
      "Resource": "arn:aws:s3:::ebms-backups-banknorwegian/*"
    }
  ]
}
```

(`s3:DeleteObject` is included in case you ever run `sync --delete`; drop it
if you prefer the bucket to be append-only from the server's perspective.)

## 4. Install the AWS CLI on the Ubuntu server

```bash
sudo apt update && sudo apt install -y awscli
aws --version
```

## 5. Configure credentials for the cron user

The backup cron job runs as `devopsrain`, so the credentials must live in
that user's home. Either interactively:

```bash
aws configure
# AWS Access Key ID:     <access key from step 3>
# AWS Secret Access Key: <secret from step 3>
# Default region name:   eu-north-1
# Default output format: json
```

Or create the files directly:

```ini
# /home/devopsrain/.aws/credentials
[default]
aws_access_key_id = AKIA...
aws_secret_access_key = ...
```

```ini
# /home/devopsrain/.aws/config
[default]
region = eu-north-1
output = json
```

Then lock them down:

```bash
chmod 700 /home/devopsrain/.aws
chmod 600 /home/devopsrain/.aws/credentials
```

## 6. Enable the sync

Add the bucket name to `/opt/ebms/Accounting/.env`:

```bash
echo 'S3_BACKUP_BUCKET=ebms-backups-banknorwegian' >> /opt/ebms/Accounting/.env
```

## 7. Test

Run the backup manually and check the output:

```bash
bash /opt/ebms/Accounting/deploy/backup.sh
```

You should see a line like
`... INFO: off-site sync to s3://ebms-backups-banknorwegian/<hostname>/ completed`.
Then verify the objects landed:

```bash
aws s3 ls "s3://ebms-backups-banknorwegian/$(hostname)/" --recursive --human-readable
```

The nightly cron run needs no changes — it already captures stdout/stderr to
`backup.log`, so sync success/failure lines will appear there.

## Cost estimate

At ~50 GB of backups: Standard-IA is ~$0.0131/GB-month in eu-north-1, so
roughly **$0.65–1/month** including requests; data older than 30 days
transitions to Glacier at ~$0.0036/GB-month, pushing steady-state cost even
lower. Uploads (data transfer in) are free.

## Restoring from S3

List the available dumps, copy the latest one down, and restore:

```bash
aws s3 ls "s3://ebms-backups-banknorwegian/$(hostname)/" --recursive | sort | tail -5

# One-liner: download latest dump and pipe into Postgres
aws s3 cp "s3://ebms-backups-banknorwegian/$(hostname)/20260731/ebms_0300.sql.gz" - \
  | gunzip \
  | docker compose -f /opt/ebms/Accounting/docker-compose.yml exec -T postgres psql -U ebms ebms
```

For the application data volume, download the matching `appdata_*.tar.gz`
and extract it into the web container's `/app/web` directory.

Note: objects already transitioned to Glacier must be restored first
(`aws s3api restore-object`) before they can be downloaded — recent backups
(under 30 days old) are in Standard-IA and download immediately.
