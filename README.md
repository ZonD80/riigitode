# Estonian Parliament Speech Tracker

A comprehensive Django application that parses speeches and politician data from the Estonian Parliament (Riigikogu) API, generates AI-powered summaries and translations, profiles politicians, and provides a web interface for exploring parliamentary data.

## Features

- **Speech Tracking**: Parses and stores speeches from parliamentary sessions with full text content
- **Politician Management**: Stores politician data including names, contact information, factions, photos, and parliamentary seniority
- **AI-Powered Summaries**: Generates AI summaries for speeches and agenda items using multiple providers (Claude, OpenAI, Gemini, Ollama)
- **Multilingual Support**: Translates content to English and Russian using AI providers
- **Politician Profiling**: Automatically generates detailed profiles for politicians based on their speeches
- **Web Interface**: Public-facing website with politician pages, agenda details, and statistics
- **Admin Interface**: Django admin panel with enhanced features and counters
- **Docker Support**: Fully containerized application with PostgreSQL database
- **Batch Processing**: Efficient batch processing for AI operations using parallel processing

## Data Models

- **Politician**: Parliament members with personal information, photos, and statistics
- **Faction**: Political factions/parties
- **PoliticianFaction**: Membership relationships between politicians and factions
- **PlenarySession**: Parliamentary session information with multilingual titles
- **AgendaItem**: Agenda items within sessions with summaries and decisions
- **Speech**: Individual speeches and statements by politicians
- **AgendaSummary**: AI-generated summaries for agenda items
- **AgendaDecision**: Decisions extracted from agenda items
- **AgendaActivePolitician**: Active politicians in agenda items
- **PoliticianProfilePart**: Detailed profile parts for politicians by category
- **Statistics**: Aggregated statistics about parliamentary activity

## Installation & Setup

### Prerequisites

- Docker and Docker Compose (recommended), or
- Python 3.8+, PostgreSQL 12+, and required system dependencies

### Initial Database Setup

The project includes a pre-populated database dump from November 11, 2025, split into 10 MB chunks in the `last_dump/` directory.

#### Reconstructing the Database Dump

The dump file has been split into multiple parts for easier version control. To reconstruct the original file:

**Using the helper script (recommended):**

```bash
# Navigate to the project directory
cd /path/to/project

# Run the reconstruction script
./reconstruct_dump.sh
```

**Manual reconstruction:**

```bash
# Navigate to the project directory
cd /path/to/project

# Combine all split parts back into the original compressed dump
cat last_dump/last_dump.sql.gz.part* > last_dump.sql.gz

# Verify the file was reconstructed correctly
ls -lh last_dump.sql.gz
gunzip -t last_dump.sql.gz  # Test gzip integrity
```

#### Importing the Database Dump

**Using Docker:**

```bash
# Start Docker containers first
docker-compose up -d db

# Wait for database to be ready, then import
gunzip -c last_dump.sql.gz | docker-compose exec -T db psql -U postgres -d parliament_tracker

# Or if you need to create the database first
docker-compose exec -T db psql -U postgres -c "CREATE DATABASE parliament_tracker;"
gunzip -c last_dump.sql.gz | docker-compose exec -T db psql -U postgres -d parliament_tracker
```

**Manual PostgreSQL:**

```bash
# Create database if it doesn't exist
createdb parliament_tracker

# Import the dump
gunzip -c last_dump.sql.gz | psql -U postgres -d parliament_tracker
```

**Note:** The dump file is large (~108 MB compressed). After reconstruction, you can optionally delete `last_dump.sql.gz` to save space, as the split parts in `last_dump/` can be used to reconstruct it again when needed.

### Using Docker (Recommended)

1. **Clone the repository:**
   ```bash
   git clone <repository-url>
   cd www
   ```

2. **Configure environment variables:**
   ```bash
   cp env.example .env
   # Edit .env with your configuration (see Environment Variables section)
   ```

3. **Build and start the containers:**
   ```bash
   docker-compose up --build -d
   ```

4. **Run migrations:**
   ```bash
   docker-compose exec web python manage.py migrate
   ```

5. **Create a superuser:**
   ```bash
   docker-compose exec web python manage.py createsuperuser
   ```

6. **Access the application:**
   - Public website: http://localhost:8000/
   - Admin interface: http://localhost:8000/admin/
   - Enhanced admin: http://localhost:8000/parliament-admin/

### Manual Installation

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Set up PostgreSQL database:**
   ```bash
   createdb parliament_tracker
   ```

3. **Configure environment variables:**
   ```bash
   cp env.example .env
   # Edit .env with your database credentials and API keys
   ```

4. **Run migrations:**
   ```bash
   python manage.py migrate
   ```

5. **Create superuser:**
   ```bash
   python manage.py createsuperuser
   ```

6. **Collect static files:**
   ```bash
   python manage.py collectstatic
   ```

7. **Start development server:**
   ```bash
   python manage.py runserver
   ```

## Environment Variables

Copy `env.example` to `.env` and configure the following variables:

### Database Settings
- `DB_NAME`: PostgreSQL database name (default: `parliament_tracker`)
- `DB_USER`: PostgreSQL user (default: `postgres`)
- `DB_PASSWORD`: PostgreSQL password (default: `postgres`)
- `DB_HOST`: Database host (default: `localhost` for manual, `db` for Docker)
- `DB_PORT`: Database port (default: `5432`)

### Django Settings
- `SECRET_KEY`: Django secret key (change in production!)
- `DEBUG`: Debug mode (set to `False` in production)

### AI Provider Configuration

Choose one or more AI providers:

**Claude (Anthropic)**
- `AI_PROVIDER=claude`
- `CLAUDE_API_KEY`: Your Claude API key

**OpenAI**
- `AI_PROVIDER=openai`
- `OPENAI_API_KEY`: Your OpenAI API key
- `OPENAI_MODEL`: Model name (default: `gpt-4o`)

**Google Gemini** (Recommended for cost-effectiveness)
- `AI_PROVIDER=gemini`
- `GEMINI_API_KEY`: Your Gemini API key
- `GEMINI_MODEL`: Model name (default: `gemini-2.5-flash-preview-09-2025`)

**Ollama** (Local/Private)
- `AI_PROVIDER=ollama`
- `OLLAMA_BASE_URL`: Ollama server URL (default: `http://localhost:11434`)
- `OLLAMA_MODEL`: Model name (default: `gemma3:12b`)

**Note**: For Docker deployments, if using Ollama on the host machine, use `http://host.docker.internal:11434` as the base URL.

## Usage

### Daily Routine Script (Recommended)

The `daily_routine` command is the recommended way to process parliament data. It runs a complete pipeline of 9 steps:

1. **Parse speeches** from Estonian Parliament API
2. **Generate AI summaries** for speeches
3. **Generate AI summaries** for agendas
4. **Translate agendas** to English and Russian
5. **Translate plenary session titles** to English and Russian
6. **Translate speech AI summaries** to English and Russian
7. **Profile all politicians** based on their speeches
8. **Translate politician profiles** to English and Russian
9. **Sync everything** (total times, profiling counts, statistics)

#### Basic Usage

```bash
# Run complete daily routine (default: processes from start of current year)
python manage.py daily_routine

# Using Docker
docker-compose exec web python manage.py daily_routine
```

#### Advanced Options

```bash
# Process from a specific date
python manage.py daily_routine --start-date 2025-01-01

# Use a specific AI provider
python manage.py daily_routine --ai-provider gemini

# Custom batch size for parallel processing
python manage.py daily_routine --batch-size 500

# Skip parsing (only process existing data)
python manage.py daily_routine --skip-parse

# Dry run (see what would be processed without saving)
python manage.py daily_routine --dry-run

# Verbose logging
python manage.py daily_routine --verbose

# Combine options
python manage.py daily_routine --start-date 2025-01-01 --ai-provider gemini --batch-size 1000
```

#### What Each Step Does

**Step 1: Parse Speeches**
- Fetches speeches from the Riigikogu API
- Processes speeches from the specified start date to today
- Creates/updates politicians, factions, sessions, agenda items, and speeches

**Step 2: Generate AI Summaries for Speeches**
- Generates concise summaries for each speech using AI
- Processes speeches in batches for efficiency
- Supports all AI providers (Claude, OpenAI, Gemini, Ollama)

**Step 3: Generate AI Summaries for Agendas**
- Creates summaries for agenda items
- Extracts key decisions and active politicians
- Uses batch API when available (Gemini)

**Step 4: Translate Agendas**
- Translates agenda titles, summaries, decisions, and active politician names
- Supports English and Russian translations
- Processes in parallel batches

**Step 5: Translate Plenary Session Titles**
- Translates session titles to English and Russian
- Maintains consistency across sessions

**Step 6: Translate Speech AI Summaries**
- Translates previously generated speech summaries
- Ensures multilingual access to speech content

**Step 7: Profile All Politicians**
- Analyzes each politician's speeches by category
- Generates detailed profile parts (topics, positions, activity patterns)
- Creates comprehensive politician profiles

**Step 8: Translate Politician Profiles**
- Translates politician profile parts to English and Russian
- Makes profiles accessible in multiple languages

**Step 9: Sync Everything**
- Calculates total speaking times for politicians
- Updates profiling counts and statistics
- Ensures data consistency

### Individual Management Commands

If you need to run individual steps:

#### Parsing Commands

```bash
# Parse speeches from last 30 days
python manage.py parse_speeches

# Parse speeches from last 7 days
python manage.py parse_speeches --days 7

# Parse speeches for specific date range
python manage.py parse_speeches --start-date 2025-01-01 --end-date 2025-01-31

# Dry run
python manage.py parse_speeches --dry-run
```

#### AI Summary Generation

```bash
# Generate summaries for speeches
python manage.py generate_ai_summaries_for_speeches --ai-provider gemini --batch-size 1000

# Generate summaries for agendas
python manage.py generate_ai_summaries_for_agendas --ai-provider gemini --batch-size 1000
```

#### Translation Commands

```bash
# Translate agendas
python manage.py translate_agendas --ai-provider gemini --target-language both

# Translate plenary session titles
python manage.py translate_plenary_session_titles --ai-provider gemini --target-language both

# Translate speech summaries
python manage.py translate_speech_ai_summaries --ai-provider gemini --target-language both

# Translate politician profiles
python manage.py translate_politician_profiles --ai-provider gemini --target-language both
```

#### Profiling Commands

```bash
# Profile all politicians
python manage.py profile_all_politicians --ai-provider gemini --batch-size 1000

# Profile specific politician
python manage.py profile_politician <politician_id> --ai-provider gemini
```

#### Sync Commands

```bash
# Sync everything (total times, counts, statistics)
python manage.py sync_everything

# Individual sync commands
python manage.py sync_total_times
python manage.py sync_profiling_counts
python manage.py sync_stats
```

#### Utility Commands

```bash
# Clear AI summaries (useful for regeneration)
python manage.py clear_ai_summaries

# Clear speeches (use with caution!)
python manage.py clear_speeches

# Clean HTML tags from speeches
python manage.py clean_html_tags

# Fix incomplete flags
python manage.py fix_incomplete_flags
```

## Web Interface

The application provides a public-facing web interface:

- **Home Page**: Overview with statistics and recent activity
- **Politicians**: List and detail pages for all parliament members
- **Plenary Sessions**: Browse sessions and view details
- **Agendas**: View agenda items with summaries and decisions
- **Decisions**: List of all parliamentary decisions
- **Politician Profiles**: Detailed profiling pages showing politician positions by category
- **API Transparency Report**: Statistics about API usage and data completeness

## API Endpoints Used

The application integrates with these Riigikogu API endpoints:

- `/api/plenary-members` - Parliament member information
- `/api/steno/verbatims` - Speech transcripts and session data

## Database Schema

Main database tables:

- `parliament_speeches_politician` - Politician data
- `parliament_speeches_faction` - Political factions
- `parliament_speeches_politicianfaction` - Faction memberships
- `parliament_speeches_plenarysession` - Session information
- `parliament_speeches_agendaitem` - Session agenda items
- `parliament_speeches_speech` - Individual speeches
- `parliament_speeches_agendasummary` - AI-generated agenda summaries
- `parliament_speeches_agendadecision` - Extracted decisions
- `parliament_speeches_agendaactivepolitician` - Active politicians in agendas
- `parliament_speeches_politicianprofilepart` - Politician profile parts
- `parliament_speeches_statistics` - Aggregated statistics

## Project Structure

```
parliament_tracker/              # Django project settings
├── settings.py                # Configuration
├── urls.py                    # URL routing
└── wsgi.py                    # WSGI application

parliament_speeches/           # Main application
├── models.py                  # Database models
├── admin.py                   # Admin interface configuration
├── views.py                   # Web views
├── ai_service.py              # AI provider abstraction
├── middleware.py              # Custom middleware
├── context_processors.py      # Template context processors
├── translation.py             # Translation utilities
├── management/
│   └── commands/              # Management commands
│       ├── daily_routine.py   # Main daily routine script
│       ├── parse_speeches.py  # Speech parsing
│       ├── generate_ai_summaries_for_speeches.py
│       ├── generate_ai_summaries_for_agendas.py
│       ├── translate_*.py     # Translation commands
│       ├── profile_*.py       # Profiling commands
│       └── sync_*.py          # Sync commands
└── templates/                 # HTML templates

requirements.txt               # Python dependencies
docker-compose.yml            # Docker configuration
Dockerfile                    # Container definition
env.example                   # Environment variables template
```

## Configuration

Key settings in `parliament_tracker/settings.py`:

- `PARLIAMENT_API_BASE_URL` - Base URL for the Riigikogu API (default: `https://api.riigikogu.ee`)
- `DATABASES` - PostgreSQL database configuration
- `LANGUAGE_CODE` - Set to English ('en-us')
- `TIME_ZONE` - Set to Estonian timezone ('Europe/Tallinn')
- `AI_PROVIDER` - Default AI provider (can be overridden per command)
- `MEDIA_ROOT` - Directory for uploaded files (politician photos)

## Troubleshooting

### Common Issues

1. **Database connection errors**
   - Ensure PostgreSQL is running and credentials are correct
   - Check environment variables in `.env` file
   - For Docker, ensure database container is healthy

2. **API timeout errors**
   - The Riigikogu API can be slow; commands include appropriate timeouts
   - Consider processing smaller date ranges

3. **Missing politicians**
   - Some speakers may not be matched to politicians due to name variations
   - Check logs for unmatched speaker names

4. **AI provider errors**
   - Verify API keys are set correctly in environment variables
   - Check API quotas and rate limits
   - For Ollama, ensure the server is running and accessible

5. **Memory issues with large batches**
   - Reduce `--batch-size` parameter
   - Process data in smaller chunks

### Logs

All commands include detailed logging. Check the console output for progress and any errors. Use `--verbose` flag for more detailed output.

## Deployment

The project includes a deployment script (`copy_to_server.sh`) for syncing files and database to a production server.

### Prerequisites

- SSH access to the production server
- Docker and Docker Compose installed on the server
- PostgreSQL running in Docker on the server
- Nginx configured on the server (optional, for web server)

### Configuration

Set the server address using one of these methods:

**Method 1: Environment Variable**
```bash
export DEPLOY_SERVER="user@your-server.com"
export DEPLOY_PATH="/path/to/deployment"  # Optional, defaults to /home/user/parliament_tracker
```

**Method 2: Command Line Argument**
```bash
./copy_to_server.sh --server=user@your-server.com --path=/path/to/deployment
```

### Deployment Options

**Default Mode (Merge Database)**
```bash
./copy_to_server.sh
```
- Syncs files to server
- Creates database dump locally
- Uploads and merges database dump on server
- Restarts Docker services
- Reloads nginx

**Replace Database Mode**
```bash
./copy_to_server.sh --replace-db
```
- Completely replaces the target database
- Drops and recreates the database
- Imports fresh database dump
- Use with caution - this will delete all existing data on the server

**Skip Database Mode**
```bash
./copy_to_server.sh --skip-db
```
- Only syncs files (code changes)
- Skips all database operations
- Faster for code-only deployments
- Restarts web service and reloads nginx

### What Gets Deployed

The script syncs:
- All project files (code, templates, static files)
- Database dump (if not using `--skip-db`)

The script excludes:
- `.git/` directory
- `__pycache__/` and `*.pyc` files
- `.env` file (keep your production secrets safe!)
- `*.log` files
- `db_dump_*.sql` files (old dumps)

### Server Requirements

The production server should have:
- Docker and Docker Compose installed
- PostgreSQL container running
- Nginx (optional, for reverse proxy)
- SSH access configured
- User with sudo privileges for Docker operations

### Deployment Process

1. **Local Preparation**
   - Ensure Docker containers are running locally
   - Verify database is up to date
   - Test changes locally

2. **Run Deployment Script**
   ```bash
   ./copy_to_server.sh
   ```

3. **What Happens on Server**
   - Files are synced via rsync
   - Database dump is uploaded (if not skipped)
   - Web service is stopped
   - Database is imported/merged
   - Docker services are restarted
   - Nginx is reloaded
   - Temporary files are cleaned up

### Troubleshooting Deployment

1. **SSH Connection Issues**
   - Verify SSH access: `ssh user@your-server.com`
   - Check SSH key authentication
   - Ensure firewall allows SSH

2. **Docker Issues on Server**
   - Verify Docker is running: `docker ps`
   - Check Docker Compose version: `docker compose version`
   - Ensure user has Docker permissions

3. **Database Import Errors**
   - Check database container is running
   - Verify database credentials
   - Check disk space on server

4. **Permission Errors**
   - Ensure user has sudo privileges
   - Check file permissions on server
   - Verify Docker socket permissions

### Manual Deployment Steps

If you prefer manual deployment:

```bash
# 1. Create database dump
docker-compose exec -T db pg_dump -U postgres -d parliament_tracker --no-owner --no-privileges > db_dump.sql

# 2. Sync files
rsync -avzP --delete \
  --exclude='.git/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='.env' \
  --exclude='*.log' \
  --exclude='db_dump_*.sql' \
  . user@server:/path/to/deployment/

# 3. Upload database dump
rsync -avzP db_dump.sql user@server:/path/to/deployment/

# 4. SSH to server and import
ssh user@server
cd /path/to/deployment
docker compose stop web
docker compose exec -T db psql -U postgres -d parliament_tracker < db_dump.sql
docker compose up -d
systemctl reload nginx
rm db_dump.sql
```

## Development

### Running Tests

```bash
python manage.py test
```

### Code Style

The project follows Django conventions and PEP 8 style guidelines.

### Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## License

This project is for educational and research purposes, using public data from the Estonian Parliament API.

## Acknowledgments

- Estonian Parliament (Riigikogu) for providing the public API
- Django community for the excellent framework
- AI providers (Anthropic, OpenAI, Google, Ollama) for their services
