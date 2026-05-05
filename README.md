# Super Personal Platform

A compact personal platform served as one deployable Python application. The frontend is built with Vite and served by the FastAPI backend on port `8888`.

## Requirements

- Python 3.12.x
- Node.js 18+
- npm 10+

## Setup

```bash
cp config.example.yaml config.yaml
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cd web
npm install
npm run build
cd ..
./run.sh
```

Open `http://localhost:8888` and log in with the token from `config.yaml`.

## Project Memory

`PROJECT_MEMORY.md` is part of the project contract. Update it whenever implementation changes behavior, architecture, commands, or operating assumptions.
