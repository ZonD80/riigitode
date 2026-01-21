#!/bin/bash

# Deploy script: Sync files and database to server using rsync with multiple streams
set -e  # Exit on any error

# Configuration
# Server configuration can be set via environment variable or command line argument
# Example: DEPLOY_SERVER="user@your-server.com" ./copy_to_server.sh
# Or: ./copy_to_server.sh --server user@your-server.com
SERVER="${DEPLOY_SERVER:-}"
SERVER_PATH="${DEPLOY_PATH:-/home/user/parliament_tracker}"
DB_NAME="parliament_tracker"
DB_USER="postgres"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
DUMP_FILE="db_dump_${TIMESTAMP}.sql"

# Parse command line arguments
REPLACE_DB=false
SKIP_DB=false
NEXT_IS_SERVER=false
NEXT_IS_PATH=false

for arg in "$@"; do
    if [ "$NEXT_IS_SERVER" = true ]; then
        SERVER="$arg"
        NEXT_IS_SERVER=false
        continue
    fi
    if [ "$NEXT_IS_PATH" = true ]; then
        SERVER_PATH="$arg"
        NEXT_IS_PATH=false
        continue
    fi
    
    case $arg in
        -r|--replace-db)
            REPLACE_DB=true
            echo "⚠️  Database replacement mode enabled!"
            ;;
        -s|--skip-db)
            SKIP_DB=true
            echo "ℹ️  Skipping database operations"
            ;;
        --server=*)
            SERVER="${arg#*=}"
            ;;
        --server)
            NEXT_IS_SERVER=true
            ;;
        --path=*)
            SERVER_PATH="${arg#*=}"
            ;;
        --path)
            NEXT_IS_PATH=true
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  -r, --replace-db        Replace the entire database on target (drops and recreates)"
            echo "  -s, --skip-db           Skip all database operations (only sync files)"
            echo "  --server=USER@HOST      Server address (or --server USER@HOST)"
            echo "  --path=PATH             Server path (or --path PATH, default: /home/user/parliament_tracker)"
            echo "  -h, --help              Show this help message"
            echo ""
            echo "Environment Variables:"
            echo "  DEPLOY_SERVER           Server address (e.g., user@your-server.com)"
            echo "  DEPLOY_PATH             Server deployment path (default: /home/user/parliament_tracker)"
            echo ""
            echo "Default behavior: Append/merge data to existing database"
            echo "Replace mode: Completely replaces the target database"
            echo "Skip DB mode: Only syncs files, no database operations"
            exit 0
            ;;
    esac
done

# Check if we're still waiting for a value
if [ "$NEXT_IS_SERVER" = true ]; then
    echo "❌ Error: --server requires a value"
    echo "Usage: --server=user@host or --server user@host"
    exit 1
fi
if [ "$NEXT_IS_PATH" = true ]; then
    echo "❌ Error: --path requires a value"
    echo "Usage: --path=/path/to/deployment or --path /path/to/deployment"
    exit 1
fi

# Validate server configuration
if [ -z "$SERVER" ]; then
    echo "❌ Error: Server address not specified"
    echo ""
    echo "Please set the server address using one of these methods:"
    echo "  1. Environment variable: export DEPLOY_SERVER='user@your-server.com'"
    echo "  2. Command line: $0 --server=user@your-server.com"
    echo ""
    echo "Run '$0 --help' for more information"
    exit 1
fi

echo "🚀 Starting deployment process..."

# Step 1: Create database dump from Docker container
if [ "$SKIP_DB" = false ]; then
    echo "📦 Creating database dump..."
    docker-compose exec -T db pg_dump -U ${DB_USER} -d ${DB_NAME} --no-owner --no-privileges > ${DUMP_FILE}

    if [ $? -eq 0 ]; then
        echo "✅ Database dump created: ${DUMP_FILE}"
        echo "📊 Dump size: $(du -h ${DUMP_FILE} | cut -f1)"
    else
        echo "❌ Failed to create database dump"
        exit 1
    fi
else
    echo "⏭️  Skipping database dump creation"
fi

# Step 2: Sync files to server using rsync with multiple streams
echo "📁 Syncing files to server..."
rsync -avzP --delete \
  --exclude='.git/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='.env' \
  --exclude='*.log' \
  --exclude='db_dump_*.sql' \
  -e "ssh -o 'ControlMaster=auto' -o 'ControlPath=~/.ssh/control-%r@%h:%p' -o 'ControlPersist=600'" \
  . ${SERVER}:${SERVER_PATH}/

if [ $? -eq 0 ]; then
    echo "✅ Files synced successfully"
else
    echo "❌ Failed to sync files"
    exit 1
fi

# Step 3: Upload database dump
if [ "$SKIP_DB" = false ]; then
    echo "🗄️  Uploading database dump..."
    rsync -avzP \
      -e "ssh -o 'ControlMaster=auto' -o 'ControlPath=~/.ssh/control-%r@%h:%p' -o 'ControlPersist=600'" \
      ${DUMP_FILE} ${SERVER}:${SERVER_PATH}/

    if [ $? -eq 0 ]; then
        echo "✅ Database dump uploaded successfully"
    else
        echo "❌ Failed to upload database dump"
        exit 1
    fi
else
    echo "⏭️  Skipping database dump upload"
fi

# Step 4: Execute all server-side operations with sudo (single SSH session)
echo "🔐 Executing server-side operations (will prompt for sudo password once)..."

if [ "$SKIP_DB" = false ]; then
    if [ "$REPLACE_DB" = true ]; then
        # Create a comprehensive replacement script
        cat > temp_db_replace.sql << EOF
-- Terminate all connections to the database
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${DB_NAME}' AND pid <> pg_backend_pid();

-- Drop and recreate database
DROP DATABASE IF EXISTS ${DB_NAME};
CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};
EOF
        
        # Upload the replacement script
        rsync -avzP \
          -e "ssh -o 'ControlMaster=auto' -o 'ControlPath=~/.ssh/control-%r@%h:%p' -o 'ControlPersist=600'" \
          temp_db_replace.sql ${SERVER}:${SERVER_PATH}/
        
        # Execute all operations in a single SSH session with sudo
        ssh -t -o 'ControlMaster=auto' -o 'ControlPath=~/.ssh/control-%r@%h:%p' -o 'ControlPersist=600' ${SERVER} "sudo bash -c '
            set -e
            cd ${SERVER_PATH}
            
            echo \"⏸️  Stopping web service...\"
            docker compose stop web
            
            echo \"🔄 Replacing database...\"
            docker compose exec -T db psql -U ${DB_USER} -d postgres < temp_db_replace.sql
            
            echo \"📥 Importing new database...\"
            docker compose exec -T db psql -U ${DB_USER} -d ${DB_NAME} < ${DUMP_FILE}
            
            echo \"🔄 Starting all Docker services...\"
            docker compose up -d
            
            echo \"🔄 Reloading nginx...\"
            systemctl reload nginx || true
            
            echo \"🧹 Cleaning up temp files...\"
            rm -f temp_db_replace.sql ${DUMP_FILE}
        '"
        
        if [ $? -eq 0 ]; then
            echo "✅ All server operations completed successfully"
        else
            echo "❌ Server operations failed"
            rm -f temp_db_replace.sql
            exit 1
        fi
        
        # Clean up local replacement script
        rm -f temp_db_replace.sql
    else
        # Merge mode - execute all operations in a single SSH session with sudo
        ssh -t -o 'ControlMaster=auto' -o 'ControlPath=~/.ssh/control-%r@%h:%p' -o 'ControlPersist=600' ${SERVER} "sudo bash -c '
            set -e
            cd ${SERVER_PATH}
            
            echo \"⏸️  Stopping web service...\"
            docker compose stop web
            
            echo \"🔄 Importing database (merge mode)...\"
            docker compose exec -T db psql -U ${DB_USER} -d ${DB_NAME} < ${DUMP_FILE}
            
            echo \"🔄 Starting all Docker services...\"
            docker compose up -d
            
            echo \"🔄 Reloading nginx...\"
            systemctl reload nginx || true
            
            echo \"🧹 Cleaning up dump file...\"
            rm -f ${DUMP_FILE}
        '"
        
        if [ $? -eq 0 ]; then
            echo "✅ All server operations completed successfully"
        else
            echo "❌ Server operations failed"
            exit 1
        fi
    fi
else
    # Skip DB mode - just restart web service and reload nginx
    ssh -t -o 'ControlMaster=auto' -o 'ControlPath=~/.ssh/control-%r@%h:%p' -o 'ControlPersist=600' ${SERVER} "sudo bash -c '
        set -e
        cd ${SERVER_PATH}
        
        echo \"🔄 Restarting web service...\"
        docker compose restart web
        
        echo \"🔄 Reloading nginx...\"
        systemctl reload nginx || true
    '"
    
    if [ $? -eq 0 ]; then
        echo "✅ Web service restarted and nginx reloaded successfully"
    else
        echo "❌ Server operations failed"
        exit 1
    fi
fi

# Step 5: Clean up local dump file
if [ "$SKIP_DB" = false ]; then
    echo "🧹 Cleaning up local dump file..."
    rm -f ${DUMP_FILE}
fi

echo "🎉 Deployment completed successfully!"
echo "📋 Summary:"
echo "   - Files synced to server"
if [ "$SKIP_DB" = true ]; then
    echo "   - Database operations skipped"
    echo "   - Web service restarted"
elif [ "$REPLACE_DB" = true ]; then
    echo "   - Database completely replaced on target"
    echo "   - All Docker services restarted"
else
    echo "   - Database dumped and merged"
    echo "   - All Docker services restarted"
fi
echo "   - Nginx reloaded"
echo "   - Cleanup completed"