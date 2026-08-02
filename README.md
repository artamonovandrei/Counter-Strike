# Counter-Strike (WebStrike)

Browserowa gra FPS (Team Deathmatch) — backend Python (FastAPI + Socket.IO), klient Three.js.

Repozytorium aplikacji do pracy dyplomowej DevOps. Infrastruktura (Terraform, Ansible, Jenkins, k3s, monitoring) jest w osobnym repo: [devops-diploma-infra](https://github.com/artamonovandrei/devops-diploma-infra).

## Stack

- Backend: Python 3.12, FastAPI, python-socketio
- Frontend: Node 22, Vite, TypeScript, Three.js
- Proxy / static: Caddy (obraz `web`)
- Testy: pytest (backend), typecheck + build (frontend)

## Uruchomienie lokalne (Docker)

```bash
cp .env.example .env
docker compose up --build
```

Gra: http://localhost  
API health: http://localhost/api/health  
Proxy health: http://localhost/healthz

Bez Dockera: `make install` potem `make dev` (backend :8000, frontend :5173).

## CI/CD

- **Jenkins** (główny pipeline dyplomu) — plik `Jenkinsfile` w rootcie  
  - dowolna gałąź: testy → build obrazów → push Docker Hub → e-mail  
  - `main`: dodatkowo deploy na k3s  
- GitHub Actions: `.github/workflows/ci.yml` (testy / lint / parity)

## Deploy na k3s

Manifesty Kubernetes leżą w `devops-diploma-infra/kubernetes/apps/`.  
Po wdrożeniu UI jest na NodePort **30080** (`http://<k3s-ip>:30080`).

## Autor

artamonovandrei
