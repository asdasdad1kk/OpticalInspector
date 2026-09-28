# Optical Inspector Frontend

## Ejecutar

```bash
npm install
npm run dev
```

Copia `.env.example` como `.env` si FastAPI no corre en `127.0.0.1:8000`.

## Endpoints consumidos

- POST `/upload/dxf`
- POST `/camera/start`
- POST `/camera/stop`
- GET `/camera/status`
- WS `/camera/ws?max_fps=15`
- POST `/inspect/capture`
- GET `/inspect/{id}/original-image`
- GET `/inspect/{id}/rectified-image`
- GET `/inspect/{id}/overlay-image`

## CORS esperado en FastAPI

Permite `http://localhost:5173` durante desarrollo.
