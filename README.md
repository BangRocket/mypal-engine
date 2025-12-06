# Clara

AI assistant with session management and persistent memory.

## Installation

```bash
poetry install
```

## Usage

```bash
poetry run python app.py
```

This starts a Gradio web interface at `http://localhost:7860`.

## Features

- Session-based conversations with automatic timeout handling
- User memory for persistent facts and preferences
- Project memory for topic-specific context
- SQLite storage via SQLAlchemy
