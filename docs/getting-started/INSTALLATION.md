# Installation Guide

This guide covers installing **JARVIS 1.0.0** on **Windows 11**.

---

## System Requirements

| Component | Requirement | Notes |
| :--- | :--- | :--- |
| **Operating System** | Windows 11 | Uses Win32 DPAPI, `RegisterHotKey`, Win32 mutex, and `HKCU\...\Run` |
| **Python** | `>=3.12` | Verified on Python `3.12.10` |
| **Database** | PostgreSQL 16 + `pgvector` | Provided via `database/docker-compose.yml` (`pgvector/pgvector:pg16`) |
| **Local AI Runtime** | Ollama | Default model `qwen3:8b`; embedding model `nomic-embed-text` |
| **Optional Hardware** | Microphone, speakers, webcam | Required only for `voice` and `vision` extras |

---

## 1. Clone the Repository

```powershell
git clone <your-repository-url> Jarvis
Set-Location Jarvis
```

---

## 2. Create a Virtual Environment

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

---

## 3. Install Dependencies

Install the core package (plus `dev` tools if running tests or linting):

```powershell
python -m pip install -e ".[dev]"
```

Optional capability extras (install progressively as needed):

```powershell
# Voice (sounddevice, faster-whisper, piper-tts, openwakeword)
python -m pip install -e ".[voice]"

# Vision (opencv-python, insightface, mediapipe)
python -m pip install -e ".[vision]"

# System telemetry (psutil)
python -m pip install -e ".[telemetry]"

# Windows executable packaging (pyinstaller)
python -m pip install -e ".[packaging]"
```

See [Dependencies](DEPENDENCIES.md) for the exact version constraints from `pyproject.toml`.

---

## 4. Start PostgreSQL + pgvector

Using Docker Desktop and the repository's Compose file (`database/docker-compose.yml`):

```powershell
docker compose -f database/docker-compose.yml up -d
docker ps --filter "name=jarvis-db"
```

The container maps port `5433` on `localhost` to avoid colliding with any native PostgreSQL instance on `5432`.

---

## 5. Install and Pull Ollama Models

Install Ollama for Windows and pull the default chat and embedding models:

```powershell
ollama pull qwen3:8b
ollama pull nomic-embed-text
```

---

## Next Step

Proceed to the [Setup Guide](SETUP_GUIDE.md) to configure `.env`, initialize the database schema, and optionally import a Groq API key.
