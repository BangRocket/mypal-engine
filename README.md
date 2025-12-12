# MyPalClara

AI assistant with session management and persistent memory. The assistant's name is Clara.

## Installation

```bash
poetry install
```

## Usage

### Development

Backend:
```bash
poetry run python api.py
```

Frontend:
```bash
cd frontend
npm install
npm run dev
```

### Docker

```bash
docker-compose up
```

## Features

- Threaded chat interface built with assistant-ui
- Session-based conversations with automatic timeout handling
- User memory for persistent facts and preferences (via mem0)
- Project memory for topic-specific context
- SQLite storage via SQLAlchemy
- Multiple LLM backend support (OpenRouter, NanoGPT)
