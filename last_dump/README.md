# Database Dump Split Files

This directory contains the split parts of `last_dump.sql.gz` (from November 11, 2025).

The dump has been split into 10 MB chunks to facilitate version control and distribution.

## Files

- `last_dump.sql.gz.partaa` through `last_dump.sql.gz.partak` - Split parts of the compressed database dump

## Reconstructing the Database Dump

To reconstruct the original `last_dump.sql.gz` file:

**Using the helper script (from project root):**

```bash
# From the project root directory
./reconstruct_dump.sh
```

**Manual reconstruction:**

```bash
# From the project root directory
cat last_dump/last_dump.sql.gz.part* > last_dump.sql.gz

# Or from within this directory
cat last_dump.sql.gz.part* > ../last_dump.sql.gz
```

## Importing the Database

After reconstructing the dump file:

```bash
# Decompress and import
gunzip -c last_dump.sql.gz | docker-compose exec -T db psql -U postgres -d parliament_tracker

# Or if using manual PostgreSQL
gunzip -c last_dump.sql.gz | psql -U postgres -d parliament_tracker
```

## Verification

To verify the reconstructed file:

```bash
# Check file size (should match original)
ls -lh last_dump.sql.gz

# Verify it's a valid gzip file
gunzip -t last_dump.sql.gz
```
