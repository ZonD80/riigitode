#!/bin/bash

# Script to reconstruct last_dump.sql.gz from split parts
set -e

DUMP_DIR="last_dump"
OUTPUT_FILE="last_dump.sql.gz"

echo "🔧 Reconstructing database dump from split parts..."

# Check if last_dump directory exists
if [ ! -d "$DUMP_DIR" ]; then
    echo "❌ Error: Directory '$DUMP_DIR' not found"
    exit 1
fi

# Check if parts exist
if ! ls "$DUMP_DIR"/last_dump.sql.gz.part* > /dev/null 2>&1; then
    echo "❌ Error: No split parts found in '$DUMP_DIR'"
    exit 1
fi

# Count parts
PART_COUNT=$(ls "$DUMP_DIR"/last_dump.sql.gz.part* | wc -l)
echo "📦 Found $PART_COUNT split parts"

# Reconstruct the file
echo "🔄 Combining parts into $OUTPUT_FILE..."
cat "$DUMP_DIR"/last_dump.sql.gz.part* > "$OUTPUT_FILE"

# Verify the file was created
if [ -f "$OUTPUT_FILE" ]; then
    FILE_SIZE=$(ls -lh "$OUTPUT_FILE" | awk '{print $5}')
    echo "✅ Successfully reconstructed $OUTPUT_FILE ($FILE_SIZE)"
    
    # Test gzip integrity
    echo "🔍 Verifying gzip integrity..."
    if gunzip -t "$OUTPUT_FILE" 2>/dev/null; then
        echo "✅ File integrity verified - ready to import"
    else
        echo "⚠️  Warning: gzip integrity check failed"
        exit 1
    fi
else
    echo "❌ Error: Failed to create $OUTPUT_FILE"
    exit 1
fi

echo ""
echo "📋 Next steps:"
echo "   To import the database:"
echo "   Docker: gunzip -c $OUTPUT_FILE | docker-compose exec -T db psql -U postgres -d parliament_tracker"
echo "   Manual: gunzip -c $OUTPUT_FILE | psql -U postgres -d parliament_tracker"
