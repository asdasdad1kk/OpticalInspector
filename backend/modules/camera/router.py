from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .service import camera_service


router = APIRouter(
    prefix="/camera",
    tags=["Camera"],
)


class CameraStartRequest(BaseModel):
    width: int = Field(default=1280, gt=0)
    height: int = Field(default=720, gt=0)
    fps: int = Field(default=30, gt=0, le=60)
    preview_quality: int = Field(default=75, ge=30, le=95)
    provider: str | None = Field(default=None, description="realsense | brio")
    device_index: int | None = Field(default=None, ge=0)


@router.post("/start")
def start_camera(data: CameraStartRequest) -> dict:
    try:
        camera_status = camera_service.start(
            width=data.width,
            height=data.height,
            fps=data.fps,
            preview_quality=data.preview_quality,
            provider=data.provider,
            device_index=data.device_index,
        )
        return {
            "message": "Cámara iniciada correctamente.",
            **camera_status,
        }
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@router.post("/restart")
def restart_camera(data: CameraStartRequest) -> dict:
    try:
        camera_status = camera_service.restart(
            width=data.width,
            height=data.height,
            fps=data.fps,
            preview_quality=data.preview_quality,
            provider=data.provider,
            device_index=data.device_index,
        )
        return {
            "message": "Cámara reiniciada correctamente.",
            **camera_status,
        }
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@router.get("/devices")
def list_camera_devices() -> dict:
    current = camera_service.status
    if current["running"]:
        # No se reabre un dispositivo en uso: se informa el activo.
        return {
            "providers": ["realsense", "brio"],
            "devices": [{
                "provider": current["provider"],
                "index": current["device_index"],
                "name": current["device_name"] or current["provider"],
                "serial": current["serial_number"],
            }],
        }
    return {
        "providers": ["realsense", "brio"],
        "devices": camera_service.list_devices(),
    }


@router.post("/stop")
def stop_camera() -> dict:
    camera_status = camera_service.stop()
    return {
        "message": "Cámara detenida correctamente.",
        **camera_status,
    }


@router.get("/status")
def get_camera_status() -> dict:
    return camera_service.status


@router.get("/snapshot.png")
def get_png_snapshot() -> Response:
    try:
        image = camera_service.capture_png()
        return Response(
            content=image,
            media_type="image/png",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
            },
        )
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/snapshot.jpg")
def get_jpeg_snapshot(quality: int = 95) -> Response:
    try:
        image = camera_service.capture_jpeg(quality=quality)
        return Response(
            content=image,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
            },
        )
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.websocket("/ws")
async def camera_websocket(websocket: WebSocket) -> None:
    """Envía frames JPEG binarios.

    El cliente debe configurar `binaryType = "arraybuffer"`. El endpoint
    limita el envío a `max_fps`, aunque la cámara capture a una tasa mayor.
    """

    await websocket.accept()

    try:
        max_fps_text = websocket.query_params.get("max_fps", "15")
        try:
            max_fps = max(1, min(int(max_fps_text), 30))
        except ValueError:
            max_fps = 15

        frame_interval = 1.0 / max_fps
        last_sequence = -1

        while True:
            if not camera_service.status["running"]:
                await websocket.send_json({
                    "type": "status",
                    "running": False,
                    "frame_available": False,
                    "message": "La cámara no está iniciada.",
                })
                await asyncio.sleep(0.5)
                continue

            frame = await asyncio.to_thread(
                camera_service.wait_for_preview,
                last_sequence,
                1.0,
            )

            if frame is None:
                current_status = camera_service.status
                if current_status["last_error"]:
                    await websocket.send_json({
                        "type": "error",
                        "message": current_status["last_error"],
                    })
                continue

            last_sequence = frame.sequence
            await websocket.send_bytes(frame.jpeg)
            await asyncio.sleep(frame_interval)

    except WebSocketDisconnect:
        return
    except Exception as error:
        try:
            await websocket.send_json({
                "type": "error",
                "message": str(error),
            })
        except Exception:
            pass
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
