# Provider Configuration

This project supports two providers for generating summaries:

## 1. Claude (Default)

To use Claude AI:

1. Set environment variables:
   ```bash
   export AI_PROVIDER=claude
   export CLAUDE_API_KEY=your-claude-api-key-here
   ```

2. Or update your `.env` file:
   ```
   AI_PROVIDER=claude
   CLAUDE_API_KEY=your-claude-api-key-here
   ```

## 2. Ollama (Local)

To use Ollama with GPT-OSS-20B locally:

### Prerequisites
- A machine with sufficient GPU memory (recommended: 16GB+ VRAM for 20B model)
- Ollama installed on your system

### Setup Ollama with GPT-OSS-20B

1. **Install Ollama** (if not already installed):
   ```bash
   # macOS
   brew install ollama
   
   # Linux
   curl -fsSL https://ollama.ai/install.sh | sh
   
   # Or download from https://ollama.ai/download
   ```

2. **Pull the GPT-OSS-20B model**:
   ```bash
   ollama pull gpt-oss:20b
   ```

3. **Start Ollama server** (usually runs automatically, but you can start manually):
   ```bash
   ollama serve
   ```

4. **Configure the Django application**:

   **For local development (non-Docker):**
   ```bash
   export AI_PROVIDER=ollama
   export OLLAMA_BASE_URL=http://localhost:11434
   export OLLAMA_MODEL=gpt-oss:20b
   ```

   **For Docker deployment:**
   ```bash
   export AI_PROVIDER=ollama
   export OLLAMA_BASE_URL=http://host.docker.internal:11434
   export OLLAMA_MODEL=gpt-oss:20b
   ```

   Or in your `.env` file:
   ```
   AI_PROVIDER=ollama
   # For local development:
   OLLAMA_BASE_URL=http://localhost:11434
   # For Docker (uncomment the line below and comment the one above):
   # OLLAMA_BASE_URL=http://host.docker.internal:11434
   OLLAMA_MODEL=gpt-oss:20b
   ```

### Available Models in Ollama
- `gpt-oss:20b` - **Recommended** - High-quality open-source GPT model (20B parameters)
- `qwen2.5:7b` - Alternative Qwen model (7B parameters, less VRAM required)
- `qwen2.5:14b` - Larger Qwen model (14B parameters)
- `llama3.1:8b` - Meta's Llama model (8B parameters)
- `codellama:13b` - Code-focused model (13B parameters)

**Note**: GPT-OSS-20B provides excellent quality but requires more VRAM (16GB+). If you have limited GPU memory, consider using `qwen2.5:7b` instead.

## Docker Configuration

When running the Django application in Docker, you need special configuration to access Ollama running on the host machine.

### Method 1: Using docker-compose (Recommended)

The `docker-compose.yml` is already configured to access the host machine's Ollama service:

```bash
# Start the services
docker-compose up

# Test the AI connection
docker-compose exec web python manage.py generate_ai_summaries_for_speeches --speech-id 1 --dry-run
```

### Method 2: Manual Docker Run

If running Docker manually, use the `--add-host` flag:

```bash
docker run --add-host host.docker.internal:host-gateway \
  -e AI_PROVIDER=ollama \
  -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  -e OLLAMA_MODEL=gpt-oss:20b \
  your-app-image
```

### Method 3: Network Host Mode (Linux only)

On Linux, you can use host networking:

```bash
docker run --network host \
  -e AI_PROVIDER=ollama \
  -e OLLAMA_BASE_URL=http://localhost:11434 \
  -e OLLAMA_MODEL=gpt-oss:20b \
  your-app-image
```

### Testing

To test the provider:

**Local development:**
```bash
python manage.py generate_ai_summaries_for_speeches --speech-id 1 --dry-run
```

**Docker:**
```bash
docker-compose exec web python manage.py generate_ai_summaries_for_speeches --speech-id 1 --dry-run
```

The command will show which provider is being used and test the connection before processing.

## Cost Comparison

- **Claude**: ~$0.01 per API call (varies by model and usage)
- **Ollama (Local)**: Free after initial setup, but requires local compute resources

## Performance Notes

- **Claude**: Fast API responses, no local resource usage
- **Ollama**: Dependent on local hardware, may be slower but provides full control and privacy
- **Model Size Impact**: Larger models (14B) provide better quality but require more VRAM and are slower

## Troubleshooting

### Ollama Issues

1. **Model not found error**:
   ```bash
   ollama list  # Check installed models
   ollama pull gpt-oss:20b  # Pull the model if missing
   ```

2. **Connection refused**:
   ```bash
   ollama serve  # Start the server manually
   # Check if running on correct port (default: 11434)
   ```

3. **Out of memory errors**:
   - Try a smaller model: `qwen2.5:7b` or `llama3.1:8b`
   - Close other applications to free up VRAM
   - Use CPU-only mode (slower): `OLLAMA_NUM_GPU=0 ollama serve`
   - For GPT-OSS-20B, ensure you have at least 16GB VRAM

### Docker Networking Issues

1. **Connection refused from Docker container**:
   - Ensure you're using `http://host.docker.internal:11434` in Docker
   - Check that `extra_hosts` is configured in docker-compose.yml
   - Verify Ollama is running on the host: `curl http://localhost:11434`

2. **host.docker.internal not working (Linux)**:
   ```bash
   # Use host IP address instead
   ip route show default | awk '/default/ {print $3}'
   # Then use: http://YOUR_HOST_IP:11434
   ```

3. **Firewall blocking connections**:
   - Ensure port 11434 is accessible
   - On macOS/Linux: `netstat -an | grep 11434`
   - Configure Ollama to bind to all interfaces: `OLLAMA_HOST=0.0.0.0 ollama serve`

## 4. Local Translation Service

The project includes a local translation service for translating content to English and Russian.

### Setup Translation Service

1. **Navigate to translation service directory**:
   ```bash
   cd local_translation_service
   ```

2. **Start the translation service**:
   ```bash
   ./start.sh
   ```

   The service will be available at `http://localhost:8001`

### Configuration

**For local development (non-Docker):**
```bash
export LOCAL_TRANSLATION_SERVICE_URL=http://localhost:8001
```

**For Docker deployment:**
```bash
export LOCAL_TRANSLATION_SERVICE_URL=http://host.docker.internal:8001
```

Or in your `.env` file:
```
# For local development:
LOCAL_TRANSLATION_SERVICE_URL=http://localhost:8001
# For Docker (uncomment the line below and comment the one above):
# LOCAL_TRANSLATION_SERVICE_URL=http://host.docker.internal:8001
```

### Usage

Use the translation management commands:

```bash
# Translate all agendas from specific plenary session
python manage.py translate_agendas --plenary-session-id 16

# Translate all speeches from specific plenary session  
python manage.py translate_speeches --plenary-session-id 16

# Translate only English agenda titles from session 16
python manage.py translate_agendas --plenary-session-id 16 --target-language en --translate-type titles

# Use OpenAI GPT-5-nano instead of local translation service
python manage.py translate_agendas --plenary-session-id 16 --use-openai
python manage.py translate_speeches --plenary-session-id 16 --use-openai

# Translate only plenary session titles
python manage.py translate_plenary_session_titles
python manage.py translate_plenary_session_titles --session-id 16
python manage.py translate_plenary_session_titles --use-openai --target-language en
```

### Docker Configuration

In `docker-compose.yml`, ensure you have:

```yaml
services:
  web:
    environment:
      - LOCAL_TRANSLATION_SERVICE_URL=http://host.docker.internal:8001
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

### Troubleshooting Translation Service

1. **Connection refused from Docker container**:
   - Ensure you're using `http://host.docker.internal:8001` in Docker
   - Check that `extra_hosts` is configured in docker-compose.yml
   - Verify translation service is running on the host: `curl http://localhost:8001`

2. **Service not responding**:
   ```bash
   # Check if service is running
   curl http://localhost:8001
   
   # Start the service manually
   cd local_translation_service
   python main.py
   ```

3. **Test translation service**:
   ```bash
   python test_translation_service.py
   ```

### Using OpenAI for Translations

As an alternative to the local translation service, you can use OpenAI GPT-5-nano for translations:

**Prerequisites:**
- OpenAI API key configured in environment variables
- `OPENAI_API_KEY` set in your environment or `.env` file
- `OPENAI_MODEL` set to `gpt-5-nano` (default)

**Usage:**
```bash
# Use OpenAI instead of local translation service
python manage.py translate_agendas --plenary-session-id 16 --use-openai
python manage.py translate_speeches --plenary-session-id 16 --use-openai

# Combine with other options
python manage.py translate_agendas --use-openai --target-language en --translate-type titles

# Translate only plenary session titles (dedicated command)
python manage.py translate_plenary_session_titles --use-openai

# Use verbose mode for detailed progress and streaming results
python manage.py translate_agendas --plenary-session-id 16 --verbose
python manage.py translate_speeches --plenary-session-id 16 --use-openai --verbose
```

**Benefits of OpenAI translations:**
- No need to run local translation service
- Higher quality translations
- Supports both English and Russian
- Works from Docker without additional networking setup

**Note:** Using OpenAI will incur API costs based on your usage.

### Verbose Mode for Detailed Progress

All translation commands support a `--verbose` flag for detailed progress information:

```bash
# Show streaming translation results and detailed progress
python manage.py translate_agendas --plenary-session-id 16 --verbose --use-openai
python manage.py translate_speeches --plenary-session-id 16 --verbose
python manage.py translate_plenary_session_titles --verbose
```

**Verbose mode shows:**
- 📝 Original text preview before translation
- Translation service being used (OpenAI or Local)
- ⏱️ API response times
- ✅ Real-time translation results as they arrive
- 📊 Detailed task breakdown (what will be translated)
- 🎯 Progress indicators with ETA calculations

This is especially useful for:
- Debugging translation issues
- Monitoring API performance
- Tracking progress on large batches
- Quality checking translation results

### Testing Ollama Directly

You can test Ollama directly before using it with Django:

```bash
# Test basic functionality
ollama run gpt-oss:20b "Write a short summary of Estonian parliament in Estonian"

# Test API endpoint
curl http://localhost:11434/api/generate \
  -d '{
    "model": "gpt-oss:20b",
    "prompt": "Test prompt",
    "stream": false
  }'
```
